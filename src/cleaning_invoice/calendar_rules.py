import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from .configuration import WEEKDAYS


def local_date(now, timezone):
    if isinstance(now, datetime):
        if now.tzinfo is None:
            raise ValueError("Datetime must be timezone-aware")
        return now.astimezone(ZoneInfo(timezone)).date()
    return now if now is not None else datetime.now(ZoneInfo(timezone)).date()


def previous_month(today):
    end = today.replace(day=1) - timedelta(days=1)
    return end.year, end.month


def billable_dates(config, year, month, site_id):
    if site_id not in {s["id"] for s in config["sites"]}:
        raise ValueError("Unknown site")
    key = f"{year}_non_billable_public_holidays"
    if key not in config["holiday_calendar"]:
        raise ValueError(f"No verified holiday calendar for {year}")
    holidays = config["holiday_calendar"][key]
    overrides = config["date_overrides"]
    result = []
    for day in range(1, calendar.monthrange(year, month)[1] + 1):
        d = date(year, month, day)
        billable = WEEKDAYS[d.weekday()] in config["working_weekdays"] and d.isoformat() not in holidays
        rule = overrides["by_site"].get(site_id, {}).get(d.isoformat(), overrides["global"].get(d.isoformat()))
        if rule:
            billable = rule["billable"]
        if billable:
            result.append(d)
    return tuple(result)
