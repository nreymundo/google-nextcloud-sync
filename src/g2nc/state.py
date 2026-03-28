from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from g2nc.models import EventState


class SqliteStateRepository:
    def __init__(self, sqlite_path: Path) -> None:
        self._sqlite_path = sqlite_path

    def initialize(self) -> None:
        self._sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS calendar_state (
                    mapping_key TEXT PRIMARY KEY,
                    sync_token TEXT
                )
                """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS event_state (
                    mapping_key TEXT NOT NULL,
                    google_event_id TEXT NOT NULL,
                    uid TEXT NOT NULL,
                    href TEXT NOT NULL,
                    etag TEXT,
                    payload_hash TEXT NOT NULL,
                    PRIMARY KEY (mapping_key, google_event_id)
                )
                """)

    def get_sync_token(self, mapping_key: str) -> str | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT sync_token FROM calendar_state WHERE mapping_key = ?", (mapping_key,)
            ).fetchone()
            if row is None:
                return None
            return str(row[0]) if row[0] is not None else None

    def set_sync_token(self, mapping_key: str, sync_token: str) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO calendar_state (mapping_key, sync_token)
                VALUES (?, ?)
                ON CONFLICT(mapping_key) DO UPDATE SET sync_token = excluded.sync_token
                """,
                (mapping_key, sync_token),
            )

    def clear_sync_token(self, mapping_key: str) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO calendar_state (mapping_key, sync_token)
                VALUES (?, NULL)
                ON CONFLICT(mapping_key) DO UPDATE SET sync_token = NULL
                """,
                (mapping_key,),
            )

    def get_event_state(self, mapping_key: str, google_event_id: str) -> EventState | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT mapping_key, google_event_id, uid, href, etag, payload_hash
                FROM event_state
                WHERE mapping_key = ? AND google_event_id = ?
                """,
                (mapping_key, google_event_id),
            ).fetchone()
            if row is None:
                return None
            return EventState(
                mapping_key=str(row[0]),
                google_event_id=str(row[1]),
                uid=str(row[2]),
                href=str(row[3]),
                etag=str(row[4]) if row[4] is not None else None,
                payload_hash=str(row[5]),
            )

    def upsert_event_state(self, state: EventState) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO event_state (
                    mapping_key,
                    google_event_id,
                    uid,
                    href,
                    etag,
                    payload_hash
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(mapping_key, google_event_id)
                DO UPDATE SET uid = excluded.uid,
                              href = excluded.href,
                              etag = excluded.etag,
                              payload_hash = excluded.payload_hash
                """,
                (
                    state.mapping_key,
                    state.google_event_id,
                    state.uid,
                    state.href,
                    state.etag,
                    state.payload_hash,
                ),
            )

    def delete_event_state(self, mapping_key: str, google_event_id: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "DELETE FROM event_state WHERE mapping_key = ? AND google_event_id = ?",
                (mapping_key, google_event_id),
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._sqlite_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
