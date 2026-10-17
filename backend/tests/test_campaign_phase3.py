"""阶段 3：数据保留清理 / config_version 影响分级 / 短窗口滚动规划。"""
from __future__ import annotations

import itertools
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.core.timeutil import now_bj
from app.models.client_site import ClientSite
from app.models.reddit import (
    RedditCampaign,
    RedditCampaignAction,
    RedditCampaignActionAttempt,
    RedditComment,
    RedditPost,
)
from app.models.social import SocialAccount
from app.services import reddit_campaign as rc

_TABLES = [
    ClientSite.__table__,
    SocialAccount.__table__,
    RedditCampaign.__table__,
    RedditCampaignAction.__table__,
    RedditCampaignActionAttempt.__table__,
    RedditPost.__table__,
    RedditComment.__table__,
]

ENQUEUE_GEN = "app.services.reddit_campaign._try_enqueue_generation"


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


def _account(db, site_id, name):
    acc = SocialAccount(
        user_id=1, site_id=site_id, platform="reddit", account_name=name, is_active=True
    )
    db.add(acc)
    db.commit()
    return acc


def _campaign(db, site_id, **kwargs):
    today = now_bj().date()
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
        subreddits=["test"],
        account_ids=[],
        content_mode="auto",
        config_version=1,
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
        config_version=campaign.config_version,
    )
    defaults.update(kwargs)
    row = RedditCampaignAction(**defaults)
    db.add(row)
    db.commit()
    return row


# ============ 数据保留清理 ============

def test_cleanup_deletes_old_terminal_actions_and_old_attempts(db, site):
    acc = _account(db, site.id, "a1")
    campaign = _campaign(db, site.id, account_ids=[acc.id])
    old = now_bj() - timedelta(days=400)
    stale = _action(db, campaign, acc, status="done", created_at=old)
    db.execute(
        RedditCampaignAction.__table__.update()
        .where(RedditCampaignAction.id == stale.id)
        .values(created_at=old, updated_at=old)
    )
    recent = _action(db, campaign, acc, status="done")
    active = _action(db, campaign, acc, status="pending")
    review = _action(db, campaign, acc, status="needs_review")
    stale_id, recent_id, active_id, review_id = stale.id, recent.id, active.id, review.id

    result = rc.cleanup_expired_campaign_data(db, action_retention_days=365, attempt_retention_days=180)

    assert result["actions_deleted"] == 1
    db.expire_all()
    ids = {a.id for a in db.query(RedditCampaignAction).all()}
    assert stale_id not in ids
    assert {recent_id, active_id, review_id} <= ids


def test_cleanup_deletes_old_attempts_but_keeps_recent(db, site):
    acc = _account(db, site.id, "a1")
    campaign = _campaign(db, site.id, account_ids=[acc.id])
    action = _action(db, campaign, acc)
    old_attempt = RedditCampaignActionAttempt(
        action_id=action.id, phase="execution", attempt_no=1,
        request_state="responded", created_at=now_bj() - timedelta(days=200),
    )
    new_attempt = RedditCampaignActionAttempt(
        action_id=action.id, phase="execution", attempt_no=2,
        request_state="responded", created_at=now_bj(),
    )
    db.add_all([old_attempt, new_attempt])
    db.commit()

    result = rc.cleanup_expired_campaign_data(db, action_retention_days=365, attempt_retention_days=180)

    assert result["attempts_deleted"] == 1
    remaining = {a.id for a in db.query(RedditCampaignActionAttempt).all()}
    assert remaining == {new_attempt.id}


def test_cleanup_zero_retention_keeps_everything(db, site):
    acc = _account(db, site.id, "a1")
    campaign = _campaign(db, site.id, account_ids=[acc.id])
    _action(db, campaign, acc, status="failed", created_at=now_bj() - timedelta(days=900))
    result = rc.cleanup_expired_campaign_data(db, action_retention_days=0, attempt_retention_days=0)
    assert result == {"attempts_deleted": 0, "actions_deleted": 0}
    assert db.query(RedditCampaignAction).count() == 1


# ============ config_version 影响分级 ============

def test_revalidate_action_account_removed(db, site):
    acc1 = _account(db, site.id, "a1")
    acc2 = _account(db, site.id, "a2")
    campaign = _campaign(db, site.id, account_ids=[acc2.id], config_version=2)
    action = _action(db, campaign, acc1, config_version=1)
    assert rc.revalidate_action_config(db, action, campaign) == (False, "config_account_removed")


