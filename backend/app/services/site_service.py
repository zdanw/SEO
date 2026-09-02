"""站点相关业务逻辑。"""
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.client_site import ClientSite
from app.models.site_member import SiteMember
from app.models.article import Article, InternalLink
from app.models.backlink import Backlink
from app.models.competitor import Competitor
from app.models.gsc_connection import GscConnection
from app.models.keyword import Keyword
from app.models.recommendation import Recommendation
from app.models.seo_audit import SeoAudit, SeoAuditPage
from app.models.serp_rank import CompetitorRankSnapshot, SerpRankSnapshot
from app.models.social import SocialAccount, SocialPost


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
    competitor_ids = [
        row[0]
        for row in db.query(Competitor.id).filter(Competitor.site_id == site_id).all()
    ]
    article_ids = [
        row[0]
        for row in db.query(Article.id).filter(Article.site_id == site_id).all()
    ]
    audit_ids = [
        row[0]
        for row in db.query(SeoAudit.id).filter(SeoAudit.site_id == site_id).all()
    ]
    account_ids = [
        row[0]
        for row in db.query(SocialAccount.id).filter(SocialAccount.site_id == site_id).all()
    ]

    if keyword_ids:
        db.query(SerpRankSnapshot).filter(
            SerpRankSnapshot.keyword_id.in_(keyword_ids)
        ).delete(synchronize_session=False)
        db.query(CompetitorRankSnapshot).filter(
            CompetitorRankSnapshot.keyword_id.in_(keyword_ids)
        ).delete(synchronize_session=False)
    if competitor_ids:
        db.query(CompetitorRankSnapshot).filter(
            CompetitorRankSnapshot.competitor_id.in_(competitor_ids)
        ).delete(synchronize_session=False)

    if audit_ids:
        db.query(SeoAuditPage).filter(SeoAuditPage.audit_id.in_(audit_ids)).delete(
            synchronize_session=False
        )
        db.query(SeoAudit).filter(SeoAudit.id.in_(audit_ids)).delete(synchronize_session=False)

    if account_ids:
        db.query(SocialPost).filter(SocialPost.account_id.in_(account_ids)).delete(
            synchronize_session=False
        )
    if article_ids:
        db.query(SocialPost).filter(SocialPost.article_id.in_(article_ids)).delete(
            synchronize_session=False
        )
        db.query(InternalLink).filter(
            InternalLink.source_article_id.in_(article_ids)
            | InternalLink.target_article_id.in_(article_ids)
        ).delete(synchronize_session=False)

    db.query(Recommendation).filter(Recommendation.site_id == site_id).delete(
        synchronize_session=False
    )
    db.query(Article).filter(Article.site_id == site_id).delete(synchronize_session=False)
    db.query(Backlink).filter(Backlink.site_id == site_id).delete(synchronize_session=False)
    db.query(Competitor).filter(Competitor.site_id == site_id).delete(synchronize_session=False)
    db.query(Keyword).filter(Keyword.site_id == site_id).delete(synchronize_session=False)
    db.query(SocialAccount).filter(SocialAccount.site_id == site_id).delete(
        synchronize_session=False
    )
    db.query(GscConnection).filter(GscConnection.site_id == site_id).delete(
        synchronize_session=False
    )

    db.expire_all()
    db.execute(text("DELETE FROM site_members WHERE site_id = :site_id"), {"site_id": site_id})
    db.execute(text("DELETE FROM client_sites WHERE id = :site_id"), {"site_id": site_id})
