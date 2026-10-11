# Isolated hosted acceptance test

Use `infrastructure/acceptance.json` only as a temporary test stack, never as a production scheduler target. It provisions a separate on-demand DynamoDB table, test Lambda and isolated roles. Its role cannot access the production ledger. Inputs and generated objects live under a unique private S3 `acceptance/<run-id>/` prefix. `tools/acceptance_handler.py` maps all S3 requests into that prefix and wraps notifications with a TEST ONLY label.

Generate a synthetic PDF using the test fixture, use sample rates, set the email recipient to the configured drafting account, and prefix the subject with `[TEST ONLY]`. The entrypoint checks the recipient and subject before creating a draft. Do not upload the real invoice template to the test prefix. Seed the separate counter with a clearly artificial number; keep production numbering unchanged.

Package the same application code and Linux dependencies used in production with `acceptance_handler.py` at the archive root. Provide the private bucket, unique prefix, package key, existing Gmail secret and notification topic as stack parameters. Keep account-specific parameter files outside source control.

After deployment, use the stack's isolated scheduler role and Lambda ARN to create a one-time EventBridge schedule in Australia/Melbourne, with no retries, at least two minutes in the future. Verify a newly created test month record and Gmail draft after the scheduled trigger. Check the attached PDF against the S3 object checksum and verify the recipient, subject and attachment name. Invoke the test Lambda again and confirm the same draft ID and test invoice number are returned, with only one ready notification event.

Finally verify the production counter and schedules are unchanged. Save the acceptance evidence privately, remove the one-time schedule and delete the temporary test stack. Retain the unsent test draft for human review; it can be manually deleted afterward. The S3 test artifacts remain private for audit. No invoice email is automatically sent.

## Verified result

On 11 October 2026, the one-time EventBridge trigger successfully invoked the isolated Lambda, generated the synthetic invoice PDF and created the labelled Gmail draft. The retrieved Gmail attachment matched the private S3 PDF byte for byte and its saved checksum. A second invocation reused the same draft ID and artificial invoice number. The ready alert was published, and the production counter stayed unchanged. All 53 offline tests passed. Human review of the unsent test draft remains available; the recurring production schedule's first run is still upcoming.
