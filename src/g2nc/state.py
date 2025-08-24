"""SQLite state store and DAO for tokens and object mappings.

Schema aligns with PRD §16. This module provides a small, typed DAO:
- Sync tokens: scope -> token
- Contacts mapping: google_id -> (href, etag, content_hash, deleted)
- Events mapping: (calendar_id, google_id) -> (href, etag, content_hash, deleted)

Design notes
- Idempotency is enforced at a higher level via stable UID and hashing, but we store
  the last-seen content_hash to avoid unnecessary PUTs.
- All writes update updated_at in UTC ISO 8601.
- We prefer upsert semantics to keep runs idempotent.

Example
  from g2nc.state import State
  st = State("/data/state.sqlite")
  st.save_token("contacts", "tok_123")
  print(st.get_token("contacts"))
  st.upsert_contact("people/c1", "/dav/contacts/1.vcf", 'W/"abc"', "deadbeef")
  rec = st.get_contact("people/c1")
  print(rec)

"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "ContactRecord",
    "EventRecord",
    "State",
]

ISO_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).strftime(ISO_FORMAT)


@dataclass(frozen=True)
class ContactRecord:
    google_id: str
    etag: str | None
    nextcloud_href: str | None
    content_hash: str | None
    deleted: int
    updated_at: str


@dataclass(frozen=True)
class EventRecord:
    calendar_id: str
    google_id: str
    etag: str | None
    nextcloud_href: str | None
    content_hash: str | None
    deleted: int
    updated_at: str


class State:
    """SQLite-backed state store.

    This class is safe to use from a single process. It is not thread-safe by design.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._conn = self._connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    # Context manager support to ensure connections are closed deterministically
    def __enter__(self) -> State:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None:
        self.close()

    # Best-effort finalizer to avoid ResourceWarning if callers forget to close
    def __del__(self) -> None:  # pragma: no cover - destructor timing nondeterministic
        try:
            self.close()
        except Exception:
            pass

    # -------------
    # Connection
    # -------------

    def _connect(self, db_path: str) -> sqlite3.Connection:
        path = Path(db_path)
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        
        # Create database file if it doesn't exist
        db_exists = path.exists()
        
        # Increase default timeout to reduce 'database is locked' errors under contention.
        conn = sqlite3.connect(str(path), timeout=30.0)
        
        # Security: Set restrictive permissions on database file (owner read/write only)
        # Apply to both new and existing databases for security
        try:
            import stat
            import os
            
            # Check if we can write to the file before attempting chmod
            if path.exists() and not os.access(path, os.W_OK):
                import logging
                log = logging.getLogger(__name__)
                log.warning(
                    "Database file %s is not writable by current user. "
                    "This may cause operational issues. Please ensure proper file ownership.", path
                )
            else:
                path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600 permissions
        except (OSError, AttributeError, PermissionError) as e:
            # Best effort - some filesystems/OS may not support this
            import logging
            log = logging.getLogger(__name__)
            log.warning(
                "Could not set restrictive permissions on database file %s: %s. "
                "Database will use default permissions.", path, e
            )
        # Pragmas for reliability and reasonable performance with single-process access.
        # - WAL improves durability and read concurrency.
        # - synchronous=NORMAL balances safety with performance (acceptable for this use case).
        # - temp_store=MEMORY reduces disk I/O for temp structures.
        # - busy_timeout helps during brief lock contention windows.
        # - foreign_keys enables referential integrity (future-proofing).
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA temp_store=MEMORY;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def close(self) -> None:
        try:
            if getattr(self, "_conn", None) is not None:
                self._conn.close()
        except Exception:
            pass
        finally:
            try:
                self._conn = None  # type: ignore[assignment]
            except Exception:
                pass

    # -------------
    # Schema
    # -------------

    def _init_schema(self) -> None:
        cur = self._conn.cursor()

        # global sync tokens
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sync_tokens (
              scope TEXT PRIMARY KEY,   -- 'contacts' or 'calendar:<id>'
              token TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )

        # contacts mapping
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS contacts_map (
              google_id TEXT PRIMARY KEY,   -- people/cNNNN
              etag TEXT,
              nextcloud_href TEXT,
              content_hash TEXT,
              deleted INTEGER DEFAULT 0,
              updated_at TEXT NOT NULL
            );
            """
        )

        # events mapping
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS events_map (
              calendar_id TEXT NOT NULL,
              google_id TEXT NOT NULL,      -- event.id
              etag TEXT,
              nextcloud_href TEXT,
              content_hash TEXT,
              deleted INTEGER DEFAULT 0,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (calendar_id, google_id)
            );
            """
        )

        self._conn.commit()

    # -------------
    # Tokens
    # -------------

    def get_token(self, scope: str) -> str | None:
        cur = self._conn.execute("SELECT token FROM sync_tokens WHERE scope = ?;", (scope,))
        row = cur.fetchone()
        return row["token"] if row else None

    def save_token(self, scope: str, token: str) -> None:
        now = _utc_now_iso()
        self._conn.execute(
            """
            INSERT INTO sync_tokens(scope, token, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(scope) DO UPDATE SET token = excluded.token, updated_at = excluded.updated_at;
            """,
            (scope, token, now),
        )
        self._conn.commit()

    def reset_token(self, scope: str) -> None:
        self._conn.execute("DELETE FROM sync_tokens WHERE scope = ?;", (scope,))
        self._conn.commit()

    # -------------
    # Contacts
    # -------------

    def get_contact(self, google_id: str) -> ContactRecord | None:
        cur = self._conn.execute(
            """
            SELECT google_id, etag, nextcloud_href, content_hash, deleted, updated_at
            FROM contacts_map WHERE google_id = ?;
            """,
            (google_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return ContactRecord(
            google_id=row["google_id"],
            etag=row["etag"],
            nextcloud_href=row["nextcloud_href"],
            content_hash=row["content_hash"],
            deleted=int(row["deleted"] or 0),
            updated_at=row["updated_at"],
        )

    def lookup_contact_href(self, google_id: str) -> str | None:
        cur = self._conn.execute(
            "SELECT nextcloud_href FROM contacts_map WHERE google_id = ?;", (google_id,)
        )
        row = cur.fetchone()
        return row["nextcloud_href"] if row and row["nextcloud_href"] else None

    def upsert_contact(
        self,
        google_id: str,
        nextcloud_href: str | None,
        etag: str | None,
        content_hash: str | None,
        deleted: int = 0,
    ) -> None:
        now = _utc_now_iso()
        self._conn.execute(
            """
            INSERT INTO contacts_map(google_id, etag, nextcloud_href, content_hash, deleted, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(google_id) DO UPDATE SET
                etag = excluded.etag,
                nextcloud_href = excluded.nextcloud_href,
                content_hash = excluded.content_hash,
                deleted = excluded.deleted,
                updated_at = excluded.updated_at;
            """,
            (google_id, etag, nextcloud_href, content_hash, int(deleted), now),
        )
        self._conn.commit()

    def remove_contact(self, google_id: str) -> None:
        """Hard-remove a contact mapping (used when deleted in Google)."""
        self._conn.execute("DELETE FROM contacts_map WHERE google_id = ?;", (google_id,))
        self._conn.commit()

    # -------------
    # Events
    # -------------

    def get_event(self, calendar_id: str, google_id: str) -> EventRecord | None:
        cur = self._conn.execute(
            """
            SELECT calendar_id, google_id, etag, nextcloud_href, content_hash, deleted, updated_at
            FROM events_map WHERE calendar_id = ? AND google_id = ?;
            """,
            (calendar_id, google_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        return EventRecord(
            calendar_id=row["calendar_id"],
            google_id=row["google_id"],
            etag=row["etag"],
            nextcloud_href=row["nextcloud_href"],
            content_hash=row["content_hash"],
            deleted=int(row["deleted"] or 0),
            updated_at=row["updated_at"],
        )

    def lookup_event_href(self, calendar_id: str, google_id: str) -> str | None:
        cur = self._conn.execute(
            "SELECT nextcloud_href FROM events_map WHERE calendar_id = ? AND google_id = ?;",
            (calendar_id, google_id),
        )
        row = cur.fetchone()
        return row["nextcloud_href"] if row and row["nextcloud_href"] else None

    def upsert_event(
        self,
        calendar_id: str,
        google_id: str,
        nextcloud_href: str | None,
        etag: str | None,
        content_hash: str | None,
        deleted: int = 0,
    ) -> None:
        now = _utc_now_iso()
        self._conn.execute(
            """
            INSERT INTO events_map(calendar_id, google_id, etag, nextcloud_href, content_hash, deleted, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(calendar_id, google_id) DO UPDATE SET
                etag = excluded.etag,
                nextcloud_href = excluded.nextcloud_href,
                content_hash = excluded.content_hash,
                deleted = excluded.deleted,
                updated_at = excluded.updated_at;
            """,
            (calendar_id, google_id, etag, nextcloud_href, content_hash, int(deleted), now),
        )
        self._conn.commit()

    def remove_event(self, calendar_id: str, google_id: str) -> None:
        """Hard-remove an event mapping (used when deleted/cancelled in Google)."""
        self._conn.execute(
            "DELETE FROM events_map WHERE calendar_id = ? AND google_id = ?;",
            (calendar_id, google_id),
        )
        self._conn.commit()
