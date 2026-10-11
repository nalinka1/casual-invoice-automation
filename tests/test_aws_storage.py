import io
import json
from pathlib import Path
import pytest
from cleaning_invoice.aws_storage import DynamoLedger, get_config


def test_s3_input_is_validated_and_downloaded(config, tmp_path):
    class S3:
        def get_object(self, Bucket, Key):
            assert Bucket == "private-example"
            return {"Body": io.BytesIO(json.dumps(config).encode() if Key == "settings/config.json" else b"private pdf")}
    result = get_config(S3(), "private-example", tmp_path / "download")
    assert Path(result["invoice"]["source_pdf"]).read_bytes() == b"private pdf"


def test_storage_template_privacy():
    stack = json.loads((Path(__file__).resolve().parents[1] / "infrastructure/storage.json").read_text())
    resources = stack["Resources"]
    assert all(resources["PrivateBucket"]["Properties"]["PublicAccessBlockConfiguration"].values())
    assert resources["PrivateBucket"]["DeletionPolicy"] == "Retain"
    assert resources["Ledger"]["Properties"]["BillingMode"] == "PAY_PER_REQUEST"
    assert "SecretString" not in resources["GmailSecret"]["Properties"]


def test_dynamo_reservation_is_atomic_and_idempotent():
    from boto3.dynamodb.types import TypeSerializer
    serializer = TypeSerializer()
    class Client:
        def __init__(self):
            self.records = {"counter": {"pk": "counter", "last_reserved": 12}}
            self.transactions = 0
        def get_item(self, TableName, Key, ConsistentRead):
            assert ConsistentRead
            row = self.records.get(Key["pk"]["S"])
            return {"Item": {k: serializer.serialize(v) for k, v in row.items()}} if row else {}
        def transact_write_items(self, TransactItems):
            from boto3.dynamodb.types import TypeDeserializer
            self.transactions += 1
            update, insert = TransactItems
            assert update["Update"]["ConditionExpression"] == "last_reserved = :previous"
            assert insert["Put"]["ConditionExpression"] == "attribute_not_exists(pk)"
            self.records["counter"]["last_reserved"] += 1
            row = {k: TypeDeserializer().deserialize(v) for k, v in insert["Put"]["Item"].items()}
            self.records[row["pk"]] = row
    client = Client()
    ledger = DynamoLedger(client, "test-table")
    assert ledger.reserve("2026-10")["number"] == 13
    assert ledger.reserve("2026-10")["number"] == 13
    assert client.transactions == 1


def test_uninitialized_dynamo_fails_closed():
    class Client:
        def get_item(self, **kwargs):
            return {}
    with pytest.raises(ValueError, match="Migrate"):
        DynamoLedger(Client(), "test").reserve("2026-10")
