"""社区加权轮转 / 冷却 / 角度去重 / 频次自适应 / 反馈校准 / 扩池。"""
from __future__ import annotations

import random
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.core.timeutil import now_bj
from app.models.client_site import ClientSite
from app.models.reddit import (
    RedditBrand,
    RedditCampaign,
    RedditCampaignAction,
    RedditComment,
    RedditCommunity,
    RedditCommunityActivity,
    RedditPost,
    RedditPostMetric,
    RedditProduct,
)
from app.models.social import SocialAccount
from app.services import reddit_community_rotation as rot
from app.services.reddit_community_verify import CommunityRulesResult, CommunityVerifyResult

# 只建轮转/校准/扩池涉及的表（SQLite 不支持 JSONB 等）
_TABLES = [
    ClientSite.__table__,
    SocialAccount.__table__,
    RedditBrand.__table__,
    RedditProduct.__table__,
    RedditCommunity.__table__,
    RedditCampaign.__table__,
    RedditCampaignAction.__table__,
    RedditPost.__table__,
    RedditPostMetric.__table__,
    RedditComment.__table__,
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


def _acc(db, site_id=1, name="a1"):
    row = SocialAccount(
        user_id=1, site_id=site_id, platform="reddit", account_name=name, is_active=True
    )
    db.add(row)
    db.commit()
    return row


# ============ 内容角度（C）============
def test_pick_angle_avoids_used():
    used = list(rot.ANGLE_LIBRARY)[:3]
    picked = rot.pick_angle(used, rng=random.Random(1))
    assert picked not in used


def test_pick_angle_all_used_returns_least_recent():
    used = list(rot.ANGLE_LIBRARY)  # 最近优先
    assert rot.pick_angle(used) == used[-1]  # 最久未用


def test_pick_angle_empty_library_choice():
    assert rot.pick_angle([], rng=random.Random(0)) in rot.ANGLE_LIBRARY


def test_angle_hint_known_and_unknown():
    assert rot.angle_hint("hard_lesson") == rot.ANGLE_LIBRARY["hard_lesson"]
    assert rot.angle_hint("nope") == ""
    assert rot.angle_hint(None) == ""


def test_pick_post_type_rotates():
    used = ["pitfall"]
    picked = rot.pick_post_type(used, rng=random.Random(2))
    assert picked != "pitfall"
    # 全部用过 → 返回轮转集内最久未用
    assert rot.pick_post_type(list(rot.POST_TYPE_ROTATION)) == rot.POST_TYPE_ROTATION[-1]


# ============ 频次自适应 ============
def test_per_sub_daily_cap_scales_with_pool():
    # 配额 12 / (2 账号 × 3 社区) = 2
    assert rot.per_sub_daily_cap(daily_total=12, n_accounts=2, n_subs=3) == 2
    # 池小（1×1）收紧到 2 封顶
    assert rot.per_sub_daily_cap(daily_total=50, n_accounts=1, n_subs=1) == 2
    # 不超硬上限
    assert rot.per_sub_daily_cap(daily_total=100, n_accounts=4, n_subs=5) == 4
    # 无配额 → 不限制
    assert rot.per_sub_daily_cap(daily_total=0, n_accounts=2, n_subs=2) == 0


def test_pool_is_small():
    assert rot.pool_is_small(n_accounts=1, n_subs=1)
    assert not rot.pool_is_small(n_accounts=2, n_subs=3)


# ============ 活动账本 upsert ============
def test_record_activity_upsert_and_angle_order(db):
    acc = _acc(db)
    rot.record_activity(db, site_id=1, account_id=acc.id, subreddit="r/alpha", angle="hard_lesson")
    row = rot._activity_row(db, acc.id, "alpha")
    assert row is not None and row.action_count_7d == 1
    assert rot.recent_angles(db, acc.id, "alpha") == ["hard_lesson"]

    rot.record_activity(db, site_id=1, account_id=acc.id, subreddit="alpha", angle="ask_advice")
    rot.record_activity(db, site_id=1, account_id=acc.id, subreddit="alpha", angle="hard_lesson")
    row = rot._activity_row(db, acc.id, "alpha")
    assert row.action_count_7d == 3
    # 最近优先 + 去重
    assert rot.recent_angles(db, acc.id, "alpha") == ["hard_lesson", "ask_advice"]
    # 单账号单社区只有一行
    assert db.query(RedditCommunityActivity).count() == 1


# ============ 加权轮转 / 冷却 / 相邻去重 ============
def test_select_subreddit_skips_cooldown(db):
    acc = _acc(db)
    rot.record_activity(db, site_id=1, account_id=acc.id, subreddit="alpha")
    row = rot._activity_row(db, acc.id, "alpha")
    row.cooldown_until = now_bj() + timedelta(days=2)
    db.commit()
    picked = rot.select_subreddit(db, account_id=acc.id, candidates=["alpha", "beta"])
    assert picked == "beta"


def test_select_subreddit_fallback_when_all_cooling(db):
    acc = _acc(db)
    for name in ("alpha", "beta"):
        rot.record_activity(db, site_id=1, account_id=acc.id, subreddit=name)
        rot._activity_row(db, acc.id, name).cooldown_until = now_bj() + timedelta(days=2)
    db.commit()
    picked = rot.select_subreddit(db, account_id=acc.id, candidates=["alpha", "beta"])
    assert picked in {"alpha", "beta"}  # 宁可重复也不停摆


def test_select_subreddit_avoids_last_community(db):
    acc = _acc(db)
    rot.record_activity(db, site_id=1, account_id=acc.id, subreddit="beta")
    rot.record_activity(
        db, site_id=1, account_id=acc.id, subreddit="alpha", at=now_bj() + timedelta(minutes=5)
    )
    assert rot.last_subreddit(db, acc.id) == "alpha"
    picked = rot.select_subreddit(db, account_id=acc.id, candidates=["alpha", "beta"])
    assert picked == "beta"  # 相邻动作不落同一社区


def test_select_subreddit_prefers_less_used(db):
    acc = _acc(db)
    rot.record_activity(db, site_id=1, account_id=acc.id, subreddit="alpha")
    rot.record_activity(db, site_id=1, account_id=acc.id, subreddit="gamma")
    rot._activity_row(db, acc.id, "alpha").action_count_7d = 500
    db.commit()
    rng = random.Random(7)
    picks = [
        rot.select_subreddit(db, account_id=acc.id, candidates=["alpha", "beta"], rng=rng)
        for _ in range(100)
    ]
    assert picks.count("beta") >= 90  # 高频社区几乎不被选中


def test_select_subreddit_respects_daily_cap(db):
    acc = _acc(db)
    for _ in range(2):
        db.add(
            RedditPost(
                site_id=1, account_id=acc.id, post_type="auto", subreddit="alpha",
                keyword="", title="t", body="b", content_intent="casual",
            )
        )
    db.commit()
    picked = rot.select_subreddit(
        db, account_id=acc.id, candidates=["alpha", "beta"], daily_cap=2
    )
    assert picked == "beta"


def test_order_subreddits_dedups_and_limits(db):
    acc = _acc(db)
    ordered = rot.order_subreddits(
        db, account_id=acc.id, candidates=["a", "b", "a", "c"], limit=2, rng=random.Random(3)
    )
    assert len(ordered) == 2
    assert len(set(ordered)) == 2


# ============ 表现反馈校准（F）============
def _post_with_metric(db, acc, subreddit, score):
    post = RedditPost(
        site_id=1, account_id=acc.id, post_type="auto", subreddit=subreddit,
        keyword="", title="t", body="b", content_intent="casual",
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    db.add(RedditPostMetric(post_id=post.id, score=score))
    db.commit()


def test_apply_feedback_penalizes_low_performer(db):
    acc = _acc(db)
    _post_with_metric(db, acc, "lowq", 0)
    _post_with_metric(db, acc, "lowq", 1)
    now = now_bj()
    assert rot.apply_feedback_calibration(db, site_id=1, now=now) == 1
    row = rot._activity_row(db, acc.id, "lowq")
    assert row.penalty > 0
    assert row.cooldown_until is not None
    # 冷却期内幂等：不重复罚分
    assert rot.apply_feedback_calibration(db, site_id=1, now=now) == 0


def test_apply_feedback_ignores_healthy_and_thin_samples(db):
    acc = _acc(db)
    _post_with_metric(db, acc, "healthy", 20)
    _post_with_metric(db, acc, "healthy", 15)
    _post_with_metric(db, acc, "thin", 0)  # 样本不足
    assert rot.apply_feedback_calibration(db, site_id=1, now=now_bj()) == 0


# ============ 扩池：相似社区扩散 ============
def test_expand_similar_communities_inserts_verified(db, monkeypatch):
    acc = _acc(db)
    db.add(
        RedditCommunity(
            site_id=1, account_id=acc.id, name="seed", category="core",
            purpose="persona", is_active=True,
        )
    )
    db.commit()

    class _FakeAI:
        def chat(self, *args, **kwargs):
            return '["newone", "newtwo", "seed"]'

    import app.services.reddit_community_verify as cv

    def _fake_verify(names, *, interests, client, ai=None, search_keyword=None, now=None):
        return [
            CommunityVerifyResult(name=n, exists=True, subscribers=8000, is_active_enough=True)
            for n in names
        ]

    monkeypatch.setattr(cv, "verify_subreddits_with_feed_llm", _fake_verify)
    monkeypatch.setattr(
        cv,
        "fetch_subreddit_rules_via_zernio",
        lambda client, name: CommunityRulesResult(name=name, rules_text="be nice"),
    )

    added = rot.expand_similar_communities(
        db, site_id=1, account_id=acc.id, client=object(), ai=_FakeAI(),
        interests=["parenting"], max_new=3,
    )
    assert set(added) == {"newone", "newtwo"}  # seed 已在库中，不重复
    rows = db.query(RedditCommunity).filter(RedditCommunity.category == "longtail").all()
    assert {r.name for r in rows} == {"newone", "newtwo"}
    assert all(r.discovered_from == "seed" for r in rows)


def test_expand_similar_communities_no_seed_returns_empty(db):
    acc = _acc(db)
    assert (
        rot.expand_similar_communities(
            db, site_id=1, account_id=acc.id, client=object(), ai=None, interests=[]
        )
        == []
    )
