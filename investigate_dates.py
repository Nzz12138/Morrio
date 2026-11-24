"""Check alignment across the three Morrio Vantage CSV exports.

Run: python3 investigate_dates.py
Optional: python3 investigate_dates.py --data-dir /path/to/data

Uses Python's standard library only. Raw files are never modified.
Outputs are investigation flags, not confirmed data errors or pipeline lag.
"""

import argparse
import csv
from collections import Counter
from datetime import date, datetime
from pathlib import Path


def read_csv(path):
    with path.open(newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def write_csv(path, rows, columns):
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def show_range(rows, column):
    values = [row[column] for row in rows if row[column]]
    if values:
        print(f"  {column}: {min(values)} to {max(values)}")
    else:
        print(f"  {column}: no recorded values")
    print(f"  Blank {column}: {len(rows) - len(values):,}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parent
    parser.add_argument("--data-dir", type=Path, default=root / "data")
    parser.add_argument("--output-dir", type=Path, default=root / "outputs")
    args = parser.parse_args()

    # Step 1: Read the original exports.
    accounts = read_csv(args.data_dir / "client_accounts.csv")
    subscriptions = read_csv(args.data_dir / "client_subscriptions.csv")
    events = read_csv(args.data_dir / "product_events.csv")

    for name, rows, columns in [
        ("Accounts", accounts, ["created_date"]),
        ("Subscriptions", subscriptions, ["start_date", "end_date"]),
        ("Events", events, ["event_timestamp"]),
    ]:
        print(f"\n{name}: {len(rows):,} records")
        print(f"  Distinct clients: {len({row['client_id'] for row in rows}):,}")
        for column in columns:
            show_range(rows, column)

    # Step 2: Build one lookup entry per account. Do not silently overwrite IDs.
    account_counts = Counter(row["client_id"] for row in accounts)
    if any(count > 1 for count in account_counts.values()):
        raise ValueError("Duplicate account client IDs found. Review before joining.")
    accounts_by_id = {row["client_id"]: row for row in accounts}
    first_start = {}
    for row in subscriptions:
        if not row["start_date"]:
            continue
        client_id = row["client_id"]
        start = date.fromisoformat(row["start_date"])
        if client_id not in first_start or start < first_start[client_id]:
            first_start[client_id] = start

    # Step 3: Check coverage. A missing event record does not prove inactivity.
    account_ids = set(accounts_by_id)
    event_ids = {row["client_id"] for row in events}
    subscription_ids = {row["client_id"] for row in subscriptions}
    missing_accounts = [row for row in accounts if row["client_id"] not in event_ids]
    unmatched_ids = [
        {"source": source, "client_id": client_id}
        for source, ids in [("subscriptions", subscription_ids), ("events", event_ids)]
        for client_id in sorted(ids - account_ids)
    ]
    print(f"\nAccounts with no events in this export: {len(missing_accounts):,}")
    print(f"Source/client ID pairs absent from CRM: {len(unmatched_ids):,}")

    # Step 4: Compare calendar dates, using the UTC event date.
    # CRM and billing dates have no timezone or time-of-day information.
    flagged = []
    skipped = 0
    for event in events:
        client_id = event["client_id"]
        account = accounts_by_id.get(client_id)
        start = first_start.get(client_id)
        if not account or not account["created_date"] or start is None:
            skipped += 1
            continue
        event_day = date.fromisoformat(event["event_timestamp"][:10])
        created = date.fromisoformat(account["created_date"])
        account_gap = (created - event_day).days
        subscription_gap = (start - event_day).days
        if account_gap > 0 or subscription_gap > 0:
            flagged.append({
                **event,
                "account_created_date": created.isoformat(),
                "first_subscription_start_date": start.isoformat(),
                "days_before_account_creation": account_gap,
                "days_before_first_subscription": subscription_gap,
            })

    flagged.sort(key=lambda row: row["days_before_account_creation"], reverse=True)
    print(f"\nFlagged events: {len(flagged):,}")
    print(f"Affected clients: {len({row['client_id'] for row in flagged}):,}")
    print(f"Events skipped due to missing account/start information: {skipped:,}")
    for column in ["platform", "app_version", "event_type"]:
        print(f"Flagged events by {column}: {dict(Counter(row[column] for row in flagged))}")

    # Step 5: Save a separate folder for each run, preserving earlier results.
    output = args.output_dir / datetime.now().strftime("date_check_%Y%m%d_%H%M%S_%f")
    output.mkdir(parents=True, exist_ok=False)
    event_columns = [
        "event_id", "client_id", "user_id", "event_type", "event_timestamp",
        "platform", "app_version", "account_created_date",
        "first_subscription_start_date", "days_before_account_creation",
        "days_before_first_subscription",
    ]
    write_csv(output / "event_date_investigation.csv", flagged, event_columns)
    write_csv(output / "accounts_without_events.csv", missing_accounts, [
        "client_id", "client_name", "created_date", "client_tier", "primary_region",
        "onboarding_cohort", "primary_use_case", "annual_contract_value",
        "store_location_count", "assigned_analyst_seats",
    ])
    write_csv(output / "unmatched_client_ids.csv", unmatched_ids, ["source", "client_id"])
    print(f"\nInvestigation files saved to: {output}")
    print("These checks do not establish pipeline lag or prove that flagged data is wrong.")


if __name__ == "__main__":
    main()
