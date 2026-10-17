"""Reddit 自动化 Campaign 调度引擎单元测试。"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.core.timeutil import now_bj
from app.models.client_site import ClientSite
from app.models.reddit import (
    RedditAccountProfile,
    RedditBrand,
    RedditCampaign,
    RedditCampaignAction,
    RedditCampaignActionAttempt,
    RedditComment,
    RedditCommunity,
    RedditCommunityActivity,
    RedditPost,
    RedditPostMetric,
    RedditProduct,
    RedditVoteLog,
)
from app.models.social import SocialAccount
from app.services import reddit_campaign as rc
from app.services.reddit_client import RedditActionRetryable, reddit_retry_delay

# task_runs 等表使用 JSONB，SQLite 无法渲染；测试只建所需表
_TABLES = [
    ClientSite.__table__,
    SocialAccount.__table__,
    RedditAccountProfile.__table__,
    RedditBrand.__table__,
    RedditProduct.__table__,
    RedditCommunity.__table__,
    RedditCampaign.__table__,
    RedditCampaignAction.__table__,
    RedditCampaignActionAttempt.__table__,
    RedditPost.__table__,
    RedditPostMetric.__table__,
    RedditComment.__table__,
    RedditVoteLog.__table__,
    RedditCommunityActivity.__table__,
]

@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    session = sessionmaker(bind=engine, autoflush=False)()
    Base.metadata.create_all(engine, tables=_TABLES)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def site(db):
    row = ClientSite(owner_user_id=1, name="S", domain="example.com")
    db.add(row)
    db.commit()
    return row


def _account(db, site_id, name, is_active=True):
    acc = SocialAccount(
        user_id=1, site_id=site_id, platform="reddit", account_name=name, is_active=is_active
    )
    db.add(acc)
    db.commit()
    return acc


def _profile(db, site_id, account_id, risk_status="normal"):
    row = RedditAccountProfile(
        site_id=site_id,
        account_id=account_id,
        risk_status=risk_status,
        stage="ready",  # warmup_week1_2 会触发禁发帖风控
        daily_post_limit=3,
        daily_comment_limit=10,
    )
    db.add(row)
    db.commit()
    return row


def _campaign(db, site_id, **kwargs):
    today = datetime.utcnow().date()
    defaults = dict(
        site_id=site_id,
        name="c1",
        status="running",
        start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=1),
        daily_posts=0,
        daily_comments=0,
        daily_upvotes=0,
        self_upvote_enabled=False,
        self_upvote_delay_minutes=240,
        subreddits=["test"],
        account_ids=[],
        content_mode="auto",
    )
    defaults.update(kwargs)
    row = RedditCampaign(**defaults)
    db.add(row)
    db.commit()
    return row


# dedup_key 用自增序号：utcnow().microsecond 在 Windows 时钟粒度下会重复导致唯一约束误撞
_dedup_seq = itertools.count()


def _action(db, campaign, account, **kwargs):
    defaults = dict(
        campaign_id=campaign.id,
        site_id=campaign.site_id,
        account_id=account.id,
        action_type="upvote_related",
        status="pending",
        planned_at=now_bj(),
        dedup_key=f"upvote:{account.id}:t3_x{next(_dedup_seq)}",
    )
    defaults.update(kwargs)
    row = RedditCampaignAction(**defaults)
    db.add(row)
    db.commit()
    return row


# ============ 活跃时段 ============
def test_in_active_window_and_clamp():
    assert rc._in_active_window(10, 8, 22)
    assert not rc._in_active_window(5, 8, 22)
    # 跨午夜窗口 20-4
    assert rc._in_active_window(22, 20, 4)
    assert rc._in_active_window(2, 20, 4)
    assert not rc._in_active_window(10, 20, 4)


def test_next_active_time_clamps_into_window():
    now = datetime(2026, 9, 29, 10, 30)
    assert rc._next_active_time(now, 8, 22) == now
    # 窗口前 → 今日窗口起点
    early = datetime(2026, 9, 29, 5, 0)
    assert rc._next_active_time(early, 8, 22) == datetime(2026, 9, 29, 8, 0)
    # 窗口后 → 次日窗口起点
    late = datetime(2026, 9, 29, 23, 30)
    assert rc._next_active_time(late, 8, 22) == datetime(2026, 9, 30, 8, 0)
    # 跨午夜：白天 → 今晚 20:00
    day = datetime(2026, 9, 29, 10, 0)
    assert rc._next_active_time(day, 20, 4) == datetime(2026, 9, 29, 20, 0)


# ============ 配额与去重 ============
def test_quota_used_counts_and_excludes_skipped(db, site):
    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    _action(db, campaign, acc, action_type="post", status="done", dedup_key="post:1:1:d1")
    _action(db, campaign, acc, action_type="post", status="skipped", dedup_key="post:1:1:d2")
    _action(db, campaign, acc, action_type="comment", status="pending", dedup_key="c1")
    _action(db, campaign, acc, action_type="upvote_related", status="pending", dedup_key="u1")
    quota = rc._quota_used(db, campaign.id, now_bj())
    assert quota == {"posts": 1, "comments": 1, "upvotes": 1}


def test_quota_used_counts_by_planned_date(db, site):
    """配额按 planned_at 归属日期：昨天创建但排到今天的计入今天；排到明天的计入明天。"""
    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    now = now_bj()
    _action(
        db, campaign, acc, action_type="post", status="pending",
        created_at=now - timedelta(days=1), planned_at=now + timedelta(hours=1), dedup_key="p_old",
    )
    _action(
        db, campaign, acc, action_type="upvote_related", status="pending",
        created_at=now, planned_at=now + timedelta(days=1), dedup_key="u_tomorrow",
    )
    quota = rc._quota_used(db, campaign.id, now)
    assert quota == {"posts": 1, "comments": 0, "upvotes": 0}


def test_insert_action_dedup_unique_per_campaign(db, site):
    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    first = rc._insert_action(
        db, campaign_id=campaign.id, site_id=site.id, account_id=acc.id,
        action_type="upvote_related", status="pending",
        planned_at=datetime.utcnow(), dedup_key="upvote:1:t3_x",
    )
    assert first is not None
    dup = rc._insert_action(
        db, campaign_id=campaign.id, site_id=site.id, account_id=acc.id,
        action_type="upvote_related", status="pending",
        planned_at=datetime.utcnow(), dedup_key="upvote:1:t3_x",
    )
    assert dup is None  # 同 campaign 同 dedup 被唯一约束拦截
    other = _campaign(db, site.id, name="c2")
    cross = rc._insert_action(
        db, campaign_id=other.id, site_id=site.id, account_id=acc.id,
        action_type="upvote_related", status="pending",
        planned_at=datetime.utcnow(), dedup_key="upvote:1:t3_x",
    )
    assert cross is not None  # 不同 campaign 允许相同 dedup


# ============ 账号池 ============
def test_pool_accounts_filters_risk_and_inactive(db, site):
    normal = _account(db, site.id, "ok")
    warned = _account(db, site.id, "warn")
    inactive = _account(db, site.id, "off", is_active=False)
    _profile(db, site.id, normal.id, "normal")
    _profile(db, site.id, warned.id, "warning")
    campaign = _campaign(db, site.id, account_ids=[normal.id, warned.id, inactive.id])
    pool = rc._pool_accounts(db, campaign)
    assert [a.id for a in pool] == [normal.id]


# ============ 排期随机化 ============
def test_plan_time_respects_min_gap_both_sides(db, site):
    """间隔双向校验：新动作与已排动作前后都保持 ≥25 分钟，可排在空档而非只顺延。"""
    acc = _account(db, site.id, "a1")
    campaign = _campaign(db, site.id)
    now = datetime(2026, 10, 9, 8, 0)
    gap = timedelta(minutes=rc.MIN_ACTION_GAP_MINUTES)
    # 已排动作 8:05，窗口起点 8:00 之前无空档 → 新动作必然顺延到 8:30 之后
    future = now + timedelta(minutes=5)
    _action(db, campaign, acc, status="pending", planned_at=future, dedup_key="future")
    planned = rc._plan_time(db, acc.id, now, 0, 23)
    assert planned >= future + gap
    # 23:00 再排一条：[8:30, 22:35] 与 [23:25 后] 空档均可落点（双向校验通过）
    late = datetime(2026, 10, 9, 23, 0)
    _action(db, campaign, acc, status="pending", planned_at=late, dedup_key="late")
    planned2 = rc._plan_time(db, acc.id, now, 0, 23)
    assert planned2 >= late + gap or planned2 <= late - gap
    assert planned2 >= future + gap


def test_plan_time_returns_next_window_when_today_full(db, site):
    """今日窗口空档被已排动作占满时，返回下一个窗口起点（跨天由调用方跳过）。"""
    acc = _account(db, site.id, "a1")
    campaign = _campaign(db, site.id)
    now = datetime(2026, 10, 9, 14, 0)
    # 每 25-30 分钟一条，占满整个 14-16:59 窗口
    for i, planned in enumerate(
        (14, 40, 70, 100, 130, 160)  # 距 14:00 的分钟数
    ):
        _action(
            db, campaign, acc, status="pending",
            planned_at=now + timedelta(minutes=planned), dedup_key=f"fill{i}",
        )
    t = rc._plan_time(db, acc.id, now, 14, 16)
    # 今日无空档 → 顺延到次日窗口内随机取点
    assert datetime(2026, 10, 10, 14, 0) <= t <= datetime(2026, 10, 10, 16, 59, 59)


# ============ 自赞：voter != author ============
def test_self_upvote_requires_other_account(db, site):
    a1 = _account(db, site.id, "a1")
    a2 = _account(db, site.id, "a2")
    _profile(db, site.id, a1.id)
    _profile(db, site.id, a2.id)
    campaign = _campaign(
        db, site.id, self_upvote_enabled=True, daily_upvotes=5, account_ids=[a1.id, a2.id]
    )
    post = RedditPost(
        site_id=site.id, account_id=a1.id, post_type="auto", subreddit="test",
        keyword="", title="t", body="b", status="posted", reddit_post_id="t3_mine",
        published_at=datetime.utcnow() - timedelta(minutes=60),
    )
    db.add(post)
    db.commit()

    planned = rc._plan_self_upvotes(db, campaign, [a1, a2], datetime.utcnow(), 3, 0, 23)
    assert planned == 1
    action = db.query(RedditCampaignAction).filter_by(action_type="upvote_self").one()
    assert action.account_id == a2.id  # 不能自己赞自己
    assert action.target_thing_id == "t3_mine"


def test_self_upvote_skips_single_account_pool(db, site):
    a1 = _account(db, site.id, "solo")
    _profile(db, site.id, a1.id)
    campaign = _campaign(db, site.id, self_upvote_enabled=True, daily_upvotes=3, account_ids=[a1.id])
    assert rc._plan_self_upvotes(db, campaign, [a1], datetime.utcnow(), 3, 0, 23) == 0


def test_self_upvote_respects_existing_votes(db, site):
    a1 = _account(db, site.id, "a1")
    a2 = _account(db, site.id, "a2")
    _profile(db, site.id, a1.id)
    _profile(db, site.id, a2.id)
    campaign = _campaign(db, site.id, self_upvote_enabled=True, daily_upvotes=5, account_ids=[a1.id, a2.id])
    post = RedditPost(
        site_id=site.id, account_id=a1.id, post_type="auto", subreddit="test",
        keyword="", title="t", body="b", status="posted", reddit_post_id="t3_mine",
        published_at=datetime.utcnow() - timedelta(minutes=60),
    )
    db.add(post)
    db.commit()
    db.add(RedditVoteLog(site_id=site.id, account_id=a2.id, thing_id="t3_mine", direction=1))
    db.commit()
    assert rc._plan_self_upvotes(db, campaign, [a1, a2], datetime.utcnow(), 3, 0, 23) == 0


def test_self_upvote_skips_existing_dedup_action(db, site):
    """已计划过同键自赞动作（pending）时不再重复排期：dedup_key 按候选 IN 反查命中。"""
    a1 = _account(db, site.id, "a1")
    a2 = _account(db, site.id, "a2")
    _profile(db, site.id, a1.id)
    _profile(db, site.id, a2.id)
    campaign = _campaign(db, site.id, self_upvote_enabled=True, daily_upvotes=5, account_ids=[a1.id, a2.id])
    post = RedditPost(
        site_id=site.id, account_id=a1.id, post_type="auto", subreddit="test",
        keyword="", title="t", body="b", status="posted", reddit_post_id="t3_mine",
        published_at=datetime.utcnow() - timedelta(minutes=60),
    )
    db.add(post)
    db.commit()
    _action(
        db, campaign, a2, action_type="upvote_self", status="pending",
        target_thing_id="t3_mine", dedup_key=f"upvote_self:{a2.id}:{post.id}",
    )
    assert rc._plan_self_upvotes(db, campaign, [a1, a2], datetime.utcnow(), 3, 0, 23) == 0


# ============ 执行：claim / vote / 对账 ============
def test_claim_action_is_atomic(db, site):
    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    action = _action(db, campaign, acc)
    gen = rc.claim_action(db, action.id)
    assert gen == 1  # 首次领取，执行代次递增到 1
    claimed = db.get(RedditCampaignAction, action.id)
    assert claimed.status == "executing"
    assert claimed.lease_owner is not None
    assert claimed.lease_until is not None
    assert rc.claim_action(db, action.id) is None  # 二次抢占失败
    # attempts 预写与领取同事务：有一条 request_pending 尝试记录
    attempts = (
        db.query(RedditCampaignActionAttempt)
        .filter_by(action_id=action.id, phase="execution")
        .all()
    )
    assert len(attempts) == 1
    assert attempts[0].attempt_no == 1
    assert attempts[0].request_state == "request_pending"


def test_execute_action_votes_and_upserts_vote_log(db, site):
    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id)
    action = _action(db, campaign, acc, target_thing_id="t3_xyz")
    # 预置旧投票记录（direction=-1）→ upsert 覆盖
    db.add(RedditVoteLog(site_id=site.id, account_id=acc.id, thing_id="t3_xyz", direction=-1, source="manual"))
    db.commit()

    client = MagicMock()
    with patch.object(rc, "get_reddit_client_for_account", return_value=client):
        out = rc.execute_action(db, action.id)
    assert out["ok"] is True
    client.vote.assert_called_once_with("t3_xyz", 1)
    refreshed = db.get(RedditCampaignAction, action.id)
    assert refreshed.status == "done"
    assert refreshed.executed_at is not None
    log = db.query(RedditVoteLog).filter_by(account_id=acc.id, thing_id="t3_xyz").one()
    assert log.direction == 1
    assert log.source == "campaign"
    assert log.campaign_action_id == action.id


def test_execute_action_fails_on_api_error(db, site):
    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id)
    action = _action(db, campaign, acc, target_thing_id="t3_xyz")
    client = MagicMock()
    client.vote.side_effect = rc.RedditApiError("boom")
    with patch.object(rc, "get_reddit_client_for_account", return_value=client):
        out = rc.execute_action(db, action.id)
    assert out["ok"] is False
    assert db.get(RedditCampaignAction, action.id).status == "failed"
    assert db.query(RedditVoteLog).count() == 0


# ============ 限流自动重试 ============
def test_retry_delay_classification():
    assert reddit_retry_delay(rc.RedditApiError("x", status_code=429, retry_after=10)) == 30
    assert reddit_retry_delay(rc.RedditApiError("x", status_code=429, retry_after=120)) == 120
    assert reddit_retry_delay(rc.RedditApiError("x", status_code=503)) == 620
    assert reddit_retry_delay(rc.RedditApiError("x", status_code=503, retry_after=5000)) == 900
    assert reddit_retry_delay(rc.RedditApiError("x", status_code=400)) is None
    assert reddit_retry_delay(rc.RedditApiError("x", status_code=504)) is None


def test_execute_action_rate_limited_resets_to_pending(db, site):
    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id)
    action = _action(db, campaign, acc, target_thing_id="t3_r1")
    client = MagicMock()
    client.vote.side_effect = rc.RedditApiError("too fast", status_code=429, retry_after=10)
    with patch.object(rc, "get_reddit_client_for_account", return_value=client):
        with pytest.raises(RedditActionRetryable) as exc_info:
            rc.execute_action(db, action.id)
    assert exc_info.value.retry_after >= 30
    refreshed = db.get(RedditCampaignAction, action.id)
    assert refreshed.status == "pending"
    assert refreshed.planned_at is not None and refreshed.planned_at > datetime.utcnow()
    assert "限流(429)" in refreshed.error_message
    assert db.query(RedditVoteLog).count() == 0


def test_execute_action_breaker_open_waits_half_open_window(db, site):
    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id)
    action = _action(db, campaign, acc, target_thing_id="t3_r2")
    client = MagicMock()
    client.vote.side_effect = rc.RedditApiError("breaker open", status_code=503)
    with patch.object(rc, "get_reddit_client_for_account", return_value=client):
        with pytest.raises(RedditActionRetryable) as exc_info:
            rc.execute_action(db, action.id)
    assert exc_info.value.retry_after >= 600  # 熔断 open 600s，等半开窗口


def test_execute_action_retries_exhausted_marks_failed(db, site):
    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id)
    action = _action(db, campaign, acc, target_thing_id="t3_r3", execution_max_attempts=1)
    client = MagicMock()
    client.vote.side_effect = rc.RedditApiError("still limited", status_code=429, retry_after=60)
    with patch.object(rc, "get_reddit_client_for_account", return_value=client):
        out = rc.execute_action(db, action.id, attempt=2)
    assert out["ok"] is False
    refreshed = db.get(RedditCampaignAction, action.id)
    assert refreshed.status == "failed"
    assert "still limited" in refreshed.error_message
    assert refreshed.result_status == "confirmed_absent"


def test_execute_action_skips_risk_warning_account(db, site):
    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id, risk_status="warning")
    action = _action(db, campaign, acc, target_thing_id="t3_xyz")
    client = MagicMock()
    with patch.object(rc, "get_reddit_client_for_account", return_value=client):
        out = rc.execute_action(db, action.id)
    assert out["ok"] is False
    assert out["reason"] == "account_risk_warning"
    client.vote.assert_not_called()
    refreshed = db.get(RedditCampaignAction, action.id)
    assert refreshed.status == "skipped"
    assert refreshed.skip_reason == "account_risk_warning"


def test_reconcile_marks_done_failed_skipped(db, site):
    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")

    def _post(status, error=None):
        row = RedditPost(
            site_id=site.id, account_id=acc.id, post_type="auto", subreddit="test",
            keyword="", title="t", body="b", status=status, error_message=error,
        )
        db.add(row)
        db.commit()
        return row

    posted = _post("posted")
    failed = _post("failed", error="rate limit")
    rejected = _post("rejected")
    a_done = _action(db, campaign, acc, action_type="post", post_id=posted.id, dedup_key="p1")
    a_fail = _action(db, campaign, acc, action_type="post", post_id=failed.id, dedup_key="p2")
    a_skip = _action(db, campaign, acc, action_type="post", post_id=rejected.id, dedup_key="p3")

    changed = rc.reconcile_campaign_actions(db, campaign.id)
    assert changed == {"done": 1, "failed": 1, "skipped": 1}
    assert db.get(RedditCampaignAction, a_done.id).status == "done"
    a_fail_row = db.get(RedditCampaignAction, a_fail.id)
    assert a_fail_row.status == "failed"
    assert "rate limit" in (a_fail_row.error_message or "")
    assert db.get(RedditCampaignAction, a_skip.id).skip_reason == "content_rejected"


# ============ tick 主流程 ============
def _patch_generation_enqueue():
    """阶段 2：tick 只落骨架并投递 generation 队列；屏蔽真实 broker 入队。"""
    return patch("app.services.reddit_campaign._try_enqueue_generation", return_value=True)


def test_tick_plans_skeleton_post_without_content(db, site):
    """阶段 2：tick 落骨架动作（queued），AI 生成出环，不产生内容行。"""
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id)
    campaign = _campaign(db, site.id, daily_posts=1, content_mode="auto", account_ids=[acc.id])
    with _patch_generation_enqueue():
        summary = rc.run_campaign_tick(db)
    assert summary["posts"] == 1
    # 骨架动作：pending + generation queued，无内容行
    action = db.query(RedditCampaignAction).filter_by(campaign_id=campaign.id, action_type="post").one()
    assert action.status == "pending"
    assert action.generation_status == "queued"
    assert action.post_id is None
    assert db.query(RedditPost).filter_by(campaign_id=campaign.id).count() == 0
    assert campaign.last_tick_at is not None


def test_tick_review_mode_also_plans_skeleton(db, site):
    """review 模式：tick 同样只落骨架（内容待审状态由生成完成时决定）。"""
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id)
    campaign = _campaign(db, site.id, daily_posts=1, content_mode="review", account_ids=[acc.id])
    with _patch_generation_enqueue():
        rc.run_campaign_tick(db)
    action = db.query(RedditCampaignAction).filter_by(campaign_id=campaign.id, action_type="post").one()
    assert action.status == "pending"
    assert action.generation_status == "queued"
    assert db.query(RedditPost).filter_by(campaign_id=campaign.id).count() == 0


def test_tick_respects_daily_quota(db, site):
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id)
    campaign = _campaign(db, site.id, daily_posts=1, content_mode="auto", account_ids=[acc.id])
    acc2 = _account(db, site.id, "a2")
    _action(db, campaign, acc2, action_type="post", status="pending", dedup_key="post:9:9:d")
    with _patch_generation_enqueue():
        summary = rc.run_campaign_tick(db)
    assert summary["posts"] == 0  # 当日配额已满，不再生成
    assert db.query(RedditPost).filter_by(campaign_id=campaign.id).count() == 0


def test_tick_ignores_out_of_range_campaign(db, site):
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id)
    today = datetime.utcnow().date()
    campaign = _campaign(
        db, site.id, daily_posts=1, content_mode="auto", account_ids=[acc.id],
        start_date=today + timedelta(days=5), end_date=today + timedelta(days=6),
    )
    with _patch_generation_enqueue():
        summary = rc.run_campaign_tick(db)
    assert summary["campaigns"] == 0
    assert summary["posts"] == 0


# ============ 每日一轮：排期不跨天 ============
def test_tick_skips_planning_when_window_passed(db, site):
    """今日活跃窗口已过：不再排今天的配额，明天由明天自己的 tick 排满一轮。"""
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id)
    campaign = _campaign(
        db, site.id, daily_posts=1, content_mode="auto",
        account_ids=[acc.id], active_hour_start=14, active_hour_end=16,
    )
    stats = rc._tick_campaign(db, campaign, datetime(2026, 10, 9, 17, 0))
    assert stats == {"posts": 0, "comments": 0, "upvotes": 0, "skipped": 0}
    assert db.query(RedditPost).filter_by(campaign_id=campaign.id).count() == 0
    assert db.query(RedditCampaignAction).filter_by(campaign_id=campaign.id).count() == 0


def test_tick_plans_into_free_slot_before_tail_action(db, site):
    """窗口尾部已有 pending 动作时，新动作排进其之前的空档，而不是被顶出窗口饿死。"""
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id)
    campaign = _campaign(
        db, site.id, daily_posts=1, content_mode="auto",
        account_ids=[acc.id], active_hour_start=14, active_hour_end=16,
    )
    # 同账号已有 pending 动作占住窗口尾（16:50）→ 空档为 [14:00, 16:25]
    _action(db, campaign, acc, planned_at=datetime(2026, 10, 9, 16, 50), dedup_key="u_tail")
    with _patch_generation_enqueue():
        stats = rc._tick_campaign(db, campaign, datetime(2026, 10, 9, 14, 0))
    assert stats["posts"] == 1
    post_action = (
        db.query(RedditCampaignAction)
        .filter_by(campaign_id=campaign.id, action_type="post")
        .one()
    )
    assert datetime(2026, 10, 9, 14, 0) <= post_action.planned_at <= datetime(2026, 10, 9, 16, 25)


def test_tick_skips_when_window_slots_exhausted(db, site):
    """今日窗口空档被已排动作占满：不再排今天的配额（明天由明天 tick 补足）。"""
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id)
    campaign = _campaign(
        db, site.id, daily_posts=1, content_mode="auto",
        account_ids=[acc.id], active_hour_start=14, active_hour_end=16,
    )
    # 每 25-30 分钟一条 pending，占满 14-16:59 窗口
    for i, minutes in enumerate((14, 40, 70, 100, 130, 160)):
        _action(
            db, campaign, acc,
            planned_at=datetime(2026, 10, 9, 14, 0) + timedelta(minutes=minutes),
            dedup_key=f"fill{i}",
        )
    stats = rc._tick_campaign(db, campaign, datetime(2026, 10, 9, 14, 0))
    assert stats["posts"] == 0
    assert db.query(RedditPost).filter_by(campaign_id=campaign.id).count() == 0


def test_each_day_gets_fresh_quota_round(db, site):
    """昨日已执行的配额不占用今天：第二天能重新排满一轮（每日执行一轮）。"""
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id)
    campaign = _campaign(db, site.id, daily_posts=1, content_mode="auto", account_ids=[acc.id])
    now = now_bj()
    _action(
        db, campaign, acc, action_type="post", status="done",
        planned_at=now - timedelta(days=1), dedup_key="post:yesterday",
    )
    with _patch_generation_enqueue():
        stats = rc._tick_campaign(db, campaign, now)
    assert stats["posts"] == 1
    action = (
        db.query(RedditCampaignAction)
        .filter_by(campaign_id=campaign.id, action_type="post", dedup_key="post:yesterday")
        .one()
    )
    assert action.status == "done"  # 昨天的动作保持不变，新增的是今天的一轮


# ============ 暂停语义 / 自动结束 / 发布门控 ============
def test_as_bj_naive_converts_aware_and_keeps_naive():
    from datetime import timezone

    from app.core.timeutil import as_bj_naive

    naive = datetime(2026, 10, 9, 15, 0)
    assert as_bj_naive(naive) is naive  # naive 原样（视为北京墙钟）
    aware_utc = datetime(2026, 10, 9, 7, 0, tzinfo=timezone.utc)
    assert as_bj_naive(aware_utc) == datetime(2026, 10, 9, 15, 0)  # UTC→北京墙钟
    assert as_bj_naive(aware_utc).tzinfo is None


def test_plan_time_handles_aware_planned_at_from_db(monkeypatch):
    """Postgres timestamptz 读回带 tzinfo，_plan_time 不得因 naive/aware 比较崩溃。"""
    from datetime import timezone

    now = datetime(2026, 10, 9, 8, 0)
    aware_last = (now + timedelta(hours=2)).replace(tzinfo=timezone(timedelta(hours=8)))
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [(aware_last,)]
    t = rc._plan_time(db, 1, now, 0, 23)
    assert t.tzinfo is None
    # 双向间隔：与已有 pending 动作保持 ≥25 分钟（此场景只能落在其之前空档）
    last_naive = aware_last.replace(tzinfo=None)
    assert t <= last_naive - timedelta(minutes=25) or t >= last_naive + timedelta(minutes=25)


def test_plan_time_overflows_to_next_window_when_no_slot():
    """窗口内空档被已排动作占满时，顺延到下一个窗口内取点。"""
    last = datetime(2026, 10, 9, 14, 40)  # 同账号 pending 占住 13-14:59 窗口的剩余空档
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [(last,)]
    now = datetime(2026, 10, 9, 14, 15)  # 窗口 13-14，剩余 14:15-14:59
    t = rc._plan_time(db, 1, now, 13, 14)
    # 14:40±25min 覆盖整个剩余窗口 → 顺延到次日窗口内随机取点
    assert t.date() == datetime(2026, 10, 10).date()
    assert 13 <= t.hour <= 14


def test_due_upvote_excludes_non_running_campaign(db, site):
    """暂停/结束 campaign 的到期点赞动作不再发放，启动后自然恢复。"""
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id)
    running = _campaign(db, site.id, name="run", account_ids=[acc.id])
    paused = _campaign(db, site.id, name="paused", status="paused", account_ids=[acc.id])
    a_run = _action(db, running, acc, target_thing_id="t3_run", dedup_key="u_run")
    _action(db, paused, acc, target_thing_id="t3_paused", dedup_key="u_paused")

    due = rc.due_upvote_action_ids(db)
    assert due == [a_run.id]


def test_run_campaign_tick_auto_ends_expired(db, site):
    """end_date 已过的 running campaign 自动置 ended，不再计划动作。"""
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id)
    today = datetime.utcnow().date()
    campaign = _campaign(
        db, site.id, daily_posts=1, content_mode="auto", account_ids=[acc.id],
        start_date=today - timedelta(days=3), end_date=today - timedelta(days=2),
    )
    with _patch_generation_enqueue():
        summary = rc.run_campaign_tick(db)
    assert summary["campaigns"] == 0
    assert summary["posts"] == 0
    assert db.get(RedditCampaign, campaign.id).status == "ended"


def test_publish_scan_skips_paused_campaign_content(db, site, monkeypatch):
    """发布扫描：暂停 campaign 的到期内容不发放；普通内容与 running campaign 内容正常入队。"""
    from app.tasks import reddit_tasks as rt

    acc = _account(db, site.id, "a1")
    running = _campaign(db, site.id, name="run")
    paused = _campaign(db, site.id, name="paused", status="paused")
    now = datetime.utcnow()

    def _post(campaign_id, title):
        return RedditPost(
            site_id=site.id, account_id=acc.id, post_type="auto", subreddit="test",
            keyword="", title=title, body="b", status="approved",
            campaign_id=campaign_id, scheduled_at=now,
        )

    p_run = _post(running.id, "t1")
    p_paused = _post(paused.id, "t2")
    p_normal = _post(None, "t3")
    db.add_all([p_run, p_paused, p_normal])
    db.flush()
    # 阶段 2 门控：campaign 内容必须有 generation ready 的骨架动作才可发放
    db.add(RedditCampaignAction(
        campaign_id=running.id, site_id=site.id, account_id=acc.id,
        action_type="post", status="pending", planned_at=now,
        dedup_key="post:run", post_id=p_run.id, generation_status="ready",
    ))
    db.commit()
    run_id, normal_id, paused_id = p_run.id, p_normal.id, p_paused.id

    monkeypatch.setattr(rt, "SessionLocal", lambda: db)
    enqueued: list[tuple[str, int]] = []
    monkeypatch.setattr(
        rt, "try_enqueue_publish", lambda kind, item_id, task: enqueued.append((kind, item_id)) or True
    )

    rt.publish_scheduled_reddit_content.run()
    # 任务 finally 会 close 会话，ORM 对象随后不可访问，故仅用预取的 ID 断言
    assert sorted(enqueued) == [("post", run_id), ("post", normal_id)]
    assert paused_id not in [i for _, i in enqueued]
