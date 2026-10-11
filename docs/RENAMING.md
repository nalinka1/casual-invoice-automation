# Package naming migration

The distribution is `casual-invoice-automation`, the source directory is `src/casual_invoice`, and the commands are `casual-invoice` and `casual-invoice-gmail`. Module invocations use `python -m casual_invoice.<module>`. Tests, imports, sample data, documentation links, acceptance tooling, and the runtime template handler use the new name.

Reinstall the editable package after pulling this change: `python -m pip install -e ".[test,gmail,aws]"`. Old console scripts from a previously installed distribution are obsolete; use the new commands. The private configuration, invoice numbering, ledger files, OAuth credentials, and existing draft MIME need no migration.

The legacy `X-Cleaning-Invoice-Operation` header is intentionally retained in generation, reconciliation, and draft recreation so already prepared MIME and remote drafts remain recoverable. It is an internal compatibility identifier, not the public project name. New Message-ID domains use `casual-invoice.local`.

This local rename does not update AWS. Existing deployed code and its handler remain paired and continue operating. For a later cloud release, package the new source and update the Lambda code and handler together using the runtime stack and existing stack parameters. Do not deploy only the new handler against the old ZIP, or only the new ZIP against the old handler. Do not create a replacement storage stack to change its name: retain the authoritative ledger and private bucket. AWS setup examples use a storage stack placeholder; operators must supply their existing stack name when updating a deployment.

Generated PDFs and private operational scripts are outside the source migration. A previously exported architecture PDF must be regenerated to reflect updated documentation. Private scripts that import the old package must be updated before they are used again.

Local verification on 11 October 2026: the renamed distribution installed successfully, all 53 offline tests passed, and both new console commands displayed their help successfully without external operations. This validates local behavior; deploying the renamed handler and artifact still requires a separate cloud release and health check.
