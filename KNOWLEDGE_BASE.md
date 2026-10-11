# Knowledge Base

Public design notes only. Real operator settings and acceptance history belong in ignored private notes and AWS private storage.

Billing covers the previous Melbourne calendar month. Configured weekdays exclude verified regional public holidays; global leave overrides apply before per-site overrides. Each site's count is multiplied by its confirmed Decimal daily rate. Unsupported calendar years stop processing.

The source PDF is private. Update only invoice number, actual creation date, two site amounts and total; retain first-page identity, payment details and layout. Do not carry unrelated receipt pages forward. Invoice numbers are reserved durably and become issued only on confirmed sending. Preserve the same monthly reservation on retries.

Email drafts use configurable exact wording and attach the PDF. Human sending approval remains required. A missing draft is not delivery evidence. Monitor missing monthly runs and pending approvals independently.

Private configuration/template/PDFs use S3; OAuth secrets use Secrets Manager; hosted state uses DynamoDB. IAM roles should grant only application resources. Public examples and tests must use synthetic identities and documents.
