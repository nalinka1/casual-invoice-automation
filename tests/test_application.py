import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import pymupdf
import pytest
from cleaning_invoice.configuration import load_config, validate, address
from cleaning_invoice.calendar_rules import billable_dates, previous_month, local_date
from cleaning_invoice.invoice_generator import calculate, total, generate_pdf, fields, invoice_filename
from cleaning_invoice.email_service import content, message, MockGmail
from cleaning_invoice.invoice_ledger import Ledger
from cleaning_invoice.notifications import MockSNS
from cleaning_invoice.monthly_job import run
from cleaning_invoice.run_monitor import check

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 5, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("year,month,expected", [(2026, 8, "Example_AUGUST_26_INVOICE.pdf"), (2026, 9, "Example_SEPTEMBER_26_INVOICE.pdf"), (2027, 1, "Example_JANUARY_27_INVOICE.pdf")])
def test_invoice_filename(year, month, expected):
    assert invoice_filename(year, month, "Example") == expected.replace("Example", "Example")


@pytest.fixture
def workflow(config, tmp_path):
    config["invoice"]["number_status"] = "confirmed"
    config["email"].update(to="client@example.com", draft_account_email="draft@example.com")
    config["aws"]["account_login_email"] = "aws@example.com"
    config["notifications"]["recipient_email"] = "alerts@example.com"
    ledger = Ledger(tmp_path / "ledger.sqlite", 11)
    yield config, ledger, MockGmail(ledger), MockSNS(ledger, "alerts@example.com", True)
    ledger.close()


@pytest.mark.parametrize("month", [8, 9, 10, 11])
def test_verified_months(config, month):
    charges = calculate(config, 2026, month)
    assert [len(c.dates) for c in charges] == [21, 21]
    assert [c.amount for c in charges] == [Decimal("714.00"), Decimal("850.50")]
    assert total(charges) == Decimal("1564.50")


def test_regional_holidays_and_leave(config):
    first, second = [s["id"] for s in config["sites"]]
    assert date(2026, 10, 21) not in billable_dates(config, 2026, 10, first)
    assert date(2026, 11, 3) in billable_dates(config, 2026, 11, first)
    config["date_overrides"]["global"]["2026-10-29"] = {"billable": False, "reason": "leave"}
    config["date_overrides"]["by_site"][first]["2026-10-29"] = {"billable": True, "reason": "worked"}
    config["date_overrides"]["by_site"][second]["2026-10-21"] = {"billable": True, "reason": "worked holiday"}
    assert len(billable_dates(config, 2026, 10, first)) == 21
    assert len(billable_dates(config, 2026, 10, second)) == 21
    with pytest.raises(ValueError, match="calendar"):
        billable_dates(config, 2027, 1, first)
    with pytest.raises(ValueError, match="Unknown"):
        billable_dates(config, 2026, 1, "unknown")


@pytest.mark.parametrize("instant,expected", [(datetime(2026, 9, 30, 14, tzinfo=timezone.utc), date(2026, 10, 1)), (datetime(2026, 12, 31, 13, tzinfo=timezone.utc), date(2027, 1, 1))])
def test_timezone_rollover(instant, expected):
    assert local_date(instant, "Australia/Melbourne") == expected
    assert previous_month(date(2027, 1, 1)) == (2026, 12)
    with pytest.raises(ValueError):
        local_date(datetime(2026, 1, 1), "Australia/Melbourne")


@pytest.mark.parametrize("mutation", [
    lambda c: c["sites"][0].update(daily_rate_aud="NaN"),
    lambda c: c["sites"][0].update(daily_rate_aud="-1"),
    lambda c: c["sites"][0].update(rate_status="unconfirmed"),
    lambda c: c["date_overrides"]["by_site"].update(unknown={}),
    lambda c: c["date_overrides"]["global"].update({"2026-10-01": {"billable": "false", "reason": "leave"}}),
    lambda c: c["date_overrides"]["global"].update({"2026-10-01": {"billable": False, "reason": ""}}),
    lambda c: c["email"].update(to="invalid"),
    lambda c: c["email"].update(send_enabled=True),
])
def test_invalid_config(config, mutation):
    mutation(config)
    with pytest.raises(ValueError):
        validate(config)


