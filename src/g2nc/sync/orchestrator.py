"""Top-level sync orchestrator.

Responsibilities
- Load config (provided by caller) and construct dependencies
- Enforce single-run lock using a filesystem lock file
- Drive contacts and calendar sync passes
- Provide an overall summary and exit code per PRD §12

Exit codes
- 0: success
- 2: partial (non-fatal errors encountered)
- 3: fatal (could not start/run)

Notes
- Nextcloud CardDAV/CalDAV clients may still be partially stubbed; sync engines
  handle NotImplementedError gracefully for find/update paths where indicated.
"""

from __future__ import annotations

import errno
import logging
import os
from dataclasses import dataclass
from types import TracebackType

from ..config import AppConfig
from ..google.auth import SCOPES_CALENDAR, SCOPES_CONTACTS, get_credentials
from ..google.calendar import CalendarClient
from ..google.contacts import PeopleClient
from ..nextcloud.caldav import CalDAVClient
from ..nextcloud.carddav import CardDAVClient
from ..state import State
from ..utils.http import RetryConfig
from .calendar_sync import CalendarSync, CalendarSyncResult
from .contacts_sync import ContactsSync, ContactsSyncResult

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunSummary:
    contacts: ContactsSyncResult | None
    calendars: dict[str, CalendarSyncResult]

    def aggregate(self) -> dict[str, int]:
        total = {"fetched": 0, "created": 0, "updated": 0, "deleted": 0, "skipped": 0, "errors": 0}
        if self.contacts:
            for k in total:
                total[k] += getattr(self.contacts, k)
        for _cid, res in self.calendars.items():
            for k in total:
                total[k] += getattr(res, k)
        return total


