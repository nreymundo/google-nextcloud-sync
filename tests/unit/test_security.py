import os
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from g2nc.config import load_config
from g2nc.nextcloud.caldav import CalDAVClient
from g2nc.nextcloud.carddav import CardDAVClient
from g2nc.state import State
from g2nc.utils.http import create_client


@pytest.fixture
def carddav_client() -> CardDAVClient:
    return CardDAVClient(
        base_url="https://cloud.example.com",
        username="user",
        app_password="pass",
        addressbook_path="/remote.php/dav/addressbooks/users/user/Contacts/",
        timeout=5.0,
        verify=True,
    )


@pytest.fixture
def caldav_client() -> CalDAVClient:
    return CalDAVClient(
        base_url="https://cloud.example.com",
        username="user",
        app_password="pass",
        calendar_path="/remote.php/dav/calendars/user/Personal/",
        timeout=5.0,
        verify=True,
    )


def test_carddav_xml_injection_prevention(monkeypatch, carddav_client: CardDAVClient) -> None:
    """Test that XML injection attacks are prevented in CardDAV find_by_uid."""
    # Malicious UID with XML injection attempt
    malicious_uid = 'test</card:text-match></card:prop-filter></card:filter><card:filter><card:prop-filter name="FN"><card:text-match>INJECTED'

    xml_request_body = None

    import g2nc.nextcloud.carddav as carddav_mod

    def _capture_request(client, method, url, **kwargs):
        nonlocal xml_request_body
        data = kwargs.get("data", "")
        if isinstance(data, bytes):
            xml_request_body = data.decode("utf-8")
        else:
            xml_request_body = data
        # Return minimal response to complete the test
        xml = """<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
</d:multistatus>"""
        return SimpleNamespace(text=xml, status_code=207, headers={})

    monkeypatch.setattr(carddav_mod, "request_with_retries", _capture_request)

    # This should not raise an exception and should escape the malicious input
    carddav_client.find_by_uid(malicious_uid)

    # Verify that the malicious XML was escaped
    assert xml_request_body is not None
    assert "&lt;" in xml_request_body  # < should be escaped
    assert "&gt;" in xml_request_body  # > should be escaped
    # The malicious content should be escaped and contained within the text-match element
    assert "&lt;/card:text-match&gt;" in xml_request_body  # Escaped injection attempt
    assert "INJECTED" in xml_request_body  # But the content should still be searchable


def test_caldav_xml_injection_prevention(monkeypatch, caldav_client: CalDAVClient) -> None:
    """Test that XML injection attacks are prevented in CalDAV find_by_uid."""
    # Malicious UID with XML injection attempt
    malicious_uid = 'event</c:text-match></c:prop-filter></c:comp-filter><c:comp-filter name="VTODO"><c:prop-filter name="SUMMARY"><c:text-match>INJECTED'

    xml_request_body = None

    import g2nc.nextcloud.caldav as caldav_mod

    def _capture_request(client, method, url, **kwargs):
        nonlocal xml_request_body
        data = kwargs.get("data", "")
        if isinstance(data, bytes):
            xml_request_body = data.decode("utf-8")
        else:
            xml_request_body = data
        # Return minimal response to complete the test
        xml = """<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
</d:multistatus>"""
        return SimpleNamespace(text=xml, status_code=207, headers={})

    monkeypatch.setattr(caldav_mod, "request_with_retries", _capture_request)

    # This should not raise an exception and should escape the malicious input
    caldav_client.find_by_uid(malicious_uid)

    # Verify that the malicious XML was escaped
    assert xml_request_body is not None
    assert "&lt;" in xml_request_body  # < should be escaped
    assert "&gt;" in xml_request_body  # > should be escaped
    # The malicious content should be escaped and contained within the text-match element
    assert "&lt;/c:text-match&gt;" in xml_request_body  # Escaped injection attempt
    assert "INJECTED" in xml_request_body  # But the content should still be searchable


def test_carddav_special_characters_handled(monkeypatch, carddav_client: CardDAVClient) -> None:
    """Test that special XML characters are properly escaped."""
    uid_with_specials = "test&uid\"with'quotes<and>tags"

    xml_request_body = None

    import g2nc.nextcloud.carddav as carddav_mod

    def _capture_request(client, method, url, **kwargs):
        nonlocal xml_request_body
        data = kwargs.get("data", "")
        if isinstance(data, bytes):
            xml_request_body = data.decode("utf-8")
        else:
            xml_request_body = data
        xml = """<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
</d:multistatus>"""
        return SimpleNamespace(text=xml, status_code=207, headers={})

    monkeypatch.setattr(carddav_mod, "request_with_retries", _capture_request)

    carddav_client.find_by_uid(uid_with_specials)

    # Verify all special characters are properly escaped
    assert "&amp;" in xml_request_body  # & should be escaped
    assert "<" not in xml_request_body  # < should be escaped
    assert ">" not in xml_request_body  # > should be escaped