def test_duplicate_json_keys(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"x": 1, "x": 2}')
    with pytest.raises(ValueError, match="Duplicate"):
        load_config(path)


def test_pdf_preserves_pixels_and_constants(config, tmp_path):
    source = pymupdf.open(ROOT / config["invoice"]["source_pdf"])
    output = generate_pdf(config, calculate(config, 2026, 8), 123, date(2026, 10, 11), tmp_path / "invoice.pdf")
    result = pymupdf.open(output)
    assert len(result) == 1
    text = result[0].get_text()
    assert "123" in text and "11/10/2026" in text and "1564.50" in text
    # Raster comparison outside all five changed areas: every other pixel must be identical.
    original = bytearray(source[0].get_pixmap().samples)
    rendered = bytearray(result[0].get_pixmap().samples)
    pix = source[0].get_pixmap()
    for key, (rect, origin, size) in fields(source[0]).items():
        rect.x1 = 544 if key in ("site1", "site2", "total") else 580
        for y in range(int(rect.y0)-2, int(rect.y1)+3):
            start = (y * pix.width + int(rect.x0)-2) * pix.n
            end = (y * pix.width + int(rect.x1)+2) * pix.n
            original[start:end] = rendered[start:end]
    assert original == rendered
    source.close()
    result.close()


def test_dry_run_no_integrations(config, tmp_path):
    result = run(config, tmp_path, NOW)
    assert result["status"] == "dry_run"
    assert result["invoice_number"] == 12
    assert not list(tmp_path.glob("*.sqlite"))
    assert len(list(tmp_path.glob("*.pdf"))) == 1
    assert "21 Days" in next(tmp_path.glob("*-email.txt")).read_text(encoding="utf-8")


def test_email_exact_content_and_attachment(workflow, tmp_path):
    c, ledger, gmail, sns = workflow
    charges = calculate(c, 2026, 8)
    subject, body = content(c, 2026, 8, charges)
    assert subject == "August 2026, Month Cleaning Invoice"
    assert body == c["email"]["body_template"].format(month_name="August", year=2026, corrections_days=21, vision_days=21)
    pdf = generate_pdf(c, charges, 12, NOW.date(), tmp_path / "a.pdf")
    msg = message(c, 2026, 8, charges, pdf)
    assert msg["From"] != c["aws"]["account_login_email"]
    assert list(msg.iter_attachments())[0].get_payload(decode=True) == pdf.read_bytes()
    assert not hasattr(gmail, "send")


def test_retry_reminder_manual_sent(workflow, tmp_path):
    c, ledger, gmail, sns = workflow
    first = run(c, tmp_path, NOW, ledger, gmail, sns)
    assert first["status"] == "awaiting_approval"
    assert run(c, tmp_path, NOW, ledger, gmail, sns)["number"] == 12
    assert ledger.db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 1
    assert ledger.db.execute("SELECT COUNT(*) FROM events WHERE key LIKE 'ready:%'").fetchone()[0] == 1
    later = datetime(2026, 10, 7, 0, tzinfo=timezone.utc)
    check(c, ledger, gmail, sns, later)
    check(c, ledger, gmail, sns, later)
    assert ledger.db.execute("SELECT COUNT(*) FROM events WHERE key LIKE 'reminder:%'").fetchone()[0] == 1
    gmail.simulate_deletion("2026-09")
    check(c, ledger, gmail, sns, later)
    assert ledger.get("2026-09")["status"] == "awaiting_approval"
    gmail.simulate_manual_send("2026-09")
    check(c, ledger, gmail, sns, later)
    assert ledger.get("2026-09")["status"] == "sent_confirmed"


