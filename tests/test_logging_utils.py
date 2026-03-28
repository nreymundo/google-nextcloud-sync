from __future__ import annotations

import io
import json
import logging
from typing import cast

from g2nc.logging_utils import JsonFormatter, configure_logging


def test_json_formatter_includes_extra_fields() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="g2nc.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.mapping = "work"

    payload = json.loads(formatter.format(record))

    assert payload["message"] == "hello"
    assert payload["mapping"] == "work"
    assert payload["level"] == "INFO"


def test_configure_logging_plain_text() -> None:
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    stream = io.StringIO()

    try:
        configure_logging("INFO", use_json=False)
        assert len(root.handlers) == 1
        handler = cast(logging.StreamHandler[io.StringIO], root.handlers[0])
        handler.stream = stream
        logging.getLogger("g2nc.test").info("plain log")
        assert "plain log" in stream.getvalue()
    finally:
        root.handlers.clear()
        root.handlers.extend(original_handlers)
        root.setLevel(original_level)
