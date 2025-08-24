from unittest.mock import Mock, call, patch

import httpx
import pytest

from g2nc.utils.http import (
    RetryConfig,
    _should_retry,
    _sleep_backoff,
    delete_with_etag,
    put_with_etag,
    request_with_retries,
)


class TestRetryConfig:
    """Test RetryConfig dataclass."""

    def test_default_values(self):
        """Test default retry configuration values."""
        config = RetryConfig()

        assert config.max_retries == 5
        assert config.backoff_initial_sec == 1.0
        assert config.backoff_factor == 2.0
        assert config.jitter_frac == 0.2
        assert config.status_forcelist == (429, 500, 502, 503, 504)
        assert "GET" in config.methods
        assert "PUT" in config.methods
        assert "DELETE" in config.methods

    def test_custom_values(self):
        """Test custom retry configuration."""
        config = RetryConfig(
            max_retries=3,
            backoff_initial_sec=0.5,
            backoff_factor=1.5,
            jitter_frac=0.1,
            status_forcelist=(500, 502),
            methods=("GET", "POST"),
        )

        assert config.max_retries == 3
        assert config.backoff_initial_sec == 0.5
        assert config.backoff_factor == 1.5
        assert config.jitter_frac == 0.1
        assert config.status_forcelist == (500, 502)
        assert config.methods == ("GET", "POST")


class TestShouldRetry:
    """Test retry decision logic."""

    def test_should_retry_retryable_status_codes(self):
        """Test that retryable status codes trigger retries."""
        config = RetryConfig()

        for status_code in config.status_forcelist:
            assert _should_retry("GET", status_code, None, config)

    def test_should_not_retry_non_retryable_status_codes(self):
        """Test that non-retryable status codes don't trigger retries."""
        config = RetryConfig()
        non_retryable = [200, 201, 400, 401, 403, 404, 422]

        for status_code in non_retryable:
            assert not _should_retry("GET", status_code, None, config)

    def test_should_retry_network_exceptions(self):
        """Test that network exceptions trigger retries."""
        config = RetryConfig()
        exceptions = [
            httpx.ConnectError("Connection failed"),
            httpx.TimeoutException("Timeout"),
            httpx.NetworkError("Network error"),
        ]

        for exc in exceptions:
            assert _should_retry("GET", None, exc, config)

    def test_should_not_retry_disallowed_methods(self):
        """Test that methods not in allowed list don't retry."""
        config = RetryConfig(methods=("GET", "PUT"))

        # POST not in allowed methods
        assert not _should_retry("POST", 500, None, config)
        assert not _should_retry("POST", None, httpx.ConnectError("Failed"), config)

        # GET is allowed
        assert _should_retry("GET", 500, None, config)

    def test_should_not_retry_no_status_or_exception(self):
        """Test that no retry occurs when no status code or exception."""
        config = RetryConfig()

        assert not _should_retry("GET", None, None, config)


class TestSleepBackoff:
    """Test backoff sleep calculation."""

    def test_backoff_timing_progression(self):
        """Test that backoff increases with attempts."""
        config = RetryConfig(
            backoff_initial_sec=1.0,
            backoff_factor=2.0,
            jitter_frac=0.0,  # No jitter for predictable testing
        )

        with patch("time.sleep") as mock_sleep:
            _sleep_backoff(1, config)
            _sleep_backoff(2, config)
            _sleep_backoff(3, config)

            mock_sleep.assert_has_calls(
                [
                    call(1.0),
                    call(2.0),
                    call(4.0),
                ]
            )

    def test_backoff_with_jitter(self):
        """Test that jitter varies sleep times."""
        config = RetryConfig(
            backoff_initial_sec=1.0, backoff_factor=2.0, jitter_frac=0.5  # 50% jitter
        )

        with patch("time.sleep") as mock_sleep:
            with patch("random.uniform", side_effect=[-0.25, 0.25, 0.0]):  # Different jitter values
                _sleep_backoff(1, config)
                _sleep_backoff(1, config)
                _sleep_backoff(1, config)

        # Should have different sleep times due to jitter
        calls = mock_sleep.call_args_list
        assert len(calls) == 3
        assert calls[0][0][0] == 0.75
        assert calls[1][0][0] == 1.25
        assert calls[2][0][0] == 1.0

    def test_no_negative_sleep(self):
        """Test that sleep time never goes negative even with jitter."""
        config = RetryConfig(
            backoff_initial_sec=0.1,
            backoff_factor=1.0,
            jitter_frac=2.0,  # Large jitter that could go negative
        )

        with patch("time.sleep") as mock_sleep:
            with patch("random.uniform", return_value=-0.2):  # Negative jitter
                _sleep_backoff(1, config)  # Base 0.1, jitter -0.2 would be -0.1

        # Should not call sleep with negative value, should be 0 or skip
        if mock_sleep.called:
            sleep_time = mock_sleep.call_args[0][0]
            assert sleep_time >= 0


