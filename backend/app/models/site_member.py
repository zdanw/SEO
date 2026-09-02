from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class SiteMember(Base):
    """客户站点成员与角色。"""

    __tablename__ = "site_members"
    __table_args__ = (
        UniqueConstraint("site_id", "user_id", name="uq_site_members_site_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    site_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("client_sites.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    # admin / operator / client_viewer
    role: Mapped[str] = mapped_column(String(20), default="operator", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )

    site: Mapped["ClientSite"] = relationship(back_populates="members")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SiteMember site={self.site_id} user={self.user_id} role={self.role}>"
