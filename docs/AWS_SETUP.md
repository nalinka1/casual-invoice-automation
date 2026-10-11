# AWS private storage

This setup keeps private operator data outside the public source tree. Use an AWS CLI profile for the intended account; never paste keys into config or source. Confirm identity with `aws sts get-caller-identity` before deployment.

## Storage foundation

Deploy `infrastructure/storage.json` with CloudFormation in the configured region:

```powershell
aws cloudformation deploy --template-file infrastructure/storage.json --stack-name YOUR_STORAGE_STACK --region ap-southeast-2
aws cloudformation describe-stacks --stack-name YOUR_STORAGE_STACK --region ap-southeast-2 --query "Stacks[0].Outputs"
```

The stack creates a private, versioned SSE-S3 bucket with all four public-access blocks enabled and HTTPS required; an on-demand encrypted DynamoDB table; and an empty Secrets Manager secret. It creates no public website or scheduled job. Business resources are retained on stack deletion; retained storage/secrets continue to incur charges. There is no NAT gateway, server, customer-managed KMS key or provisioned database capacity.

After validating outputs and privacy settings, install `pip install -e ".[aws]"`, then run the private migration with the output bucket/table/secret IDs and verified account:

```powershell
python -m casual_invoice.aws_storage --account-id YOUR_ACCOUNT_ID --bucket YOUR_BUCKET --table YOUR_TABLE --secret-arn YOUR_SECRET_ARN
```

The migration uploads ignored `config.json`, the private source template, issued PDFs and Gmail OAuth data. It imports local issued invoice records and the last reserved number into DynamoDB atomically; initialization refuses to overwrite an existing ledger. It uses the existing default profile, or pass `--profile PROFILE_NAME`. Do not log secret values. Partial S3/Secrets failures require inspection before recovery; the migration does not silently reset initialized state.

Private S3 keys are `settings/config.json`, `templates/invoice-template.pdf` and `invoices/<billing-month>/<filename>`. `get_config` loads/validates S3 configuration and downloads the template to a caller-selected private working directory. `DynamoLedger.reserve` atomically creates the month record and increments the counter; retries reuse the month record.

## Runtime deployment prerequisites

`infrastructure/runtime.json` deploys Python 3.12 Lambda, a restricted execution role, SNS email subscription, two schedules initially disabled, a dead-letter queue and Lambda/delivery failure alarms. Monthly generation is the 3rd at 21:00 Melbourne time; monitoring is daily at 21:15. The missed-run cutoff is the 6th, configured privately. Linux dependencies are packaged with `tools/package_lambda.py`. Public source is included; configuration, PDFs and secrets are excluded from the package.

To reproduce runtime packaging on Windows, install dependencies into a separate ignored directory with pip's `--target`, `--platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64`, `--python-version 3.12`, `--implementation cp`, `--abi cp312 --abi abi3 --abi none` and `--only-binary=:all:` options. Include PyMuPDF, tzdata and google-api-python-client. Lambda supplies boto3. Run `python tools/package_lambda.py --dependencies DEPENDENCY_DIRECTORY` and verify the uncompressed archive stays below Lambda's size limit. Upload the ZIP to `deployments/<unique-version>.zip` in the private bucket.

Deploy the runtime template using CloudFormation and `CAPABILITY_IAM`, supplying storage outputs as `PrivateBucket`, `LedgerTable`, `GmailSecret`, the versioned ZIP key as `CodeKey`, the private notification address as `NotificationEmail`, and `ScheduleState=DISABLED`. Keep personal parameter files ignored. Changing code requires a new object key and a stack update. Read-only acceptance invokes the function with `{"action":"health"}`; a generation invocation uses `{"action":"generate"}`. Enable schedules only after confirming the subscription, updated OAuth secret and successful health check.

Cloud generation loads inputs from private storage and freezes PDF/MIME objects before a conditional DynamoDB create claim. Ambiguous Gmail create outcomes are reconciled against the operation header. Notifications use durable publish intents; an ambiguous SNS publish is not blindly retried, so a crash can suppress a notification. Exactly-once external notification delivery is not guaranteed. Failures also route through independent Lambda/SQS alarms. A deleted/missing draft produces a delivery-unconfirmed alert, never a sent status.

After manually sending a hosted invoice, run `python -m casual_invoice.cloud_state --month YYYY-MM --invoice-number NUMBER --confirm-manual-send`. This records explicit human evidence with conditional month/number checks. Live Sent API reconciliation is not implemented because current Gmail access is compose-only. Once private configuration selects `deployment_mode: aws`, local Gmail generation is blocked to prevent competing ledgers. OAuth connection/reauthorization remains available locally.

For OAuth production onboarding, change Google Auth Platform → Audience to In production and run `casual-invoice-gmail connect --reauthorize`. Then use `python -m casual_invoice.aws_storage --refresh-gmail-secret --account-id ACCOUNT --secret-arn ARN` to refresh the hosted secret without reinitializing DynamoDB. Confirm the SNS subscription email and run a Lambda health check before enabling schedules. Personal-use verification exceptions depend on Google policy; publishing does not automatically mean verified.

Storage does not run the job. Lambda packaging needs Linux-compatible PyMuPDF dependencies, a role limited to the specific bucket/table/secret/topic, and a hosted workflow using DynamoDB state rather than local SQLite. Migrate the issued ledger before activating schedules. Separate monthly generation and independent monitoring, and add failure handling. Keep schedules disabled until end-to-end acceptance succeeds. Gmail tokens for an External app in Testing can expire after seven days; complete appropriate OAuth production configuration before relying on monthly runs. See [Google token expiration guidance](https://developers.google.com/identity/protocols/oauth2#expiration).

Public repository checklist: use example config and synthetic tests; exclude private config, notes, tokens, PDFs and ledgers; scan every committed revision for sensitive content; push only reviewed code. The local initial Git history contains only a generic README. Remote history/branches have not been inspected, and the repository has not been published.

## Deployment progress

Initial live verification invoked health, generation for an already-issued month, and monitoring. All returned successfully; generation reused the sent record and did not create a duplicate or increment the counter. Enabled scheduler state and confirmed subscription were checked. SNS accepted a labelled test publish, and the operator confirmed inbox receipt. Subsequent testing below covered the complete generation path through an isolated scheduled trigger.

Subsequent isolated acceptance verified a real one-time EventBridge trigger through Lambda PDF generation, private S3 storage, Gmail draft/attachment and labelled SNS publish. A retry reused the test draft, and production numbering was unchanged. Temporary compute/state resources were removed after success; private test artifacts and the unsent Gmail test draft remain for review. See [acceptance test details](ACCEPTANCE_TEST.md). The first recurring production run remains upcoming.

Storage and runtime stacks deployed successfully in the selected region. Bucket privacy/versioning and the Lambda health check were verified against real AWS resources; the issued local record was imported and the counter preserved. No duplicate invoice was created. After user OAuth reauthorization and SNS confirmation, both schedules were ENABLED and verified: monthly generation on the 3rd at 21:00 and daily monitoring at 21:15, Australia/Melbourne. First scheduled execution remains unobserved. All 50 tests passed. Source cleanup preserved original private notes under ignored `.private/`; actual config remains local and ignored, with an encrypted private S3 copy. Public tests use generated synthetic PDFs.
