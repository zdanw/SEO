from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.sites import router as sites_router
from app.api.v1.keywords import router as keywords_router
from app.api.v1.social import router as social_router
from app.api.v1.serp import router as serp_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.reddit import router as reddit_router

api = APIRouter()

api.include_router(auth_router, prefix="/auth", tags=["认证"])
api.include_router(sites_router, prefix="/sites", tags=["客户站点"])
api.include_router(keywords_router, prefix="/keywords", tags=["关键词库"])
api.include_router(social_router, prefix="/social", tags=["社交分发"])
api.include_router(reddit_router, prefix="/reddit", tags=["Reddit 运营"])
api.include_router(serp_router, prefix="/serp", tags=["排名监控"])
api.include_router(dashboard_router, prefix="/dashboard", tags=["数据大屏"])
