# Setup

## 1) Create Google OAuth desktop credentials

1. In Google Cloud Console, enable the Google Calendar API.
2. Create OAuth client credentials of type Desktop app.
3. Save the JSON client credentials file.

## 2) Configure Nextcloud

1. Create an app password in Nextcloud Security settings.
2. Identify target CalDAV calendar URLs for each mapped calendar.

## 3) Prepare config files

Create `config/settings.json` from `config/settings.example.json`.

You can provide Google credentials in either way:

- `google.credentials_file` in settings JSON, or `GOOGLE_CREDENTIALS_FILE` env var.
- `google.credentials_json` in settings JSON, or `GOOGLE_CREDENTIALS_JSON` env var.

For Nextcloud password, use `nextcloud.app_password` or `NEXTCLOUD_APP_PASSWORD`.

## 4) Bootstrap OAuth token once

```bash
g2nc --config config/settings.json auth bootstrap
```

This writes the authorized user token JSON to `google.token_file`.

## 5) Run unattended sync

```bash
g2nc --config config/settings.json sync
```

The process uses file locking (`lock_file`) to prevent concurrent runs.

## 6) Recommended cron usage

Run every 5-15 minutes:

```cron
*/10 * * * * /path/to/venv/bin/g2nc --config /path/to/repo/config/settings.json sync
```
