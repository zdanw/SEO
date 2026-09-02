from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class GscConnection(Base):
    """Google Search Console OAuth 连接（每客户站点一条）。"""

    __tablename__ = "gsc_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    site_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("client_sites.id"), nullable=True, unique=True, index=True
    )
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    site_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    google_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    oauth_code_verifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_state_nonce: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<GscConnection user={self.user_id} site={self.site_url}>"
