# Phase 01 Execution Plan: Google → Nextcloud Sync

Source PRD: [docs/01-phase-01.md](docs/01-phase-01.md)

Scope baseline (confirmed)
- One-way, incremental sync: Google (People + Calendar) → Nextcloud (CardDAV + CalDAV)
- Idempotent via UID = Google ID + normalized content hashing
- Sync tokens with bounded resync window on invalidation
- Docker + cron/systemd examples, tests, and CI
- Approved stack and defaults:
  - Typer (CLI), httpx (HTTP), pydantic v2 (+ pydantic-settings) for config
  - vobject (vCard), icalendar (ICS), python-caldav (CardDAV/CalDAV)
  - photo_sync = true, overwrite_local = true (protect-local off), vCard 4.0 default, time_window_days = 730, lock_path = /tmp/g2nc.lock
  - Package name = g2nc, CLI entrypoint = g2nc, Docker image = ghcr.io/yourorg/g2nc

Deliverables overview
- Working CLI: g2nc sync, g2nc status, g2nc prune
- SQLite state store with tokens and mappings
- Google API integrations for People and Calendar incremental sync
- Nextcloud DAV clients for vCard/ICS upsert/delete with ETag handling and UID search
- Mappers with stable normalization + hashing
- Tests (unit + mocked integration) covering idempotency, deletions, token invalidation
- Docker, CI workflows, documentation

Repository structure (per PRD §14)
- Code under: src/g2nc/
  - Core: cli.py, config.py, logging.py, state.py
  - Google: google/auth.py, google/contacts.py, google/calendar.py
  - Nextcloud: nextcloud/carddav.py, nextcloud/caldav.py
  - Mapping: mapping/contacts.py, mapping/events.py
  - Sync: sync/contacts_sync.py, sync/calendar_sync.py, sync/orchestrator.py
  - Utils: utils/hashing.py, utils/timezones.py, utils/http.py