def test_caldav_special_characters_handled(monkeypatch, caldav_client: CalDAVClient) -> None:
    """Test that special XML characters are properly escaped in CalDAV."""
    uid_with_specials = "event&id\"with'quotes<and>tags"

    xml_request_body = None

    import g2nc.nextcloud.caldav as caldav_mod

    def _capture_request(client, method, url, **kwargs):
        nonlocal xml_request_body
        data = kwargs.get("data", "")
        if isinstance(data, bytes):
            xml_request_body = data.decode("utf-8")
        else:
            xml_request_body = data
        xml = """<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
</d:multistatus>"""
        return SimpleNamespace(text=xml, status_code=207, headers={})

    monkeypatch.setattr(caldav_mod, "request_with_retries", _capture_request)

    caldav_client.find_by_uid(uid_with_specials)

    # Verify all special characters are properly escaped
    assert "&amp;" in xml_request_body  # & should be escaped
    assert "<" not in xml_request_body  # < should be escaped
    assert ">" not in xml_request_body  # > should be escaped


def test_ssl_verification_production_protection(monkeypatch) -> None:
    """Test that SSL verification cannot be disabled in production environment."""
    # Set production environment
    monkeypatch.setenv("G2NC_ENVIRONMENT", "production")

    # Attempting to create client with verify=False should raise ValueError
    with pytest.raises(
        ValueError, match="SSL certificate verification cannot be disabled in production"
    ):
        create_client(timeout=10.0, verify=False)


def test_ssl_verification_development_allowed(monkeypatch) -> None:
    """Test that SSL verification can be disabled in development environment."""
    # Set development environment
    monkeypatch.setenv("G2NC_ENVIRONMENT", "development")

    # Should not raise an exception
    client = create_client(timeout=10.0, verify=False)
    assert client is not None


def test_ssl_verification_test_allowed(monkeypatch) -> None:
    """Test that SSL verification can be disabled in test environment."""
    # Set test environment
    monkeypatch.setenv("G2NC_ENVIRONMENT", "test")

    # Should not raise an exception
    client = create_client(timeout=10.0, verify=False)
    assert client is not None


def test_ssl_verification_no_env_allows_disable(monkeypatch) -> None:
    """Test that SSL verification can be disabled when no environment is set."""
    # Clear environment variable if it exists
    monkeypatch.delenv("G2NC_ENVIRONMENT", raising=False)

    # Should not raise an exception (defaults to allowing disable)
    client = create_client(timeout=10.0, verify=False)
    assert client is not None


def test_ssl_verification_enabled_always_works(monkeypatch) -> None:
    """Test that SSL verification enabled always works regardless of environment."""
    # Set production environment
    monkeypatch.setenv("G2NC_ENVIRONMENT", "production")

    # Should not raise an exception when verify=True
    client = create_client(timeout=10.0, verify=True)
    assert client is not None


def test_config_path_validation_prevents_traversal(tmp_path) -> None:
    """Test that path validation prevents path traversal attacks."""
    # Create a config file in an unsafe location (simulating /etc/passwd)
    unsafe_path = tmp_path / ".." / ".." / ".." / ".." / ".." / ".." / "etc" / "passwd"

    # This should raise a ValueError due to path validation
    with pytest.raises(ValueError, match="is outside allowed directories"):
        load_config(file_path=unsafe_path)


def test_config_path_validation_allows_home_directory(tmp_path) -> None:
    """Test that path validation allows files in user home directory."""
    # Create a config file in a temporary directory under home
    home_config = Path.home() / "config.yaml"
    home_config.write_text(
        """
nextcloud:
  base_url: https://example.com
  username: test
  addressbook_path: /remote.php/dav/addressbooks/users/test/Contacts/
"""
    )

    # This should not raise an exception
    config = load_config(file_path=str(home_config))
    assert config.nextcloud.base_url == "https://example.com"
    os.remove(home_config)


def test_config_path_validation_allows_current_directory(tmp_path, monkeypatch) -> None:
    """Test that path validation allows files in current working directory."""
    # Change to tmp_path as current directory
    monkeypatch.chdir(tmp_path)

    # Create config in current directory
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
nextcloud:
  base_url: https://example.com
  username: test
  addressbook_path: /remote.php/dav/addressbooks/users/test/Contacts/
"""
    )

    # This should not raise an exception
    config = load_config(file_path=str(config_file))
    assert config.nextcloud.base_url == "https://example.com"


def test_config_path_validation_allows_data_directory(tmp_path) -> None:
    """Test that path validation allows /data directory."""
    # Create a mock config file path in /data
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    data_config = data_dir / "config.yaml"
    data_config.write_text(
        """
nextcloud:
  base_url: https://example.com
  username: test
  addressbook_path: /remote.php/dav/addressbooks/users/test/Contacts/
