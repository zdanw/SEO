"""阶段 1 可靠性 PostgreSQL 集成测试（重构最终方案 §4 测试表）。

SQLite 无法覆盖 SKIP LOCKED、并发事务与迁移行为——本文件在真实 PostgreSQL 上
执行（默认 127.0.0.1:5432，seo 用户；无可用 PG 时自动跳过）。

覆盖场景：
1. 两个 worker 并发领取同一动作 → 仅一个获得租约（代次单调递增）
2. 批量领取 FOR UPDATE SKIP LOCKED → 并发 worker 不重叠
3. 领取后崩溃、租约过期 + 请求未发出 → failed（confirmed_absent）
4. 请求发出后、响应回写前强杀 → reconciling → upvote 对账 → needs_review
5. fencing：A 租约过期、B 重新领取、A 迟到回写 → 旧代次被拒
6. 暂停 campaign 与领取竞态 → 执行前校验停止（回 pending，不伪装终态）
7. 内容 + 台账原子提交：dedup 冲突 → 无孤儿内容
8. attempts 表唯一约束
"""
from __future__ import annotations

import os
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.core.timeutil import now_bj
import app.models  # noqa: F401 — 导入完整模型注册表（FK 依赖全量建表）
from app.models.client_site import ClientSite
from app.models.reddit import (
    RedditAccountProfile,
    RedditCampaign,
    RedditCampaignAction,
    RedditCampaignActionAttempt,
    RedditPost,
    RedditVoteLog,
)
from app.models.social import SocialAccount
from app.models.user import User
from app.services import reddit_campaign as rc

PG_HOST = os.environ.get("PG_TEST_HOST", "127.0.0.1")
PG_PORT = os.environ.get("PG_TEST_PORT", "5432")
PG_USER = os.environ.get("PG_TEST_USER", "seo")
PG_PASS = os.environ.get("PG_TEST_PASS", "seo_dev_pass")
TEST_DB = "seo_test_campaign_reliability"


def _pg_available() -> bool:
    try:
        probe = create_engine(
            f"postgresql+psycopg2://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/postgres",
            connect_args={"connect_timeout": 3},
        )
        with probe.connect():
            probe.dispose()
            return True
    except Exception:  # noqa: BLE001
        return False


PG_SKIP = pytest.mark.skipif(
    not _pg_available(), reason=f"PostgreSQL {PG_HOST}:{PG_PORT} 不可用"
)


@pytest.fixture(scope="module")
def pg_session_factory():
    admin = create_engine(
        f"postgresql+psycopg2://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/postgres",
        isolation_level="AUTOCOMMIT",
    )
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}"'))
        conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    admin.dispose()

    url = f"postgresql+psycopg2://{PG_USER}:{PG_PASS}@{PG_HOST}:{PG_PORT}/{TEST_DB}"
    engine = create_engine(url, pool_pre_ping=True)

    @event.listens_for(engine, "connect")
    def _set_tz(dbapi_conn, _record):  # 与 database.py 同约定：北京墙钟语义
        cur = dbapi_conn.cursor()
        cur.execute("SET TIME ZONE 'Asia/Shanghai'")
        cur.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}"'))
    admin.dispose()


