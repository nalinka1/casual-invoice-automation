from datetime import datetime, timedelta
from .calendar_rules import local_date, previous_month


def check(config, ledger, gmail, sns, now):
    if now.tzinfo is None:
        raise ValueError("Monitor requires aware datetime")
    today = local_date(now, config["timezone"])
    year, month = previous_month(today)
    key = f"{year:04d}-{month:02d}"
    row = ledger.get(key)
    if config["notifications"]["missed_run_detection"] and today.day >= config["scheduling"]["missed_run_check_day_of_month"]:
        if row is None or row["status"] in ("scheduled", "running", "failed"):
            sns.publish(f"missed:{key}", {"billing_month": key, "status": "missed_run"})
    for saved in ledger.db.execute("SELECT * FROM runs WHERE draft_id IS NOT NULL AND status != 'sent_confirmed'").fetchall():
        key = saved["month"]
        status = gmail.status(key)
        if status == "sent":
            ledger.update(key, now.isoformat(), status="sent_confirmed")
        elif now >= datetime.fromisoformat(saved["created"]) + timedelta(days=config["notifications"]["pending_draft_reminder_after_days"]):
            sns.publish(f"reminder:{key}:initial", {"billing_month": key, "invoice_number": saved["number"],
                "status": "awaiting_approval" if status == "draft" else "delivery_unconfirmed",
                "instruction": "Review draft" if status == "draft" else "Check Sent or obtain manual confirmation; draft absence is not delivery evidence"})
