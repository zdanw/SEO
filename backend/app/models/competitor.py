from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Competitor(Base):
    """竞品域名配置。"""
    __tablename__ = "competitors"
    __table_args__ = (
        UniqueConstraint("site_id", "domain", name="uq_competitors_site_domain"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    site_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("client_sites.id"), nullable=True, index=True
    )
    domain: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Competitor #{self.id} {self.domain}>"
