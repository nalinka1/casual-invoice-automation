"""Isolated Lambda acceptance entrypoint; never attach this to production schedules."""
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import boto3
from casual_invoice.aws_storage import get_config
from casual_invoice.cloud_job import CloudJob


class PrefixedS3:
    def __init__(self, client, prefix):
        if not prefix.startswith("acceptance/") or not prefix.endswith("/"):
            raise ValueError("Acceptance objects must use a dedicated acceptance prefix")
        self.client, self.prefix = client, prefix

    def get_object(self, **arguments):
        arguments["Key"] = self.prefix + arguments["Key"]
        return self.client.get_object(**arguments)

    def put_object(self, **arguments):
        arguments["Key"] = self.prefix + arguments["Key"]
        return self.client.put_object(**arguments)


class TestSNS:
    def __init__(self, client):
        self.client = client

    def publish(self, **arguments):
        arguments["Subject"] = "TEST ONLY - AWS invoice flow"
        arguments["Message"] = "TEST ONLY - synthetic invoice; do not send or pay.\n" + arguments["Message"]
        return self.client.publish(**arguments)


def handler(event, context):
    job = CloudJob(boto3.Session(), os.environ["PRIVATE_BUCKET"], os.environ["TEST_LEDGER"], os.environ["GMAIL_SECRET"], os.environ["NOTIFICATION_TOPIC"])
    job.s3 = PrefixedS3(job.s3, os.environ["TEST_PREFIX"])
    job.sns = TestSNS(job.sns)
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        config = get_config(job.s3, job.bucket, directory)
        if config["email"]["to"] != config["email"]["draft_account_email"] or not config["email"]["subject_template"].startswith("[TEST ONLY]"):
            raise ValueError("Acceptance draft must be labelled and addressed to the drafting inbox")
        return job.generate(config, directory, datetime.now(ZoneInfo(config["timezone"])))
