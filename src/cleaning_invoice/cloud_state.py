"""Record explicit human sending confirmation for a hosted invoice."""
import argparse
from datetime import date, datetime
from zoneinfo import ZoneInfo
from .configuration import load_config


def confirm_sent(table, month, number, now):
    if date.fromisoformat(month + "-01").strftime("%Y-%m") != month:
        raise ValueError("Invalid billing month")
    if type(number) is not int or number < 1:
        raise ValueError("Invalid invoice number")
    table.update_item(Key={"pk": f"month#{month}"},
        UpdateExpression="SET #status=:sent, confirmation_evidence=:evidence, confirmed_at=:now",
        ConditionExpression="#number=:number AND attribute_exists(draft_id) AND (#status=:awaiting OR #status=:failed)",
        ExpressionAttributeNames={"#status": "status", "#number": "number"},
        ExpressionAttributeValues={":number": number, ":sent": "sent_confirmed", ":awaiting": "awaiting_approval", ":failed": "failed",
                                   ":evidence": "explicit_user_manual_confirmation", ":now": now.isoformat()})


def main():
    parser = argparse.ArgumentParser(description="Record explicit confirmation that a Gmail invoice was manually sent")
    parser.add_argument("--month", required=True)
    parser.add_argument("--invoice-number", type=int, required=True)
    parser.add_argument("--confirm-manual-send", action="store_true", required=True)
    args = parser.parse_args()
    import boto3
    config = load_config()
    session = boto3.Session(region_name=config["aws"]["region"])
    if session.client("sts").get_caller_identity()["Account"] != config["aws"]["account_id"]:
        parser.exit(1, "AWS account mismatch\n")
    table = session.resource("dynamodb").Table(config["aws"]["ledger_table"])
    confirm_sent(table, args.month, args.invoice_number, datetime.now(ZoneInfo(config["timezone"])))
    print("Manual sending confirmation recorded; no email sent by this command.")


if __name__ == "__main__":
    main()
