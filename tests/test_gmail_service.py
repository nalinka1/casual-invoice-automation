import base64
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
import pytest
from casual_invoice.configuration import load_config
from casual_invoice.gmail_service import GmailDrafts, ReconciliationRequired, draft_invoice, oauth_service
from casual_invoice.invoice_ledger import Ledger
from conftest import example_config


class FakeGmail:
    def __init__(self):
        self.account = "draft@example.com"
        self.actual_account = self.account
        self.posts = []
        self.matches = []
        self.fail_after_create = False
        self.exists = True

    def draft_exists(self, draft_id):
        return self.exists

    def preflight(self):
        if self.actual_account != self.account:
            raise ValueError("Authorized Gmail account differs")

    def create(self, raw):
        self.posts.append(raw)
        self.matches = ["gmail-draft-123"]
        if self.fail_after_create:
            raise TimeoutError("private request")
        return "gmail-draft-123"

    def find_operation(self, operation_id):
        if self.posts:
            parsed = BytesParser(policy=policy.default).parsebytes(self.posts[0])
            assert parsed["X-Cleaning-Invoice-Operation"] == operation_id
        return self.matches


@pytest.fixture
def setup(tmp_path):
    config = example_config(tmp_path)
    config["invoice"]["number_status"] = "confirmed"
    config["email"].update(to="client@example.com", draft_account_email="draft@example.com")
    ledger = Ledger(tmp_path / "gmail.sqlite", 11)
    yield config, tmp_path, ledger, FakeGmail()
    ledger.close()


NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


def test_live_workflow_uses_one_post_and_persists(setup):
    config, path, ledger, gmail = setup
    first = draft_invoice(config, path, ledger, gmail, NOW)
    second = draft_invoice(config, path, ledger, gmail, NOW)
    assert first["number"] == second["number"] == 12
    assert first["draft_id"] == "gmail-draft-123"
    assert first["status"] == "awaiting_approval"
    assert Path(first["pdf"]).name == "Example_SEPTEMBER_26_INVOICE.pdf"
    assert len(gmail.posts) == 1
    parsed = BytesParser(policy=policy.default).parsebytes(gmail.posts[0])
    assert parsed["To"] == "client@example.com"
    assert parsed["From"] == gmail.account
    assert list(parsed.iter_attachments())[0].get_payload(decode=True) == Path(first["pdf"]).read_bytes()
    assert list(parsed.iter_attachments())[0].get_filename() == "Example_SEPTEMBER_26_INVOICE.pdf"
    assert ledger.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert not hasattr(GmailDrafts, "send")


def test_timeout_requires_reconciliation_without_duplicate(setup):
    config, path, ledger, gmail = setup
    gmail.fail_after_create = True
    with pytest.raises(ReconciliationRequired):
        draft_invoice(config, path, ledger, gmail, NOW)
    with pytest.raises(ReconciliationRequired):
        draft_invoice(config, path, ledger, gmail, NOW)
    assert len(gmail.posts) == 1
    result = draft_invoice(config, path, ledger, gmail, NOW, reconcile=True)
    assert result["draft_id"] == "gmail-draft-123"
    assert result["status"] == "awaiting_approval"
    assert len(gmail.posts) == 1


@pytest.mark.parametrize("matches", [[], ["one", "two"]])
def test_missing_or_ambiguous_draft_blocks_recreation(setup, matches):
    config, path, ledger, gmail = setup
    gmail.fail_after_create = True
    with pytest.raises(ReconciliationRequired):
        draft_invoice(config, path, ledger, gmail, NOW)
    gmail.matches = matches
    with pytest.raises(ReconciliationRequired):
        draft_invoice(config, path, ledger, gmail, NOW, reconcile=True)
    assert len(gmail.posts) == 1
    assert ledger.get("2026-09")["status"] != "sent_confirmed"


def test_wrong_account_preflight_does_not_reserve(setup):
    config, path, ledger, gmail = setup
    gmail.actual_account = "aws@example.com"
    with pytest.raises(ValueError):
        draft_invoice(config, path, ledger, gmail, NOW)
    assert ledger.get("2026-09") is None
    assert gmail.posts == []


def test_refuses_mock_ledger(setup):
    config, path, ledger, gmail = setup
    ledger.reserve("2026-09", NOW.isoformat())
    ledger.update("2026-09", NOW.isoformat(), draft_id="mock-2026-09")
    with pytest.raises(ValueError, match="dedicated"):
        draft_invoice(config, path, ledger, gmail, NOW)
    assert gmail.posts == []


def test_sdk_calls_only_profile_and_drafts_with_no_post_retries():
    class Request:
        def __init__(self, value):
            self.value = value
        def execute(self, num_retries):
            assert num_retries == 0
            return self.value
    class Service:
        def __init__(self):
            self.body = None
        def users(self):
            return self
        def getProfile(self, userId):
            assert userId == "me"
            return Request({"emailAddress": "draft@example.com"})
        def drafts(self):
            return self
        def create(self, userId, body):
            self.body = body
            return Request({"id": "draft123"})
        def list(self, **kwargs):
            return Request({"drafts": [{"id": "draft123"}]})
        def get(self, **kwargs):
            assert kwargs["format"] == "raw"
            raw = b"X-Cleaning-Invoice-Operation: op123\r\n\r\nbody"
            return Request({"message": {"raw": base64.urlsafe_b64encode(raw).decode().rstrip("=")}})
    service = Service()
    gmail = GmailDrafts(service, "draft@example.com")
    gmail.preflight()
    assert gmail.create(b"test mime") == "draft123"
    assert base64.urlsafe_b64decode(service.body["message"]["raw"]) == b"test mime"
    assert gmail.find_operation("op123") == ["draft123"]


def test_missing_client_file_has_actionable_error(tmp_path):
    pytest.importorskip("google_auth_oauthlib")
    with pytest.raises(ValueError, match="Desktop OAuth client file missing"):
        oauth_service(tmp_path / "client.json", tmp_path / "token.json", "draft@example.com", interactive=True)


def test_explicit_deleted_draft_recreation(setup):
    config, path, ledger, gmail = setup
    first = draft_invoice(config, path, ledger, gmail, NOW)
    with pytest.raises(ValueError, match="still exists"):
        draft_invoice(config, path, ledger, gmail, NOW, recreate_deleted=True)
    gmail.exists = False
    second = draft_invoice(config, path, ledger, gmail, NOW, recreate_deleted=True)
    assert second["number"] == first["number"] == 12
    assert len(gmail.posts) == 2
    first_msg = BytesParser(policy=policy.default).parsebytes(gmail.posts[0])
    second_msg = BytesParser(policy=policy.default).parsebytes(gmail.posts[1])
    assert first_msg["X-Cleaning-Invoice-Operation"] != second_msg["X-Cleaning-Invoice-Operation"]
    assert list(second_msg.iter_attachments())[0].get_filename() == "Example_SEPTEMBER_26_INVOICE.pdf"
    assert ledger.db.execute("SELECT COUNT(*) FROM gmail_operation_history").fetchone()[0] == 1
    draft_invoice(config, path, ledger, gmail, NOW)
    assert len(gmail.posts) == 2


def test_issued_invoice_cannot_be_recreated(setup):
    config, path, ledger, gmail = setup
    draft_invoice(config, path, ledger, gmail, NOW)
    ledger.update("2026-09", NOW.isoformat(), status="sent_confirmed")
    gmail.exists = False
    with pytest.raises(ValueError, match="issued"):
        draft_invoice(config, path, ledger, gmail, NOW, recreate_deleted=True)