@pytest.fixture()
def db(pg_session_factory):
    session = pg_session_factory()
    try:
        yield session
    finally:
        session.rollback()
        # 全表 TRUNCATE（CASCADE 处理 FK 依赖），保持表结构供下一用例使用
        tables = session.execute(
            text(
                "SELECT coalesce(string_agg(format('%I', tablename), ', '), 'public.alembic_version') "
                "FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        ).scalar()
        session.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
        session.commit()
        session.close()


@pytest.fixture()
def site(db):
    user = User(email="pg-test@example.com", hashed_password="x")
    db.add(user)
    db.commit()
    s = ClientSite(owner_user_id=user.id, name="pg-test-site", domain="example.com")
    db.add(s)
    db.commit()
    return s


def _campaign(db, site_id: int) -> RedditCampaign:
    today = now_bj().date()
    c = RedditCampaign(
        site_id=site_id,
        name="pg-campaign",
        status="running",
        content_mode="auto",
        daily_posts=1,
        daily_comments=2,
        daily_upvotes=3,
        start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=7),
    )
    db.add(c)
    db.commit()
    return c


def _account(db, site_id: int, name: str) -> SocialAccount:
    a = SocialAccount(
        user_id=1, site_id=site_id, platform="reddit", account_name=name, is_active=True
    )
    db.add(a)
    db.commit()
    return a


def _profile(db, site_id: int, account_id: int) -> None:
    db.add(
        RedditAccountProfile(
            site_id=site_id,
            account_id=account_id,
            risk_status="normal",
            stage="ready",
        )
    )
    db.commit()


def _action(
    db,
    campaign: RedditCampaign,
    account: SocialAccount,
    *,
    thing_id: str = "t3_abc",
    **kw,
) -> RedditCampaignAction:
    a = RedditCampaignAction(
        campaign_id=campaign.id,
        site_id=campaign.site_id,
        account_id=account.id,
        action_type="upvote_related",
        status="pending",
        planned_at=now_bj() - timedelta(minutes=5),
        target_thing_id=thing_id,
        dedup_key=f"upvote:{account.id}:{thing_id}",
    )
    for k, v in kw.items():
        setattr(a, k, v)
    db.add(a)
    db.commit()
    return a


@PG_SKIP
def test_pg_concurrent_claim_single_winner(db, site):
    """场景 1：两个 worker 并发领取同一动作 → 仅一个获得租约。"""
    from sqlalchemy.orm import sessionmaker

    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    action = _action(db, campaign, acc)

    s2 = sessionmaker(bind=db.get_bind(), expire_on_commit=False)()
    gen_a = rc.claim_action(db, action.id, worker_id="worker-A")
    gen_b = rc.claim_action(s2, action.id, worker_id="worker-B")
    assert gen_a == 1
    assert gen_b is None
    db.expire_all()  # 同 session 原生 SQL 更新对 identity map 不可见
    refreshed = db.get(RedditCampaignAction, action.id)
    assert refreshed.status == "executing"
    assert refreshed.lease_owner == "worker-A"
    s2.close()


@PG_SKIP
def test_pg_batch_claim_skip_locked_no_overlap(db, site):
    """场景 2：批量领取 FOR UPDATE SKIP LOCKED → 并发 worker 各领各的。"""
    from sqlalchemy.orm import sessionmaker

    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    for i in range(4):
        _action(db, campaign, acc, thing_id=f"t3_b{i}")

    s2 = sessionmaker(bind=db.get_bind(), expire_on_commit=False)()
    batch_a = rc.claim_due_actions_batch(db, limit=10)
    batch_b = rc.claim_due_actions_batch(s2, limit=10)
    ids_a = {x["action_id"] for x in batch_a}
    ids_b = {x["action_id"] for x in batch_b}
    assert len(batch_a) + len(batch_b) == 4
    assert not (ids_a & ids_b), "SKIP LOCKED 失效：两个 worker 领到了同一动作"
    s2.close()


@PG_SKIP
def test_pg_recover_lease_expired_request_not_sent(db, site):
    """场景 3：领取后崩溃、租约过期且请求未发出 → failed（confirmed_absent，可重试）。"""
    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    action = _action(db, campaign, acc)
    gen = rc.claim_action(db, action.id, worker_id="dead-worker")
    assert gen == 1
    # 模拟租约到期（worker 崩溃，未发出请求：request_started_at 保持 NULL）
    db.execute(
        text(
            "UPDATE reddit_campaign_actions SET lease_until = :past WHERE id = :id"
        ),
        {"past": now_bj() - timedelta(minutes=1), "id": action.id},
    )
    db.commit()
    counts = rc.recover_stale_actions(db)
    assert counts["failed"] == 1 and counts["reconciling"] == 0
    db.expire_all()  # 同 session 原生 SQL 更新对 identity map 不可见
    refreshed = db.get(RedditCampaignAction, action.id)
    assert refreshed.status == "failed"
    assert refreshed.result_status == "confirmed_absent"
    attempt = (
        db.query(RedditCampaignActionAttempt)
        .filter_by(action_id=action.id, phase="execution", attempt_no=1)
        .one()
    )
    assert attempt.request_state == "responded"
    assert attempt.error_code == "lease_expired_not_sent"


@PG_SKIP
def test_pg_recover_request_sent_goes_reconciling_then_needs_review(db, site):
    """场景 4：请求发出后、响应回写前强杀 → reconciling → upvote 无权威查询 → needs_review。"""
    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id)
    action = _action(db, campaign, acc)
    gen = rc.claim_action(db, action.id, worker_id="dead-worker")
    assert rc._mark_request_started(db, action.id, gen) is True
    # 强杀点：请求已发出，响应永远没有回写
    db.execute(
        text("UPDATE reddit_campaign_actions SET lease_until = :past WHERE id = :id"),
        {"past": now_bj() - timedelta(minutes=1), "id": action.id},
    )
    db.commit()
    counts = rc.recover_stale_actions(db)
    assert counts["reconciling"] == 1 and counts["failed"] == 0
    db.expire_all()  # 同 session 原生 SQL 更新对 identity map 不可见
    assert db.get(RedditCampaignAction, action.id).status == "reconciling"

    # 对账分流：upvote 无权威投票查询 → 无法可靠判断 → needs_review（禁止凭目标帖存在推断成功）
    rc.reconcile_uncertain_actions(db)
    db.expire_all()  # 同 session 原生 SQL 更新对 identity map 不可见
    refreshed = db.get(RedditCampaignAction, action.id)
    assert refreshed.status == "needs_review"
    assert refreshed.result_status == "unknown"