class TestRequestWithRetries:
    """Test HTTP request retry functionality."""

    def test_successful_request_no_retry(self):
        """Test successful request that doesn't need retries."""
        mock_client = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_client.request.return_value = mock_response

        result = request_with_retries(mock_client, "GET", "http://example.com")

        assert result == mock_response
        assert mock_client.request.call_count == 1

    def test_retry_on_server_error(self):
        """Test retry behavior on server errors."""
        mock_client = Mock()

        # First call returns 500, second returns 200
        responses = [Mock(status_code=500), Mock(status_code=200)]
        mock_client.request.side_effect = responses

        config = RetryConfig(max_retries=3)

        with patch("g2nc.utils.http._sleep_backoff") as mock_sleep:
            result = request_with_retries(mock_client, "GET", "http://example.com", retry=config)

        assert result == responses[1]
        assert mock_client.request.call_count == 2
        assert mock_sleep.call_count == 1  # Slept once between attempts

    def test_retry_exhaustion_returns_last_response(self):
        """Test that after exhausting retries, last response is returned."""
        mock_client = Mock()
        mock_response = Mock(status_code=500)
        mock_client.request.return_value = mock_response

        config = RetryConfig(max_retries=2)

        with patch("g2nc.utils.http._sleep_backoff") as mock_sleep:
            result = request_with_retries(mock_client, "GET", "http://example.com", retry=config)

        assert result == mock_response
        assert mock_client.request.call_count == 2
        assert mock_sleep.call_count == 1

    def test_retry_on_network_error_then_success(self):
        """Test retry on network error followed by success."""
        mock_client = Mock()
        mock_response = Mock(status_code=200)

        # First call raises exception, second succeeds
        mock_client.request.side_effect = [httpx.ConnectError("Connection failed"), mock_response]

        config = RetryConfig(max_retries=3)

        with patch("g2nc.utils.http._sleep_backoff") as mock_sleep:
            result = request_with_retries(mock_client, "GET", "http://example.com", retry=config)

        assert result == mock_response
        assert mock_client.request.call_count == 2
        assert mock_sleep.call_count == 1

    def test_retry_exhaustion_raises_exception(self):
        """Test that after exhausting retries on exceptions, exception is raised."""
        mock_client = Mock()
        network_error = httpx.ConnectError("Connection failed")
        mock_client.request.side_effect = network_error

        config = RetryConfig(max_retries=2)

        with patch("g2nc.utils.http._sleep_backoff"):
            with pytest.raises(httpx.ConnectError, match="Connection failed"):
                request_with_retries(mock_client, "GET", "http://example.com", retry=config)

        assert mock_client.request.call_count == 2

    def test_non_retryable_status_code_no_retry(self):
        """Test that non-retryable status codes don't trigger retries."""
        mock_client = Mock()
        mock_response = Mock(status_code=404)
        mock_client.request.return_value = mock_response

        config = RetryConfig(max_retries=3)

        result = request_with_retries(mock_client, "GET", "http://example.com", retry=config)

        assert result == mock_response
        assert mock_client.request.call_count == 1  # No retries

    def test_non_retryable_exception_immediate_raise(self):
        """Test that non-retryable exceptions are raised immediately."""
        mock_client = Mock()

        # Create a custom config where ConnectError is not retryable
        config = RetryConfig(max_retries=3)

        # Mock _should_retry to return False for this exception
        with patch("g2nc.utils.http._should_retry", return_value=False):
            mock_client.request.side_effect = httpx.ConnectError("Connection failed")

            with pytest.raises(httpx.ConnectError):
                request_with_retries(mock_client, "GET", "http://example.com", retry=config)

            assert mock_client.request.call_count == 1  # No retries

    def test_custom_expected_status_codes(self):
        """Test custom expected status codes."""
        mock_client = Mock()
        mock_response = Mock(status_code=202)  # Not in default expected codes
        mock_client.request.return_value = mock_response

        # Should succeed with custom expected codes
        result = request_with_retries(
            mock_client, "POST", "http://example.com", expected=(200, 202)
        )

        assert result == mock_response
        assert mock_client.request.call_count == 1


