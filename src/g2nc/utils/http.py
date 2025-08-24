"""HTTP utilities and a small retry wrapper built on httpx.

Intended use:
- Provide a single place for timeouts, retries, backoff, and User-Agent.
- Helpers for common conditional requests using ETag (If-Match / If-None-Match).

Notes:
- We keep this light; DAV operations will primarily go through the `caldav` library,
  but these helpers are useful for health checks or fallbacks.
"""

from __future__ import annotations

import logging
import os
import random
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from time import sleep

import httpx

log = logging.getLogger(__name__)

__all__ = [
    "RetryConfig",
    "create_client",
    "delete_with_etag",
    "put_with_etag",
    "request_with_retries",
]


@dataclass(frozen=True)
class RetryConfig:
    max_retries: int = 5
    backoff_initial_sec: float = 1.0
    backoff_factor: float = 2.0
    jitter_frac: float = 0.2  # +/- 20%
    status_forcelist: tuple[int, ...] = (429, 500, 502, 503, 504)
    methods: tuple[str, ...] = ("GET", "PUT", "DELETE", "HEAD", "OPTIONS", "POST", "PATCH")


def _user_agent() -> str:
    return "g2nc/0.1 (+https://github.com/yourorg/g2nc)"


def create_client(
    base_url: str | None = None,
    auth: httpx.Auth | None = None,
    timeout: float = 30.0,
    headers: Mapping[str, str] | None = None,
    verify: bool | str = True,
    limits: httpx.Limits | None = None,
) -> httpx.Client:
    """Create a configured httpx client.

    Notes on connection limits:
    - Keep-alive connections are pooled; conservative defaults avoid server overload.
    - Adjust via the `limits` parameter if your deployment requires more concurrency.
    """
    # Security: Prevent SSL verification bypass in production environments
    if verify is False and os.getenv("G2NC_ENVIRONMENT") == "production":
        raise ValueError(
            "SSL certificate verification cannot be disabled in production environment. "
            "Set G2NC_ENVIRONMENT to 'development' or 'test' to allow insecure connections."
        )
    
    # Log warning when SSL verification is disabled
    if verify is False:
        log.warning(
            "SSL certificate verification is DISABLED. This should only be used in development/testing. "
            "Production deployments must use verified SSL connections."
        )
    
    base_headers: MutableMapping[str, str] = {"User-Agent": _user_agent()}
    if headers:
        base_headers.update(headers)
    # Conservative defaults to prevent accidental overloads
    conn_limits = limits or httpx.Limits(max_keepalive_connections=10, max_connections=50)
    if base_url is None:
        return httpx.Client(
            auth=auth,
            timeout=timeout,
            headers=base_headers,
            verify=verify,
            http2=False,
            limits=conn_limits,
        )
    else:
        return httpx.Client(
            base_url=base_url,
            auth=auth,
            timeout=timeout,
            headers=base_headers,
            verify=verify,
            http2=False,
            limits=conn_limits,
        )


def _should_retry(
    method: str,
    status_code: int | None,
    exc: Exception | None,
    retry: RetryConfig,
) -> bool:
    if method.upper() not in retry.methods:
        return False
    if exc is not None:
        # Network/transport errors are retryable
        return True
    if status_code is None:
        return False
    return status_code in retry.status_forcelist


def _sleep_backoff(attempt: int, retry: RetryConfig) -> None:
    # attempt starts at 1
    base = retry.backoff_initial_sec * (retry.backoff_factor ** (attempt - 1))
    jitter = base * retry.jitter_frac
    delay = base + random.uniform(-jitter, jitter)
    if delay > 0:
        sleep(delay)


def request_with_retries(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, str] | None = None,
    data: str | bytes | None = None,
    json: object | None = None,
    retry: RetryConfig | None = None,
    expected: Iterable[int] = (200, 201, 204, 207),  # 207 for WebDAV multi-status
) -> httpx.Response:
    """Perform an HTTP request with retries on transient errors."""
    cfg = retry or RetryConfig()
    last_exc: Exception | None = None
    resp: httpx.Response | None = None

    for attempt in range(1, cfg.max_retries + 1):
        try:
            resp = client.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                content=data,
                json=json,
            )
            if resp.status_code in expected:
                return resp
            # If not expected, check if retryable
            if not _should_retry(method, resp.status_code, None, cfg):
                return resp
        except httpx.HTTPError as exc:
            last_exc = exc
            if not _should_retry(method, None, exc, cfg):
                raise

        if attempt < cfg.max_retries:
            _sleep_backoff(attempt, cfg)

    # Exhausted retries; if we have a response return it; else raise last_exc
    if resp is not None:
        return resp
    assert last_exc is not None
    raise last_exc


def put_with_etag(
    client: httpx.Client,
    url: str,
    body: str | bytes,
    *,
    content_type: str,
    etag: str | None = None,
    create_if_missing: bool = False,
    retry: RetryConfig | None = None,
) -> httpx.Response:
    """PUT with If-Match or If-None-Match.

    - If `etag` is provided, uses `If-Match: <etag>` (update existing).
    - If `create_if_missing` and no etag, uses `If-None-Match: *` (create-if-not-exists).
    - Otherwise sends without conditional headers.
    """
    headers = {"Content-Type": content_type}
    if etag:
        headers["If-Match"] = etag
    elif create_if_missing:
        headers["If-None-Match"] = "*"

    return request_with_retries(
        client,
        "PUT",
        url,
        headers=headers,
        data=body if isinstance(body, bytes | bytearray) else body.encode("utf-8"),
        retry=retry,
        expected=(200, 201, 204),
    )


def delete_with_etag(
    client: httpx.Client,
    url: str,
    *,
    etag: str | None = None,
    retry: RetryConfig | None = None,
) -> httpx.Response:
    """DELETE with optional If-Match ETag."""
    headers = {}
    if etag:
        headers["If-Match"] = etag
    return request_with_retries(
        client,
        "DELETE",
        url,
        headers=headers,
        retry=retry,
        expected=(200, 204),
    )
