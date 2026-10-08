"""Reddit 自动化 Campaign 调度引擎单元测试。"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.client_site import ClientSite
from app.models.reddit import (
    RedditAccountProfile,
    RedditBrand,
    RedditCampaign,
    RedditCampaignAction,
    RedditComment,
    RedditCommunity,
    RedditPost,
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
    RedditPost.__table__,
    RedditComment.__table__,
    RedditVoteLog.__table__,
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


def _action(db, campaign, account, **kwargs):
    defaults = dict(
        campaign_id=campaign.id,
        site_id=campaign.site_id,
        account_id=account.id,
        action_type="upvote_related",
        status="pending",
        planned_at=datetime.utcnow(),
        dedup_key=f"upvote:{account.id}:t3_x{datetime.utcnow().microsecond}",
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
    quota = rc._quota_used(db, campaign.id, datetime.utcnow())
    assert quota == {"posts": 1, "comments": 1, "upvotes": 1}


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
def test_plan_time_respects_min_gap(db, site):
    acc = _account(db, site.id, "a1")
    now = datetime.utcnow()
    future = now + timedelta(minutes=5)
    _action(db, _campaign(db, site.id), acc, status="pending", planned_at=future)
    planned = rc._plan_time(db, acc.id, now, 0, 23)
    assert planned >= future + timedelta(minutes=rc.MIN_ACTION_GAP_MINUTES)


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


# ============ 执行：claim / vote / 对账 ============
def test_claim_action_is_atomic(db, site):
    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    action = _action(db, campaign, acc)
    assert rc.claim_action(db, action.id) is True
    assert db.get(RedditCampaignAction, action.id).status == "executing"
    assert rc.claim_action(db, action.id) is False  # 二次抢占失败


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
    action = _action(db, campaign, acc, target_thing_id="t3_r3")
    client = MagicMock()
    client.vote.side_effect = rc.RedditApiError("still limited", status_code=429, retry_after=60)
    with patch.object(rc, "get_reddit_client_for_account", return_value=client):
        out = rc.execute_action(db, action.id, attempt=2)
    assert out["ok"] is False
    refreshed = db.get(RedditCampaignAction, action.id)
    assert refreshed.status == "failed"
    assert "still limited" in refreshed.error_message


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
def _patch_generation(title="T", body="b"):
    return patch(
        "app.services.reddit_content.generate_post_content",
        return_value={"title": title, "body": body, "keyword": "", "site_url": None, "intent": "casual", "subreddit": "test"},
    )


def test_tick_generates_auto_post_with_schedule(db, site):
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id)
    campaign = _campaign(db, site.id, daily_posts=1, content_mode="auto", account_ids=[acc.id])
    with _patch_generation():
        summary = rc.run_campaign_tick(db)
    assert summary["posts"] == 1
    post = db.query(RedditPost).filter_by(campaign_id=campaign.id).one()
    assert post.status == "approved"
    assert post.scheduled_at is not None
    action = db.query(RedditCampaignAction).filter_by(campaign_id=campaign.id, action_type="post").one()
    assert action.status == "pending"
    assert campaign.last_tick_at is not None


def test_tick_review_mode_goes_to_pending_review(db, site):
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id)
    campaign = _campaign(db, site.id, daily_posts=1, content_mode="review", account_ids=[acc.id])
    with _patch_generation():
        rc.run_campaign_tick(db)
    post = db.query(RedditPost).filter_by(campaign_id=campaign.id).one()
    assert post.status == "pending_review"
    assert post.scheduled_at is None


def test_tick_respects_daily_quota(db, site):
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id)
    campaign = _campaign(db, site.id, daily_posts=1, content_mode="auto", account_ids=[acc.id])
    acc2 = _account(db, site.id, "a2")
    _action(db, campaign, acc2, action_type="post", status="pending", dedup_key="post:9:9:d")
    with _patch_generation():
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
    with _patch_generation():
        summary = rc.run_campaign_tick(db)
    assert summary["campaigns"] == 0
    assert summary["posts"] == 0
