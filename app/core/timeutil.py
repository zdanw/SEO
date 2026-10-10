"""统一时间语义：全链路北京时间墙钟。

DB session 时区为 Asia/Shanghai（docker-compose TZ），timestamptz 列写入 naive
墙钟时按 +08 解释。全系统统一用北京时间墙钟（naive），保证：
- 写入 = 北京墙钟的真实时刻
- SQL 比较（naive 参数按 +08 解释）语义一致
- 前端读回 +08:00 渲染即北京时间

注意：JWT（security.py）与 Reddit epoch 换算等纯内存 aware-UTC 计算不受影响。
"""
from datetime import datetime, timedelta, timezone

_BJ = timezone(timedelta(hours=8), "Asia/Shanghai")


def now_bj() -> datetime:
    """当前北京时间墙钟（naive，无 tzinfo）。"""
    return datetime.now(_BJ).replace(tzinfo=None)


def from_epoch_bj(ts: float) -> datetime:
    """Unix epoch → 北京时间墙钟（naive）。"""
    return datetime.fromtimestamp(ts, _BJ).replace(tzinfo=None)


def as_bj_naive(dt: datetime) -> datetime:
    """任意 datetime → 北京时间墙钟（naive），统一可与 now_bj() 比较。

    - aware（如 Postgres timestamptz 读回 +08）：换算到北京墙钟后剥掉 tzinfo
    - naive：视为北京墙钟原样返回（与写入语义一致）
    """
    if dt.tzinfo is not None:
        return dt.astimezone(_BJ).replace(tzinfo=None)
    return dt
