from __future__ import annotations

import fcntl
from pathlib import Path


class LockError(RuntimeError):
    pass


class FileLock:
    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._handle: object | None = None

    def __enter__(self) -> FileLock:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise LockError(f"lock already held: {self._lock_path}") from exc
        self._handle = handle
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._handle is None:
            return
        handle = self._handle
        if hasattr(handle, "fileno"):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        if hasattr(handle, "close"):
            handle.close()
        self._handle = None