"""
    )

    with patch("g2nc.config.Path.cwd", return_value=tmp_path):
        # This should not raise a path validation error
        config = load_config(file_path=str(data_config))
        assert config.nextcloud.base_url == "https://example.com"


def test_config_path_validation_allows_temp_directory() -> None:
    """Test that path validation allows temporary directories."""
    # Create a real temporary file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tf:
        tf.write(
            """
nextcloud:
  base_url: https://example.com
  username: test
  addressbook_path: /remote.php/dav/addressbooks/users/test/Contacts/
"""
        )
        temp_config_path = tf.name

    try:
        # This should not raise an exception
        config = load_config(file_path=temp_config_path)
        assert config.nextcloud.base_url == "https://example.com"
    finally:
        # Clean up
        os.unlink(temp_config_path)


def test_config_path_validation_blocks_system_files() -> None:
    """Test that path validation blocks access to sensitive system files."""
    dangerous_paths = [
        "/etc/shadow",
        "/root/.ssh/id_rsa",
        "/proc/version",
        "/sys/devices",
        "../../../../../../etc/passwd",  # Relative path traversal
    ]

    for dangerous_path in dangerous_paths:
        with pytest.raises(ValueError, match="is outside allowed directories"):
            load_config(file_path=dangerous_path)


def test_database_permissions_set_on_creation(tmp_path) -> None:
    """Test that database file gets restrictive permissions (600) when created."""
    db_path = tmp_path / "test.db"

    # Create State instance (which creates the database)
    State(str(db_path))

    # Verify database file exists
    assert db_path.exists()

    # Check permissions are 600 (owner read/write only)
    file_mode = db_path.stat().st_mode
    permissions = stat.filemode(file_mode)
    assert permissions.endswith("rw-------"), f"Expected rw------- permissions, got {permissions}"


def test_database_permissions_graceful_error_handling(tmp_path, monkeypatch) -> None:
    """Test graceful handling when chmod fails."""
    db_path = tmp_path / "test.db"

    # Mock chmod to raise an OSError
    def mock_chmod(mode):
        raise OSError("Operation not permitted")

    # Patch Path.chmod to simulate permission error
    with patch.object(Path, "chmod", mock_chmod):
        # Should not raise an exception, just log a warning
        with patch("logging.Logger.warning") as mock_log:
            state = State(str(db_path))
            mock_log.assert_called_once()

        # Database should still be created and functional
        assert db_path.exists()

        # Basic functionality should work
        state.save_token("test", "token123")
        assert state.get_token("test") == "token123"


def test_database_permissions_non_writable_file_warning(tmp_path, monkeypatch) -> None:
    """Test warning when database file is not writable by current user."""
    db_path = tmp_path / "test.db"

    # Create the database file first
    db_path.write_text("")  # Create empty file

    # Make the file read-only
    db_path.chmod(stat.S_IRUSR)  # 400 permissions (read-only)

    # Mock os.access to return False for write access
    def mock_access(path, mode):
        if mode == os.W_OK and str(path) == str(db_path):
            return False
        return True

    with patch("os.access", mock_access):
        # Should not raise an exception, but should log a warning
        with patch("logging.Logger.warning") as mock_log:
            # The database creation will still proceed
            State(str(db_path))
            mock_log.assert_called_once()

        # Database should exist
        assert db_path.exists()


def test_database_permissions_existing_file_upgrade(tmp_path) -> None:
    """Test that existing database files get permissions updated."""
    db_path = tmp_path / "existing.db"

    # Create an existing database file with loose permissions
    db_path.write_text("")  # Create empty file
    db_path.chmod(0o644)  # World-readable permissions

    # Verify initial loose permissions
    initial_mode = db_path.stat().st_mode
    initial_permissions = stat.filemode(initial_mode)
    assert initial_permissions.endswith(
        "rw-r--r--"
    ), f"Setup failed: expected rw-r--r--, got {initial_permissions}"

    # Create State instance with existing file
    State(str(db_path))

    # Permissions should now be restrictive
    final_mode = db_path.stat().st_mode
    final_permissions = stat.filemode(final_mode)
    assert final_permissions.endswith(
        "rw-------"
    ), f"Expected rw------- permissions, got {final_permissions}"


def test_database_permissions_filesystem_not_supporting_chmod(tmp_path) -> None:
    """Test handling of filesystems that don't support chmod."""
    db_path = tmp_path / "test.db"

    # Mock chmod to raise AttributeError (some filesystems don't support it)
    def mock_chmod(self, mode):
        raise AttributeError("'Path' object has no attribute 'chmod'")

    with patch.object(Path, "chmod", mock_chmod):
        # Should not raise an exception
        state = State(str(db_path))

        # Database should still be created and functional
        assert db_path.exists()

        # Basic functionality should work
        state.save_token("test", "token123")
        assert state.get_token("test") == "token123"
