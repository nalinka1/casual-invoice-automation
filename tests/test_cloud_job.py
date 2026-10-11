import io
from datetime import datetime, timezone
from pathlib import Path
from botocore.exceptions import ClientError
import pytest
from casual_invoice.cloud_job import CloudJob

NOW = datetime(2026, 11, 3, 10, tzinfo=timezone.utc)


class FakeLedger:
    def __init__(self):
        self.row = {"pk": "month#2026-10", "billing_month": "2026-10", "number": 13, "status": "running"}
    def reserve(self, key):
        assert key == "2026-10"
        return dict(self.row)
    def get(self, key):
        return dict(self.row) if key == self.row["pk"] else None


class FakeTable:
    def __init__(self, ledger):
        self.ledger = ledger
        self.events = set()
    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues, ConditionExpression=None):
        row = self.ledger.row
        if ConditionExpression == "attribute_not_exists(#phase)" and "phase" in row:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        if ConditionExpression == "#phase=:prepared" and row.get("phase") != "prepared":
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        for assignment in UpdateExpression.removeprefix("SET ").split(", "):
            key, value = assignment.split("=")
            row[ExpressionAttributeNames[key]] = ExpressionAttributeValues[value]
    def put_item(self, Item, ConditionExpression):
        if Item["pk"] in self.events:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.events.add(Item["pk"])
    def scan(self, **kwargs):
        return {"Items": [dict(self.ledger.row)]}


class FakeS3:
    def __init__(self):
        self.objects = {}
    def put_object(self, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body
    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}


class FakeGmail:
    def __init__(self):
        self.creates = 0
        self.fail = False
        self.matches = []
    def create(self, raw):
        self.creates += 1
        self.matches = ["real-draft"]
        if self.fail:
            raise TimeoutError()
        return "real-draft"
    def find_operation(self, key):
        return self.matches
    def draft_exists(self, key):
        return True


@pytest.fixture
def job():
    cloud = CloudJob.__new__(CloudJob)
    cloud.bucket = "private"
    cloud.ledger = FakeLedger()
    cloud.table = FakeTable(cloud.ledger)
    cloud.s3 = FakeS3()
    gmail = FakeGmail()
    cloud.gmail = lambda config: gmail
    cloud.alerts = {}
    cloud.patch = lambda key, values: cloud.ledger.row.update(values)
    cloud.notify = lambda key, payload: cloud.alerts.setdefault(key, payload)
    return cloud, gmail


def test_cloud_generation_retry_and_private_artifacts(config, tmp_path, job):
    cloud, gmail = job
    result = cloud.generate(config, tmp_path, NOW)
    assert result["invoice_number"] == 13
    assert result["status"] == "awaiting_approval"
    cloud.generate(config, tmp_path, NOW)
    assert gmail.creates == 1
    assert len(cloud.alerts) == 1
    assert set(cloud.alerts["ready:2026-10"]) == {"billing_month", "invoice_number", "status", "site_days", "total", "instruction"}
    assert any(key.endswith("Example_OCTOBER_26_INVOICE.pdf") for key in cloud.s3.objects)


def test_cloud_uncertain_create_reconciles(config, tmp_path, job):
    cloud, gmail = job
    gmail.fail = True
    with pytest.raises(TimeoutError):
        cloud.generate(config, tmp_path, NOW)
    result = cloud.generate(config, tmp_path, NOW)
    assert result["status"] == "awaiting_approval"
    assert gmail.creates == 1


def test_cloud_sent_record_never_generates(config, tmp_path, job):
    cloud, gmail = job
    cloud.ledger.row["status"] = "sent_confirmed"
    result = cloud.generate(config, tmp_path, NOW)
    assert result["status"] == "sent_confirmed"
    assert gmail.creates == 0
    assert not cloud.s3.objects


def test_cloud_monitor_without_job(config, job):
    cloud, gmail = job
    cloud.ledger.get = lambda key: None
    cloud.table.scan = lambda **kwargs: {"Items": []}
    cloud.monitor(config, datetime(2026, 11, 6, tzinfo=timezone.utc))
    assert cloud.alerts["missed:2026-10"]["status"] == "missed_or_failed_run"


def test_cloud_missing_draft_never_confirms_sent(config, job):
    cloud, gmail = job
    cloud.ledger.row.update(status="awaiting_approval", draft_id="draft", created=NOW.isoformat())
    gmail.draft_exists = lambda key: False
    cloud.monitor(config, datetime(2026, 11, 7, tzinfo=timezone.utc))
    assert cloud.ledger.row["status"] == "awaiting_approval"
    assert cloud.alerts["reminder:2026-10:initial"]["status"] == "delivery_unconfirmed"


def test_cloud_failed_notification_recovery(config, tmp_path, job):
    cloud, gmail = job
    cloud.generate(config, tmp_path, NOW)
    cloud.ledger.row["status"] = "failed"
    assert cloud.generate(config, tmp_path, NOW)["status"] == "awaiting_approval"
    assert gmail.creates == 1


def test_manual_cloud_confirmation_requires_matching_number():
    from casual_invoice.cloud_state import confirm_sent
    class Table:
        def update_item(self, **kwargs):
            assert kwargs["Key"] == {"pk": "month#2026-10"}
            assert "#number=:number" in kwargs["ConditionExpression"]
            assert "attribute_exists(draft_id)" in kwargs["ConditionExpression"]
            assert kwargs["ExpressionAttributeValues"][":evidence"] == "explicit_user_manual_confirmation"
    confirm_sent(Table(), "2026-10", 13, NOW)
