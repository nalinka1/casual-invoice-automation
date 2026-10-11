"""Hosted draft generation with private S3, OAuth secret and DynamoDB state."""
import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta
from email import policy
from pathlib import Path
from zoneinfo import ZoneInfo

from .aws_storage import DynamoLedger, get_config
from .calendar_rules import previous_month, local_date
from .email_service import message
from .gmail_service import GmailDrafts, SCOPES, ReconciliationRequired
from .invoice_generator import calculate, generate_pdf, invoice_filename, total


def conditional_failed(exc):
    return getattr(exc, "response", {}).get("Error", {}).get("Code") == "ConditionalCheckFailedException"


class CloudJob:
    def __init__(self, session, bucket, table_name, secret_arn, topic):
        self.s3 = session.client("s3")
        self.secret_client = session.client("secretsmanager")
        self.sns = session.client("sns")
        self.table = session.resource("dynamodb").Table(table_name)
        self.ledger = DynamoLedger(session.client("dynamodb"), table_name)
        self.bucket, self.secret_arn, self.topic = bucket, secret_arn, topic

    def gmail(self, config):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        value = json.loads(self.secret_client.get_secret_value(SecretId=self.secret_arn)["SecretString"])
        credentials = Credentials.from_authorized_user_info(value, SCOPES)
        if not credentials.valid:
            credentials.refresh(Request())
            self.secret_client.put_secret_value(SecretId=self.secret_arn, SecretString=credentials.to_json())
        adapter = GmailDrafts(build("gmail", "v1", credentials=credentials, cache_discovery=False), config["email"]["draft_account_email"])
        adapter.preflight()
        return adapter

    def patch(self, key, values):
        names = {f"#f{i}": k for i, k in enumerate(values)}
        attributes = {f":v{i}": v for i, v in enumerate(values.values())}
        self.table.update_item(Key={"pk": key}, UpdateExpression="SET " + ", ".join(f"#f{i}=:v{i}" for i in range(len(values))),
                               ExpressionAttributeNames=names, ExpressionAttributeValues=attributes)

    def notify(self, event_key, payload):
        # Commit an intent before publishing. An ambiguous publish is never blindly
        # repeated; monitoring/alarms must surface unresolved notification intents.
        try:
            self.table.put_item(Item={"pk": f"event#{event_key}", "status": "publishing", "created": datetime.now(ZoneInfo("Australia/Melbourne")).isoformat()},
                                ConditionExpression="attribute_not_exists(pk)")
        except Exception as exc:
            if conditional_failed(exc):
                return False
            raise
        self.sns.publish(TopicArn=self.topic, Subject="Cleaning invoice status", Message=json.dumps(payload))
        self.patch(f"event#{event_key}", {"status": "published"})
        return True

    def generate(self, config, directory, now):
        year, month = previous_month(local_date(now, config["timezone"]))
        key = f"{year:04d}-{month:02d}"
        charges = calculate(config, year, month)
        row = self.ledger.reserve(key)
        pk = row["pk"]
        if row["status"] == "sent_confirmed":
            return {"billing_month": key, "status": "sent_confirmed", "invoice_number": int(row["number"])}
        gmail = self.gmail(config)
        if "phase" not in row:
            operation_id = uuid.uuid4().hex
            filename = invoice_filename(year, month, config["invoice"].get("filename_prefix", "Example"))
            pdf = generate_pdf(config, charges, int(row["number"]), local_date(now, config["timezone"]), directory / filename)
            msg = message(config, year, month, charges, pdf)
            msg["X-Cleaning-Invoice-Operation"] = operation_id
            msg["Message-ID"] = f"<{operation_id}@cleaning-invoice.local>"
            prefix = f"invoices/{key}/{operation_id}"
            pdf_key, mime_key = f"{prefix}/{filename}", f"{prefix}/draft.eml"
            self.s3.put_object(Bucket=self.bucket, Key=pdf_key, Body=pdf.read_bytes(), ServerSideEncryption="AES256")
            self.s3.put_object(Bucket=self.bucket, Key=mime_key, Body=msg.as_bytes(policy=policy.SMTP), ServerSideEncryption="AES256")
            metadata = {"phase": "prepared", "operation_id": operation_id, "pdf_key": pdf_key, "mime_key": mime_key,
                        "checksum": hashlib.sha256(pdf.read_bytes()).hexdigest(), "created": now.isoformat(),
                        "site_days": {s.site_id: len(s.dates) for s in charges}, "total": str(total(charges))}
            names = {f"#f{i}": k for i, k in enumerate(metadata)}
            attributes = {f":v{i}": v for i, v in enumerate(metadata.values())}
            names["#phase"] = "phase"
            try:
                self.table.update_item(Key={"pk": pk}, UpdateExpression="SET " + ", ".join(f"#f{i}=:v{i}" for i in range(len(metadata))),
                                       ConditionExpression="attribute_not_exists(#phase)", ExpressionAttributeNames=names, ExpressionAttributeValues=attributes)
            except Exception as exc:
                if not conditional_failed(exc):
                    raise
            row = self.ledger.get(pk)
        if row["phase"] != "done":
            create = False
            if row["phase"] == "prepared":
                try:
                    self.table.update_item(Key={"pk": pk}, UpdateExpression="SET #phase=:creating", ConditionExpression="#phase=:prepared",
                                           ExpressionAttributeNames={"#phase": "phase"}, ExpressionAttributeValues={":creating": "creating", ":prepared": "prepared"})
                    create = True
                except Exception as exc:
                    if not conditional_failed(exc):
                        raise
            if create:
                raw = self.s3.get_object(Bucket=self.bucket, Key=row["mime_key"])["Body"].read()
                draft_id = gmail.create(raw)
            else:
                matches = gmail.find_operation(row["operation_id"])
                if len(matches) != 1:
                    raise ReconciliationRequired("Remote create unresolved; manually inspect Drafts and Sent")
                draft_id = matches[0]
            self.patch(pk, {"phase": "done", "draft_id": draft_id, "status": "awaiting_approval", "updated": now.isoformat()})
            row = self.ledger.get(pk)
        if row["phase"] == "done" and row["status"] == "failed":
            self.patch(pk, {"status": "awaiting_approval", "updated": now.isoformat()})
            row = self.ledger.get(pk)
        if config["notifications"]["success"]:
            self.notify(f"ready:{key}", {"billing_month": key, "invoice_number": int(row["number"]), "status": row["status"],
                                        "site_days": {k: int(v) for k, v in row["site_days"].items()}, "total": row["total"], "instruction": "Review Gmail Drafts and manually send"})
        return {"billing_month": key, "status": row["status"], "invoice_number": int(row["number"])}

    def monitor(self, config, now):
        today = local_date(now, config["timezone"])
        year, month = previous_month(today)
        key = f"{year:04d}-{month:02d}"
        row = self.ledger.get(f"month#{key}")
        if today.day >= config["scheduling"]["missed_run_check_day_of_month"] and (not row or row["status"] in ("running", "failed", "scheduled")):
            self.notify(f"missed:{key}", {"billing_month": key, "status": "missed_or_failed_run"})
        gmail = None
        arguments = {}
        while True:
            result = self.table.scan(**arguments)
            for pending in result.get("Items", []):
                if not pending["pk"].startswith("month#") or pending.get("status") == "sent_confirmed" or not pending.get("draft_id"):
                    continue
                due = datetime.fromisoformat(pending["created"]) + timedelta(days=config["notifications"]["pending_draft_reminder_after_days"])
                if now < due:
                    continue
                gmail = gmail or self.gmail(config)
                exists = gmail.draft_exists(pending["draft_id"])
                self.notify(f"reminder:{pending['billing_month']}:initial", {"billing_month": pending["billing_month"], "invoice_number": int(pending["number"]),
                    "status": "awaiting_approval" if exists else "delivery_unconfirmed", "instruction": "Review Gmail Drafts" if exists else "Check Gmail Sent and explicitly confirm delivery; missing draft is not evidence"})
            if not result.get("LastEvaluatedKey"):
                break
            arguments = {"ExclusiveStartKey": result["LastEvaluatedKey"]}
        return {"status": "monitor_checked"}


