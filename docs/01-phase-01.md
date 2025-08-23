# PRD: Google → Nextcloud Contacts & Calendar Sync (One-Way, Incremental)

## 1) Overview

A Python tool to **safely, incrementally sync** Google Contacts and Google Calendar events **into** a Nextcloud instance (CardDAV + CalDAV). Google is the source of truth; updates and deletions propagate to Nextcloud. The tool must be **idempotent**, handle **repeated runs without duplicates**, and support **local runs, Docker, and cron/systemd scheduling**. It ships with tests, docs, and GitHub Actions.

---

## 2) Goals & Non-Goals

### Goals
- One-way sync: Google → Nextcloud.
- Incremental sync using Google **sync tokens** (avoid full scans).
- Propagate **creates, updates, deletions**.
- **Idempotent**: no duplicates when re-run; safe to run multiple times/day.
- Flexible configuration: CLI flags and/or config file; secrets via env or file.
- Works **locally** and in **Docker**; easy to schedule via **cron** or **systemd**.
- Comprehensive **tests** (unit + integration/mocked), **docs**, and **CI**.

### Non-Goals (v1)
- Two-way sync (Nextcloud → Google).
- Complex conflict resolution (Google is authoritative).
- Attachments for events; tasks (VTODO); shared permissions.
- Exotic contact fields beyond standard vCard mapping.

---

## 3) Users & Use Cases

- **Individual power user / homelab**: run nightly cron to mirror Google to self-hosted Nextcloud.
- **Team admin**: sync a service account’s event calendar to a shared Nextcloud calendar feed.
- **Backup/migration**: keep a live mirror in Nextcloud for offline clients and mobile CardDAV/CalDAV.

---

## 4) High-Level Architecture

### Components
- **Google API Layer**  
  - People API (Contacts): uses `syncToken` for incremental connections; returns `deleted` markers.  
  - Calendar API (Events): uses `nextSyncToken` for incremental event changes; returns `cancelled/deleted`.
- **Nextcloud DAV Layer**  
  - **CardDAV** for contacts (vCard 3.0/4.0).  
  - **CalDAV** for events (iCalendar/ICS VEVENT).
- **Mapper**:  
  - Google Contact → vCard; Google Event → VEVENT (ICS).  
  - Set `UID` to Google’s stable ID to ensure idempotency.  
- **State Store (SQLite)**:  
  - Tracks Google IDs ↔ Nextcloud object HREFs, ETags, hashes, and last sync tokens.
- **Sync Orchestrator**:  
  - Performs incremental fetch, maps changes, upserts/deletes in Nextcloud, persists state.
- **Config & Secrets**:  
  - CLI (argparse/Typer) + YAML/INI config + env vars; precedence: CLI > env > file.
- **Logging & Retry**:  
  - Structured logs, exponential backoff, resumable on next run.

---

## 5) Data Flow (per run)

1. Load config + secrets; open SQLite state DB.
2. Obtain Google `syncToken`/`nextSyncToken` from state; if absent → first full sync.
3. Fetch incremental changes from Google:
   - Contacts: `connections.list` with `syncToken` (People API).
   - Calendar: `events.list` per mapped calendar with `syncToken`.
4. For each change:
   - If **deleted/cancelled** → DELETE in Nextcloud using stored HREF; prune mapping.
   - If **new/updated** → map to vCard/ICS; compute hash; if changed or not present:
     - Search by **UID = Google ID** in Nextcloud (or use stored HREF).  
     - PUT (create/update) and store returned HREF/ETag and new hash.
5. Persist new Google sync tokens and per-item etags/hashes.
6. Emit summary; exit 0 on success; non-zero on fatal.

---

## 6) Identifiers, Idempotency & De-duplication

- **Contacts UID**: use Google `resourceName` (e.g., `people/c12345`) as vCard `UID`.
- **Events UID**: use Google `event.id` as ICS `UID`.
- **Mapping DB**: table(s) store `google_id`, `nextcloud_href`, `etag`, `hash`, `deleted_flag`.
- **Hashing**: normalize mapped vCard/ICS text, then compute a stable hash to avoid unnecessary PUTs.
- **Search on create**: if no mapping exists, first try GET query on Nextcloud for existing object with matching `UID` to avoid duplicates (fallback: mapping DB).

---

## 7) Scope of Mappings