- Tests under: tests/unit/*, tests/integration/*
- Docs: docs/index.md, docs/setup.md, docs/config.md, docs/troubleshooting.md
- Packaging/CI: pyproject.toml, .pre-commit-config.yaml, .github/workflows/*
- Docker: Dockerfile, scripts/docker-entrypoint.sh, docker-compose.yml (example)

Milestones, sequence, and exit criteria

M0: Bootstrap & QA rails
- Create package skeleton and pyproject with [dev] extras (ruff, mypy, pytest, pytest-cov, vcrpy, pre-commit)
- Pre-commit hooks and consistent formatting (black via ruff-format or keep black; ruff active)
- Exit criteria:
  - import g2nc works; g2nc.__version__ present
  - pre-commit runs locally without errors

M1: Config, CLI, Logging, State
- config.py: Pydantic models with pydantic-settings for ENV + YAML merge (precedence CLI > ENV > file)
- cli.py: Typer app with commands: sync, status, prune; flags from PRD §18
- logging.py: structured logging (JSON opt), PII redaction helpers (mask emails/phones)
- state.py: SQLite schema per PRD §16 + DAO CRUD, migrations, and safe commit behavior
- Exit criteria:
  - Unit tests for config precedence, CLI parsing, and state DAO (create/read/update/delete tokens and mappings)
  - Log lines demonstrate redaction and JSON format when enabled

M2: Google auth + API clients
- google/auth.py: OAuth installed app flow; token persistence; headless refresh
- google/contacts.py: People API incremental connections.list with deleted markers and paging
- google/calendar.py: incremental events.list per calendar; 410 handling triggers bounded resync; paging
- Exit criteria:
  - Mocked integration (vcrpy) fixtures exercising token refresh, paging, and deleted markers
  - Errors mapped to retry-able/non-retry-able and surfaced to orchestrator

M3: Nextcloud DAV clients
- nextcloud/carddav.py: find by UID, PUT (create/update) with ETag, DELETE; addressbook path from config
- nextcloud/caldav.py: same behaviors for calendars; accept calendar mapping key → CalDAV collection URL
- Exit criteria:
  - Stubbed DAV tests (local stub or adapter mocks) verifying UID search, ETag use, and delete behavior
  - Configurable timeouts/retries via utils/http.py

M4: Mappers + normalization + hashing
- mapping/contacts.py: People → vCard 4.0; mapping of core fields; optional PHOTO; CATEGORIES from groups
- mapping/events.py: Event → VEVENT; TZID, RRULE/RECURRENCE, EXDATE, all-day; optional VALARM
- utils/hashing.py: normalized text production for hashing; deterministic ordering
- Exit criteria:
  - Golden-sample tests for vCard/ICS with stable hashes across runs
  - All-day and timezone recurrence cases represented and validated

M5: Sync engines + orchestrator
- sync/contacts_sync.py and sync/calendar_sync.py implement PRD §17 pseudocode
- sync/orchestrator.py: lock file, config load, multi-calendar iteration, summary + exit codes
- Exit criteria:
  - Idempotency test: two consecutive runs produce zero writes on second run
  - Deletion test: deleted items removed from Nextcloud and state pruned

M6: Docs and UX polish
- README quick start; docs/setup.md (Google OAuth & Nextcloud endpoints), docs/config.md, docs/troubleshooting.md
- Examples: sample config.yaml, docker-compose.yml, systemd unit/timer snippet, .env.example
- Exit criteria:
  - A user can configure and run against mocks or limited live test credentials following docs

M7: Docker & CI
- Dockerfile + entrypoint; mount /data with tokens/state/config
- GitHub Actions: lint, type, tests on PR; release on tag builds and pushes Docker image
- Exit criteria:
  - CI green, coverage ≥ 80% on src; tag v0.1.0 produces a release and pushed images

Risk register and mitigations
- CardDAV/CalDAV UID search variance in Nextcloud versions
  - Mitigate: first search by stored href, fallback to UID REPORT via caldav library; add integration guard test
- Timezone & recurrence fidelity
  - Mitigate: rely on icalendar library; unit golden tests covering RRULE, EXDATE; use tzdata + dateutil
- OAuth bootstrap in container/headless
  - Mitigate: document initial local grant producing token file in /data; support GOOGLE_CREDENTIALS_JSON env
- Token invalidation resync load
  - Mitigate: bounded window default 730 days with configurable time_window_days; log resync scope and counts
- ETag conflicts and race conditions
  - Mitigate: if 412 or ETag mismatch, re-fetch by UID to reconcile and re-try once with backoff
- PII exposure in logs
  - Mitigate: central log redaction helper; unit tests assert masking

Test strategy mapping (PRD §21)
- Unit
  - config precedence; CLI parsing; hashing normalization; state DAO CRUD
  - mapping correctness: contacts/events golden samples; timezone/all-day/recurrence
- Mocked integration
  - Google via vcrpy cassettes (created/updated/deleted, paging, token invalidation 410)
  - Nextcloud DAV via stub/mocks: UID search, PUT update path (ETag), DELETE
- End-to-end behaviors
  - Idempotency: second run zero writes
  - Deletions propagate
  - Token invalidation triggers bounded resync

Observability & operational behaviors
- Exit codes: 0 success; 2 partial; 3 fatal
- Structured logs with optional JSON; redaction enforced at formatter stage
- Summary output: counts for fetched, created, updated, deleted, skipped, retries; token save status

Quality gates and DOD
- ruff check . && mypy src && pytest -q --cov=src ≥ 80%
- Docker image build local and in CI
- Docs complete and consistent with CLI contract
- Re-run idempotency holds across restarts and container runs

Estimated timeline (ideal engineering days)
- M0: 0.5d
- M1: 1.5d
- M2: 2.0d
- M3: 1.5d
- M4: 2.0d
- M5: 2.0d
- M6: 1.0d
- M7: 1.0d
Total: ~11.5d (buffer not included)

Immediate next steps (day 0–1)
1) M0 bootstrap and QA rails
   - Ensure pyproject with [dev] extras, ruff/mypy/pytest configured
   - Add package skeleton files under src/g2nc/
   - Pre-commit set up
2) M1 config/CLI/logging/state stubs + tests scaffolding
   - Implement minimal config model and file+env loader
   - Wire Typer CLI skeleton with commands and options
   - Add state schema and migrations with smoke tests

References
- PRD sections: §§6–9, 16–18, 21–24
- Repo paths: src/g2nc/*, tests/*, docs/*