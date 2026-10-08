import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.exception_handlers import register_exception_handlers
from app.core.logging_config import AccessLogMiddleware, setup_logging

logger = logging.getLogger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """为每个请求注入/回传 X-Request-ID，便于 API→Celery 链路排查。"""

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response


def create_application() -> FastAPI:
    settings.validate_production()
    setup_logging(debug=settings.APP_DEBUG)

    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        debug=settings.APP_DEBUG,
        description="SEO 平台 - 数据监控 / Reddit 社交分发 / 排名监控",
    )

    register_exception_handlers(app)

    # add_middleware 后添加的在外层：CORS → RequestId → AccessLog → 路由
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIdMiddleware)
    cors_origins = settings.cors_origins_list()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials="*" not in cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.get("/api/health", tags=["基础"])
    async def health_check() -> dict[str, Any]:
        from app.core.health import get_health_snapshot

        return get_health_snapshot()

    from app.api.v1 import api as api_v1_router

    app.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)

    return app


app = create_application()