def handler(event, context):
    import boto3
    job = CloudJob(boto3.Session(), os.environ["PRIVATE_BUCKET"], os.environ["LEDGER_TABLE"], os.environ["GMAIL_SECRET"], os.environ["NOTIFICATION_TOPIC"])
    now = datetime.now(ZoneInfo("Australia/Melbourne"))
    year, month = previous_month(now.date())
    key = f"{year:04d}-{month:02d}"
    try:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            config = get_config(job.s3, job.bucket, directory)
            action = event.get("action", "generate")
            if action == "health":
                job.gmail(config)
                counter = job.ledger.get("counter")
                return {"status": "healthy", "last_reserved": int(counter["last_reserved"])}
            if action == "monitor":
                return job.monitor(config, now)
            if action != "generate":
                raise ValueError("Unsupported cloud action")
            return job.generate(config, directory, now)
    except Exception as exc:
        existing = job.ledger.get(f"month#{key}")
        if existing and existing["status"] != "sent_confirmed" and event.get("action", "generate") == "generate":
            job.patch(existing["pk"], {"status": "failed", "error_type": type(exc).__name__})
        try:
            job.notify(f"failure:{key}:{now.date().isoformat()}", {"billing_month": key, "status": "failed", "error_type": type(exc).__name__})
        except Exception:
            pass  # Lambda error alarm provides an independent notification route.
        raise RuntimeError(f"Invoice cloud operation failed: {type(exc).__name__}") from None
