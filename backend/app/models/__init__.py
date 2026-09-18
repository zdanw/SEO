# 所有 SQLAlchemy 模型在此 import，供 Alembic 自动检测
from app.models.user import User  # noqa: F401
from app.models.client_site import ClientSite  # noqa: F401
from app.models.site_member import SiteMember  # noqa: F401
from app.models.keyword import Keyword  # noqa: F401
from app.models.social import SocialAccount, SocialPost  # noqa: F401
from app.models.serp_rank import SerpRankSnapshot  # noqa: F401
from app.models.reddit import (  # noqa: F401
    RedditAccountProfile,
    RedditBrand,
    RedditComment,
    RedditCommunity,
    RedditKeyword,
    RedditPost,
    RedditPostMetric,
    RedditProduct,
)
from app.models.zernio_key import ZernioApiKey  # noqa: F401