### Contacts (People API → vCard)
- Name (FN, N), Nickname.
- Emails (types), Phones (types), URLs.
- Organization (ORG, TITLE), Notes (NOTE), Birthday (BDAY).
- Addresses (ADR).
- Photos (PHOTO, optional toggle).
- Google groups → optional `CATEGORIES` (configurable).
- vCard version: **4.0** preferred (Nextcloud supports 3.0/4.0; choose 4.0 unless config sets 3.0).

### Events (Calendar API → ICS VEVENT)
- UID (Google `event.id`), SUMMARY, DESCRIPTION, LOCATION.
- DTSTART/DTEND with proper **TZID**; support all-day events.
- RRULE/RECURRENCE, EXDATE, RDATE.
- STATUS (CONFIRMED/CANCELLED), VISIBILITY.
- Attendees (CN + mailto), Organizer (optional).
- Reminders → optional VALARM (simple mapping; configurable).
- Keep Google as authoritative: Nextcloud edits get overwritten unless `--protect-local` is set (off by default).

---

## 8) Incremental Sync & Deletions

- **Contacts**: use People API `syncToken`; handle 410 GONE by re-doing a full sync (API contract).
- **Calendar**: per calendar `nextSyncToken`; on token invalidation → do a bounded full resync (time window config, default 2 years) to limit API load.
- **Deletions**: both APIs surface deleted; delete from Nextcloud and mark mapping as removed.

---

## 9) Configuration

### CLI (examples)
```
$ g2nc sync --contacts --calendar   --config /etc/g2nc/config.yaml   --dry-run   --photo-sync=false   --calendar-map default:work,team:team-cal
```

### Config file (YAML)
```yaml
google:
  credentials_file: /secrets/google_oauth.json   # or env GOOGLE_CREDENTIALS_JSON / GOOGLE_CREDENTIALS_FILE
  token_store: /data/google_token.json           # stores refresh/access tokens
  contact_groups: ["contactGroups/myContacts"]   # optional filter; default: all connections
  calendar_ids:
    work: primary
    team: some-team-calendar-id@group.calendar.google.com

nextcloud:
  base_url: https://cloud.example.com
  username: nc_user                   # or env NEXTCLOUD_USERNAME
  app_password: ${NEXTCLOUD_APP_PASSWORD}
  addressbook_path: /remote.php/dav/addressbooks/users/nc_user/Contacts/   # CardDAV collection
  calendars:
    work: /remote.php/dav/calendars/nc_user/work/
    team: /remote.php/dav/calendars/nc_user/team/

sync:
  photo_sync: true
  overwrite_local: true      # Google authoritative
  time_window_days: 730      # resync window when tokens invalid
  batch_size: 200
  max_retries: 5
  backoff_initial_sec: 1.0

state:
  db_path: /data/state.sqlite

logging:
  level: INFO
  json: true

runtime:
  lock_path: /tmp/g2nc.lock
```

### Precedence
`CLI > ENV > config file` (explicit beats implicit).

### Secrets
- **Google**: OAuth 2.0 installed app flow; persist refresh token to `token_store`. Initial interactive grant once; headless thereafter.  
  - Env alternatives: `GOOGLE_CREDENTIALS_JSON` (inline JSON) or `GOOGLE_CREDENTIALS_FILE` (path).
- **Nextcloud**: **App password** via `NEXTCLOUD_APP_PASSWORD` or config file (discouraged).

---

## 10) Running Modes

- **Local**: install via `pipx` or `uv`; `g2nc sync …`.
- **Docker**: image bundles code; mount `/data` for state + tokens + config.
- **Cron**:  
  ```
  # Run every 2 hours
  0 */2 * * * docker run --rm -v /opt/g2nc:/data     -e NEXTCLOUD_APP_PASSWORD=*** ghcr.io/yourorg/g2nc:latest     g2nc sync --config /data/config.yaml
  ```
- **systemd timer** (optional example included in docs).

---

## 11) Error Handling & Resilience

- **Retries** with exponential backoff on 429/5xx; jitter.
- **Token invalidation**: detect 410/invalidSyncToken and fall back to windowed resync.
- **Lock file** prevents concurrent runs (configurable).
- **Partial failures** are surfaced with non-zero exit and per-item error counts; state persists only after successful write.

---

## 12) Observability