def test_revalidate_post_subreddit_removed(db, site):
    acc = _account(db, site.id, "a1")
    campaign = _campaign(db, site.id, account_ids=[acc.id], subreddits=["newsub"], config_version=2)
    post = RedditPost(
        site_id=site.id, account_id=acc.id, post_type="auto", subreddit="test",
        keyword="", title="t", body="b", status="approved", campaign_id=campaign.id,
    )
    db.add(post)
    db.commit()
    action = _action(db, campaign, acc, action_type="post", post_id=post.id, config_version=1)
    assert rc.revalidate_action_config(db, action, campaign) == (False, "config_subreddit_removed")


def test_revalidate_unrelated_change_allows_execution(db, site):
    """不影响执行目标的配置变更（时段/配额等）→ 继续执行。"""
    acc = _account(db, site.id, "a1")
    campaign = _campaign(db, site.id, account_ids=[acc.id], daily_upvotes=99, config_version=5)
    action = _action(db, campaign, acc, config_version=1)
    assert rc.revalidate_action_config(db, action, campaign) == (True, None)


def test_content_gate_invalidates_content_and_action(db, site):
    acc = _account(db, site.id, "a1")
    # 配置版本 3：账号池已不含 acc（模拟账号被移出）
    campaign = _campaign(db, site.id, account_ids=[], config_version=3)
    post = RedditPost(
        site_id=site.id, account_id=acc.id, post_type="auto", subreddit="test",
        keyword="", title="t", body="b", status="approved", campaign_id=campaign.id,
    )
    db.add(post)
    db.commit()
    action = _action(db, campaign, acc, action_type="post", post_id=post.id, config_version=1)

    assert rc.revalidate_campaign_content_config(db, kind="post", item_id=post.id) is False
    db.refresh(post)
    db.refresh(action)
    assert post.status == "rejected"
    assert "config_account_removed" in (post.error_message or "")
    assert action.status == "skipped"
    assert action.skip_reason == "config_account_removed"
    assert action.result_status == "config_version_mismatch"


def test_content_gate_allows_benign_mismatch(db, site):
    acc = _account(db, site.id, "a1")
    campaign = _campaign(db, site.id, account_ids=[acc.id], daily_comments=9, config_version=4)
    post = RedditPost(
        site_id=site.id, account_id=acc.id, post_type="auto", subreddit="test",
        keyword="", title="t", body="b", status="approved", campaign_id=campaign.id,
    )
    db.add(post)
    db.commit()
    _action(db, campaign, acc, action_type="post", post_id=post.id, config_version=1)
    assert rc.revalidate_campaign_content_config(db, kind="post", item_id=post.id) is True
    db.refresh(post)
    assert post.status == "approved"


def test_content_gate_passes_non_campaign_content(db, site):
    acc = _account(db, site.id, "a1")
    post = RedditPost(
        site_id=site.id, account_id=acc.id, post_type="auto", subreddit="test",
        keyword="", title="t", body="b", status="approved", campaign_id=None,
    )
    db.add(post)
    db.commit()
    assert rc.revalidate_campaign_content_config(db, kind="post", item_id=post.id) is True


# ============ 短窗口滚动规划 ============

def test_generation_due_respects_lead_window(monkeypatch):
    monkeypatch.setattr(rc.settings, "CAMPAIGN_GENERATION_LEAD_MINUTES", 240)
    within = now_bj() + timedelta(minutes=100)
    beyond = now_bj() + timedelta(hours=10)
    assert rc._generation_due(within) is True
    assert rc._generation_due(beyond) is False
    assert rc._generation_due(None) is True
    monkeypatch.setattr(rc.settings, "CAMPAIGN_GENERATION_LEAD_MINUTES", 0)
    assert rc._generation_due(beyond) is True  # 0 = 关闭滚动窗口


def test_requeue_skips_out_of_window_actions(db, site, monkeypatch):
    """心跳重投：生成提前窗口外的骨架不投递，窗口内正常拾取。"""
    acc = _account(db, site.id, "a1")
    campaign = _campaign(db, site.id, account_ids=[acc.id])
    far = _action(
        db, campaign, acc, action_type="post", generation_status="queued",
        planned_at=now_bj() + timedelta(hours=10),
    )
    near = _action(
        db, campaign, acc, action_type="comment", generation_status="queued",
        planned_at=now_bj() + timedelta(minutes=30),
    )
    enqueue = MagicMock(return_value=True)
    monkeypatch.setattr(rc.settings, "CAMPAIGN_GENERATION_LEAD_MINUTES", 240)
    with patch(ENQUEUE_GEN, enqueue):
        requeued = rc.requeue_stale_generation_actions(db)
    assert requeued == 1
    enqueue.assert_called_once_with(near.id)
    assert far.generation_status == "queued"  # 窗口外保持 queued，等窗口临近
