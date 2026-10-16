from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from typing import Generator

from app.core.config import settings


engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=False,  # SQL 日志统一走 logging（sqlalchemy.engine logger），避免 echo handler 与 root 重复输出
)


@event.listens_for(engine, "connect")
def _set_pg_session_timezone(dbapi_conn, _record):
    """强制会话时区为 Asia/Shanghai。

    全后端写入 timestamptz 的是 naive 北京墙钟字面量，其真实时刻取决于会话
    TimeZone：本地 docker PG 为 Asia/Shanghai；外部库（Supabase 等）默认 UTC，
    会把所有时间平移 8 小时（排期落半夜、到期判断漂移）。逐连接固定会话时区，
    保证任意 DATABASE_URL 下语义与本地一致。
    """
    if not settings.DATABASE_URL.startswith("postgresql"):
        return
    cursor = dbapi_conn.cursor()
    cursor.execute("SET TIME ZONE 'Asia/Shanghai'")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator:
    """FastAPI dependency: 获取 DB session，请求结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