- Structured logs (JSON option), redacting PII (emails/phones masked).  
- Exit codes: 0 success; 2 partial; 3 fatal.  
- Optional Prometheus metrics endpoint (future enhancement).

---

## 13) Security & Privacy

- Don’t log raw PII.  
- Secrets from env or mounted files; avoid baking into images.  
- SQLite and token stores readable only by process user.  
- License: MIT.

---

## 14) Repository Structure

```
g2nc/
  src/g2nc/
    __init__.py
    cli.py
    config.py
    logging.py
    state.py                # sqlite schema + DAO
    google/
      auth.py
      contacts.py
      calendar.py
    nextcloud/
      carddav.py
      caldav.py
    mapping/
      contacts.py           # Google → vCard
      events.py             # Google → ICS
    sync/
      contacts_sync.py
      calendar_sync.py
      orchestrator.py
    utils/
      hashing.py
      timezones.py
      http.py
  tests/
    unit/...
    integration/...
  docs/
    index.md
    setup.md
    config.md
    troubleshooting.md
  scripts/
    docker-entrypoint.sh
  .github/
    workflows/
      ci.yml
      release.yml
  Dockerfile
  docker-compose.yml (example)
  pyproject.toml (ruff, mypy, pytest)
  README.md
  LICENSE
```

---

## 15) Dependencies (suggested)

- Google: `google-api-python-client`, `google-auth`, `google-auth-oauthlib`
- DAV/Formats: `caldav`, `vobject`, `icalendar`
- Core: `httpx` or `requests`, `pydantic` (config), `sqlite3` (stdlib) or `aiosqlite` if async later
- Tooling: `typer` (CLI) or `argparse`, `rich` (pretty logs) optional
- QA: `pytest`, `pytest-cov`, `vcrpy`, `mypy`, `ruff`, `pre-commit`

---

## 16) SQLite Schema (v1)

```sql
-- global sync tokens
CREATE TABLE IF NOT EXISTS sync_tokens (
  scope TEXT PRIMARY KEY,          -- 'contacts' or 'calendar:<id>'
  token TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- contacts mapping
CREATE TABLE IF NOT EXISTS contacts_map (
  google_id TEXT PRIMARY KEY,      -- people/cNNNN
  etag TEXT,
  nextcloud_href TEXT,             -- full CardDAV item URL
  content_hash TEXT,
  deleted INTEGER DEFAULT 0,
  updated_at TEXT NOT NULL
);

-- events mapping
CREATE TABLE IF NOT EXISTS events_map (
  calendar_id TEXT NOT NULL,
  google_id TEXT NOT NULL,         -- event.id
  etag TEXT,
  nextcloud_href TEXT,             -- full CalDAV item URL
  content_hash TEXT,
  deleted INTEGER DEFAULT 0,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (calendar_id, google_id)
);
```

---

## 17) Sync Algorithms (pseudocode)

### Contacts
```
token = state.get_token('contacts')
resp = google.list_connections(syncToken=token or None)

for change in resp.items:
  if change.deleted:
    href = state.lookup_contact_href(change.id)
    if href: nextcloud.carddav.delete(href)
    state.remove_contact(change.id)
  else:
    vcard = map_google_contact_to_vcard(change.person)
    h = hash(normalize(vcard))
    record = state.get_contact(change.id)
    if not record or record.content_hash != h:
      href = record.nextcloud_href or find_by_uid_in_carddav(vcard.uid)
      new_href, etag = nextcloud.carddav.put(vcard, href)
      state.upsert_contact(change.id, new_href, etag, h)

state.save_token('contacts', resp.nextSyncToken)
```

### Calendar (per calendar)
```
token = state.get_token('calendar:'+cal_id)
resp = google.list_events(calendarId=cal_id, syncToken=token or None, timeMin=now-Window)

for ev in resp.items:
  if ev.status in ['cancelled', 'deleted'] or ev.deleted:
    href = state.lookup_event_href(cal_id, ev.id)
    if href: nextcloud.caldav.delete(href)
    state.remove_event(cal_id, ev.id)
  else:
    vevent = map_google_event_to_vevent(ev)
    h = hash(normalize(vevent))
    record = state.get_event(cal_id, ev.id)
    if not record or record.content_hash != h:
      href = record.nextcloud_href or find_by_uid_in_caldav(vevent.uid, cal_collection)
      new_href, etag = nextcloud.caldav.put(vevent, href)
      state.upsert_event(cal_id, ev.id, new_href, etag, h)

state.save_token('calendar:'+cal_id, resp.nextSyncToken)
```

