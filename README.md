# g2nc

> **Important:** this project will not meet the intended unattended long-term Google sync behavior on a personal Google account unless you complete a **production OAuth app setup** with Google's required verification flow for the needed sensitive scopes. Testing-mode OAuth is not a durable substitute.

`g2nc` is a Python MVP for one-way Google Calendar to Nextcloud Calendar sync.

## Scope

- Single personal Google account.
- One-time OAuth bootstrap, then unattended headless runs.
- Multiple explicit 1:1 Google calendar to Nextcloud calendar mappings.
- Mapped Nextcloud calendars are owned by this sync process.
- One-way overwrite semantics from Google to Nextcloud.
- Core event fields: title, description, location, start/end, all-day, recurrence, deletions.
- Incremental sync with Google `syncToken` and SQLite state on disk.

## Commands

- Validate configuration:
  - `g2nc --config config/settings.json validate-config`
- Bootstrap OAuth token:
  - `g2nc --config config/settings.json auth bootstrap`
  - `g2nc --config config/settings.json auth bootstrap --no-browser`
- Run sync:
  - `g2nc --config config/settings.json sync`

## Install for development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

## Quality gates

```bash
ruff check .
black --check .
mypy src
pytest -q --cov=src
```

## Configuration

Use `config/settings.example.json` as a template. See `docs/setup.md` for full setup.
