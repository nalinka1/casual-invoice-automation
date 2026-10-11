"""Private AWS inputs and atomic ledger; no credentials embedded in source."""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from .configuration import validate


def get_config(s3, bucket, destination):
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    config = json.loads(s3.get_object(Bucket=bucket, Key="settings/config.json")["Body"].read())
    validate(config)
    template = root / "invoice-template.pdf"
    template.write_bytes(s3.get_object(Bucket=bucket, Key="templates/invoice-template.pdf")["Body"].read())
    config["invoice"]["source_pdf"] = str(template)
    config["_root"] = str(root)
    return config


class DynamoLedger:
    def __init__(self, client, table):
        self.client, self.table = client, table

    def get(self, key):
        from boto3.dynamodb.types import TypeDeserializer
        result = self.client.get_item(TableName=self.table, Key={"pk": {"S": key}}, ConsistentRead=True)
        return {k: TypeDeserializer().deserialize(v) for k, v in result.get("Item", {}).items()} or None

    def reserve(self, month):
        from boto3.dynamodb.types import TypeSerializer
        from botocore.exceptions import ClientError
        serializer = TypeSerializer()
        for _ in range(20):
            existing = self.get(f"month#{month}")
            if existing:
                return existing
            counter = self.get("counter")
            if not counter:
                raise ValueError("Migrate the confirmed issued ledger before reserving a number")
            previous = int(counter["last_reserved"])
            row = {"pk": f"month#{month}", "billing_month": month, "number": previous + 1, "status": "running"}
            try:
                self.client.transact_write_items(TransactItems=[
                    {"Update": {"TableName": self.table, "Key": {"pk": {"S": "counter"}},
                                "UpdateExpression": "SET last_reserved = :next", "ConditionExpression": "last_reserved = :previous",
                                "ExpressionAttributeValues": {":next": {"N": str(previous + 1)}, ":previous": {"N": str(previous)}}}},
                    {"Put": {"TableName": self.table, "Item": {k: serializer.serialize(v) for k, v in row.items()}, "ConditionExpression": "attribute_not_exists(pk)"}}
                ])
                return row
            except ClientError as exc:
                if exc.response["Error"]["Code"] not in ("TransactionCanceledException", "TransactionConflictException"):
                    raise
        raise RuntimeError("Could not reserve number after concurrent updates")


def migrate(config_path, sqlite_path, token_path, s3, dynamo, secrets, bucket, table, secret_arn, expected_account, sts):
    from boto3.dynamodb.types import TypeSerializer
    actual = sts.get_caller_identity()["Account"]
    if actual != expected_account:
        raise ValueError("AWS account differs from the intended deployment account")
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate(config)
    config["aws"]["account_id"] = actual
    source = config_path.parent / config["invoice"]["source_pdf"]
    token = json.loads(Path(token_path).read_text(encoding="utf-8"))
    if not all(token.get(k) for k in ("client_id", "client_secret", "refresh_token")):
        raise ValueError("Gmail token must include OAuth client credentials and offline refresh token")
    with sqlite3.connect(f"file:{Path(sqlite_path).resolve().as_posix()}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute("SELECT * FROM runs")]
    if any(row["status"] != "sent_confirmed" for row in rows):
        raise ValueError("Resolve pending local invoices before cloud migration")
    template_bytes = source.read_bytes()
    issued_pdfs = []
    for row in rows:
        pdf = Path(row["pdf"])
        raw = pdf.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["checksum"]:
            raise ValueError("Local issued PDF checksum mismatch")
        issued_pdfs.append((row["month"], pdf.name, raw))
    serializer = TypeSerializer()
    last = max([config["invoice"]["last_confirmed_issued_invoice_number"], *[row["number"] for row in rows]])
    counter = {"pk": "counter", "last_reserved": last}
    items = [counter, *[{"pk": f"month#{r['month']}", "billing_month": r["month"], "number": r["number"], "status": r["status"], "evidence": "imported_local_manual_confirmation"} for r in rows]]
    if len(items) > 100:
        raise ValueError("Large migrations require a separately reviewed batch process")
    # Conditional transaction refuses to reset an existing cloud ledger.
    dynamo.transact_write_items(TransactItems=[{"Put": {"TableName": table, "Item": {k: serializer.serialize(v) for k, v in item.items()}, "ConditionExpression": "attribute_not_exists(pk)"}} for item in items])
    config["invoice"]["source_pdf"] = "templates/invoice-template.pdf"
    s3.put_object(Bucket=bucket, Key="settings/config.json", Body=json.dumps(config).encode(), ServerSideEncryption="AES256")
    s3.put_object(Bucket=bucket, Key="templates/invoice-template.pdf", Body=template_bytes, ServerSideEncryption="AES256")
    for month, filename, raw in issued_pdfs:
        s3.put_object(Bucket=bucket, Key=f"invoices/{month}/{filename}", Body=raw, ServerSideEncryption="AES256")
    secrets.put_secret_value(SecretId=secret_arn, SecretString=json.dumps(token))
    return {"last_reserved": last, "imported_months": len(rows)}


def main():
    parser = argparse.ArgumentParser(description="One-time private migration to an initialized AWS storage stack")
    parser.add_argument("--profile")
    parser.add_argument("--region", default="ap-southeast-2")
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--bucket")
    parser.add_argument("--table")
    parser.add_argument("--secret-arn", required=True)
    parser.add_argument("--refresh-gmail-secret", action="store_true", help="Update only the OAuth secret after reauthorization; do not reinitialize the ledger")
    args = parser.parse_args()
    import boto3
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    try:
        if args.refresh_gmail_secret:
            if session.client("sts").get_caller_identity()["Account"] != args.account_id:
                raise ValueError("AWS account mismatch")
            from .gmail_service import oauth_service
            from .configuration import load_config
            config = load_config()
            oauth_service(".secrets/gmail-client.json", ".secrets/gmail-token.json", config["email"]["draft_account_email"])
            token = Path(".secrets/gmail-token.json").read_text(encoding="utf-8")
            if not json.loads(token).get("refresh_token"):
                raise ValueError("Offline refresh token required")
            session.client("secretsmanager").put_secret_value(SecretId=args.secret_arn, SecretString=token)
            print("Gmail secret refreshed; no invoice generated or sent.")
            return
        if not args.bucket or not args.table:
            raise ValueError("Migration requires --bucket and --table")
        result = migrate("config.json", "output/gmail/gmail-ledger.sqlite", ".secrets/gmail-token.json",
                         session.client("s3"), session.client("dynamodb"), session.client("secretsmanager"),
                         args.bucket, args.table, args.secret_arn, args.account_id, session.client("sts"))
        print(json.dumps(result))
    except Exception as exc:
        parser.exit(1, f"Migration stopped ({type(exc).__name__}); inspect partial state before retry. No credentials printed.\n")


if __name__ == "__main__":
    main()
