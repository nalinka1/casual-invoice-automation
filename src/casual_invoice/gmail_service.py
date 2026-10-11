"""Explicit Gmail draft-only workflow, separate from mock ledgers and AWS."""
import argparse
import base64
import hashlib
import json
import os
import uuid
from datetime import datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path
from zoneinfo import ZoneInfo

from .calendar_rules import local_date, previous_month
from .configuration import address, load_config
from .email_service import message
from .invoice_generator import calculate, generate_pdf, total, invoice_filename
from .invoice_ledger import Ledger

SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]


class ReconciliationRequired(RuntimeError):
    """A previous create may have succeeded; never blindly repeat it."""


def save_token(credentials, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(credentials.to_json())
    temporary.replace(path)


def oauth_service(client_file, token_file, expected_account, interactive=False, reauthorize=False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    client_file, token_file = Path(client_file), Path(token_file)
    credentials = None
    if token_file.exists() and not reauthorize:
        credentials = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
    if not credentials or not credentials.valid:
        if not interactive:
            raise ValueError("Connect first with the 'connect' command and your Desktop OAuth client file")
        if not client_file.exists():
            raise ValueError(f"Desktop OAuth client file missing: {client_file}. See docs/GMAIL_SETUP.md")
        flow = InstalledAppFlow.from_client_secrets_file(str(client_file), SCOPES)
        credentials = flow.run_local_server(port=0, login_hint=expected_account, prompt="consent", timeout_seconds=180)
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    GmailDrafts(service, expected_account).preflight()
    # Save only after verifying the authorized mailbox; never print credential data.
    save_token(credentials, token_file)
    return service


class GmailDrafts:
    """Only profile, draft-list/get and draft-create methods are exposed."""
    def __init__(self, service, account):
        self.service = service
        self.account = address(account)

    def preflight(self):
        actual = self.service.users().getProfile(userId="me").execute(num_retries=0)["emailAddress"]
        if actual.casefold() != self.account.casefold():
            raise ValueError("Authorized Gmail account differs from configured drafting account")

    def create(self, raw):
        body = {"message": {"raw": base64.urlsafe_b64encode(raw).decode("ascii")}}
        # Retrying a non-idempotent POST automatically could create duplicate drafts.
        result = self.service.users().drafts().create(userId="me", body=body).execute(num_retries=0)
        if not result.get("id"):
            raise ReconciliationRequired("Gmail response lacks draft ID; reconcile before retrying")
        return result["id"]

    def draft_exists(self, draft_id):
        from googleapiclient.errors import HttpError
        try:
            self.service.users().drafts().get(userId="me", id=draft_id, format="minimal").execute(num_retries=0)
            return True
        except HttpError as exc:
            if exc.resp.status == 404:
                return False
            raise

    def find_operation(self, operation_id):
        token = None
        found = []
        while True:
            arguments = {"userId": "me", "maxResults": 100}
            if token:
                arguments["pageToken"] = token
            listing = self.service.users().drafts().list(**arguments).execute(num_retries=0)
            for item in listing.get("drafts", []):
                result = self.service.users().drafts().get(userId="me", id=item["id"], format="raw").execute(num_retries=0)
                raw = result["message"]["raw"]
                parsed = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
                if parsed.get("X-Cleaning-Invoice-Operation") == operation_id:
                    found.append(item["id"])
            token = listing.get("nextPageToken")
            if not token:
                return found


def draft_invoice(config, output, ledger, gmail, now=None, reconcile=False, recreate_deleted=False):
    if config["email"].get("send_enabled") is not False:
        raise ValueError("Sending must remain disabled")
    if config["invoice"]["number_status"] != "confirmed":
        raise ValueError("Confirm the last issued invoice number first")
    address(config["email"]["to"])
    if gmail.account.casefold() != config["email"]["draft_account_email"].casefold():
        raise ValueError("Adapter account differs from configuration")
    gmail.preflight()
    now = now or datetime.now(ZoneInfo(config["timezone"]))
    year, month = previous_month(local_date(now, config["timezone"]))
    charges = calculate(config, year, month)
    key = f"{year:04d}-{month:02d}"
    stamp = now.isoformat()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with ledger.db:
        ledger.db.execute("""CREATE TABLE IF NOT EXISTS gmail_operations (
            month TEXT PRIMARY KEY, operation_id TEXT UNIQUE, phase TEXT, raw BLOB, draft_id TEXT,
            account TEXT)""")
    ledger.reserve(key, stamp)
    with ledger.transaction():
        row = ledger.get(key)
        operation = ledger.db.execute("SELECT * FROM gmail_operations WHERE month=?", (key,)).fetchone()
        if operation and operation["account"].casefold() != gmail.account.casefold():
            raise ValueError("This ledger belongs to another drafting account")
        if not operation:
            if row["draft_id"] or row["status"] == "sent_confirmed":
                raise ValueError("Use a dedicated Gmail ledger, not an existing mock ledger")
            operation_id = uuid.uuid4().hex
            pdf = output / invoice_filename(year, month, config['invoice'].get('filename_prefix', 'Example'))
            if pdf.exists():
                raise ValueError("Unexpected PDF already exists; inspect it before recovery")
            generate_pdf(config, charges, row["number"], local_date(now, config["timezone"]), pdf)
            msg = message(config, year, month, charges, pdf)
            msg["X-Cleaning-Invoice-Operation"] = operation_id
            msg["Message-ID"] = f"<{operation_id}@casual-invoice.local>"
            raw = msg.as_bytes(policy=policy.SMTP)
            ledger.db.execute("INSERT INTO gmail_operations VALUES (?, ?, 'prepared', ?, NULL, ?)", (key, operation_id, raw, gmail.account))
            summary = {"billing_month": key, "site_days": {s.site_id: len(s.dates) for s in charges}, "total": str(total(charges))}
            ledger.db.execute("UPDATE runs SET pdf=?, checksum=?, summary=?, updated=? WHERE month=?",
                              (str(pdf), hashlib.sha256(pdf.read_bytes()).hexdigest(), json.dumps(summary), stamp, key))
        operation = dict(ledger.db.execute("SELECT * FROM gmail_operations WHERE month=?", (key,)).fetchone())
        if recreate_deleted and operation["phase"] == "done":
            if row["status"] == "sent_confirmed":
                raise ValueError("Cannot recreate an issued invoice")
            if gmail.draft_exists(operation["draft_id"]):
                raise ValueError("Saved Gmail draft still exists; recreation blocked")
            # Explicit user confirmation of deletion is required: a 404 alone
            # does not distinguish deletion from sending.
            parsed = BytesParser(policy=policy.default).parsebytes(operation["raw"])
            attachments = list(parsed.iter_attachments())
            if len(attachments) != 1 or attachments[0].get_content_type() != "application/pdf":
                raise ValueError("Stored invoice attachment is invalid")
            pdf_bytes = attachments[0].get_payload(decode=True)
            if hashlib.sha256(pdf_bytes).hexdigest() != row["checksum"]:
                raise ValueError("Stored attachment checksum mismatch")
            filename = invoice_filename(year, month, config['invoice'].get('filename_prefix', 'Example'))
            pdf = output / filename
            if pdf.exists() and pdf.read_bytes() != pdf_bytes:
                raise ValueError("Named PDF differs from stored invoice")
            pdf.write_bytes(pdf_bytes)
            attachments[0].set_param("filename", filename, header="Content-Disposition")
            operation_id = uuid.uuid4().hex
            parsed.replace_header("X-Cleaning-Invoice-Operation", operation_id)
            parsed.replace_header("Message-ID", f"<{operation_id}@casual-invoice.local>")
            ledger.db.execute("CREATE TABLE IF NOT EXISTS gmail_operation_history (operation_id TEXT PRIMARY KEY, month TEXT, draft_id TEXT, raw BLOB, replaced TEXT)")
            ledger.db.execute("INSERT INTO gmail_operation_history VALUES (?, ?, ?, ?, ?)",
                              (operation["operation_id"], key, operation["draft_id"], operation["raw"], stamp))
            ledger.db.execute("UPDATE gmail_operations SET operation_id=?, phase='prepared', raw=?, draft_id=NULL WHERE month=?",
                              (operation_id, parsed.as_bytes(policy=policy.SMTP), key))
            ledger.db.execute("UPDATE runs SET draft_id=NULL, status='running', pdf=?, updated=? WHERE month=?", (str(pdf), stamp, key))
            operation = dict(ledger.db.execute("SELECT * FROM gmail_operations WHERE month=?", (key,)).fetchone())
        if operation["phase"] == "done":
            return ledger.get(key)
        if operation["phase"] == "prepared":
            ledger.db.execute("UPDATE gmail_operations SET phase='creating' WHERE month=?", (key,))
            should_create = True
        else:
            should_create = False
    # The durable creating marker commits before any remote POST. Other processes
    # must reconcile, never initiate another POST for the same month.
    if should_create:
        try:
            draft_id = gmail.create(operation["raw"])
        except Exception:
            ledger.update(key, stamp, status="failed", error="GmailCreateOutcomeUnconfirmed")
            raise ReconciliationRequired("Draft creation outcome is unconfirmed. Run with --reconcile; do not create another draft manually.") from None
    else:
        if not reconcile:
            raise ReconciliationRequired("An earlier create may have succeeded. Run with --reconcile to locate its draft.")
        matches = gmail.find_operation(operation["operation_id"])
        if len(matches) != 1:
            raise ReconciliationRequired("Could not identify exactly one draft. Check Gmail Drafts and Sent manually; automatic recreation is blocked.")
        draft_id = matches[0]
    with ledger.transaction():
        ledger.db.execute("UPDATE gmail_operations SET phase='done', draft_id=? WHERE month=?", (draft_id, key))
        ledger.db.execute("UPDATE runs SET draft_id=?, status='awaiting_approval', error=NULL, updated=? WHERE month=?", (draft_id, stamp, key))
    return ledger.get(key)


def main():
    parser = argparse.ArgumentParser(description="Connect Gmail or create a draft; never sends email or calls AWS")
    parser.add_argument("action", choices=("connect", "draft"))
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--client", default=".secrets/gmail-client.json")
    parser.add_argument("--token", default=".secrets/gmail-token.json")
    parser.add_argument("--output", default="output/gmail")
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("--recreate-deleted-draft", action="store_true", help="Explicitly confirm previous draft was deleted without sending; reuse invoice number")
    parser.add_argument("--reauthorize", action="store_true", help="For connect: obtain fresh consent after changing OAuth publishing status")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        if args.reauthorize and args.action != "connect":
            raise ValueError("Use --reauthorize with connect only")
        service = oauth_service(args.client, args.token, config["email"]["draft_account_email"], interactive=args.action == "connect", reauthorize=args.reauthorize)
        if args.action == "connect":
            print("Configured Gmail account verified. No draft created or email sent.")
            return
        if config.get("execution_state", {}).get("deployment_mode") == "aws":
            raise ValueError("Hosted ledger is authoritative. Invoke the AWS job instead of allocating from local SQLite.")
        Path(args.output).mkdir(parents=True, exist_ok=True)
        ledger = Ledger(Path(args.output) / "gmail-ledger.sqlite", config["invoice"]["last_confirmed_issued_invoice_number"])
        try:
            row = draft_invoice(config, args.output, ledger, GmailDrafts(service, config["email"]["draft_account_email"]), reconcile=args.reconcile, recreate_deleted=args.recreate_deleted_draft)
            print(json.dumps({k: row[k] for k in ("month", "number", "status", "draft_id")}, indent=2))
            print("Review Gmail Drafts and manually send when ready. AWS notifications remain disabled.")
        finally:
            ledger.close()
    except (ValueError, ReconciliationRequired) as exc:
        parser.exit(1, str(exc) + "\n")
    except Exception:
        # Google exceptions may include private request content; keep terminal output sanitized.
        parser.exit(1, "Gmail operation failed. Check OAuth/API setup and local ledger; do not blindly repeat draft creation.\n")


if __name__ == "__main__":
    main()
