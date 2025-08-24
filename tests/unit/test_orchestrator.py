import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from g2nc.sync.orchestrator import FileLock, Orchestrator


@pytest.fixture
def temp_lock_path():
    """Create a temporary lock file path."""
    with tempfile.NamedTemporaryFile(delete=False) as tf:
        lock_path = tf.name
    # Remove the file so we can test lock creation
    os.unlink(lock_path)
    yield lock_path
    # Cleanup
    if os.path.exists(lock_path):
        os.unlink(lock_path)


def test_file_lock_creation_and_cleanup(temp_lock_path) -> None:
    """Test that FileLock creates and cleans up lock files properly."""
    lock = FileLock(temp_lock_path)

    # Lock should not exist initially
    assert not Path(temp_lock_path).exists()

    # Acquire lock
    with lock:
        # Lock file should exist and contain current PID
        assert Path(temp_lock_path).exists()
        with open(temp_lock_path) as f:
            pid_in_file = f.read().strip()
        assert pid_in_file == str(os.getpid())

    # Lock should be cleaned up after exiting context
    assert not Path(temp_lock_path).exists()


def test_file_lock_concurrent_acquisition_fails(temp_lock_path) -> None:
    """Test that concurrent lock acquisition fails properly."""
    lock1 = FileLock(temp_lock_path)
    lock2 = FileLock(temp_lock_path)

    # First lock should succeed
    with lock1:
        # Second lock should fail
        with pytest.raises(RuntimeError, match="Another instance is running"):
            with lock2:
                pass


def test_file_lock_stale_lock_detection(temp_lock_path) -> None:
    """Test that stale locks (from dead processes) are detected and removed."""
    # Create a lock file with a fake PID that doesn't exist
    fake_pid = 999999  # Very unlikely to exist
    Path(temp_lock_path).write_text(str(fake_pid))

    # Mock os.kill to always raise OSError (process doesn't exist)
    with patch("os.kill", side_effect=OSError("No such process")):
        lock = FileLock(temp_lock_path)

        # Should be able to acquire the lock despite existing file (stale lock)
        with lock:
            # Verify the lock now contains our PID
            with open(temp_lock_path) as f:
                pid_in_file = f.read().strip()
            assert pid_in_file == str(os.getpid())


def test_file_lock_valid_existing_process(temp_lock_path) -> None:
    """Test that lock respects existing process if it's still running."""
    # Create a lock file with current PID (simulating another instance)
    current_pid = os.getpid()
    Path(temp_lock_path).write_text(str(current_pid))

    lock = FileLock(temp_lock_path)

    # Should fail to acquire since the process (us) is still running
    with pytest.raises(RuntimeError, match="Another instance is running"):
        with lock:
            pass


def test_file_lock_invalid_pid_format(temp_lock_path) -> None:
    """Test that invalid PID format in lock file is treated as stale."""
    # Create a lock file with invalid PID format
    Path(temp_lock_path).write_text("not-a-number")

    lock = FileLock(temp_lock_path)

    # Should be able to acquire the lock (treats invalid format as stale)
    with lock:
        # Verify the lock now contains our PID
        with open(temp_lock_path) as f:
            pid_in_file = f.read().strip()
        assert pid_in_file == str(os.getpid())


def test_file_lock_permission_error_handling(temp_lock_path) -> None:
    """Test graceful handling of permission errors."""
    # Create the lock file first
    Path(temp_lock_path).write_text("12345")

    # Mock os.kill to simulate process doesn't exist
    with patch("os.kill", side_effect=OSError("No such process")):
        # Mock os.open to simulate permission error when trying to create
        with patch("os.open", side_effect=PermissionError("Permission denied")):
            lock = FileLock(temp_lock_path)

            # Should raise the original PermissionError
            with pytest.raises(PermissionError):
                with lock:
                    pass


def test_orchestrator_initialization() -> None:
    """Test that orchestrator initializes with expected attributes."""

    class MockConfig:
        def __init__(self):
            self.state = type("", (), {"db_path": "/tmp/test.db"})()

    config = MockConfig()
    orchestrator = Orchestrator(config)

    # Basic attributes
    assert orchestrator.cfg == config


def test_file_lock_cleanup_on_exception(temp_lock_path) -> None:
    """Test that lock is properly cleaned up even when exceptions occur."""
    lock = FileLock(temp_lock_path)

    # Lock should not exist initially
    assert not Path(temp_lock_path).exists()

    # Use lock and raise exception
    try:
        with lock:
            # Lock file should exist
            assert Path(temp_lock_path).exists()
            raise ValueError("Test exception")
    except ValueError:
        pass

    # Lock should still be cleaned up after exception
    assert not Path(temp_lock_path).exists()


def test_file_lock_explicit_acquire_release(temp_lock_path) -> None:
    """Test explicit acquire and release methods."""
    lock = FileLock(temp_lock_path)

    # Lock should not exist initially
    assert not Path(temp_lock_path).exists()

    # Acquire lock explicitly
    lock.acquire()
    try:
        # Lock file should exist
        assert Path(temp_lock_path).exists()
        with open(temp_lock_path) as f:
            pid_in_file = f.read().strip()
        assert pid_in_file == str(os.getpid())
    finally:
        # Release lock
        lock.release()

    # Lock should be cleaned up
    assert not Path(temp_lock_path).exists()
