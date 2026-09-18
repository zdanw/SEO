from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Keyword(Base):
    __tablename__ = "keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    site_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("client_sites.id"), nullable=True, index=True
    )
    keyword: Mapped[str] = mapped_column(String(200), nullable=False)
    target_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    search_engine: Mapped[str] = mapped_column(String(20), default="google", nullable=False)
    region: Mapped[str] = mapped_column(String(10), default="us", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Keyword #{self.id} {self.keyword!r}>"
