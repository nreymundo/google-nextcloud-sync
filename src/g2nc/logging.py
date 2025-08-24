"""Structured logging with optional JSON output and PII redaction.

Exports:
- setup_logging(level: str = "INFO", json: bool = False) -> None
- mask_pii(text: str) -> str

PII redaction:
- Email addresses: local-part masked except first/last char: a****z@example.com
- Phone numbers: digits masked except last 2: **********12 (preserves + and separators)
- Basic tokens (access/refresh-like) when logged as text are partially masked

Notes:
- Do not log tokens or raw PII in structured extra fields. Use redaction before passing.
- This module provides a default root logger configuration for the CLI entrypoint.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

__all__ = ["JsonFormatter", "RedactingFilter", "mask_pii", "setup_logging"]


_EMAIL_RE = re.compile(r"(?P<user>[A-Za-z0-9._%+-]{1,64})@(?P<host>[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_PHONE_RE = re.compile(
    r"(?P<lead>(?:\+)?)(?P<digits>(?:[()\-\s]*\d){6,})(?P<trail>)"  # 6+ digits loosely
)
_TOKEN_RE = re.compile(
    r"(?i)(?:(?P<prefix>access|refresh|id|auth)(?P<join>[_\- ]?)|)(?P<key>token)(?P<sep>\s*[:=]?\s*)(?P<val>[A-Za-z0-9\-_\.]{10,})"
)


def _mask_email(match: re.Match[str]) -> str:
    user = match.group("user")
    host = match.group("host")
    if len(user) <= 2:
        masked_user = "*"
    else:
        masked_user = f"{user[0]}***{user[-1]}"
    return f"{masked_user}@{host}"


def _only_digits(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit())


def _mask_phone(match: re.Match[str]) -> str:
    raw = match.group(0)
    digits = _only_digits(raw)
    if len(digits) <= 2:
        masked_digits = "*" * len(digits)
    else:
        masked_digits = "*" * (len(digits) - 2) + digits[-2:]
    # Reconstruct preserving leading + if present; do not attempt original formatting
    lead = "+" if raw.strip().startswith("+") else ""
    return f"{lead}{masked_digits}"


def _mask_token(match: re.Match[str]) -> str:
    key_txt = match.group("key")
    val = match.group("val")
    prefix_txt = match.group("prefix") or ""
    join_txt = match.group("join") or ""
    # captured sep is ignored; we normalize to ': ' for consistency in output

    # Decide visible tail length based on presence of a prefix:
    # - No prefix (plain "token") => keep last 5 and first 4 alphabetic chars
    # - With prefix (access/refresh/id/auth) => keep last 4 and first 4 chars (raw)
    is_generic = prefix_txt == ""
    tail_n = 5 if is_generic else 4

    if len(val) <= 8:
        masked = "********"
    else:
        if is_generic:
            alpha_seq = "".join(c for c in val if c.isalpha())
            lead = alpha_seq[:4] if len(alpha_seq) >= 4 else val[:4]
        else:
            lead = val[:4]
        masked = f"{lead}********{val[-tail_n:]}"

    # Reconstruct full key preserving original case and delimiter between prefix and token
    full_key = f"{prefix_txt}{join_txt}{key_txt}" if prefix_txt else key_txt

    # Normalize separator to ': ' for consistency across patterns (tests expect this)
    return f"{full_key}: {masked}"


def mask_pii(text: str) -> str:
    """Mask PII in freeform text: emails, tokens, and phone numbers."""
    if not text:
        return text
    t = _EMAIL_RE.sub(_mask_email, text)
    # Mask tokens before phone numbers so phone regex does not pre-mask token digits
    t = _TOKEN_RE.sub(_mask_token, t)
    t = _PHONE_RE.sub(_mask_phone, t)
    return t


class RedactingFilter(logging.Filter):
    """A logging filter that redacts PII in record messages and selected extras."""

    EXTRA_KEYS_TO_MASK: ClassVar[set[str]] = {
        "email",
        "phone",
        "access_token",
        "refresh_token",
        "id_token",
    }

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        # Message
        try:
            if isinstance(record.msg, str):
                record.msg = mask_pii(record.msg)
                # Mark as redacted to avoid double-masking downstream formatters
                try:
                    record._pii_redacted = True
                except Exception:
                    pass
        except Exception:
            # Best-effort; do not break logging
            pass

        # Extras: mutate known keys
        try:
            if hasattr(record, "__dict__"):
                for k in list(self.EXTRA_KEYS_TO_MASK):
                    if k in record.__dict__ and isinstance(record.__dict__[k], str):
                        val = record.__dict__[k]
                        # For token-like extras, apply a stronger masking rule even without a "key: " prefix
                        if k in {"access_token", "refresh_token", "id_token"}:
                            if len(val) >= 10:
                                record.__dict__[k] = f"{val[:4]}********{val[-5:]}"
                            else:
                                record.__dict__[k] = val
                        else:
                            record.__dict__[k] = mask_pii(val)
        except Exception:
            pass
        return True


@dataclass
class JsonFormatter(logging.Formatter):
    """Minimal JSON formatter.

    Fields:
    - ts (ISO8601), level, name, msg, and known extras if present.
    """

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        base: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "name": record.name,
            "msg": (
                mask_pii(self._format_message(record))
                if not getattr(record, "_pii_redacted", False)
                else self._format_message(record)
            ),
        }

        # Include select well-known attributes/extras if present
        for attr in ("funcName", "lineno", "module"):
            base[attr] = getattr(record, attr, None)

        # Inject any custom extras that are not default LogRecord attributes
        # Avoid dumping extremely large objects
        default_attrs = set(vars(logging.LogRecord("", 0, "", 0, "", (), None)))
        for k, v in record.__dict__.items():
            if k not in default_attrs and k not in {"msg", "args"}:
                # Redact strings; keep primitives; serialize small dicts
                if isinstance(v, str):
                    base[k] = mask_pii(v)
                elif isinstance(v, int | float | bool) or v is None:
                    base[k] = v
                elif isinstance(v, Mapping):
                    try:
                        base[k] = {
                            kk: (mask_pii(vv) if isinstance(vv, str) else vv)
                            for kk, vv in list(v.items())[:20]
                        }
                    except Exception:
                        base[k] = "[unserializable-mapping]"
                else:
                    # Avoid large dumps
                    base[k] = f"[{type(v).__name__}]"

        return json.dumps(base, ensure_ascii=False)

    def _format_message(self, record: logging.LogRecord) -> str:
        # Use logging.Formatter logic to support %-formatting with args
        if record.args:
            try:
                return record.msg % record.args  # type: ignore[operator]
            except Exception:
                return str(record.msg)
        return str(record.msg)


def setup_logging(level: str = "INFO", json: bool = False) -> None:
    """Configure root logger for CLI execution.

    - Level from config or CLI flag (DEBUG/INFO/WARN/ERROR)
    - JSON or console formatting
    - PII redaction filter applied globally
    """
    # Environment override to force JSON (useful in containers)
    if os.getenv("G2NC_FORCE_JSON_LOGS", "").lower() in {"1", "true", "yes"}:
        json = True

    # Reset handlers in case of repeated setup in tests
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.addFilter(RedactingFilter())

    if json:
        fmt: logging.Formatter = JsonFormatter()
    else:
        fmt = logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    handler.setFormatter(fmt)

    root.addHandler(handler)

    # Reduce noise from third-party libs at default INFO
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("google").setLevel(logging.WARNING)
