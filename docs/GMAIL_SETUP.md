# Gmail draft-only connection

An operator must explicitly authorize connecting their configured Gmail account and creating an invoice draft. Sending remains manual. AWS and notifications remain local mocks; this command does not call them. The ChatGPT Gmail plugin connection is separate from this Python application's OAuth credentials.

## One-time Google setup

Follow the [official Python quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python) for Google Cloud project selection, enabling the Gmail API and configuring Google Auth Platform. This is a Google API project, separate from the AWS account. Choose an existing suitable project or create one yourself. Configure an External audience and, while testing, add the **drafting Gmail address** as a test user. Create an OAuth client of application type **Desktop app** and download its JSON file.

Store that file locally as `.secrets/gmail-client.json` in the project. Do not paste its contents into chat or commit it. `.secrets/` is ignored by Git. Restrict this folder's Windows permissions to your user; tokens stored here grant mailbox access. This local implementation uses private files, not an encrypted credential vault; use a secrets facility before hosting.

Install optional dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test,gmail]"
```

Connect interactively:

```powershell
.\.venv\Scripts\python.exe -m cleaning_invoice.gmail_service connect
```

A browser consent window opens. Select **draft@example.com**, review and approve the requested access. The program verifies the authorized Gmail profile before saving `.secrets/gmail-token.json`. Choosing a different account fails. This command creates no draft. Do not share the token file.

The requested `gmail.compose` scope permits managing drafts **and sending** according to [Google's scope documentation](https://developers.google.com/workspace/gmail/api/auth/scopes). Draft-only behavior is enforced by application code, which contains no send method or API invocation. A Google consent warning does not mean the scope technically prevents sending. If Google blocks consent, check test-user, client and consent configuration; do not bypass an account policy.

## Create September's draft

While running in October 2026:

```powershell
.\.venv\Scripts\python.exe -m cleaning_invoice.gmail_service draft
```

This bills the previous Melbourne calendar month, uses the configured real recipient, reserves number 12 in a dedicated `output/gmail/gmail-ledger.sqlite`, and attaches the generated PDF. Open Gmail Drafts in the verified account to review and **manually send**. Reruns use the saved draft ID; they do not update the draft or create another one. Mock state in `output/mock` is separate and does not count as issuance. Do not create a second Gmail ledger for the same real billing scope.

The local ledger records `awaiting_approval`, not actual issuance. Live Sent reconciliation and live reminders are not yet implemented; the existing monitor is for mocks. Do not pass this Gmail ledger to the mock monitor or simulate sending on it. If you edit/delete/send a draft in Gmail, rerunning does not recreate it. Actual issuance must be reconciled before allocating later numbers or changing the baseline.

## Interrupted draft creation

When the user explicitly confirms a saved draft was deleted **without sending**, `draft --recreate-deleted-draft` checks that Gmail returns 404 for the previous draft ID, archives the prior operation and recreates the same invoice number with its original PDF bytes and the current filename convention. Existing drafts and `sent_confirmed` records block recreation. The flag asserts the user's deletion confirmation; draft absence alone is never treated as evidence of deletion or delivery. Ordinary reruns still do not recreate drafts.

A durable operation marker and frozen MIME payload are stored before calling Gmail. Automatic retries on draft-create POST are disabled. If a timeout or interruption leaves the outcome uncertain, automatic recreation is blocked. Run:

```powershell
.\.venv\Scripts\python.exe -m cleaning_invoice.gmail_service draft --reconcile
```

This reads draft metadata/content to locate exactly one draft with the operation header, including pagination. It adopts that ID without another create request. If none or multiple match, it stays blocked: inspect Drafts and Sent manually. Missing draft is not sending evidence. If preparation stops before the database commit and leaves an unexpected PDF, inspect it before recovery; it will not silently overwrite the file. Database loss cannot be recovered automatically; back up private durable state.

Gmail drafts use MIME encoded as base64url, as described in [Google's draft guide](https://developers.google.com/workspace/gmail/api/guides/drafts). The stored PDF checksum records the locally attached version; human edits in Gmail are not automatically validated.

## Current verification

Offline tests use a fake Gmail service to check account mismatch, attachment content, successful retries, ambiguous or missing draft recovery, and no send operation. A live draft has not been created until OAuth setup and the draft command succeed. No monthly scheduler or AWS resource is created here.

