"""异步任务执行台账（可观测性）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, Session

from app.core.database import Base


class TaskRun(Base):
    __tablename__ = "task_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    site_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    business_type: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    business_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="started", nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retries: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TaskRun #{self.id} {self.task_name} {self.status}>"


def start_task_run(
    db: Session,
    *,
    task_name: str,
    celery_task_id: str | None = None,
    request_id: str | None = None,
    site_id: int | None = None,
    business_type: str | None = None,
    business_id: int | None = None,
    retries: int = 0,
    meta: dict[str, Any] | None = None,
) -> TaskRun:
    run = TaskRun(
        task_name=task_name,
        celery_task_id=celery_task_id,
        request_id=request_id,
        site_id=site_id,
        business_type=business_type,
        business_id=business_id,
        status="started",
        retries=retries,
        meta=meta,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def finish_task_run(
    db: Session,
    run: TaskRun | None,
    *,
    status: str,
    error_message: str | None = None,
    meta_update: dict[str, Any] | None = None,
) -> None:
    if run is None:
        return
    run.status = status
    run.finished_at = datetime.utcnow()
    if error_message is not None:
        run.error_message = error_message[:2000]
    if meta_update:
        base = dict(run.meta or {})
        base.update(meta_update)
        run.meta = base
    db.commit()