@PG_SKIP
def test_pg_fencing_rejects_stale_generation_write(db, site):
    """场景 6：A 租约过期、B 重新领取、A 迟到回写 → fencing 校验拒绝旧代次。"""
    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id)
    action = _action(db, campaign, acc)
    gen_a = rc.claim_action(db, action.id, worker_id="worker-A")
    assert gen_a == 1
    # A 租约过期后，恢复路径把动作放回 pending，B 重新领取
    db.execute(
        text(
            "UPDATE reddit_campaign_actions SET status='pending', lease_until=NULL, lease_owner=NULL "
            "WHERE id=:id"
        ),
        {"id": action.id},
    )
    db.commit()
    gen_b = rc.claim_action(db, action.id, worker_id="worker-B")
    assert gen_b == 2
    # A（旧代次）迟到回写 → 拒绝；B 状态不被覆盖
    assert rc._finish_executing(db, action.id, gen_a, "done") is False
    assert db.get(RedditCampaignAction, action.id).status == "executing"
    # B（当前代次）回写 → 接受
    assert rc._finish_executing(
        db, action.id, gen_b, "done", result_status="confirmed_success", external_object_id="t3_abc"
    ) is True
    db.expire_all()  # 同 session 原生 SQL 更新对 identity map 不可见
    refreshed = db.get(RedditCampaignAction, action.id)
    assert refreshed.status == "done"
    assert refreshed.lease_owner is None


@PG_SKIP
def test_pg_pause_race_stops_before_request(db, site):
    """场景 7：暂停 campaign 与领取竞态 → 发出前校验为停止边界（回 pending）。"""
    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id)
    action = _action(db, campaign, acc)
    # 竞态注入：任务领取动作后、外部请求发出前，campaign 被暂停——
    # execute_action 内部的发出前校验点（claim 成功 → campaign 状态检查）必须停止
    campaign.status = "paused"
    db.commit()
    out = rc.execute_action(db, action.id, worker_id="worker-A")
    assert out.get("reason") == "campaign_not_running"
    db.expire_all()  # 同 session 原生 SQL 更新对 identity map 不可见
    refreshed = db.get(RedditCampaignAction, action.id)
    # 不伪装终态：回 pending 等恢复后重取
    assert refreshed.status == "pending"
    assert refreshed.result_status == "confirmed_absent"
    attempt = (
        db.query(RedditCampaignActionAttempt)
        .filter_by(action_id=action.id, phase="execution", attempt_no=1)
        .one()
    )
    assert attempt.request_state == "responded"
    assert attempt.error_code == "paused_before_request"
    # 请求从未发出
    assert attempt.request_started_at is None


