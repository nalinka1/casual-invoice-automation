from email.message import EmailMessage
from pathlib import Path
import calendar
from .configuration import address


def content(config, year, month, charges):
    values = dict(month_name=calendar.month_name[month], year=year,
                  corrections_days=len(charges[0].dates), vision_days=len(charges[1].dates))
    return config["email"]["subject_template"].format(**values), config["email"]["body_template"].format(**values)


def message(config, year, month, charges, pdf):
    subject, body = content(config, year, month, charges)
    msg = EmailMessage()
    msg["From"] = address(config["email"]["draft_account_email"])
    msg["To"] = address(config["email"]["to"])
    msg["Subject"] = subject
    msg.set_content(body)
    msg.add_attachment(Path(pdf).read_bytes(), maintype="application", subtype="pdf", filename=Path(pdf).name)
    return msg


class MockGmail:
    """Durable mock inbox. Intentionally provides no send operation."""
    def __init__(self, ledger):
        self.ledger = ledger

    def create_draft(self, key, msg):
        with self.ledger.db:
            self.ledger.db.execute("INSERT OR IGNORE INTO drafts VALUES (?, ?, ?, 'draft')", (key, f"mock-{key}", msg.as_bytes()))
        return self.ledger.db.execute("SELECT id FROM drafts WHERE month=?", (key,)).fetchone()[0]

    def status(self, key):
        row = self.ledger.db.execute("SELECT status FROM drafts WHERE month=?", (key,)).fetchone()
        return row[0] if row else "missing"

    def simulate_manual_send(self, key):
        with self.ledger.db:
            self.ledger.db.execute("UPDATE drafts SET status='sent' WHERE month=?", (key,))

    def simulate_deletion(self, key):
        with self.ledger.db:
            self.ledger.db.execute("UPDATE drafts SET status='missing' WHERE month=?", (key,))
