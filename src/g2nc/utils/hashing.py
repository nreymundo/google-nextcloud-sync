"""Deterministic normalization and hashing utilities for vCard and ICS content.

Goals
- Produce stable hashes across runs regardless of non-semantic differences:
  - Line endings (CRLF vs LF), trailing spaces
  - Folding (RFC line continuations)
  - Volatile lines (timestamps, REV/PRODID, etc.)
  - Property ordering (sorted with BEGIN/END pinned to edges)
- Keep UID lines intact to bind content to the authoritative Google ID.

Caveats
- Sorting lines breaks original block ordering but preserves a deterministic
  representation for hashing; both producer and consumer must apply the same
  normalization before hashing to compare changes safely.
- We pin BEGIN:* to the start and END:* to the end to avoid edge cases.

Public API
- normalize_vcard(text: str) -> str
- normalize_ics(text: str) -> str
- hash_vcard(text: str) -> str
- hash_ics(text: str) -> str
- sha256_hex(data: str | bytes) -> str
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from hashlib import sha256

VOLATILE_ICS_PREFIXES: Sequence[str] = (
    "DTSTAMP:",
    "CREATED:",
    "LAST-MODIFIED:",
    "SEQUENCE:",
    "PRODID:",
)
VOLATILE_VCARD_PREFIXES: Sequence[str] = (
    "REV:",
    "PRODID:",
)


# ical folding: a CRLF (or LF) followed by space or tab indicates continuation
def _unfold(text: str) -> str:
    t = text.replace("\r\n", "\n")
    lines = t.split("\n")
    out: list[str] = []
    for line in lines:
        if out and (line.startswith(" ") or line.startswith("\t")):
            out[-1] = out[-1] + line.lstrip(" \t")
        else:
            out.append(line)
    return "\n".join(out)


def _strip_volatile(lines: Iterable[str], volatile_prefixes: Sequence[str]) -> list[str]:
    filtered: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        # drop volatile lines
        if any(s.startswith(pfx) for pfx in volatile_prefixes):
            continue
        filtered.append(s)
    return filtered


def _canonicalize_properties(lines: Iterable[str]) -> list[str]:
    """Canonicalize property lines:
    - Trim surrounding whitespace
    - For 'NAME:VALUE' form, trim around both and compress internal whitespace in VALUE
    """
    out: list[str] = []
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        if ":" in s:
            name, val = s.split(":", 1)
            canon_val = " ".join(val.strip().split())
            out.append(f"{name.strip()}:{canon_val}")
        else:
            out.append(" ".join(s.split()))
    return out


def _sorted_stable(lines: Iterable[str]) -> list[str]:
    """Sort lines lexicographically with BEGIN:* first and END:* last."""

    def key_func(s: str) -> tuple[int, str]:
        if s.startswith("BEGIN:"):
            return (0, s)
        if s.startswith("END:"):
            return (2, s)
        return (1, s)

    return sorted(lines, key=key_func)


def _normalize_common(text: str, volatile_prefixes: Sequence[str]) -> str:
    t = _unfold(text).replace("\r\n", "\n")
    raw_lines = t.split("\n")
    # trim trailing spaces and drop empties
    cleaned = [ln.rstrip() for ln in raw_lines if ln.strip() != ""]
    # strip volatile
    stable = _strip_volatile(cleaned, volatile_prefixes)
    # canonicalize property formatting
    canon = _canonicalize_properties(stable)
    # sort with pinned edges
    ordered = _sorted_stable(canon)
    # final newline-free normalized string
    return "\n".join(ordered)


def normalize_vcard(text: str) -> str:
    """Normalize vCard text for stable hashing."""
    return _normalize_common(text, VOLATILE_VCARD_PREFIXES)


def normalize_ics(text: str) -> str:
    """Normalize ICS text for stable hashing."""
    return _normalize_common(text, VOLATILE_ICS_PREFIXES)


def sha256_hex(data: str | bytes) -> str:
    """Compute sha256 hex digest."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return sha256(data).hexdigest()


def hash_vcard(text: str) -> str:
    """Stable hash for vCard content (after normalization)."""
    return sha256_hex(normalize_vcard(text))


def hash_ics(text: str) -> str:
    """Stable hash for ICS content (after normalization)."""
    return sha256_hex(normalize_ics(text))
