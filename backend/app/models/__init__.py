# 所有 SQLAlchemy 模型在此 import，供 Alembic 自动检测
from app.models.user import User  # noqa: F401
from app.models.client_site import ClientSite  # noqa: F401
from app.models.site_member import SiteMember  # noqa: F401
from app.models.keyword import Keyword  # noqa: F401
from app.models.article import Article, InternalLink  # noqa: F401
from app.models.social import SocialAccount, SocialPost  # noqa: F401
from app.models.competitor import Competitor  # noqa: F401
from app.models.backlink import Backlink  # noqa: F401
from app.models.serp_rank import SerpRankSnapshot, CompetitorRankSnapshot  # noqa: F401
from app.models.recommendation import Recommendation  # noqa: F401
from app.models.gsc_connection import GscConnection  # noqa: F401
from app.models.seo_audit import SeoAudit, SeoAuditPage  # noqa: F401
