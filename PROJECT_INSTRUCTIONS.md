# Project Instructions

Read this document, KNOWLEDGE_BASE.md and config.example.json for public development. Read ignored config.json and private notes only for authorized operator work; do not copy personal settings or credentials into source or logs.

Use Python 3.12+, pytest, modular deterministic billing and Decimal currency arithmetic. Keep mocks available, tests offline and synthetic. Preserve invoice template constant fields and fail closed on unsupported years or ambiguous invoice/draft outcomes.

Human sending approval is mandatory. Never infer sending from missing drafts. Separate mock and real ledgers. Confirm issued-number baselines before initializing new storage, and migrate existing issued records before activating hosted generation.

Personal data, tokens, source PDFs and generated artifacts must remain private. Use private S3, Secrets Manager, least-privilege roles and DynamoDB conditional transactions for hosted state. Do not make a repository public or rewrite Git history without explicit authorization. Document actual deployment status separately from planned architecture.
