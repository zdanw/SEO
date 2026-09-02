import logging
from datetime import datetime
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings

logger = logging.getLogger(__name__)


def create_application() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        debug=settings.APP_DEBUG,
        description="SEO 闭环系统 - 内容创作 / 站内优化 / 社交分发 / 排名监控",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 开发环境，生产请限制域名
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health", tags=["基础"])
    async def health_check() -> dict[str, Any]:
        return {
            "status": "ok",
            "app": settings.APP_NAME,
            "env": settings.APP_ENV,
            "time": datetime.now().isoformat(),
        }

    from app.api.v1 import api as api_v1_router
    app.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)

    return app


app = create_application()