class TestPutWithEtag:
    """Test PUT requests with ETag handling."""

    def test_put_with_etag_if_match(self):
        """Test PUT with If-Match header."""
        mock_client = Mock()
        mock_response = Mock(status_code=200)
        mock_client.request.return_value = mock_response

        result = put_with_etag(
            mock_client,
            "http://example.com/resource",
            "test body",
            content_type="text/plain",
            etag='"abc123"',
        )

        assert result == mock_response

        # Verify request was called with correct headers
        call_args = mock_client.request.call_args
        headers = call_args.kwargs["headers"]
        assert headers["Content-Type"] == "text/plain"
        assert headers["If-Match"] == '"abc123"'

    def test_put_with_create_if_missing(self):
        """Test PUT with If-None-Match for creation."""
        mock_client = Mock()
        mock_response = Mock(status_code=201)
        mock_client.request.return_value = mock_response

        result = put_with_etag(
            mock_client,
            "http://example.com/resource",
            b"binary body",
            content_type="application/octet-stream",
            create_if_missing=True,
        )

        assert result == mock_response

        # Verify request was called with If-None-Match
        call_args = mock_client.request.call_args
        headers = call_args.kwargs["headers"]
        assert headers["If-None-Match"] == "*"

    def test_put_without_etag_conditions(self):
        """Test PUT without ETag conditions."""
        mock_client = Mock()
        mock_response = Mock(status_code=200)
        mock_client.request.return_value = mock_response

        result = put_with_etag(
            mock_client, "http://example.com/resource", "test body", content_type="text/plain"
        )

        assert result == mock_response

        # Verify request was called without conditional headers
        call_args = mock_client.request.call_args
        headers = call_args.kwargs["headers"]
        assert "If-Match" not in headers
        assert "If-None-Match" not in headers
        assert headers["Content-Type"] == "text/plain"


class TestDeleteWithEtag:
    """Test DELETE requests with ETag handling."""

    def test_delete_with_etag(self):
        """Test DELETE with If-Match header."""
        mock_client = Mock()
        mock_response = Mock(status_code=204)
        mock_client.request.return_value = mock_response

        result = delete_with_etag(mock_client, "http://example.com/resource", etag='"abc123"')

        assert result == mock_response

        # Verify request was called with If-Match header
        call_args = mock_client.request.call_args
        headers = call_args.kwargs["headers"]
        assert headers["If-Match"] == '"abc123"'

    def test_delete_without_etag(self):
        """Test DELETE without ETag condition."""
        mock_client = Mock()
        mock_response = Mock(status_code=200)
        mock_client.request.return_value = mock_response

        result = delete_with_etag(mock_client, "http://example.com/resource")

        assert result == mock_response

        # Verify request was called without conditional headers
        call_args = mock_client.request.call_args
        headers = call_args.kwargs["headers"]
        assert "If-Match" not in headers