@PG_SKIP
def test_pg_dedup_conflict_no_orphan_action(db, site):
    """场景 8（阶段 2 骨架语义）：dedup 冲突 → 规划返回 None，无重复动作、无内容行。

    新架构下 dedup 冲突发生在骨架规划时（内容尚未生成），天然无孤儿内容——
    原子性从"内容+台账同提交"前移为"骨架先行，内容在生成完成事务"。
    """
    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    planned_at = now_bj().replace(minute=0, second=0, microsecond=0)
    dedup_key = f"post:{campaign.id}:{acc.id}:{planned_at:%Y%m%d}"
    # 已存在同 dedup_key 的动作（前一轮 tick 已排）
    _action(db, campaign, acc, thing_id=None, dedup_key=dedup_key, action_type="post")
    actions_before = db.query(RedditCampaignAction).filter_by(campaign_id=campaign.id).count()

    out = rc._create_campaign_post(db, campaign, acc, planned_at)
    assert out is None
    # 无重复动作行、无内容行
    assert (
        db.query(RedditCampaignAction).filter_by(campaign_id=campaign.id).count() == actions_before
    )
    assert db.query(RedditPost).filter_by(campaign_id=campaign.id).count() == 0


@PG_SKIP
def test_pg_generation_claim_single_winner(db, site):
    """场景 10（阶段 2）：生成领取原子性——同动作重复领取只赢一次，代次单调。"""
    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    action = RedditCampaignAction(
        campaign_id=campaign.id,
        site_id=campaign.site_id,
        account_id=acc.id,
        action_type="post",
        status="pending",
        planned_at=now_bj(),
        dedup_key=f"post:{campaign.id}:{acc.id}:{now_bj():%Y%m%d}",
        generation_status="queued",
    )
    db.add(action)
    db.commit()

    gen_a = rc._claim_generate(db, action.id, "gen-worker-A", now_bj(), 20)
    assert gen_a == 1
    db.expire_all()
    assert db.get(RedditCampaignAction, action.id).generation_status == "generating"
    # 有效租约内重复投递 → 不可领取（not_claimable）
    assert rc._claim_generate(db, action.id, "gen-worker-B", now_bj(), 20) is None
    # attempts 预写
    attempt = (
        db.query(RedditCampaignActionAttempt)
        .filter_by(action_id=action.id, phase="generation", attempt_no=1)
        .one()
    )
    assert attempt.request_state == "request_pending"


@PG_SKIP
def test_pg_generation_complete_atomic_writeback(db, site, monkeypatch):
    """场景 11（阶段 2）：生成完成事务——内容行 + post_id 回填 + ready 原子提交。"""
    import app.services.reddit_community_rotation as rotation
    import app.services.reddit_content as content_mod

    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    action = RedditCampaignAction(
        campaign_id=campaign.id,
        site_id=campaign.site_id,
        account_id=acc.id,
        action_type="post",
        status="pending",
        planned_at=now_bj(),
        dedup_key=f"post:{campaign.id}:{acc.id}:{now_bj():%Y%m%d}",
        generation_status="queued",
    )
    db.add(action)
    db.commit()
    # 领取由 generate_action_content 内部完成（request_started_at 亦由内部写入）
    generated = {
        "keyword": "k",
        "title": "t",
        "body": "b",
        "site_url": None,
        "intent": "experience",
        "angle": "experience",
        "ai_risk": "human",
        "ai_score": 10,
    }
    monkeypatch.setattr(rc, "_target_subreddits", lambda *a, **k: ["r/test"])
    monkeypatch.setattr(rc, "_pool_accounts", lambda *a, **k: [acc])
    monkeypatch.setattr(rc, "_ai_budget_available", lambda *a, **k: (True, 40))
    monkeypatch.setattr(rc, "_ai_budget_consume", lambda *a, **k: None)
    monkeypatch.setattr(rotation, "select_subreddit", lambda *a, **k: "r/test")
    monkeypatch.setattr(rotation, "pick_post_type_weighted", lambda *a, **k: "experience")
    monkeypatch.setattr(rotation, "pick_angle_weighted", lambda *a, **k: "experience")
    monkeypatch.setattr(rotation, "recent_angles", lambda *a, **k: [])
    monkeypatch.setattr(rotation, "record_activity", lambda *a, **k: None)
    monkeypatch.setattr(rotation, "per_sub_daily_cap", lambda *a, **k: 1)
    monkeypatch.setattr(rotation, "pool_is_small", lambda *a, **k: False)
    monkeypatch.setattr(content_mod, "generate_post_content", lambda *a, **k: dict(generated))
    monkeypatch.setattr(rc, "check_post_publish", lambda *a, **k: type("R", (), {"ok": True, "errors": []})())
    monkeypatch.setattr(rc, "_try_direct_dispatch", lambda *a, **k: None)

    out = rc.generate_action_content(db, action.id, worker_id="gen-worker")
    assert out["ok"] is True and out.get("post_id")
    db.expire_all()
    refreshed = db.get(RedditCampaignAction, action.id)
    assert refreshed.generation_status == "ready"
    assert refreshed.status == "pending"  # auto 模式过闸 → 待发布
    assert refreshed.post_id == out["post_id"]
    post = db.get(RedditPost, out["post_id"])
    assert post.status == "approved" and post.scheduled_at is not None
    attempt = (
        db.query(RedditCampaignActionAttempt)
        .filter_by(action_id=action.id, phase="generation", attempt_no=1)
        .one()
    )
    assert attempt.request_state == "responded"
    assert attempt.external_object_id == f"reddit_post:{out['post_id']}"
    assert attempt.request_started_at is not None


