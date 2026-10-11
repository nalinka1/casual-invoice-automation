import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("acceptance_handler", Path(__file__).resolve().parents[1] / "tools/acceptance_handler.py")
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


def test_acceptance_prefix_never_reads_production():
    class Client:
        def get_object(self, **arguments):
            assert arguments["Key"] == "acceptance/abc/settings/config.json"
            return "isolated"
    assert acceptance.PrefixedS3(Client(), "acceptance/abc/").get_object(Bucket="private", Key="settings/config.json") == "isolated"
    with pytest.raises(ValueError):
        acceptance.PrefixedS3(Client(), "invoices/")


def test_acceptance_alert_is_labelled():
    class Client:
        def publish(self, **arguments):
            assert arguments["Subject"].startswith("TEST ONLY")
            assert "synthetic invoice" in arguments["Message"]
    acceptance.TestSNS(Client()).publish(TopicArn="test", Subject="ready", Message="body")


def test_acceptance_stack_has_separate_ledger_and_no_recurring_schedule():
    import json
    stack=json.loads((Path(__file__).resolve().parents[1]/"infrastructure/acceptance.json").read_text())
    resources=stack["Resources"]
    assert resources["TestLedger"]["Type"]=="AWS::DynamoDB::Table"
    assert not any(r["Type"]=="AWS::Scheduler::Schedule" for r in resources.values())
    policy=resources["TestRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    dynamo=next(s for s in policy if "dynamodb:GetItem" in s["Action"])
    assert dynamo["Resource"]=={"Fn::GetAtt":["TestLedger","Arn"]}
