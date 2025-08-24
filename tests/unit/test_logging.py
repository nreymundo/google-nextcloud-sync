import json
import logging
from io import StringIO
from unittest.mock import patch

from g2nc.logging import (
    JsonFormatter,
    RedactingFilter,
    _mask_email,
    _mask_phone,
    _mask_token,
    mask_pii,
    setup_logging,
)


class TestMaskPII:
    """Test PII masking functionality."""

    def test_mask_email_addresses(self):
        """Test email address masking."""
        test_cases = [
            ("user@example.com", "u***r@example.com"),
            ("a@b.co", "*@b.co"),  # Single char becomes *
            ("verylongusername@domain.org", "v***e@domain.org"),
            (
                "Contact me at john.doe@company.com for details",
                "Contact me at j***e@company.com for details",
            ),
        ]

        for input_text, expected in test_cases:
            assert mask_pii(input_text) == expected

    def test_mask_phone_numbers(self):
        """Test phone number masking."""
        test_cases = [
            ("+1234567890", "+********90"),
            ("123-456-7890", "********90"),
            ("(555) 123-4567", "********67"),
            ("+1 (555) 123-4567", "+*********67"),
            ("12", "12"),  # Short numbers not masked
            ("Call me at +1-555-123-4567", "Call me at +*********67"),
        ]

        for input_text, expected in test_cases:
            assert mask_pii(input_text) == expected

    def test_mask_tokens(self):
        """Test token masking."""
        test_cases = [
            ("access_token: abcd1234567890", "access_token: abcd********7890"),
            ("refresh-token=xyz987654321abc", "refresh-token: xyz9********1abc"),
            ("auth token: secret123456789", "auth token: secr********6789"),
            ("ID_TOKEN: verylongtokenvalue123", "ID_TOKEN: very********e123"),
            ("token: short", "token: short"),  # Short tokens not masked
        ]

        for input_text, expected in test_cases:
            assert mask_pii(input_text) == expected

    def test_mask_combined_pii(self):
        """Test masking multiple PII types in same text."""
        input_text = (
            "Contact john.doe@example.com at +1-555-123-4567 with access_token: abc123def456ghi"
        )
        expected = "Contact j***e@example.com at +*********67 with access_token: abc1********6ghi"
        assert mask_pii(input_text) == expected

    def test_mask_empty_or_none(self):
        """Test masking empty or None input."""
        assert mask_pii("") == ""
        assert mask_pii(None) is None  # type: ignore


class TestMaskingHelpers:
    """Test individual masking helper functions."""

    def test_mask_email_helper(self):
        """Test _mask_email helper function directly."""
        from g2nc.logging import _EMAIL_RE

        match = _EMAIL_RE.search("test.user@example.com")
        assert match is not None
        result = _mask_email(match)
        assert result == "t***r@example.com"

    def test_mask_phone_helper(self):
        """Test _mask_phone helper function directly."""
        from g2nc.logging import _PHONE_RE

        match = _PHONE_RE.search("+1-555-123-4567")
        assert match is not None
        result = _mask_phone(match)
        assert result == "+*********67"

    def test_mask_token_helper(self):
        """Test _mask_token helper function directly."""
        from g2nc.logging import _TOKEN_RE

        match = _TOKEN_RE.search("access_token: abcdef123456")
        assert match is not None
        result = _mask_token(match)
        assert result == "access_token: abcd********3456"


class TestRedactingFilter:
    """Test PII redaction in log records."""

    def test_filter_masks_message(self):
        """Test that filter masks PII in log messages."""
        filter_obj = RedactingFilter()

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=1,
            msg="User email: user@example.com",
            args=(),
            exc_info=None,
        )

        result = filter_obj.filter(record)
        assert result is True
        assert record.msg == "User email: u***r@example.com"

    def test_filter_masks_extra_keys(self):
        """Test that filter masks known PII keys in extras."""
        filter_obj = RedactingFilter()

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=1,
            msg="Processing user",
            args=(),
            exc_info=None,
        )

        # Add extra fields that should be masked
        record.email = "user@example.com"  # type: ignore
        record.access_token = "abc123def456ghi789"  # type: ignore
        record.phone = "+1-555-123-4567"  # type: ignore
        record.safe_field = "this should not be masked"  # type: ignore

        filter_obj.filter(record)

        assert record.email == "u***r@example.com"  # type: ignore
        assert record.access_token == "abc1********hi789"  # type: ignore # Token masking happens in message, not extra keys
        assert record.phone == "+*********67"  # type: ignore
        assert record.safe_field == "this should not be masked"  # type: ignore

    def test_filter_handles_non_string_extras(self):
        """Test that filter handles non-string extra values gracefully."""
        filter_obj = RedactingFilter()

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        # Add non-string extras
        record.email = 12345  # type: ignore # Non-string, should be ignored
        record.count = 42  # type: ignore # Not in mask list anyway

        # Should not raise exception
        result = filter_obj.filter(record)
        assert result is True
        assert record.email == 12345  # type: ignore # Unchanged
        assert record.count == 42  # type: ignore

    def test_filter_exception_handling(self):
        """Test that filter handles exceptions gracefully."""
        filter_obj = RedactingFilter()

        # Create a record with problematic msg
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=1,
            msg=None,  # This might cause issues
            args=(),
            exc_info=None,
        )

        # Should not raise exception, just return True
        result = filter_obj.filter(record)
        assert result is True


