# Project: Google → Nextcloud Sync (Python)
**Goal:** Build an idempotent, incremental, one-way sync from Google (People API, Calendar API) to Nextcloud (CardDAV, CalDAV) with tokens, deletions, updates, Docker, cron, tests, docs, and CI. Use the PRD in `docs/prd-google-nextcloud-sync.md` as the single source of truth.

## Environment & Shell
- OS: Arch Linux
- Shell: zsh
- Python: 3.12 with venv at `.venv` (activate with `source .venv/bin/activate` in zsh)
- No `sudo` or global installs unless the user explicitly asks.

## Tooling & Commands
- Package & QA: `pip install -e .[dev]`, `ruff check .`, `mypy src`, `pytest -q --cov=src`
- Runner: `g2nc sync --config /data/config.yaml`
- Docker: `docker build -t g2nc:dev .` then `docker run --rm -v "$PWD/data:/data" g2nc:dev g2nc sync --config /data/config.yaml`

## Kilo Code Tool Usage (guardrails)
- Prefer **read/edit tools** over free-form typing: `read_file`, `search_files`, `write_to_file`, `apply_diff`.  
- Use `execute_command` only for safe, deterministic commands (lint/tests/build). Never run destructive commands without confirmation.  
- Keep **auto-approval OFF** by default. Ask before running multi-minute commands or anything network-heavy. (Kilo tool refs: read/edit/execute/attempt_completion). :contentReference[oaicite:1]{index=1}

## Security & Secrets
- Never commit real secrets. Create `.env.example` and `docs/setup.md` for secret setup.  
- Read secrets from env or mounted files only.  
- Mask PII in logs (emails/phones). Redact in examples.

## Files & Structure (must follow)
- `src/g2nc/` modules as defined in the PRD (google/, nextcloud/, mapping/, sync/, utils/…).  
- `state.sqlite` and Google token JSON live under `/data` (git-ignored).  
- `pyproject.toml` with extras `[dev]` for `ruff`, `mypy`, `pytest`, `pytest-cov`, `vcrpy`, `pre-commit`.  
- Docs in `docs/` (setup, config, troubleshooting), plus `README.md`.

## Coding Standards
- Type-annotated Python 3.12.  
- Small, single-purpose functions.  
- Raise explicit exceptions with context.  
- Log with structured JSON option; never log raw PII.  
- vCard 4.0 unless config sets 3.0; ICS with stable `UID` = Google ID.

## Source-of-Truth & Idempotency
- **Google authoritative.** Local edits in Nextcloud can be overwritten unless `--protect-local` (default: off).  
- **UIDs:** vCard UID=`people/...`, VEVENT UID=`event.id`.  
- Use sync tokens; on token invalidation, bounded window resync.  
- De-dup by searching UID in CardDAV/CalDAV before create; store href + etag + hash in SQLite.

## Testing Requirements
- Unit tests for mapping, hashing, state DAO, CLI.  
- Mocked integration with `vcrpy` for Google; DAV stub for Nextcloud.  
- Idempotency test: second run → zero writes.  
- Token invalidation test: simulate 410 → windowed resync.  
- Coverage target ≥80% on `src/`.

## CI & Releases
- GitHub Actions: lint + type + tests on PR; Docker image on tag.  
- Conventional commits; generate changelog on release.

## Task Workflow (every change)
1) Plan: create/update a TODO checklist and implementation notes referencing PRD sections.  
2) Scaffold/edit files with `write_to_file`/`apply_diff`.  
3) Install deps, run `ruff`, `mypy`, `pytest`. Fix failures.  
4) Update docs/examples.  
5) Show a concise diff summary and **propose** commits (no auto-commit).  
6) Use `attempt_completion` with what changed, how to run it, and next steps.

## Don’ts
- Don’t invent undocumented APIs; if unsure, ask or stub behind an interface.  
- Don’t change the public CLI contract without updating docs and tests.  
- Don’t log access tokens or PII.

## Definition of Done (per PR)
- All tests green locally and in CI.  
- Idempotent rerun produces zero changes.  
- Docs updated (README + docs/*).  
- Minimal diff; conventional commit message proposals included.

## Formatting & Quality Gates (Kilo rules)
- Always run: `ruff check . && black --check . && mypy src && pytest -q` before proposing a PR.
- If any gate fails, fix and re-run. Do not open a PR with red checks.

## Milestone → PR Policy
- Each milestone must land in a **short-lived branch** (e.g., `feat/mapper-vcard`).
- After completing a milestone:
  1) Update tests/docs.
  2) Prepare a concise PR description referencing PRD sections.
  3) Open a PR and request review from CODEOWNERS.
  4) Do not self-merge. Await review and green CI.

## Commit Policy
- Use Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`, etc.).
- Commits must be small and meaningful; avoid mixed concerns.
