from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Recommendation(Base):
    """优化建议工单（P3 告警任务写入，P4 策略推荐引擎扩展）。"""
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    site_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("client_sites.id"), nullable=True, index=True
    )
    article_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("articles.id"), nullable=True)
    keyword_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("keywords.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False)  # content/performance/social/link/crawler
    severity: Mapped[str] = mapped_column(String(20), default="info", nullable=False)  # info/warning/critical
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Recommendation #{self.id} [{self.severity}] {self.title}>"