def test_missing_run_without_job(workflow):
    c, ledger, gmail, sns = workflow
    check(c, ledger, gmail, sns, datetime(2026, 10, 6, tzinfo=timezone.utc))
    check(c, ledger, gmail, sns, datetime(2026, 10, 6, tzinfo=timezone.utc))
    assert ledger.get("2026-09") is None
    assert ledger.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_failure_and_recovery(workflow, tmp_path, monkeypatch):
    import cleaning_invoice.monthly_job as job
    c, ledger, gmail, sns = workflow
    original = job.generate_pdf
    def fail(*args):
        raise RuntimeError("private error data")
    monkeypatch.setattr(job, "generate_pdf", fail)
    with pytest.raises(RuntimeError):
        run(c, tmp_path, NOW, ledger, gmail, sns)
    assert ledger.get("2026-09")["status"] == "failed"
    assert "private" not in ledger.get("2026-09")["error"]
    assert ledger.db.execute("SELECT key FROM events").fetchone()[0] == "failure:2026-09"
    monkeypatch.setattr(job, "generate_pdf", original)
    assert run(c, tmp_path, NOW, ledger, gmail, sns)["number"] == 12


def test_subscription_and_missing_addresses(workflow, tmp_path):
    c, ledger, gmail, sns = workflow
    sns.subscription_confirmed = False
    with pytest.raises(ValueError, match="subscription"):
        run(c, tmp_path, NOW, ledger, gmail, sns)
    assert ledger.db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0
    for value in (None, "", "bad", "x@y.com\nBcc:bad@example.com"):
        with pytest.raises(ValueError):
            address(value)
    c["email"]["draft_account_email"] = None
    with pytest.raises(ValueError, match="email"):
        run(c, tmp_path, NOW, ledger, gmail, sns)


def test_atomic_numbering(tmp_path):
    path = tmp_path / "state.sqlite"
    Ledger(path, 11).close()
    def reserve(key):
        ledger = Ledger(path, 11)
        try:
            return ledger.reserve(key, NOW.isoformat())["number"]
        finally:
            ledger.close()
    with ThreadPoolExecutor(max_workers=4) as executor:
        same = list(executor.map(reserve, ["2026-09"] * 4))
        other = list(executor.map(reserve, ["2026-10", "2026-11", "2026-12"]))
    assert same == [12] * 4
    assert sorted(other) == [13, 14, 15]
    with pytest.raises(ValueError, match="baseline"):
        Ledger(path, 12)


def test_notification_failure_after_draft_retry(workflow, tmp_path, monkeypatch):
    c, ledger, gmail, sns = workflow
    original = sns.publish
    def fail_ready(key, payload):
        if key.startswith("ready:"):
            raise RuntimeError("mock transport failed")
        return original(key, payload)
    monkeypatch.setattr(sns, "publish", fail_ready)
    with pytest.raises(RuntimeError):
        run(c, tmp_path, NOW, ledger, gmail, sns)
    assert ledger.get("2026-09")["draft_id"] == "mock-2026-09"
    monkeypatch.setattr(sns, "publish", original)
    assert run(c, tmp_path, NOW, ledger, gmail, sns)["status"] == "awaiting_approval"
    assert ledger.db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 1


def test_draft_deleted_before_reminder(workflow, tmp_path):
    c, ledger, gmail, sns = workflow
    run(c, tmp_path, NOW, ledger, gmail, sns)
    gmail.simulate_deletion("2026-09")
    check(c, ledger, gmail, sns, datetime(2026, 10, 7, tzinfo=timezone.utc))
    payload = ledger.db.execute("SELECT payload FROM events WHERE key LIKE 'reminder:%'").fetchone()[0]
    assert json.loads(payload)["status"] == "delivery_unconfirmed"
    assert ledger.get("2026-09")["status"] != "sent_confirmed"
