import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from .calendar_rules import local_date, previous_month
from .configuration import load_config, address
from .email_service import content, message, MockGmail
from .invoice_generator import calculate, generate_pdf, total, invoice_filename
from .invoice_ledger import Ledger
from .notifications import MockSNS


def run(config, output="output", now=None, ledger=None, gmail=None, sns=None):
    today = local_date(now, config["timezone"])
    year, month = previous_month(today)
    key = f"{year:04d}-{month:02d}"
    charges = calculate(config, year, month)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    summary = {"billing_month": key, "site_days": {s.site_id: len(s.dates) for s in charges},
               "billable_dates": {s.site_id: [d.isoformat() for d in s.dates] for s in charges},
               "amounts": {s.site_id: str(s.amount) for s in charges}, "total": str(total(charges))}
    if ledger is None:
        number = config["invoice"]["last_confirmed_issued_invoice_number"] + 1
        pdf = generate_pdf(config, charges, number, today, output / invoice_filename(year, month, config['invoice'].get('filename_prefix', 'Example')))
        subject, body = content(config, year, month, charges)
        (output / f"preview-{key}-email.txt").write_text(f"Subject: {subject}\n\n{body}\n", encoding="utf-8")
        summary.update(invoice_number=number, number_status="provisional_unreserved", status="dry_run", pdf=str(pdf))
        (output / f"preview-{key}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary
    if not isinstance(gmail, MockGmail) or not isinstance(sns, MockSNS):
        raise ValueError("Only local mock integrations are supported")
    if config["invoice"]["number_status"] != "confirmed":
        raise ValueError("Reconcile last issued invoice number and set number_status to confirmed for mock reservations")
    stamp = datetime.now(ZoneInfo(config["timezone"])) if now is None else now
    if not isinstance(stamp, datetime) or stamp.tzinfo is None:
        raise ValueError("Mock workflow requires an aware datetime")
    timestamp = stamp.isoformat()
    row = ledger.reserve(key, timestamp)
    if row["status"] == "sent_confirmed":
        return row
    try:
        address(config["email"]["draft_account_email"])
        address(config["email"]["to"])
        sns.preflight()
        # A database write lock serializes competing local job processes through draft creation.
        with ledger.transaction():
            row = ledger.get(key)
            if row["status"] == "sent_confirmed":
                return row
            if not row["draft_id"]:
                pdf = output / invoice_filename(year, month, config['invoice'].get('filename_prefix', 'Example'))
                if pdf.exists():
                    pdf.unlink()
                generate_pdf(config, charges, row["number"], today, pdf)
                msg = message(config, year, month, charges, pdf)
                # Insert directly inside the same atomic local transaction.
                ledger.db.execute("INSERT OR IGNORE INTO drafts VALUES (?, ?, ?, 'draft')", (key, f"mock-{key}", msg.as_bytes()))
                checksum = hashlib.sha256(pdf.read_bytes()).hexdigest()
                ledger.db.execute("UPDATE runs SET status='awaiting_approval', draft_id=?, pdf=?, checksum=?, summary=?, error=NULL, updated=? WHERE month=?",
                                  (f"mock-{key}", str(pdf.resolve()), checksum, json.dumps(summary), timestamp, key))
            elif row["status"] == "failed":
                ledger.db.execute("UPDATE runs SET status='awaiting_approval', error=NULL, updated=? WHERE month=?", (timestamp, key))
        row = ledger.get(key)
        if config["notifications"]["success"]:
            saved = json.loads(row["summary"])
            sns.publish(f"ready:{key}", {"billing_month": key, "invoice_number": row["number"], "status": "awaiting_approval",
                                        "site_days": saved["site_days"], "total": saved["total"], "instruction": "Review the local mock draft; no email was sent."})
        return ledger.get(key)
    except Exception as exc:
        ledger.update(key, timestamp, status="failed", error=type(exc).__name__)
        try:
            if config["notifications"]["failure"]:
                sns.publish(f"failure:{key}", {"billing_month": key, "invoice_number": row["number"], "status": "failed"})
        except Exception:
            ledger.event(f"notification-failure:{key}", {"status": "notification_failed"})
        raise


def main():
    parser = argparse.ArgumentParser(description="Local casual services invoice previews and mock workflow; never sends email")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--output", default="output")
    parser.add_argument("--mock-workflow", action="store_true")
    parser.add_argument("--mock-subscription-confirmed", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.mock_workflow:
        Path(args.output).mkdir(parents=True, exist_ok=True)
        ledger = Ledger(Path(args.output) / "ledger.sqlite", config["invoice"]["last_confirmed_issued_invoice_number"])
        try:
            result = run(config, args.output, ledger=ledger, gmail=MockGmail(ledger),
                         sns=MockSNS(ledger, config["notifications"]["recipient_email"], args.mock_subscription_confirmed))
            print(json.dumps({k: result[k] for k in ("month", "number", "status")}, indent=2))
        finally:
            ledger.close()
    else:
        print(json.dumps(run(config, args.output), indent=2))


if __name__ == "__main__":
    main()