@PG_SKIP
def test_pg_generation_failure_returns_to_queue(db, site, monkeypatch):
    """场景 12（阶段 2）：AI 瞬时失败 → 回 queued 退避，执行代次不动，可被心跳重投。"""
    import app.services.reddit_content as content_mod
    from app.services.ai_writer import DeepSeekError

    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    action = RedditCampaignAction(
        campaign_id=campaign.id,
        site_id=campaign.site_id,
        account_id=acc.id,
        action_type="post",
        status="pending",
        planned_at=now_bj(),
        dedup_key=f"post:{campaign.id}:{acc.id}:{now_bj():%Y%m%d}",
        generation_status="queued",
    )
    db.add(action)
    db.commit()

    monkeypatch.setattr(rc, "_ai_budget_available", lambda *a, **k: (True, 40))

    def _boom(*a, **k):
        raise DeepSeekError("boom")

    monkeypatch.setattr(content_mod, "generate_post_content", _boom)
    # 走到 generate_post_content 前先被 _target_subreddits 拦住？不会——先 monkeypatch 空社区
    # 保持社区可用，让失败发生在 AI 调用点
    monkeypatch.setattr(rc, "_target_subreddits", lambda *a, **k: ["r/test"])
    monkeypatch.setattr(rc, "_pool_accounts", lambda *a, **k: [acc])
    import app.services.reddit_community_rotation as rotation

    monkeypatch.setattr(rotation, "select_subreddit", lambda *a, **k: "r/test")
    monkeypatch.setattr(rotation, "per_sub_daily_cap", lambda *a, **k: 1)
    monkeypatch.setattr(rotation, "pool_is_small", lambda *a, **k: False)
    monkeypatch.setattr(rotation, "recent_angles", lambda *a, **k: [])
    monkeypatch.setattr(rotation, "pick_post_type_weighted", lambda *a, **k: "experience")
    monkeypatch.setattr(rotation, "pick_angle_weighted", lambda *a, **k: "experience")

    out = rc.generate_action_content(db, action.id, worker_id="gen-worker")
    assert out == {"ok": False, "reason": "ai_transient_error"}
    db.expire_all()
    refreshed = db.get(RedditCampaignAction, action.id)
    assert refreshed.generation_status == "queued"
    assert refreshed.available_at is not None  # 退避后由心跳重投
    # 执行代次/状态不受生成失败影响（生成失败不占执行租约）
    assert refreshed.execution_lease_generation == 0
    assert refreshed.status == "pending"
    assert refreshed.execution_attempt_count == 0
    attempt = (
        db.query(RedditCampaignActionAttempt)
        .filter_by(action_id=action.id, phase="generation", attempt_no=1)
        .one()
    )
    assert attempt.request_state == "responded"
    assert attempt.error_code == "ai_transient_error"


