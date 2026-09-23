"""社区 few-shot 样例 + 按站点隔离的检测反向 negative examples。"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter, defaultdict, deque
from typing import Any

logger = logging.getLogger(__name__)

_MAX_PATTERN_KEYS = 24
_PATTERN_TTL_SEC = 7 * 24 * 3600
_EXAMPLES_TTL_SEC = 3600
_hits_lock = threading.Lock()
# site_id -> Counter / samples（Redis 不可用时的进程内兜底，仍按站点分桶）
_pattern_hits: dict[int, Counter[str]] = defaultdict(Counter)
_match_samples: dict[int, dict[str, deque[str]]] = defaultdict(dict)
_mem_examples: dict[str, tuple[float, list[dict[str, str]]]] = {}


def _redis():
    try:
        from app.utils.rate_limiter import get_redis

        return get_redis()
    except Exception:
        return None


def _pattern_redis_key(site_id: int) -> str:
    return f"reddit:ai_patterns:v1:{int(site_id)}"


def _examples_redis_key(subreddit: str) -> str:
    return f"reddit:style_examples:v1:{subreddit.lower().strip()}"


def record_detection_patterns(
    signs_of_ai: dict[str, Any] | None,
    *,
    site_id: int | None,
) -> None:
    """按站点累计近期命中的 AI 模式键；不写入原文 matches，避免品牌串站。"""
    if site_id is None or not signs_of_ai:
        return
    patterns = signs_of_ai.get("patterns") or []
    if not patterns:
        return
    sid = int(site_id)
    increments: dict[str, int] = {}
    for p in patterns:
        key = str(p.get("key") or "").strip()
        if not key:
            continue
        increments[key] = increments.get(key, 0) + max(1, int(p.get("count") or 1))
    if not increments:
        return

    r = _redis()
    if r is not None:
        try:
            rk = _pattern_redis_key(sid)
            pipe = r.pipeline()
            for key, n in increments.items():
                pipe.hincrby(rk, key, n)
            pipe.expire(rk, _PATTERN_TTL_SEC)
            pipe.execute()
            # 裁剪低频键
            data = r.hgetall(rk) or {}
            if len(data) > _MAX_PATTERN_KEYS:
                ranked = sorted(data.items(), key=lambda kv: int(kv[1] or 0), reverse=True)
                drop = [k for k, _ in ranked[_MAX_PATTERN_KEYS:]]
                if drop:
                    r.hdel(rk, *drop)
            return
        except Exception as exc:
            logger.info("pattern redis write failed, fallback memory: %s", exc)

    with _hits_lock:
        c = _pattern_hits[sid]
        for key, n in increments.items():
            c[key] += n
        if len(c) > _MAX_PATTERN_KEYS:
            keep = {k for k, _ in c.most_common(_MAX_PATTERN_KEYS)}
            for k in list(c.keys()):
                if k not in keep:
                    del c[k]


def reset_detection_patterns(site_id: int | None = None) -> None:
    """测试用：清空累计。"""
    r = _redis()
    if site_id is None:
        with _hits_lock:
            _pattern_hits.clear()
            _match_samples.clear()
        if r is not None:
            try:
                for key in r.scan_iter(match="reddit:ai_patterns:v1:*", count=100):
                    r.delete(key)
            except Exception:
                pass
        return
    sid = int(site_id)
    with _hits_lock:
        _pattern_hits.pop(sid, None)
        _match_samples.pop(sid, None)
    if r is not None:
        try:
            r.delete(_pattern_redis_key(sid))
        except Exception:
            pass


def top_negative_examples(*, site_id: int | None, limit: int = 6) -> list[tuple[str, list[str]]]:
    if site_id is None:
        return []
    sid = int(site_id)
    r = _redis()
    if r is not None:
        try:
            data = r.hgetall(_pattern_redis_key(sid)) or {}
            ranked = sorted(data.items(), key=lambda kv: int(kv[1] or 0), reverse=True)
            return [(k, []) for k, _ in ranked[:limit]]
        except Exception as exc:
            logger.info("pattern redis read failed, fallback memory: %s", exc)
    with _hits_lock:
        return [(k, []) for k, _ in _pattern_hits.get(sid, Counter()).most_common(limit)]


def format_negative_examples_block(*, site_id: int | None, limit: int = 6) -> str:
    rows = top_negative_examples(site_id=site_id, limit=limit)
    if not rows:
        return ""
    lines = ["Hard avoid these AI patterns (from this site's recent drafts):"]
    for key, _samples in rows:
        lines.append(f"- {key}")
    return "\n".join(lines)


def with_negative_examples(base_system: str, *, site_id: int | None = None) -> str:
    block = format_negative_examples_block(site_id=site_id)
    if not block:
        return base_system
    return f"{base_system}\n{block}"


def pick_community_examples(items: list[dict], *, limit: int = 3) -> list[dict[str, str]]:
    """从 feed/search 结果里按分数挑高赞帖，作风格 few-shot。"""
    ranked = sorted(
        (i for i in items if (i.get("title") or "").strip()),
        key=lambda i: int(i.get("score") or 0),
        reverse=True,
    )
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in ranked:
        title = " ".join(str(item.get("title") or "").split()).strip()[:120]
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        body = " ".join(str(item.get("body") or "").split()).strip()[:220]
        out.append({"title": title, "body": body})
        if len(out) >= limit:
            break
    return out


def format_community_examples_block(
    examples: list[dict[str, str]] | None,
    *,
    for_post: bool = False,
) -> str:
    if not examples:
        return ""
    header = (
        "Real high-scoring posts from this sub (match tone/register only — "
        "do NOT copy titles, facts, or phrasing):"
        if for_post
        else "Real high-scoring posts from this sub (match how people write here — "
        "do NOT copy titles or facts):"
    )
    lines = [header]
    for i, ex in enumerate(examples[:3], 1):
        title = (ex.get("title") or "").strip()
        body = (ex.get("body") or "").strip()
        lines.append(f"{i}. Title: {title}")
        if body:
            lines.append(f"   Body: {body}")
    return "\n".join(lines)


def _load_cached_examples(subreddit: str) -> list[dict[str, str]] | None:
    key = subreddit.lower().strip()
    r = _redis()
    if r is not None:
        try:
            raw = r.get(_examples_redis_key(key))
            if raw:
                data = json.loads(raw)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    hit = _mem_examples.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    return None


def _store_cached_examples(subreddit: str, examples: list[dict[str, str]]) -> None:
    key = subreddit.lower().strip()
    _mem_examples[key] = (time.time() + _EXAMPLES_TTL_SEC, examples)
    r = _redis()
    if r is None:
        return
    try:
        r.setex(_examples_redis_key(key), _EXAMPLES_TTL_SEC, json.dumps(examples, ensure_ascii=False))
    except Exception as exc:
        logger.info("examples redis cache write failed: %s", exc)


def fetch_community_examples(client: Any, subreddit: str, *, limit: int = 3) -> list[dict[str, str]]:
    """Best-effort 拉该 sub feed；Redis/内存 TTL 1h 缓存，失败返回空。"""
    cached = _load_cached_examples(subreddit)
    if cached is not None:
        return cached[:limit]

    raw: list[dict] = []
    try:
        raw = list(client.list_feed(subreddit, limit=max(12, limit * 4)) or [])
    except Exception as exc:
        logger.info("community examples feed failed for r/%s: %s", subreddit, exc)
        try:
            raw = list(client.search_posts(subreddit, "discussion", limit=max(12, limit * 4)) or [])
        except Exception as exc2:
            logger.info("community examples search failed for r/%s: %s", subreddit, exc2)
            return []
    picked = pick_community_examples(raw, limit=limit)
    if picked:
        _store_cached_examples(subreddit, picked)
    return picked