class FileLock:
    """Simple non-blocking PID file lock using O_CREAT|O_EXCL.

    Lock is removed on explicit release or process exit (best-effort).
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        try:
            # First attempt to acquire lock
            self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(self._fd, str(os.getpid()).encode("utf-8"))
            os.fsync(self._fd)
        except OSError as e:
            if e.errno == errno.EEXIST:
                # Lock file exists - check if process is still running
                if self._is_stale_lock():
                    log.warning("Removing stale lock file at %s", self.path)
                    try:
                        os.unlink(self.path)
                        # Retry acquisition after removing stale lock
                        self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                        os.write(self._fd, str(os.getpid()).encode("utf-8"))
                        os.fsync(self._fd)
                        return
                    except OSError:
                        # Another process might have created the lock in the meantime
                        pass
                
                raise RuntimeError(
                    f"Another instance is running (lock exists at {self.path})"
                ) from e
            raise

    def _is_stale_lock(self) -> bool:
        """Check if the lock file contains a PID of a process that no longer exists."""
        try:
            with open(self.path, encoding="utf-8") as f:
                pid_str = f.read().strip()
                if not pid_str.isdigit():
                    # Invalid PID format, consider it stale
                    return True
                
                pid = int(pid_str)
                # Check if process exists
                try:
                    os.kill(pid, 0)  # Signal 0 doesn't kill, just checks existence
                    return False  # Process exists, lock is valid
                except OSError:
                    return True  # Process doesn't exist, lock is stale
        except (FileNotFoundError, PermissionError, ValueError):
            # If we can't read the lock file or parse PID, consider it stale
            return True

    def release(self) -> None:
        try:
            if self._fd is not None:
                os.close(self._fd)
        except Exception:
            pass
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


class Orchestrator:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg

    def _build_state(self) -> State:
        return State(self.cfg.state.db_path)

    def _build_nextcloud_clients(self) -> tuple[CardDAVClient, dict[str, CalDAVClient]]:
        nc = self.cfg.nextcloud
        if not nc.app_password:
            # Allow auth via env even if not set in config model
            # It is acceptable for app_password to be set via env var at runtime.
            app_password = os.getenv("NEXTCLOUD_APP_PASSWORD")
        else:
            app_password = nc.app_password

        if not app_password:
            raise RuntimeError(
                "Nextcloud app password is required (env NEXTCLOUD_APP_PASSWORD or config)."
            )

        # Align retry behavior with SyncConfig to avoid magic numbers diverging
        retry_cfg = RetryConfig(
            max_retries=self.cfg.sync.max_retries,
            backoff_initial_sec=self.cfg.sync.backoff_initial_sec,
        )
        carddav = CardDAVClient(
            base_url=nc.base_url,
            username=nc.username,
            app_password=app_password,
            addressbook_path=nc.addressbook_path,
            retry=retry_cfg,
        )
        caldav_by_key: dict[str, CalDAVClient] = {}
        for key, path in nc.calendars.items():
            caldav_by_key[key] = CalDAVClient(
                base_url=nc.base_url,
                username=nc.username,
                app_password=app_password,
                calendar_path=path,
                retry=retry_cfg,
            )
        return carddav, caldav_by_key

    def _build_google_clients(
        self, need_contacts: bool, need_calendar: bool
    ) -> tuple[PeopleClient | None, CalendarClient | None]:
        scopes: list[str] = []
        if need_contacts:
            scopes.extend(SCOPES_CONTACTS)
        if need_calendar:
            scopes.extend(SCOPES_CALENDAR)
        if not scopes:
            return None, None

        creds = get_credentials(self.cfg.google, scopes=scopes, allow_interactive=True)
        people = PeopleClient(creds) if need_contacts else None
        cal = CalendarClient(creds) if need_calendar else None
        return people, cal

    def run(
        self,
        *,
        do_contacts: bool,
        do_calendar: bool,
        reset_tokens: bool = False,
    ) -> tuple[int, RunSummary]:
        """Run sync for requested scopes; returns exit code and summary."""
        # 1) Acquire lock
        lock_path = self.cfg.runtime.lock_path
        log.info("acquiring-lock %s", lock_path)
        try:
            lock = FileLock(lock_path)
            lock.acquire()
        except Exception as e:
            log.error("lock-failed %s", e)
            return 3, RunSummary(contacts=None, calendars={})

        exit_code: int = 3
        contacts_res: ContactsSyncResult | None = None
        calendar_results: dict[str, CalendarSyncResult] = {}
        summary: RunSummary = RunSummary(contacts=None, calendars={})

        try:
            # 2) Build deps
            state = self._build_state()
            try:
                carddav, caldav_by_key = self._build_nextcloud_clients()
            except Exception:
                log.exception("nextcloud-init-failed")
                raise

            try:
                people, gcal = self._build_google_clients(do_contacts, do_calendar)
            except Exception:
                log.exception("google-auth-init-failed")
                raise

            # 3) Execute syncs
            if do_contacts and people:
                try:
                    contacts_res = ContactsSync(self.cfg, state, people, carddav).run(
                        dry_run=self.cfg.sync.dry_run, reset_token=reset_tokens
                    )
                except Exception:
                    log.exception("contacts-sync-fatal")
                    raise

            if do_calendar and gcal:
                for key, gcal_id in self.cfg.google.calendar_ids.items():
                    cal_path = self.cfg.nextcloud.calendars.get(key)
                    if not cal_path:
                        log.warning("calendar-mapping-missing key=%s id=%s", key, gcal_id)
                        continue
                    try:
                        cal_client = caldav_by_key[key]
                    except KeyError:
                        log.error("caldav-client-missing key=%s", key)
                        raise
                    try:
                        res = CalendarSync(self.cfg, state, gcal, cal_client, gcal_id).run(
                            dry_run=self.cfg.sync.dry_run, reset_token=reset_tokens
                        )
                        calendar_results[gcal_id] = res
                    except Exception:
                        log.exception("calendar-sync-fatal", extra={"calendar_id": gcal_id})
                        raise

            # 4) Compute exit code
            summary = RunSummary(contacts=contacts_res, calendars=calendar_results)
            agg = summary.aggregate()
            exit_code = 0 if agg["errors"] == 0 else 2

        except Exception:
            # Keep default exit_code=3 and summary from partial state
            summary = RunSummary(contacts=contacts_res, calendars=calendar_results)
        finally:
            try:
                lock.release()
            except Exception:
                pass

        return exit_code, summary
