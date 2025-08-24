import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from g2nc.config import GoogleConfig
from g2nc.google.auth import (
    SCOPES_CALENDAR,
    SCOPES_CONTACTS,
    _interactive_flow,
    _load_saved_credentials,
    _read_client_config,
    _refresh_if_needed,
    _save_credentials,
    get_credentials,
)


@pytest.fixture
def mock_google_config(tmp_path):
    """Create a mock GoogleConfig with temporary paths."""
    credentials_file = tmp_path / "credentials.json"
    token_store = tmp_path / "token.json"

    # Create a sample credentials file
    credentials_data = {
        "installed": {
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    credentials_file.write_text(json.dumps(credentials_data))

    return GoogleConfig(
        credentials_file=str(credentials_file),
        token_store=str(token_store),
        calendar_ids={"default": "primary"},
    )


@pytest.fixture
def mock_credentials():
    """Create a mock Credentials object."""
    mock_creds = Mock()
    mock_creds.valid = True
    mock_creds.expired = False
    mock_creds.refresh_token = "refresh_token_123"
    mock_creds.to_json.return_value = json.dumps(
        {
            "token": "access_token_123",
            "refresh_token": "refresh_token_123",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",
            "scopes": ["https://www.googleapis.com/auth/contacts.readonly"],
        }
    )
    return mock_creds


def test_scopes_constants():
    """Test that scope constants are defined correctly."""
    assert SCOPES_CONTACTS == ["https://www.googleapis.com/auth/contacts.readonly"]
    assert SCOPES_CALENDAR == ["https://www.googleapis.com/auth/calendar.readonly"]


def test_read_client_config_from_file(mock_google_config):
    """Test reading client config from file."""
    config = _read_client_config(mock_google_config)

    assert "installed" in config
    assert config["installed"]["client_id"] == "test_client_id"
    assert config["installed"]["client_secret"] == "test_client_secret"


def test_read_client_config_from_env_json(monkeypatch):
    """Test reading client config from GOOGLE_CREDENTIALS_JSON env var."""
    credentials_data = {
        "installed": {
            "client_id": "env_client_id",
            "client_secret": "env_client_secret",
        }
    }
    monkeypatch.setenv("GOOGLE_CREDENTIALS_JSON", json.dumps(credentials_data))

    # Config file path doesn't matter since env takes precedence
    google_cfg = GoogleConfig(credentials_file="", token_store="", calendar_ids={})

    config = _read_client_config(google_cfg)
    assert config["installed"]["client_id"] == "env_client_id"


def test_read_client_config_from_env_file(monkeypatch, tmp_path):
    """Test reading client config from GOOGLE_CREDENTIALS_FILE env var."""
    credentials_file = tmp_path / "env_credentials.json"
    credentials_data = {
        "installed": {
            "client_id": "env_file_client_id",
            "client_secret": "env_file_client_secret",
        }
    }
    credentials_file.write_text(json.dumps(credentials_data))

    monkeypatch.setenv("GOOGLE_CREDENTIALS_FILE", str(credentials_file))

    # Config file path doesn't matter since env takes precedence
    google_cfg = GoogleConfig(credentials_file="", token_store="", calendar_ids={})

    config = _read_client_config(google_cfg)
    assert config["installed"]["client_id"] == "env_file_client_id"


def test_read_client_config_invalid_json_env(monkeypatch):
    """Test error handling for invalid JSON in env var."""
    monkeypatch.setenv("GOOGLE_CREDENTIALS_JSON", "invalid json{")

    google_cfg = GoogleConfig(credentials_file="", token_store="", calendar_ids={})

    with pytest.raises(ValueError, match="Invalid JSON in GOOGLE_CREDENTIALS_JSON"):
        _read_client_config(google_cfg)


def test_read_client_config_missing_file():
    """Test error handling for missing credentials file."""
    google_cfg = GoogleConfig(
        credentials_file="/nonexistent/file.json", token_store="", calendar_ids={}
    )

    with pytest.raises(FileNotFoundError, match="Google credentials file not found"):
        _read_client_config(google_cfg)


def test_read_client_config_no_credentials():
    """Test error handling when no credentials are provided."""
    google_cfg = GoogleConfig(credentials_file="", token_store="", calendar_ids={})

    with pytest.raises(ValueError, match="Google credentials not provided"):
        _read_client_config(google_cfg)


def test_load_saved_credentials_missing_file(tmp_path):
    """Test loading credentials when token store file doesn't exist."""
    token_store = tmp_path / "nonexistent.json"

    # Since file doesn't exist, function should return None early without importing
    result = _load_saved_credentials(str(token_store), SCOPES_CONTACTS)
    assert result is None


def test_load_saved_credentials_success(tmp_path, mock_credentials):
    """Test successful loading of saved credentials."""
    token_store = tmp_path / "token.json"
    token_store.write_text(mock_credentials.to_json())

    with patch("google.oauth2.credentials.Credentials") as mock_creds_class:
        mock_creds_class.from_authorized_user_file.return_value = mock_credentials

        result = _load_saved_credentials(str(token_store), SCOPES_CONTACTS)

        assert result == mock_credentials
        mock_creds_class.from_authorized_user_file.assert_called_once_with(
            str(token_store), scopes=list(SCOPES_CONTACTS)
        )


def test_load_saved_credentials_exception_handling(tmp_path):
    """Test handling of exceptions when loading credentials."""
    token_store = tmp_path / "corrupt.json"
    token_store.write_text("invalid json")

    with patch("google.oauth2.credentials.Credentials") as mock_creds_class:
        mock_creds_class.from_authorized_user_file.side_effect = Exception("Corrupt file")

        result = _load_saved_credentials(str(token_store), SCOPES_CONTACTS)
        assert result is None


def test_save_credentials(tmp_path, mock_credentials):
    """Test saving credentials to token store."""
    token_store = tmp_path / "subdir" / "token.json"  # Test directory creation

    _save_credentials(str(token_store), mock_credentials)

    # Verify file was created
    assert token_store.exists()

    # Verify content
    saved_content = json.loads(token_store.read_text())
    expected_content = json.loads(mock_credentials.to_json())
    assert saved_content == expected_content


def test_interactive_flow_missing_dependency():
    """Test error handling when google-auth-oauthlib is not available."""
    with patch.dict("sys.modules", {"google_auth_oauthlib.flow": None}):
        with pytest.raises(ImportError):
            _interactive_flow({}, SCOPES_CONTACTS)


def test_interactive_flow_success(mock_credentials):
    """Test successful interactive OAuth flow."""
    client_config = {"installed": {"client_id": "test"}}

    mock_flow = Mock()
    mock_flow.run_local_server.return_value = mock_credentials

    with patch("google_auth_oauthlib.flow.InstalledAppFlow") as mock_flow_class:
        mock_flow_class.from_client_config.return_value = mock_flow

        result = _interactive_flow(client_config, SCOPES_CONTACTS)

        assert result == mock_credentials
        mock_flow_class.from_client_config.assert_called_once_with(
            client_config, scopes=list(SCOPES_CONTACTS)
        )
        mock_flow.run_local_server.assert_called_once_with(
            open_browser=True, host="localhost", port=0, authorization_prompt_message=""
        )


def test_refresh_if_needed_not_expired():
    """Test refresh when credentials are not expired."""
    mock_creds = Mock()
    mock_creds.expired = False
    mock_creds.refresh_token = "refresh_token"

    _refresh_if_needed(mock_creds)

    # Should not call refresh
    mock_creds.refresh.assert_not_called()


def test_refresh_if_needed_no_refresh_token():
    """Test refresh when no refresh token available."""
    mock_creds = Mock()
    mock_creds.expired = True
    mock_creds.refresh_token = None

    _refresh_if_needed(mock_creds)

    # Should not call refresh
    mock_creds.refresh.assert_not_called()


def test_refresh_if_needed_success():
    """Test successful token refresh."""
    mock_creds = Mock()
    mock_creds.expired = True
    mock_creds.refresh_token = "refresh_token"

    with patch("google.auth.transport.requests.Request") as mock_request_class:
        mock_request = Mock()
        mock_request_class.return_value = mock_request

        _refresh_if_needed(mock_creds)

        mock_creds.refresh.assert_called_once_with(mock_request)


def test_refresh_if_needed_missing_dependency():
    """Test error handling when google-auth Request is not available."""
    mock_creds = Mock()
    mock_creds.expired = True
    mock_creds.refresh_token = "refresh_token"

    with patch.dict("sys.modules", {"google.auth.transport.requests": None}):
        with pytest.raises(ImportError):
            _refresh_if_needed(mock_creds)


def test_get_credentials_from_valid_store(mock_google_config, mock_credentials):
    """Test getting credentials from valid token store."""
    # Create token store file
    token_store_path = Path(mock_google_config.token_store)
    token_store_path.write_text(mock_credentials.to_json())

    with patch("g2nc.google.auth._load_saved_credentials") as mock_load:
        with patch("g2nc.google.auth._refresh_if_needed") as mock_refresh:
            with patch("g2nc.google.auth._save_credentials") as mock_save:
                mock_load.return_value = mock_credentials
                mock_credentials.valid = True

                result = get_credentials(mock_google_config, SCOPES_CONTACTS)

                assert result == mock_credentials
                mock_load.assert_called_once()
                mock_refresh.assert_called_once_with(mock_credentials)
                mock_save.assert_called_once()


def test_get_credentials_interactive_flow(mock_google_config, mock_credentials):
    """Test getting credentials via interactive flow when no valid store exists."""
    with patch("g2nc.google.auth._load_saved_credentials") as mock_load:
        with patch("g2nc.google.auth._read_client_config") as mock_read_config:
            with patch("g2nc.google.auth._interactive_flow") as mock_interactive:
                with patch("g2nc.google.auth._save_credentials") as mock_save:
                    mock_load.return_value = None  # No saved credentials
                    mock_read_config.return_value = {"installed": {"client_id": "test"}}
                    mock_interactive.return_value = mock_credentials

                    result = get_credentials(mock_google_config, SCOPES_CONTACTS)

                    assert result == mock_credentials
                    mock_interactive.assert_called_once()
                    mock_save.assert_called_once_with(
                        mock_google_config.token_store, mock_credentials
                    )


def test_get_credentials_non_interactive_no_store(mock_google_config):
    """Test error when no credentials and interactive flow is disabled."""
    with patch("g2nc.google.auth._load_saved_credentials") as mock_load:
        mock_load.return_value = None

        with pytest.raises(
            RuntimeError, match="No valid Google token found and allow_interactive=False"
        ):
            get_credentials(mock_google_config, SCOPES_CONTACTS, allow_interactive=False)


def test_get_credentials_refresh_failure_fallback(mock_google_config, mock_credentials):
    """Test fallback to interactive flow when refresh fails."""
    with patch("g2nc.google.auth._load_saved_credentials") as mock_load:
        with patch("g2nc.google.auth._refresh_if_needed") as mock_refresh:
            with patch("g2nc.google.auth._read_client_config") as mock_read_config:
                with patch("g2nc.google.auth._interactive_flow") as mock_interactive:
                    with patch("g2nc.google.auth._save_credentials"):
                        # First return expired credentials, then fresh ones from interactive flow
                        expired_creds = Mock()
                        expired_creds.valid = False  # Refresh failed
                        mock_load.return_value = expired_creds

                        mock_read_config.return_value = {"installed": {"client_id": "test"}}
                        mock_interactive.return_value = mock_credentials

                        result = get_credentials(mock_google_config, SCOPES_CONTACTS)

                        assert result == mock_credentials
                        mock_refresh.assert_called_once_with(expired_creds)
                        mock_interactive.assert_called_once()


def test_get_credentials_import_error_handling():
    """Test error handling when google auth libraries are not available."""
    google_cfg = GoogleConfig(credentials_file="", token_store="/tmp/token.json", calendar_ids={})

    with patch(
        "g2nc.google.auth._load_saved_credentials",
        side_effect=RuntimeError("google-auth is required"),
    ):
        with pytest.raises(RuntimeError, match="google-auth is required"):
            get_credentials(google_cfg, SCOPES_CONTACTS)
