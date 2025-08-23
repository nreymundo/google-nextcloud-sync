# Configuration

This app reads configuration from a YAML file, with overrides from environment variables and then CLI flags (highest precedence). The effective precedence is:
1) File (YAML)
2) Environment variables (prefix G2NC__)
3) CLI overrides

Key files:
- /data/config.yaml (mounted volume in Docker; suggested location)
- /data/google_token.json (persisted after first OAuth consent; do not commit)
- /data/state.sqlite (sync state; do not commit)

Schema (pydantic models)
- google (credentials and mappings)
- nextcloud (server and collection paths)
- sync (behavioral flags and retry tuning)
- state (SQLite DB path)
- logging (level and JSON mode)
- runtime (lock path)

Minimal example
Save under ./data/config.yaml and mount ./data:/data in Docker.

```yaml
# ./data/config.yaml
google:
  # Either provide GOOGLE_CREDENTIALS_JSON or GOOGLE_CREDENTIALS_FILE via env.
  # credentials_file is optional when env provides credentials.
  credentials_file: /data/google_credentials.json
  token_store: /data/google_token.json

  # Contacts: optional People API contact groups to include (resource names)
  # contact_groups:
  #   - contactGroups/your-group-id

  # Calendar mapping: key -> Google calendarId. Keys must match nextcloud.calendars keys.
  calendar_ids:
    default: primary
    team: team@group.calendar.google.com

nextcloud:
  base_url: https://cloud.example.com
  username: nc_user
  # app_password via env: NEXTCLOUD_APP_PASSWORD (recommended)
  addressbook_path: /remote.php/dav/addressbooks/users/nc_user/Contacts/
  calendars:
    default: /remote.php/dav/calendars/nc_user/Personal/
    team: /remote.php/dav/calendars/nc_user/Team/

sync:
  photo_sync: true
  overwrite_local: true       # Google authoritative
  protect_local: false        # If true, do not overwrite local changes
  time_window_days: 730       # bounded resync window on token invalidation
  batch_size: 200
  max_retries: 5
  backoff_initial_sec: 1.0
  dry_run: false

state:
  db_path: /data/state.sqlite

logging:
  level: INFO
  json: true                  # structured logs

runtime:
  lock_path: /tmp/g2nc.lock
```

Environment variable overrides
- Prefix: G2NC__ (nested with __)
- Basic types (bool/int/float/list) are auto-coerced
- Examples:
  - G2NC__nextcloud__base_url=https://cloud.example.com
  - G2NC__nextcloud__username=nc_user
  - G2NC__sync__photo_sync=false
  - G2NC__google__calendar_ids__default=primary
  - G2NC__google__calendar_ids__team=team@group.calendar.google.com

Google credentials
Provide one of:
- GOOGLE_CREDENTIALS_JSON: a single-line JSON string for the OAuth client
- GOOGLE_CREDENTIALS_FILE: a file path (mounted into the container)
The token store (google.token_store) will be created and refreshed automatically under /data.

Nextcloud credentials
- Use env NEXTCLOUD_APP_PASSWORD to supply the app password (recommended)
- Do not commit secrets; use .env (see .env.example) with docker compose

Calendar mapping
- The google.calendar_ids keys must match nextcloud.calendars keys
- Each key references (left) a Nextcloud calendar path; (right) a Google calendarId
- The sync engine uses this mapping to pair each Google calendar to the corresponding CalDAV collection:
  - google.calendar_ids.default → nextcloud.calendars.default
  - google.calendar_ids.team → nextcloud.calendars.team

CLI flags (selected)
- --config /path/to/config.yaml
- --contacts / --calendar (select scopes to run; default both)
- --calendar-map "default:primary,team:team@group.calendar.google.com"
- --photo-sync/--no-photo-sync
- --dry-run/--no-dry-run
- --protect-local/--no-protect-local
- --time-window-days 365
- --reset-tokens

Notes
- Logging.json is referred to internally as logging.as_json to avoid pydantic BaseModel.json clashes; the config uses json as the external key.
- All writes update updated_at timestamps (UTC ISO 8601) in SQLite for auditing.
- UID rules: vCard UID = People API resourceName; VEVENT UID = Google event.id, per PRD.

Examples
- Compose file created at project root: docker-compose.yml (one-shot run)
- Environment example: .env.example
- Suggested workflow:
  - docker compose build
  - docker compose run --rm g2nc
  - First run may prompt an interactive OAuth flow when run locally (outside container). For headless environments, generate /data/google_token.json locally, then mount it in the container.