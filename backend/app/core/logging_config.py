"""产品级结构化日志。

- 生产（APP_DEBUG=false）输出 JSON 日志，便于采集到 ELK/Loki；
- 开发输出人类可读格式；
- 通过 ContextVar 透传 request_id / user_id，API→Celery 链路可用同一 ID 排查；
- AccessLogMiddleware 输出统一访问日志（含耗时），关闭 uvicorn.access 避免双写。
"""
from __future__ import annotations

import json
import logging
import sys
import time
from contextvars import ContextVar
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
user_id_var: ContextVar[str] = ContextVar("user_id", default="-")

_EXTRA_KEYS = (
    "method",
    "path",
    "status",
    "duration_ms",
    "client",
    # 阶段 0 观测埋点：campaign 调度 / AI 生成结构化指标字段（extra 传入才输出）
    "event",  # ai_call / ai_pipeline / tick_complete / tick_overlap / campaign_planned / queue_depth
    "site_id",
    "campaign_id",
    "account_id",
    "action_id",
    "elapsed_ms",
    "detail",  # dict：自由维度（rewrite_count、队列深度、成功率等），JSON 序列化输出
)


class RequestContextFilter(logging.Filter):
    """为每条日志记录注入 request_id / user_id。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.user_id = user_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
            "user_id": user_id_var.get(),
        }
        for key in _EXTRA_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_PLAIN_FORMAT = "%(asctime)s %(levelname)s [%(name)s] [req:%(request_id)s] [uid:%(user_id)s] %(message)s"


def setup_logging(*, debug: bool = False, json_output: Optional[bool] = None) -> None:
    """初始化根日志器；重复调用幂等（先清理已有 handler）。"""
    if json_output is None:
        json_output = not debug

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter() if json_output else logging.Formatter(_PLAIN_FORMAT)
    )
    handler.addFilter(RequestContextFilter())
    root.addHandler(handler)

    # access 日志由 AccessLogMiddleware 输出；uvicorn 自带格式无法注入 request_id，关闭避免双写
    for name in ("uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.propagate = False

    # 第三方库降噪
    for name in ("httpx", "httpcore", "urllib3", "sqlalchemy", "kombu"):
        logging.getLogger(name).setLevel(logging.WARNING)

    # Celery 内部心跳/任务生命周期日志保留 INFO；debug 模式下防止 root DEBUG 放大其内部细节
    logging.getLogger("celery").setLevel(logging.INFO)

    # SQL 日志：engine echo 已关闭，改由 logger 统一单行输出；debug 保留便于排查，生产仅告警以上
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if debug else logging.WARNING
    )


class AccessLogMiddleware(BaseHTTPMiddleware):
    """统一访问日志：method/path/status/耗时/客户端 IP，并尽力从 JWT 提取 user_id。"""

    async def dispatch(self, request: Request, call_next) -> Response:
        from app.core.security import decode_access_token

        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            payload = decode_access_token(auth[7:].strip())
            if payload and payload.get("sub"):
                user_id_var.set(str(payload["sub"]))

        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            client = request.client.host if request.client else "-"
            logger = logging.getLogger("app.access")
            extra = {
                "method": request.method,
                "path": request.url.path,
                "status": status,
                "duration_ms": duration_ms,
                "client": client,
            }
            if status >= 500:
                logger.error("access %s %s -> %s", request.method, request.url.path, status, extra=extra)
            elif status >= 400:
                logger.warning("access %s %s -> %s", request.method, request.url.path, status, extra=extra)
            else:
                logger.info("access %s %s -> %s", request.method, request.url.path, status, extra=extra)