---

## 18) CLI Contract

- `g2nc sync [--contacts] [--calendar] [--calendar-map ...] [--photo-sync=<bool>] [--dry-run] [--config <path>] [--reset-tokens] [--protect-local] [--time-window-days N] [--verbose]`
- `g2nc status` → shows tokens, counts, last run.
- `g2nc prune` → cleans orphaned mappings (safety checks).
- Exit codes as in §12.

---

## 19) Docker

**Dockerfile (sketch)**
```dockerfile
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml /app/
RUN pip install --upgrade pip && pip install -e .[all]
COPY src/ /app/src/
COPY scripts/docker-entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["g2nc", "sync", "--config", "/data/config.yaml"]
```

**docker-compose example**
```yaml
services:
  g2nc:
    image: ghcr.io/yourorg/g2nc:latest
    restart: unless-stopped
    environment:
      NEXTCLOUD_APP_PASSWORD: ${NEXTCLOUD_APP_PASSWORD}
    volumes:
      - ./data:/data
```
---

## 20) Scheduling Examples

**Cron (host)**
```
# Every 6 hours
0 */6 * * * /usr/bin/docker run --rm -v /opt/g2nc:/data  ghcr.io/yourorg/g2nc:latest g2nc sync --config /data/config.yaml >> /var/log/g2nc.log 2>&1
```

**systemd timer** (docs will include `.service` + `.timer` samples).

---

## 21) Testing Strategy

- **Unit**: mappers (contacts/events), hash normalization, state DAO, CLI parsing.  
- **Mocked integration**:  
  - Google APIs via `vcrpy` or `responses` fixtures (golden cassettes).  
  - Nextcloud DAV via local stub or dockerized Nextcloud for CI-opt-in.  
- **Idempotency tests**: run twice; assert zero changes second run.  
- **Deletion tests**: simulate deleted items in Google; assert deletions in Nextcloud.  
- **Token invalidation**: simulate 410; assert windowed resync behavior.  
- **Performance**: batch processing; ensure no N^2 searches (cache UIDs).

---

## 22) CI/CD (GitHub Actions)

**ci.yml (outline)**
```yaml
name: CI
on:
  push: { branches: [main] }
  pull_request:
jobs:
  lint-type-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e .[dev]
      - run: ruff check .
      - run: mypy src
      - run: pytest --maxfail=1 --disable-warnings -q --cov=src
      - uses: codecov/codecov-action@v4
  docker:
    if: startsWith(github.ref, 'refs/tags/')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with: { registry: ghcr.io, username: ${{ github.actor }}, password: ${{ secrets.GITHUB_TOKEN }} }
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: ghcr.io/${{ github.repository }}:${{ github.ref_name }},ghcr.io/${{ github.repository }}:latest
```

**release.yml**
- On tag push: create GitHub Release with changelog (conventional commits), push Docker images.

Pre-commit hooks: `ruff`, `mypy`, `end-of-file-fixer`, `trailing-whitespace`.

---

## 23) Documentation Plan

