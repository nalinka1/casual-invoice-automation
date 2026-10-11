import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

SITE_IDS = ("corrections_north_geelong", "vision_australia_geelong")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def address(value):
    if not isinstance(value, str) or not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value):
        raise ValueError("A valid email address is required")
    return value


def load_config(path="config.json"):
    path = Path(path).resolve()
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate configuration key: {key}")
            result[key] = value
        return result
    config = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    validate(config)
    config["_root"] = str(path.parent)
    return config


def validate(c):
    ZoneInfo(c["timezone"])
    if c["currency"] != "AUD" or c["billing_period"] != "previous_calendar_month":
        raise ValueError("Only AUD previous-month billing is supported")
    if not c["working_weekdays"] or any(d not in WEEKDAYS for d in c["working_weekdays"]):
        raise ValueError("Invalid working weekdays")
    if tuple(s["id"] for s in c["sites"]) != SITE_IDS:
        raise ValueError("Exactly the two configured sites in template order are required")
    for s in c["sites"]:
        try:
            rate = Decimal(s["daily_rate_aud"])
        except (InvalidOperation, TypeError):
            raise ValueError("Invalid daily rate") from None
        if not rate.is_finite() or rate <= 0 or rate != rate.quantize(Decimal("0.01")):
            raise ValueError("Rates must be positive finite cent amounts")
        if not s["rate_status"].startswith("confirmed"):
            raise ValueError("Daily rates require confirmation")
    overrides = c["date_overrides"]
    if set(overrides["by_site"]) - set(SITE_IDS):
        raise ValueError("Unknown override site")
    for group in [overrides["global"], *overrides["by_site"].values()]:
        for day, rule in group.items():
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError("Override dates must use YYYY-MM-DD")
            if type(rule.get("billable")) is not bool or not isinstance(rule.get("reason"), str) or not rule["reason"].strip():
                raise ValueError("Overrides need a boolean billable and a reason")
    for key, holidays in c["holiday_calendar"].items():
        if key.endswith("_non_billable_public_holidays"):
            year = int(key[:4])
            for day, name in holidays.items():
                if date.fromisoformat(day).year != year or not name:
                    raise ValueError("Invalid holiday calendar entry")
    for value in (c["email"].get("to"), c["email"].get("draft_account_email"), c["notifications"].get("recipient_email"), c["aws"].get("account_login_email")):
        if value is not None:
            address(value)
    if c["email"].get("send_enabled") is not False or c["email"]["mode"] != "draft_for_review":
        raise ValueError("Only draft-for-review mode is supported")
    number = c["invoice"]["last_confirmed_issued_invoice_number"]
    if type(number) is not int or number < 0:
        raise ValueError("Invalid invoice baseline")
    if not 1 <= c["scheduling"]["monthly_day_of_month"] <= 28 or not 1 <= c["scheduling"]["missed_run_check_day_of_month"] <= 28:
        raise ValueError("Schedule days must be 1..28")
    if c["notifications"]["pending_draft_reminder_after_days"] < 1:
        raise ValueError("Reminder delay must be positive")
