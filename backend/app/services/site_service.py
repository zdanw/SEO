"""站点相关业务逻辑。"""
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.client_site import ClientSite
from app.models.site_member import SiteMember
from app.models.keyword import Keyword
from app.models.serp_rank import SerpRankSnapshot
from app.models.social import SocialAccount, SocialPost
from app.models.reddit import (
    RedditAccountProfile,
    RedditBrand,
    RedditComment,
    RedditCommunity,
    RedditKeyword,
    RedditPost,
    RedditPostMetric,
    RedditProduct,
)


def ensure_default_site(db: Session, user_id: int) -> tuple[ClientSite, SiteMember]:
    """获取用户默认站点；若不存在则自动创建。"""
    row = (
        db.query(ClientSite, SiteMember)
        .join(SiteMember, SiteMember.site_id == ClientSite.id)
        .filter(SiteMember.user_id == user_id)
        .order_by(ClientSite.created_at.asc())
        .first()
    )
    if row:
        return row[0], row[1]

    site = ClientSite(name="默认站点", domain="localhost", owner_user_id=user_id)
    db.add(site)
    db.flush()
    member = SiteMember(site_id=site.id, user_id=user_id, role="admin")
    db.add(member)
    db.commit()
    db.refresh(site)
    db.refresh(member)
    return site, member


def delete_client_site(db: Session, site_id: int) -> None:
    """删除客户站点及其全部关联数据。"""
    keyword_ids = [
        row[0]
        for row in db.query(Keyword.id).filter(Keyword.site_id == site_id).all()
    ]
    account_ids = [
        row[0]
        for row in db.query(SocialAccount.id).filter(SocialAccount.site_id == site_id).all()
    ]

    post_ids = [
        row[0] for row in db.query(RedditPost.id).filter(RedditPost.site_id == site_id).all()
    ]
    if post_ids:
        db.query(RedditPostMetric).filter(RedditPostMetric.post_id.in_(post_ids)).delete(
            synchronize_session=False
        )
    db.query(RedditPost).filter(RedditPost.site_id == site_id).delete(synchronize_session=False)
    db.query(RedditComment).filter(RedditComment.site_id == site_id).delete(
        synchronize_session=False
    )
    db.query(RedditAccountProfile).filter(RedditAccountProfile.site_id == site_id).delete(
        synchronize_session=False
    )
    db.query(RedditCommunity).filter(RedditCommunity.site_id == site_id).delete(
        synchronize_session=False
    )
    db.query(RedditKeyword).filter(RedditKeyword.site_id == site_id).delete(
        synchronize_session=False
    )
    brand_ids = [
        r[0]
        for r in db.query(RedditBrand.id).filter(RedditBrand.site_id == site_id).all()
    ]
    if brand_ids:
        db.query(RedditProduct).filter(RedditProduct.brand_id.in_(brand_ids)).delete(
            synchronize_session=False
        )
    db.query(RedditBrand).filter(RedditBrand.site_id == site_id).delete(
        synchronize_session=False
    )

    if keyword_ids:
        db.query(SerpRankSnapshot).filter(
            SerpRankSnapshot.keyword_id.in_(keyword_ids)
        ).delete(synchronize_session=False)

    if account_ids:
        db.query(SocialPost).filter(SocialPost.account_id.in_(account_ids)).delete(
            synchronize_session=False
        )

    db.query(Keyword).filter(Keyword.site_id == site_id).delete(synchronize_session=False)
    db.query(SocialAccount).filter(SocialAccount.site_id == site_id).delete(
        synchronize_session=False
    )

    db.expire_all()
    db.execute(text("DELETE FROM site_members WHERE site_id = :site_id"), {"site_id": site_id})
    db.execute(text("DELETE FROM client_sites WHERE id = :site_id"), {"site_id": site_id})
