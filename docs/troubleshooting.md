# Troubleshooting

This guide lists common issues and how to resolve them.

General Diagnosis
- Run with DEBUG logs:
  - CLI: add -v/--verbose
  - In config.yaml: logging.level: DEBUG
- Verify config precedence:
  - CLI overrides > ENV (G2NC__) > file (YAML)
- Confirm secrets are present at runtime:
  - NEXTCLOUD_APP_PASSWORD
  - GOOGLE_CREDENTIALS_JSON or GOOGLE_CREDENTIALS_FILE
- Validate volume mount:
  - /data contains config.yaml and (optionally) google_credentials.json
  - token_store and state.sqlite can be created/written

Google OAuth
1) “No valid Google token found and allow_interactive=False”
   - Cause: Headless environment without a token
   - Fix:
     - Run locally once: g2nc sync --config /data/config.yaml (prompts browser)
     - Copy /data/google_token.json to the server before Docker runs

2) “Invalid JSON in GOOGLE_CREDENTIALS_JSON”
   - Cause: The inline JSON is not valid or not escaped appropriately
   - Fix:
     - Use GOOGLE_CREDENTIALS_FILE pointing to a mounted JSON file
     - Or ensure JSON is a single line and properly quoted; test via: python -c 'import json,os; json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])'

3) Token refresh failures (expired/invalid refresh token)
   - Fix:
     - Delete /data/google_token.json and re-run interactive flow locally
     - Confirm Google OAuth project has the People and Calendar APIs enabled

Nextcloud / DAV
1) 401 Unauthorized / 403 Forbidden
   - Cause: Wrong NEXTCLOUD_APP_PASSWORD or username
   - Fix:
     - Generate a new app password in Nextcloud and export it as NEXTCLOUD_APP_PASSWORD
     - Ensure username in config matches Nextcloud account

2) 404 Not Found for addressbook_path or calendar_path
   - Cause: Incorrect DAV path
   - Fix:
     - Verify in Nextcloud settings → Calendars and Contacts (copy WebDAV URLs)
     - Ensure paths in config.yaml match exactly (and include trailing slash)

3) ETag conflicts on PUT/DELETE
   - Cause: Resource changed on server since last sync
   - Fix:
     - The app uses If-Match when etag is known. If conflicts persist, consider:
       - Resetting the mapping entry in SQLite
       - Allowing overwrite (configure sync.overwrite_local true)
       - Running with --reset-tokens (only affects Google tokens); for local DAV conflict, remove the affected mapping rows and rerun

4) HREF Normalization
   - Symptom: DELETE fails with relative path
   - Fix:
     - Clients normalize href to absolute using base_url. Ensure base_url is correct.

State (SQLite)
1) ResourceWarning: unclosed database
   - Cause: Process termination without close
   - Fix:
     - State now supports context manager and robust close()
     - Ensure orchestrator shuts down cleanly; avoid force-killing processes

2) Manual inspection
   - sqlite3 /data/state.sqlite
   - SELECT * FROM sync_tokens;
   - SELECT * FROM contacts_map WHERE google_id = 'people/...';
   - SELECT * FROM events_map WHERE calendar_id = '...' AND google_id = '...';

Incremental Tokens (People/Calendar)
1) Calendar 410 Gone
   - Expected: App falls back to bounded window (sync.time_window_days)
   - Fix:
     - Increase time_window_days if your history is longer
     - Verify time skew and TZ environment

2) People tokens stale
   - Fix:
     - Use --reset-tokens
     - Or delete the token row in sync_tokens for scope 'contacts'

Docker
1) “File not found: /data/config.yaml”
   - Cause: Volume mount incorrect
   - Fix:
     - Use: -v "$PWD/data:/data"
     - docker compose: volumes: [ "./data:/data:rw" ]

2) Locale/TZ differences
   - Fix:
     - Set TZ in .env or container env; ensure predictable time computations

Logging & PII
- The app masks emails/phones and token-like strings in logs.
- If you need to share logs, scrub remaining PII manually before posting.

Getting Help
- Increase logging to DEBUG and capture error traces
- Provide:
  - g2nc version (pip show g2nc or src/g2nc/__init__.py)
  - Sanitized config.yaml (remove secrets)
  - Relevant logs (sanitized)
- Re-run a failing case with --dry-run when possible to get hints without making changes.