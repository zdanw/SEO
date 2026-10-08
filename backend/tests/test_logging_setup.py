"""结构化日志测试：JSON 输出、请求上下文注入、幂等初始化。"""
from __future__ import annotations

import json
import logging
import sys

from app.core.logging_config import (
    JsonFormatter,
    RequestContextFilter,
    setup_logging,
    request_id_var,
    user_id_var,
)


def _record(msg: str, level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord(
        name="app.test", level=level, pathname=__file__, lineno=1,
        args=None, msg=msg, exc_info=None,
    )


def test_json_formatter_outputs_valid_json():
    token = request_id_var.set("rid-001")
    uid = user_id_var.set("42")
    try:
        formatter = JsonFormatter()
        record = _record("hello %s", logging.WARNING)
        record.args = ("world",)
        payload = json.loads(formatter.format(record))
    finally:
        request_id_var.reset(token)
        user_id_var.reset(uid)

    assert payload["message"] == "hello world"
    assert payload["level"] == "WARNING"
    assert payload["logger"] == "app.test"
    assert payload["request_id"] == "rid-001"
    assert payload["user_id"] == "42"
    assert "ts" in payload


def test_json_formatter_includes_extras_and_exc_info():
    token = request_id_var.set("rid-002")
    try:
        record = _record("access")
        record.method = "GET"
        record.path = "/api/v1/x"
        record.status = 200
        record.duration_ms = 12.3
        try:
            raise ValueError("boom")
        except ValueError:
            record.exc_info = sys.exc_info()
        payload = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)

    assert payload["method"] == "GET"
    assert payload["status"] == 200
    assert payload["duration_ms"] == 12.3
    assert "ValueError: boom" in payload["exc_info"]


def test_request_context_filter_defaults():
    record = _record("no context")
    assert RequestContextFilter().filter(record) is True
    assert record.request_id == "-"
    assert record.user_id == "-"


def test_setup_logging_replaces_handlers():
    root = logging.getLogger()
    root.addHandler(logging.NullHandler())  # 模拟已有 handler
    setup_logging(json_output=True)
    setup_logging(json_output=True)  # 幂等：不叠加
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
    # 清理：移除我们的 handler，避免影响其他测试的日志输出
    root.removeHandler(root.handlers[0])