- **README.md**: quick start, config, examples, cron, Docker.  
- **docs/**: setup (Google OAuth app + tokens), Nextcloud endpoints, mappings, troubleshooting (token 410, 401, ETag mismatch, timezones), privacy.  
- **Examples**: sample configs; example docker-compose; systemd timer files.

---

## 24) Acceptance Criteria (v1)

- First run performs full import; subsequent runs **only** apply deltas.  
- Creating/updating/deleting a Google contact/event is reflected in Nextcloud within one subsequent run.  
- Re-running immediately after a successful run performs **zero writes** (idempotent).  
- No duplicates are created in Nextcloud even after multiple runs or container restarts.  
- Works via CLI **and** Docker; cron example verified.  
- Unit tests pass (≥80% coverage on core logic); mocked integration tests validate end-to-end flows.  
- CI runs lint, type-check, tests on PRs; release builds Docker images on tags.

---

## 25) Open Questions (defer if not specified)
- Support for **photos** default ON? (Initial default: **ON**, with `--photo-sync=false` to disable.)
- VALARM details (minutes before start). Default: map Google default reminders to a single DISPLAY alarm if present.
- Handling of **attendees** privacy (masking emails in logs already handled).

---

## 26) Step-by-Step Plan for the AI Coding Agent

### Guardrails & General Rules
- Never log raw PII (mask emails/phones).  
- Enforce idempotency via **UID = Google ID** and content hashing.  
- Use **sync tokens**; if invalid, do bounded resync.  
- Always write unit tests alongside code.  
- Respect config precedence and dry-run behavior.

### Implementation Steps

1. **Scaffold project**
   - Create repo structure (§14), `pyproject.toml` with extras: `[dev]`.
   - Add `ruff`, `mypy`, `pytest`, `pre-commit` configs.

2. **Config & CLI**
   - Implement `config.py` using `pydantic` (env + file) with precedence merge.
   - Implement `cli.py` (Typer or argparse) with commands: `sync`, `status`, `prune`.

3. **Logging**
   - `logging.py` for JSON/pretty logging; redact helper for emails/phones.

4. **State Store**
   - `state.py`: initialize SQLite, migrations if needed; CRUD for tokens/mappings.

5. **Google Auth**
   - `google/auth.py`: Installed app OAuth flow; save refresh token; headless refresh.
   - Read creds from env JSON or file.

6. **Google API Clients**
   - `google/contacts.py`: list connections incrementally; yield changes incl. deleted.
   - `google/calendar.py`: list events incrementally per calendar; handle token paging.

7. **Nextcloud DAV Clients**
   - `nextcloud/carddav.py`: list/find by UID, PUT (create/update), DELETE.
   - `nextcloud/caldav.py`: same for cal; collection URL from config.

8. **Mappers**
   - `mapping/contacts.py`: People → vCard 4.0. Normalize to consistent text ordering before hashing.
   - `mapping/events.py`: Event → VEVENT; set UID; include TZID, RRULE, EXDATE.

9. **Sync Orchestrators**
   - `sync/contacts_sync.py`: implement algorithm in §17 (dry-run support).
   - `sync/calendar_sync.py`: ditto per calendar id.
   - `sync/orchestrator.py`: parse config, iterate calendars, manage tokens, lock file.

10. **Tests**
    - Unit tests for mapping correctness (golden samples).
    - State store tests; idempotency test (double run ⇒ zero changes).
    - Mock Google responses (created/updated/deleted) with `vcrpy`.
    - Mock Nextcloud (DAV server stub) verifying correct methods and payloads.

11. **Docker**
    - Author `Dockerfile` + `docker-entrypoint.sh` that expands env + invokes CLI.
    - Build locally and run smoke test with mocked endpoints.

12. **Docs**
    - README quick start; docs pages for setup, config, troubleshooting.

13. **CI**
    - Add workflows from §22; ensure tests + lint pass.
    - Tag `v0.1.0` to trigger release and Docker publish.

### Done When
- All acceptance criteria in §24 pass locally and in CI.
- README demonstrates a real run with masked logs.
- Re-runs are idempotent; deletions propagate.

---

## 27) Example: .env & First Run

```
NEXTCLOUD_USERNAME=nc_user
NEXTCLOUD_APP_PASSWORD=app-xxxx
GOOGLE_CREDENTIALS_FILE=/data/google_oauth.json
```

```
g2nc sync --config /data/config.yaml
# First run prompts browser auth (local), stores refresh token.
# Subsequent runs headless.
```

---

## 28) Example: systemd (optional)

`/etc/systemd/system/g2nc.service`
```
[Unit]
Description=Google -> Nextcloud Sync

[Service]
Type=oneshot
EnvironmentFile=/opt/g2nc/.env
ExecStart=/usr/bin/docker run --rm -v /opt/g2nc:/data   -e NEXTCLOUD_APP_PASSWORD=%E{NEXTCLOUD_APP_PASSWORD} ghcr.io/yourorg/g2nc:latest   g2nc sync --config /data/config.yaml
```

`/etc/systemd/system/g2nc.timer`
```
[Unit]
Description=Run g2nc periodically

[Timer]
OnCalendar=*-*-* 01:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

---

## 29) Future Enhancements

- Two-way sync with conflict strategies.
- Web UI and webhook triggers.
- Prometheus metrics exporter.
- Kubernetes CronJob manifests and Helm chart.

---

If you want, I can generate the initial repo scaffold (files, configs, workflows, and stub modules) in one go so you can `git init` and start coding immediately.
