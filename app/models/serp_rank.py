from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SerpRankSnapshot(Base):
    """SERP 排名快照 - TimescaleDB hypertable。

    复合主键 (time, keyword_id) 满足 hypertable 对唯一约束必须含分区列的要求。
    不设自增 id：查询模式是按时间范围 + keyword_id，无单行引用需求。
    """
    __tablename__ = "serp_rank_snapshots"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, nullable=False)
    keyword_id: Mapped[int] = mapped_column(Integer, ForeignKey("keywords.id"), primary_key=True, nullable=False)
    target_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    serp_features: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    search_region: Mapped[str] = mapped_column(String(10), default="us", nullable=False)
    proxy_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    crawl_status: Mapped[str] = mapped_column(String(20), default="success", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SerpRankSnapshot kw={self.keyword_id} rank={self.rank} at={self.time}>"
