"""FastAPI 公共依赖。"""
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.client_site import ClientSite
from app.models.site_member import SiteMember
from app.models.user import User
from app.services.site_service import ensure_default_site

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

WRITE_ROLES = {"admin", "operator"}


@dataclass
class SiteContext:
    site: ClientSite
    member: SiteMember


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """从 Bearer Token 解析当前登录用户。"""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供身份凭证",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_access_token(token)
    if not payload or not payload.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="身份凭证无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        user_id = int(payload["sub"])
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="身份凭证无效",
        )
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已被禁用",
        )
    return user


def get_site_id_header(
    x_site_id: int | None = Header(default=None, alias="X-Site-Id"),
) -> int | None:
    return x_site_id


def get_site_context(
    site_id: int | None = Depends(get_site_id_header),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SiteContext:
    """解析当前站点；未传 X-Site-Id 时自动使用用户默认站点。"""
    if not site_id:
        site, member = ensure_default_site(db, current_user.id)
        return SiteContext(site=site, member=member)

    site = db.query(ClientSite).filter(ClientSite.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")
    member = (
        db.query(SiteMember)
        .filter(SiteMember.site_id == site_id, SiteMember.user_id == current_user.id)
        .first()
    )
    if not member and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="无权访问该站点")
    if not member:
        member = SiteMember(site_id=site_id, user_id=current_user.id, role="admin")
    return SiteContext(site=site, member=member)


def require_site_write(ctx: SiteContext = Depends(get_site_context)) -> SiteContext:
    """要求具备写权限（admin / operator）。"""
    if ctx.member.role not in WRITE_ROLES:
        raise HTTPException(status_code=403, detail="只读账号无权修改数据")
    return ctx
