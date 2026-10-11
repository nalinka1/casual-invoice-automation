from .configuration import address


class MockSNS:
    """SQLite-backed mock notification delivery; no network calls."""
    def __init__(self, ledger, recipient, subscription_confirmed=False):
        self.ledger = ledger
        self.recipient = recipient
        self.subscription_confirmed = subscription_confirmed

    def preflight(self):
        address(self.recipient)
        if not self.subscription_confirmed:
            raise ValueError("Mock SNS subscription must be confirmed")

    def publish(self, key, payload):
        self.preflight()
        if set(payload) - {"billing_month", "invoice_number", "status", "site_days", "total", "instruction"}:
            raise ValueError("Sensitive or unsupported notification fields")
        return self.ledger.event(key, payload)