class TestJsonFormatter:
    """Test JSON log formatting with PII redaction."""

    def test_json_formatter_basic(self):
        """Test basic JSON formatting."""
        formatter = JsonFormatter()

        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="/path/test.py",
            lineno=42,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        record.funcName = "test_function"
        record.module = "test_module"

        result = formatter.format(record)
        parsed = json.loads(result)

        assert parsed["level"] == "INFO"
        assert parsed["name"] == "test.logger"
        assert parsed["msg"] == "Test message"
        assert parsed["funcName"] == "test_function"
        assert parsed["lineno"] == 42
        assert parsed["module"] == "test_module"
        assert "ts" in parsed

    def test_json_formatter_with_pii(self):
        """Test JSON formatting masks PII in message."""
        formatter = JsonFormatter()

        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="",
            lineno=1,
            msg="User email: user@example.com",
            args=(),
            exc_info=None,
        )

        result = formatter.format(record)
        parsed = json.loads(result)

        assert parsed["msg"] == "User email: u***r@example.com"

    def test_json_formatter_with_extras(self):
        """Test JSON formatting includes and masks extras."""
        formatter = JsonFormatter()

        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="",
            lineno=1,
            msg="Processing",
            args=(),
            exc_info=None,
        )

        # Add various types of extras
        record.user_email = "admin@company.com"  # type: ignore
        record.count = 5  # type: ignore
        record.enabled = True  # type: ignore
        record.config = {"key": "value@test.com"}  # type: ignore

        result = formatter.format(record)
        parsed = json.loads(result)

        assert parsed["user_email"] == "a***n@company.com"
        assert parsed["count"] == 5
        assert parsed["enabled"] is True
        assert parsed["config"]["key"] == "v***e@test.com"  # PII masked in dict values

    def test_json_formatter_message_with_args(self):
        """Test JSON formatting with message args."""
        formatter = JsonFormatter()

        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="",
            lineno=1,
            msg="User %s logged in from %s",
            args=("user@example.com", "192.168.1.1"),
            exc_info=None,
        )

        result = formatter.format(record)
        parsed = json.loads(result)

        assert parsed["msg"] == "User u***r@example.com logged in from 192.168.1.1"

    def test_json_formatter_unserializable_extras(self):
        """Test JSON formatting handles unserializable extras."""
        formatter = JsonFormatter()

        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )

        # Add unserializable object
        record.complex_obj = object()  # type: ignore

        result = formatter.format(record)
        parsed = json.loads(result)

        assert parsed["complex_obj"] == "[object]"


class TestSetupLogging:
    """Test logging setup functionality."""

    def setUp(self):
        """Clear any existing handlers."""
        root = logging.getLogger()
        for handler in root.handlers[:]:
            root.removeHandler(handler)

    def test_setup_logging_console_format(self):
        """Test console logging setup."""
        with patch("sys.stdout", new_callable=StringIO):
            setup_logging(level="DEBUG", json=False)

        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert len(root.handlers) == 1

        handler = root.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        assert any(isinstance(f, RedactingFilter) for f in handler.filters)
        assert not isinstance(handler.formatter, JsonFormatter)

    def test_setup_logging_json_format(self):
        """Test JSON logging setup."""
        with patch("sys.stdout", new_callable=StringIO):
            setup_logging(level="INFO", json=True)

        root = logging.getLogger()
        assert root.level == logging.INFO
        assert len(root.handlers) == 1

        handler = root.handlers[0]
        assert isinstance(handler.formatter, JsonFormatter)

    def test_setup_logging_environment_override(self, monkeypatch):
        """Test environment variable forces JSON logging."""
        monkeypatch.setenv("G2NC_FORCE_JSON_LOGS", "true")

        with patch("sys.stdout", new_callable=StringIO):
            setup_logging(level="INFO", json=False)  # Request console but env forces JSON

        root = logging.getLogger()
        handler = root.handlers[0]
        assert isinstance(handler.formatter, JsonFormatter)

    def test_setup_logging_integration(self):
        """Test full logging integration with PII redaction."""
        output = StringIO()

        with patch("sys.stdout", output):
            setup_logging(level="INFO", json=True)

            logger = logging.getLogger("test")
            logger.info("User login: user@example.com with token abc123def456")

        log_output = output.getvalue().strip()
        parsed = json.loads(log_output)

        assert "u***r@example.com" in parsed["msg"]
        assert "abcd********ef456" in parsed["msg"]  # Token not masked in this case
        assert parsed["name"] == "test"
        assert parsed["level"] == "INFO"

    def test_setup_logging_third_party_levels(self):
        """Test that third-party library log levels are set correctly."""
        with patch("sys.stdout", new_callable=StringIO):
            setup_logging(level="DEBUG", json=False)

        # Check third-party loggers are set to WARNING
        assert logging.getLogger("urllib3").level == logging.WARNING
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("google").level == logging.WARNING
