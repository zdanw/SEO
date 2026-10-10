"""全局异常处理器。

设计约束：前端 http.ts 统一读取 `error.response.data.detail` 展示错误，
因此所有响应体保留 `detail` 字段，并额外附加 `code` / `request_id` 供排查。

- AppException            → 业务异常，按 status_code 返回
- RequestValidationError  → 422，精简校验错误明细
- StarletteHTTPException  → 401/403/404 等，保留原 detail 语义
- Exception               → 兜底 500，生产不泄漏内部信息，服务端记录完整堆栈
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.errors import AppException

logger = logging.getLogger(__name__)

_STATUS_CODE_MAP = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    429: "rate_limited",
}

_GENERIC_500 = "Internal server error"


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


def _error_body(
    request: Request,
    detail: object,
    code: str,
    details: object = None,
) -> dict[str, object]:
    body: dict[str, object] = {
        "detail": detail,
        "code": code,
        "request_id": _request_id(request),
    }
    if details is not None:
        body["errors"] = details
    return body


def _trim_validation_errors(errors: list[dict]) -> list[dict]:
    """只保留排查所需字段，避免把完整 input 回显给客户端。"""
    return [
        {"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")}
        for e in errors
    ]


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def handle_app_exception(request: Request, exc: AppException) -> JSONResponse:
        logger.warning(
            "业务异常 %s: %s (path=%s)",
            exc.code,
            exc.message,
            request.url.path,
            extra={"method": request.method, "path": request.url.path, "status": exc.status_code},
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(request, exc.message, exc.code, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        trimmed = _trim_validation_errors(exc.errors())
        logger.warning(
            "参数校验失败 (path=%s): %s",
            request.url.path,
            trimmed,
            extra={"method": request.method, "path": request.url.path, "status": 422},
        )
        return JSONResponse(
            status_code=422,
            content=_error_body(request, trimmed, "validation_error"),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else _GENERIC_500
        code = _STATUS_CODE_MAP.get(exc.status_code, "http_error")
        log = logger.warning if exc.status_code < 500 else logger.error
        log(
            "HTTP %s: %s (path=%s)",
            exc.status_code,
            detail,
            request.url.path,
            extra={"method": request.method, "path": request.url.path, "status": exc.status_code},
        )
        headers = getattr(exc, "headers", None)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(request, detail, code),
            headers=headers,
        )

    @app.exception_handler(Exception)
    async def handle_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "未处理异常 (path=%s): %s",
            request.url.path,
            exc,
            extra={"method": request.method, "path": request.url.path, "status": 500},
        )
        # 生产环境不泄漏内部信息；调试模式返回异常描述便于开发
        detail = f"{type(exc).__name__}: {exc}" if settings.APP_DEBUG else _GENERIC_500
        response = JSONResponse(
            status_code=500,
            content=_error_body(request, detail, "internal_error"),
        )
        rid = _request_id(request)
        if rid != "-":
            response.headers["X-Request-ID"] = rid
        return response
