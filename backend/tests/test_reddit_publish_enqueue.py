"""定时发布去重入队与超时标记。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tasks import reddit_tasks as tasks


def test_try_enqueue_publish_skips_when_lock_exists():
    redis = MagicMock()
    redis.set.return_value = False
    task = MagicMock()
    with patch.object(tasks, "get_redis", return_value=redis):
        assert tasks.try_enqueue_publish("post", 42, task) is False
    task.apply_async.assert_not_called()


def test_try_enqueue_publish_sets_lock_and_enqueues():
    redis = MagicMock()
    redis.set.return_value = True
    task = MagicMock()
    with patch.object(tasks, "get_redis", return_value=redis):
        assert tasks.try_enqueue_publish("comment", 7, task) is True
    redis.set.assert_called_once()
    args, kwargs = redis.set.call_args
    assert args[0] == "enqueue:reddit:publish:comment:7"
    assert kwargs["nx"] is True
    task.apply_async.assert_called_once_with(
        args=[7],
        task_id="reddit-publish-comment-7",
    )


def test_try_enqueue_clears_lock_when_apply_fails():
    redis = MagicMock()
    redis.set.return_value = True
    task = MagicMock()
    task.apply_async.side_effect = RuntimeError("broker down")
    with patch.object(tasks, "get_redis", return_value=redis):
        try:
            tasks.try_enqueue_publish("post", 1, task)
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected RuntimeError")
    redis.delete.assert_called_once_with("enqueue:reddit:publish:post:1")


def test_mark_post_timeout_only_when_posting_without_external_id():
    post = MagicMock()
    post.status = "posting"
    post.reddit_post_id = None
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = post
    tasks._mark_post_timeout(db, 9)
    assert post.status == "failed"
    assert "超时" in post.error_message
    db.commit.assert_called_once()


def test_zernio_timeout_maps_to_504():
    import httpx
    from app.services.zernio_client import ZernioClient, ZernioError

    client = ZernioClient(account_id=1, api_key="k", profile_id="p")
    with patch("app.services.zernio_client.httpx.Client") as client_cls:
        inst = client_cls.return_value.__enter__.return_value
        inst.request.side_effect = httpx.TimeoutException("timed out")
        with patch("app.services.zernio_client.acquire_token", return_value=True):
            breaker = MagicMock()
            breaker.allow_request.return_value = True
            with patch("app.services.zernio_client.CircuitBreaker", return_value=breaker):
                try:
                    client._request("POST", "/posts", json_body={"a": 1})
                except ZernioError as exc:
                    assert exc.status_code == 504
                    assert "超时" in str(exc)
                    breaker.record_failure.assert_called()
                    return
                raise AssertionError("expected ZernioError")
