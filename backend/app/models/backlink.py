from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, Text, Boolean, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Backlink(Base):
    """外链记录。"""
    __tablename__ = "backlinks"
    __table_args__ = (
        UniqueConstraint("target_url", "source_url", name="uq_backlinks_target_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    site_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("client_sites.id"), nullable=True, index=True
    )
    target_url: Mapped[str] = mapped_column(String(500), nullable=False)
    source_url: Mapped[str] = mapped_column(String(500), nullable=False)
    anchor_text: Mapped[str | None] = mapped_column(String(300), nullable=True)
    domain_authority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_alive: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lost_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Backlink #{self.id} {self.source_url} -> {self.target_url}>"
