# g2nc — Google → Nextcloud Sync (Contacts & Calendar)

One-way, incremental, idempotent sync from Google (People + Calendar) into Nextcloud (CardDAV + CalDAV). Google is the source of truth; updates and deletions propagate to Nextcloud. Designed to run locally and in Docker and to be scheduled via cron or systemd.

Status: Phase 01 scaffold implemented per PRD. Engines, orchestrator, Google clients, mappers, and state DAO are present. CardDAV/CalDAV UID search implemented via WebDAV REPORT. CI and Docker are set up.

## Features

- One-way sync: Google → Nextcloud
- Incremental using Google sync tokens with windowed resync on invalidation
- Idempotent: UID = Google ID with normalized content hashing, no duplicates
- vCard 4.0 (default) and ICS VEVENT mapping
- CLI + YAML config + ENV with strict precedence (CLI > ENV > file)
- Docker image + examples for cron/systemd
- Tests: unit + scaffolding for mocked integrations
- CI: lint, type-check, and tests on PRs

## Install (local dev)

- Python 3.12+

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

Run quality gates:
```bash
ruff check .
mypy src
pytest -q --cov=src
```

## Quick Start (local)

1) Prepare config and secrets. Create `/opt/g2nc/data/config.yaml` like:

```yaml
google:
  credentials_file: /data/google_oauth.json
  token_store: /data/google_token.json
  calendar_ids:
    work: primary
    team: some-team-calendar-id@group.calendar.google.com

nextcloud:
  base_url: https://cloud.example.com
  username: nc_user
  addressbook_path: /remote.php/dav/addressbooks/users/nc_user/Contacts/
  calendars:
    work: /remote.php/dav/calendars/nc_user/work/
    team: /remote.php/dav/calendars/nc_user/team/

sync:
  photo_sync: true
  overwrite_local: true
  time_window_days: 730
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

2) Export Nextcloud app password securely:
```bash
export NEXTCLOUD_APP_PASSWORD=app-xxxx
```

3) First run to obtain Google refresh token (interactive browser on the dev machine):
```bash
g2nc sync --config /opt/g2nc/data/config.yaml --dry-run
# Follow the browser prompt; tokens will be saved to /data/google_token.json
```

Subsequent runs are headless.

## Docker

Build and run:

```bash
docker build -t g2nc:dev .
docker run --rm \
  -v "$PWD/data:/data" \
  -e NEXTCLOUD_APP_PASSWORD="$NEXTCLOUD_APP_PASSWORD" \
  g2nc:dev g2nc sync --config /data/config.yaml
```

Compose example:

```yaml
services:
  g2nc:
    image: ghcr.io/yourorg/g2nc:latest
    restart: unless-stopped
    environment:
      NEXTCLOUD_APP_PASSWORD: ${NEXTCLOUD_APP_PASSWORD}
    volumes:
      - ./data:/data
    command: ["g2nc", "sync", "--config", "/data/config.yaml"]
```

## Scheduling

Cron (host):
```
# Every 6 hours
0 */6 * * * /usr/bin/docker run --rm -v /opt/g2nc:/data \
  -e NEXTCLOUD_APP_PASSWORD=*** ghcr.io/yourorg/g2nc:latest \
  g2nc sync --config /data/config.yaml >> /var/log/g2nc.log 2>&1
```

systemd timer: see docs/setup.md for full example service+timer units.

## CLI

```
g2nc sync [--contacts] [--calendar]
          [--calendar-map key:id,...]
          [--photo-sync/--no-photo-sync]
          [--dry-run/--no-dry-run]
          [--config PATH]
          [--reset-tokens]
          [--protect-local/--no-protect-local]
          [--time-window-days N]
          [-v|--verbose]
```

Other commands:
- `g2nc status`
- `g2nc prune`

Notes:
- If neither `--contacts` nor `--calendar` is set, both run by default.
- `--protect-local` implies overwrite_local = false for safety.

## Configuration

Precedence: CLI > ENV > file, merged deeply. ENV variables use nested keys with `G2NC__` prefix:
```
G2NC__nextcloud__base_url=https://cloud.example.com
G2NC__sync__photo_sync=false
G2NC__google__calendar_ids__work=primary
```

Secrets:
- Nextcloud: `NEXTCLOUD_APP_PASSWORD`
- Google: `GOOGLE_CREDENTIALS_JSON` or `GOOGLE_CREDENTIALS_FILE`

## Security & Privacy

- Do not log raw PII; logging redacts emails/phones and token-like values.
- Store tokens and SQLite under `/data` with least privilege.
- License: MIT.

## Development Notes

- Engines:
  - Contacts: People API → vCard, UID = resourceName, hashing to avoid unnecessary PUTs.
  - Calendar: Calendar API → ICS, UID = event.id, recurrence best-effort.
- Nextcloud clients:
  - UID search via WebDAV REPORT (addressbook-query/calendar-query)
  - ETag-aware PUT/DELETE helpers provided
- Token invalidation:
  - People: standard syncToken rotation
  - Calendar: 410 → bounded window resync using `time_window_days`

## Acceptance Criteria

- First run performs full import; subsequent runs only apply deltas
- No duplicates over repeated runs/restarts
- Works via CLI and Docker; cron example verified
- Unit tests pass; CI green

## Contributing

- Conventional Commits
- `ruff check . && mypy src && pytest -q --cov=src`
- PRs run CI on GitHub Actions