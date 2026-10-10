"""Campaign 调度：事件触发防抖与 tick 心跳读写的单元测试。"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

from app.core.timeutil import now_bj
from app.services import reddit_campaign as rc
from app.tasks import alert_tasks
from app.tasks import reddit_campaign_tasks as rc_tasks

REDIS_TARGET = "app.utils.rate_limiter.get_redis"


# ============ trigger_campaign_tick：事件加速触发 ============

def test_trigger_campaign_tick_enqueues_with_debounce_key():
    redis = MagicMock()
    redis.set.return_value = True
    task = MagicMock()
    with patch(REDIS_TARGET, return_value=redis), patch.object(rc_tasks, "campaign_tick", task):
        assert rc_tasks.trigger_campaign_tick() is True
    args, kwargs = redis.set.call_args
    assert args[0] == "trigger:reddit:campaign_tick"
    assert kwargs["nx"] is True
    task.apply_async.assert_called_once_with()


def test_trigger_campaign_tick_skips_within_debounce():
    redis = MagicMock()
    redis.set.return_value = False  # 防抖键仍存在
    task = MagicMock()
    with patch(REDIS_TARGET, return_value=redis), patch.object(rc_tasks, "campaign_tick", task):
        assert rc_tasks.trigger_campaign_tick() is False
    task.apply_async.assert_not_called()


def test_trigger_campaign_tick_clears_key_when_broker_down():
    redis = MagicMock()
    redis.set.return_value = True
    task = MagicMock()
    task.apply_async.side_effect = RuntimeError("broker down")
    with patch(REDIS_TARGET, return_value=redis), patch.object(rc_tasks, "campaign_tick", task):
        assert rc_tasks.trigger_campaign_tick() is False  # 静默降级，不抛出
    redis.delete.assert_called_once_with("trigger:reddit:campaign_tick")


def test_trigger_campaign_tick_swallows_redis_outage():
    with patch(REDIS_TARGET, side_effect=RuntimeError("redis down")):
        assert rc_tasks.trigger_campaign_tick() is False


# ============ 心跳写入 / 读取 ============

def test_write_campaign_heartbeat_sets_key_with_ttl():
    import datetime as dt

    redis = MagicMock()
    with patch(REDIS_TARGET, return_value=redis):
        rc.write_campaign_heartbeat()
    args, kwargs = redis.set.call_args
    assert args[0] == rc.TICK_HEARTBEAT_KEY
    assert kwargs["ex"] == rc.TICK_HEARTBEAT_TTL
    dt.datetime.fromisoformat(args[1])  # 值必须可解析


def test_read_heartbeat_missing_when_key_absent():
    redis = MagicMock()
    redis.get.return_value = None
    with patch(REDIS_TARGET, return_value=redis):
        hb = rc.read_campaign_tick_heartbeat()
    assert hb["stale"] is True
    assert hb["reason"] == "missing"


def test_read_heartbeat_fresh():
    redis = MagicMock()
    redis.get.return_value = now_bj().isoformat()
    with patch(REDIS_TARGET, return_value=redis):
        hb = rc.read_campaign_tick_heartbeat()
    assert hb["stale"] is False
    assert hb["reason"] is None
    assert hb["age_minutes"] < rc.TICK_STALE_MINUTES


def test_read_heartbeat_stale():
    redis = MagicMock()
    redis.get.return_value = (now_bj() - timedelta(minutes=30)).isoformat()
    with patch(REDIS_TARGET, return_value=redis):
        hb = rc.read_campaign_tick_heartbeat()
    assert hb["stale"] is True
    assert hb["reason"] == "stale"


def test_read_heartbeat_accepts_bytes_from_redis():
    redis = MagicMock()
    redis.get.return_value = now_bj().isoformat().encode()  # redis-py 默认返回 bytes
    with patch(REDIS_TARGET, return_value=redis):
        hb = rc.read_campaign_tick_heartbeat()
    assert hb["stale"] is False


def test_read_heartbeat_redis_outage():
    with patch(REDIS_TARGET, side_effect=RuntimeError("redis down")):
        hb = rc.read_campaign_tick_heartbeat()
    assert hb["stale"] is True
    assert hb["reason"] == "redis_unavailable"


# ============ 心跳告警任务 ============

def test_heartbeat_alert_ok_when_fresh():
    redis = MagicMock()
    redis.get.return_value = now_bj().isoformat()
    with patch(REDIS_TARGET, return_value=redis):
        result = alert_tasks.check_campaign_tick_heartbeat()
    assert result["ok"] is True


def test_heartbeat_alert_warns_when_stale(caplog):
    redis = MagicMock()
    redis.get.return_value = (now_bj() - timedelta(minutes=30)).isoformat()
    with patch(REDIS_TARGET, return_value=redis), caplog.at_level("WARNING"):
        result = alert_tasks.check_campaign_tick_heartbeat()
    assert result["ok"] is False
    assert result["reason"] == "stale"
    assert any("心跳过期" in rec.message for rec in caplog.records)


def test_heartbeat_alert_warns_when_missing():
    redis = MagicMock()
    redis.get.return_value = None
    with patch(REDIS_TARGET, return_value=redis):
        result = alert_tasks.check_campaign_tick_heartbeat()
    assert result["ok"] is False
    assert result["reason"] == "missing"
