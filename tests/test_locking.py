from __future__ import annotations

from pathlib import Path

import pytest

from g2nc.locking import FileLock, LockError


def test_file_lock_prevents_second_acquire(tmp_path: Path) -> None:
    lock_path = tmp_path / "g2nc.lock"

    first = FileLock(lock_path)
    with first:
        with pytest.raises(LockError):
            with FileLock(lock_path):
                pass
