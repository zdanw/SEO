"""阶段 0 容量基线报告（重构最终方案 · 阶段 0）。

基于现有表输出瓶颈排序所需的实测数据：
1. 每账号实际日动作量 / 成功率 / 429 频率分布（近 14 天）
2. 动作类型构成与整体状态分布
3. 内容生成 → 发布转化率与作废率（campaign 关联内容状态）
4. 各表行数与增长速率（近 7/30 天）
5. 失败错误分类 top（error_message 前缀聚类）
6. tick 健康度（last_tick_at 分布）

用法（conda env SEO）：
    python scripts/campaign_baseline_report.py

时间语义：database.py 逐连接 SET TIME ZONE 'Asia/Shanghai'，
所有 date()/date_trunc 均按北京墙钟日切，与配额口径一致。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.core.database import SessionLocal


def _section(title: str) -> None:
    print(f"\n{'=' * 72}\n# {title}\n{'=' * 72}")


def _rows(db, sql: str):
    result = db.execute(text(sql))
    cols = list(result.keys())
    data = result.fetchall()
    print(" | ".join(cols))
    print("-" * (min(120, max(len(" | ".join(cols)), 40))))
    for row in data:
        print(" | ".join("" if v is None else str(v) for v in row))
    if not data:
        print("(no rows)")
    return data


def main() -> int:
    db = SessionLocal()
    try:
        _section("1. 每账号日动作量 / 成功率 / 429 频率（近 14 天，按 planned_at 北京日）")
        _rows(
            db,
            """
            SELECT account_id,
                   date(planned_at)                          AS day,
                   count(*)                                  AS total,
                   count(*) FILTER (WHERE status = 'done')    AS done,
                   count(*) FILTER (WHERE status = 'failed')  AS failed,
                   count(*) FILTER (WHERE status = 'skipped') AS skipped,
                   count(*) FILTER (WHERE status = 'pending') AS pending,
                   count(*) FILTER (
                       WHERE error_message ILIKE '%429%'
                          OR error_message ILIKE '%限流%'
                          OR error_message ILIKE '%Retry in%') AS rate_limited
            FROM reddit_campaign_actions
            WHERE planned_at >= date_trunc('day', now()) - interval '13 days'
              AND planned_at IS NOT NULL
            GROUP BY account_id, day
            ORDER BY account_id, day
            """,
        )

        _section("2. 动作类型构成与状态分布（全量）")
        _rows(
            db,
            """
            SELECT action_type,
                   status,
                   count(*)                                            AS cnt,
                   round(100.0 * count(*) / sum(count(*)) OVER (PARTITION BY action_type), 1) AS pct_of_type
            FROM reddit_campaign_actions
            GROUP BY action_type, status
            ORDER BY action_type, cnt DESC
            """,
        )

        _section("3. 内容生成 → 发布转化率（campaign 关联内容状态分布）")
        _rows(
            db,
            """
            SELECT 'post' AS kind, status, count(*) AS cnt
            FROM reddit_posts WHERE campaign_id IS NOT NULL GROUP BY status
            UNION ALL
            SELECT 'comment', status, count(*)
            FROM reddit_comments WHERE campaign_id IS NOT NULL GROUP BY status
            ORDER BY kind, cnt DESC
            """,
        )

        _section("4. 表行数与增长速率")
        _rows(
            db,
            """
            SELECT 'reddit_campaign_actions' AS tbl, count(*) AS total_rows,
                   count(*) FILTER (WHERE created_at >= now() - interval '7 days')  AS last_7d,
                   count(*) FILTER (WHERE created_at >= now() - interval '30 days') AS last_30d
            FROM reddit_campaign_actions
            UNION ALL
            SELECT 'reddit_posts', count(*),
                   count(*) FILTER (WHERE created_at >= now() - interval '7 days'),
                   count(*) FILTER (WHERE created_at >= now() - interval '30 days')
            FROM reddit_posts
            UNION ALL
            SELECT 'reddit_comments', count(*),
                   count(*) FILTER (WHERE created_at >= now() - interval '7 days'),
                   count(*) FILTER (WHERE created_at >= now() - interval '30 days')
            FROM reddit_comments
            UNION ALL
            SELECT 'reddit_vote_logs', count(*),
                   count(*) FILTER (WHERE created_at >= now() - interval '7 days'),
                   count(*) FILTER (WHERE created_at >= now() - interval '30 days')
            FROM reddit_vote_logs
            ORDER BY total_rows DESC
            """,
        )

        _section("5. 失败错误分类 top（error_message 前缀 24 字符聚类）")
        _rows(
            db,
            """
            SELECT left(regexp_replace(error_message, '\\s+', ' ', 'g'), 24) AS err_bucket,
                   count(*) AS cnt
            FROM reddit_campaign_actions
            WHERE status = 'failed' AND error_message IS NOT NULL
            GROUP BY 1 ORDER BY cnt DESC LIMIT 20
            """,
        )

        _section("6. tick 健康度（running campaign 的 last_tick_at 时滞）")
        _rows(
            db,
            """
            SELECT id, site_id, name, status,
                   last_tick_at,
                   round(extract(epoch FROM (now() - last_tick_at)) / 60, 1) AS minutes_since_tick
            FROM reddit_campaigns
            WHERE status IN ('running', 'paused')
            ORDER BY minutes_since_tick DESC NULLS FIRST
            """,
        )

        _section("7. 配额与窗口利用率（running campaign 今日已排 / 配额）")
        _rows(
            db,
            """
            SELECT c.id, c.name,
                   c.daily_posts, c.daily_comments, c.daily_upvotes,
                   count(a.id) FILTER (WHERE a.action_type = 'post')            AS planned_posts,
                   count(a.id) FILTER (WHERE a.action_type = 'comment')          AS planned_comments,
                   count(a.id) FILTER (WHERE a.action_type LIKE 'upvote%')       AS planned_upvotes
            FROM reddit_campaigns c
            LEFT JOIN reddit_campaign_actions a
                   ON a.campaign_id = c.id AND a.status != 'skipped'
                  AND a.planned_at >= date_trunc('day', now())
                  AND a.planned_at <  date_trunc('day', now()) + interval '1 day'
            WHERE c.status = 'running'
            GROUP BY c.id, c.name, c.daily_posts, c.daily_comments, c.daily_upvotes
            ORDER BY c.id
            """,
        )
        print(
            "\n备注：AI 生成耗时 / 调用次数 / 重写轮次 / 超时率、tick 耗时与队列深度"
            "\n来自阶段 0 结构化日志（event=ai_call / ai_pipeline / tick_complete / queue_depth），"
            "\n部署埋点后按日志聚合，不入库。"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
