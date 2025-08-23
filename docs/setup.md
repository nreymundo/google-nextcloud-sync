# Setup Guide

This document covers local and Docker installation, initial OAuth bootstrap, configuration, and scheduling.

Prerequisites
- Google Cloud OAuth client (Installed App)
- Nextcloud instance with an app password
- Linux/macOS/WSL recommended

1) Create OAuth Client (Google)
- In Google Cloud Console:
  - APIs & Services → Credentials → Create Credentials → OAuth client ID → Desktop app
  - Download JSON for the OAuth client
- You will need either:
  - GOOGLE_CREDENTIALS_JSON: the entire JSON as a single-line env var
  - GOOGLE_CREDENTIALS_FILE: a path to the JSON file available to the app (e.g. /data/google_credentials.json)

2) Create an App Password (Nextcloud)
- Nextcloud → Settings → Security → Devices & Sessions → Create new app password
- Save it securely; do not commit it. Use env NEXTCLOUD_APP_PASSWORD to provide it at runtime.

3) Directory Layout
Project/
├─ data/                          # persisted state and config
│  ├─ config.yaml                 # required
│  ├─ google_credentials.json     # optional if using GOOGLE_CREDENTIALS_JSON
│  ├─ google_token.json           # generated on first run
│  └─ state.sqlite                # generated on first run
├─ docker-compose.yml             # example compose service
├─ .env                           # copy from .env.example (do not commit)
└─ ...

4) Configuration File
- See docs/config.md for complete schema and examples.
- Minimal ./data/config.yaml:

```yaml
google:
  credentials_file: /data/google_credentials.json
  token_store: /data/google_token.json
  calendar_ids:
    default: primary

nextcloud:
  base_url: https://cloud.example.com
  username: nc_user
  addressbook_path: /remote.php/dav/addressbooks/users/nc_user/Contacts/
  calendars:
    default: /remote.php/dav/calendars/nc_user/Personal/

sync:
  photo_sync: true
  overwrite_local: true
  time_window_days: 730
  batch_size: 200
  max_retries: 5
  backoff_initial_sec: 1.0
  dry_run: false

state:
  db_path: /data/state.sqlite

logging:
  level: INFO
  json: true

runtime:
  lock_path: /tmp/g2nc.lock
```

5) Local Run (dev)
- Python 3.12+ recommended

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
export NEXTCLOUD_APP_PASSWORD=your-app-password
# Option A: file path
export GOOGLE_CREDENTIALS_FILE=$PWD/data/google_credentials.json
# Option B: inline JSON (single line)
# export GOOGLE_CREDENTIALS_JSON='{"installed":{...}}'

g2nc sync --config ./data/config.yaml --dry-run
# For first run, an interactive browser consent may open (installed app flow).
# The token is saved to /data/google_token.json.
```

- Quality gates (recommended before PRs):

```bash
ruff check .
mypy src
pytest -q --cov=src
```

6) Docker (recommended in production)
- Use docker-compose.yml at project root (provided):

.env
- Copy .env.example → .env and set:
  - NEXTCLOUD_APP_PASSWORD=...
  - TZ=Europe/Berlin (optional)
  - Optionally set GOOGLE_CREDENTIALS_JSON, or mount a credentials file in /data

Compose build and run

```bash
docker compose build
docker compose run --rm g2nc
```

Notes:
- /data/config.yaml and either /data/google_credentials.json or GOOGLE_CREDENTIALS_JSON are required.
- On first run, the app may require interactive OAuth consent. For headless servers, generate /data/google_token.json locally and then copy it to the server before running in Docker.

7) Scheduling
- Cron on host:

```cron
# Every 6 hours
0 */6 * * * cd /path/to/project && \
  docker compose run --rm g2nc >> /var/log/g2nc.log 2>&1
```

- systemd timer (host)
Create /etc/systemd/system/g2nc.service:

```ini
[Unit]
Description=Google to Nextcloud Sync

[Service]
Type=oneshot
WorkingDirectory=/path/to/project
EnvironmentFile=/path/to/project/.env
ExecStart=/usr/bin/docker compose run --rm g2nc
```

Create /etc/systemd/system/g2nc.timer:

```ini
[Unit]
Description=Run g2nc periodically

[Timer]
OnCalendar=*-*-* 00/6:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now g2nc.timer
```

8) Troubleshooting
- Invalid tokens
  - Calendar: on HTTP 410, the app will bounded-resync using time_window_days.
  - People: set --reset-tokens or remove the token from /data/google_token.json to bootstrap anew.
- ETag conflicts
  - The app uses If-Match/If-None-Match for safety. If conflicts persist, ensure Nextcloud app password and paths are correct and try a fresh run.
- Permissions
  - Ensure ./data is writable by the process executing g2nc (or Docker has write access to the volume).
- Logs
  - With logging.json=true, logs are structured; set level to DEBUG for troubleshooting.

9) Security
- Never commit real secrets.
- Use app password for Nextcloud and keep tokens under /data with least privilege.
- If you rotate OAuth credentials, re-run the interactive flow locally to refresh token_store.

10) Uninstall / Cleanup
- Stop timers/cron, remove generated files under ./data if desired:
  - google_token.json
  - state.sqlite
- Remove Docker images/containers as needed.