@PG_SKIP
def test_pg_heartbeat_requeue_picks_up_lost_message(db, site, monkeypatch):
    """场景 13（阶段 2）：心跳重投——消息丢失的 queued 骨架与租约过期的 generating 都被拾取。"""
    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    enqueued: list[int] = []
    monkeypatch.setattr(rc, "_try_enqueue_generation", lambda aid: enqueued.append(aid) or True)

    # ① 消息丢失：骨架 queued 落库但投递失败 → 心跳拾取
    lost = RedditCampaignAction(
        campaign_id=campaign.id, site_id=campaign.site_id, account_id=acc.id,
        action_type="post", status="pending", planned_at=now_bj(),
        dedup_key="post:lost", generation_status="queued",
    )
    db.add(lost)
    # ② 生成 worker 崩溃：generating 租约过期 → 原子失效回 queued 再重投
    crashed = RedditCampaignAction(
        campaign_id=campaign.id, site_id=campaign.site_id, account_id=acc.id,
        action_type="comment", status="pending", planned_at=now_bj(),
        dedup_key="comment:crashed", generation_status="generating",
        generation_lease_generation=1, generation_attempt_count=1,
        lease_owner="dead-gen-worker", lease_until=now_bj() - timedelta(minutes=1),
    )
    db.add(crashed)
    db.flush()
    # 生成 worker 崩溃前的 attempt 预写（领取事务已提交，崩溃在调用中）
    db.add(RedditCampaignActionAttempt(
        action_id=crashed.id, phase="generation", attempt_no=1,
        request_state="request_pending", worker_id="dead-gen-worker",
        request_started_at=now_bj() - timedelta(minutes=2),
    ))
    # ③ 暂停 campaign 的骨架 → 不重投（暂停即停，独立 campaign 置 paused）
    paused_campaign = _campaign(db, site.id)
    paused_campaign.status = "paused"
    db.commit()
    paused = RedditCampaignAction(
        campaign_id=paused_campaign.id, site_id=campaign.site_id, account_id=acc.id,
        action_type="post", status="pending", planned_at=now_bj(),
        dedup_key="post:paused", generation_status="queued",
    )
    db.add(paused)
    db.commit()

    count = rc.requeue_stale_generation_actions(db)

    db.expire_all()
    refreshed = db.get(RedditCampaignAction, lost.id)
    assert refreshed.generation_status == "queued"
    crashed_row = db.get(RedditCampaignAction, crashed.id)
    assert crashed_row.generation_status == "queued"
    assert crashed_row.lease_owner is None and crashed_row.lease_until is None
    assert paused.id not in enqueued
    assert set(enqueued) == {lost.id, crashed.id}
    assert count == 2
    # 崩溃 attempt 已回写回收标记
    attempt = (
        db.query(RedditCampaignActionAttempt)
        .filter_by(action_id=crashed.id, phase="generation", attempt_no=1)
        .one()
    )
    assert attempt.request_state == "responded"
    assert attempt.error_code == "generation_lease_expired"


@PG_SKIP
def test_pg_attempt_unique_constraint(db, site):
    """场景 9：attempts 表 UNIQUE(action_id, phase, attempt_no)。"""
    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    action = _action(db, campaign, acc)
    rc.claim_action(db, action.id)
    dup = RedditCampaignActionAttempt(
        action_id=action.id, phase="execution", attempt_no=1, request_state="request_pending"
    )
    db.add(dup)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


@PG_SKIP
def test_pg_execute_success_writes_vote_log_and_attempt(db, site, monkeypatch):
    """成功路径：done + RedditVoteLog upsert + attempt responded 同事务。"""
    from unittest.mock import MagicMock

    campaign = _campaign(db, site.id)
    acc = _account(db, site.id, "a1")
    _profile(db, site.id, acc.id)
    action = _action(db, campaign, acc, thing_id="t3_ok")

    client = MagicMock()
    client.vote.return_value = None
    monkeypatch.setattr(rc, "get_reddit_client_for_account", lambda a: client)
    out = rc.execute_action(db, action.id, worker_id="worker-ok")
    assert out["ok"] is True
    db.expire_all()  # 同 session 原生 SQL 更新对 identity map 不可见
    refreshed = db.get(RedditCampaignAction, action.id)
    assert refreshed.status == "done"
    assert refreshed.result_status == "confirmed_success"
    log = db.query(RedditVoteLog).filter_by(account_id=acc.id, thing_id="t3_ok").one()
    assert log.direction == 1
    attempt = (
        db.query(RedditCampaignActionAttempt)
        .filter_by(action_id=action.id, phase="execution", attempt_no=1)
        .one()
    )
    assert attempt.request_state == "responded"
    assert attempt.external_object_id == "t3_ok"
    assert attempt.request_started_at is not None
