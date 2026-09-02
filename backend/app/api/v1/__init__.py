from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.keywords import router as keywords_router
from app.api.v1.articles import router as articles_router
from app.api.v1.ai_writer import router as ai_writer_router
from app.api.v1.seo_checker import router as seo_checker_router
from app.api.v1.social import router as social_router
from app.api.v1.community import router as community_router
from app.api.v1.serp import router as serp_router
from app.api.v1.backlinks import router as backlinks_router
from app.api.v1.competitors import router as competitors_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.gsc import router as gsc_router
from app.api.v1.reports import router as reports_router

api = APIRouter()

api.include_router(auth_router, prefix="/auth", tags=["认证"])
api.include_router(keywords_router, prefix="/keywords", tags=["关键词库"])
api.include_router(articles_router, prefix="/articles", tags=["文章管理"])
api.include_router(ai_writer_router, prefix="/ai", tags=["AI 写作"])
api.include_router(seo_checker_router, prefix="/seo", tags=["SEO 检查"])
api.include_router(social_router, prefix="/social", tags=["社交分发"])
api.include_router(community_router, prefix="/community", tags=["社区互动"])
api.include_router(serp_router, prefix="/serp", tags=["排名监控"])
api.include_router(backlinks_router, prefix="/backlinks", tags=["外链监控"])
api.include_router(competitors_router, prefix="/competitors", tags=["竞品对标"])
api.include_router(dashboard_router, prefix="/dashboard", tags=["数据大屏"])
api.include_router(gsc_router, prefix="/gsc", tags=["Search Console"])
api.include_router(reports_router, prefix="/reports", tags=["SEO 报告"])
