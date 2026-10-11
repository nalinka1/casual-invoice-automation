# Casual Services Invoice Automation Platform

Deterministic Python 3.12+ billing for two service sites: regional business-day calendars, leave overrides, Decimal arithmetic, a preserved PDF template, and Gmail drafts for manual sending.

## Local development

Create a virtual environment and install `pip install -e ".[test,gmail,aws]"`. Run `python -m pytest`. Tests generate a synthetic invoice and load `config.example.json`; no private files or network access are required. On restricted Windows environments use `-p no:cacheprovider --basetemp output/pytest-<unique-run-id>`.

Copy `config.example.json` to the ignored `config.json`, supply your own private PDF and settings, and run `cleaning-invoice` for a local preview. Invoice numbers in previews are unreserved. Use `cleaning-invoice-gmail connect` and then `cleaning-invoice-gmail draft` for explicit Gmail draft creation. Sending is always manual.

## Architecture

See the [advanced architecture and developer review](docs/ARCHITECTURE.md) for system diagrams, execution and state models, security boundaries, performance measurement plans, and a prioritized review backlog. The professional project name is used in documentation; existing package and command names remain compatible.

`src/cleaning_invoice` separates configuration validation, calendar resolution, PDF generation, email composition, SQLite state, Gmail OAuth and private AWS storage. `Decimal` handles currency and `zoneinfo` handles Melbourne dates. Site overrides win over global overrides. Unknown calendar years fail closed. Regional holiday configuration replaces Melbourne Cup with Geelong Cup where applicable.

Five dynamic PDF fields are replaced; all other content is preserved. PDF tests compare rendered pixels outside those fields. Filenames use a configurable prefix, billing month and year. Gmail operations save a frozen MIME payload and durable intent before the remote request. Ambiguous outcomes require reconciliation; they never trigger blind duplicate creation. Deleted-draft recreation requires explicit user confirmation.

## Privacy and AWS

Real config, PDFs, credentials, generated files, ledgers and private notes are ignored. Private S3 stores configuration/templates/invoices, Secrets Manager stores Gmail OAuth data, and on-demand DynamoDB stores invoice state and reservations. The storage stack blocks S3 public access, requires HTTPS, encrypts data, versions objects and retains business data if the stack is deleted.

See [AWS setup](docs/AWS_SETUP.md) and [Gmail setup](docs/GMAIL_SETUP.md). Storage provisioning is separate from runtime scheduling; do not claim a recurring task is active until deployment and scheduled behavior are verified. Code and sample data can be shared; do not upload the entire workspace. Scan the Git history before publishing. This repository has not been made public by the application.

## Validation and limits

Current verification: **53 offline tests pass**. Private storage and the Lambda/SNS runtime are deployed; the hosted health check verified configuration, ledger and Gmail access. Monthly and monitor schedules are enabled in Australia/Melbourne. An isolated one-time schedule successfully exercised synthetic PDF generation, Gmail attachment creation, notification publish and same-draft retry without changing production numbering. The recurring production schedule has not yet reached its first run. See [acceptance testing](docs/ACCEPTANCE_TEST.md). Repository visibility is unchanged; personal resource identifiers and operational notes stay outside public source.

Automated tests cover regional calendars, leave, timezone rollover, monetary calculations, invalid configuration, synthetic PDF fidelity, concurrent SQLite numbering, draft retries, mock monitoring and explicit deleted-draft recovery. Live mail and cloud acceptance are recorded only in private operator notes.

No GST calculation is inferred from a source invoice. Real contracts, holiday entitlement, yearly calendars, issued numbers and email addresses must be confirmed by the operator. OAuth compose permission also grants sending capability at Google; application code exposes no send path. Remote notification exactly-once delivery cannot be guaranteed across a publish/commit interruption.
