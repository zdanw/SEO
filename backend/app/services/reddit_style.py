"""社区 few-shot 样例 + 检测器反向 negative examples。"""
from __future__ import annotations

import logging
import threading
from collections import Counter, deque
from typing import Any

logger = logging.getLogger(__name__)

_MAX_PATTERN_KEYS = 24
_hits_lock = threading.Lock()
_pattern_hits: Counter[str] = Counter()
_match_samples: dict[str, deque[str]] = {}


def record_detection_patterns(signs_of_ai: dict[str, Any] | None) -> None:
    """累计近期命中的 AI 模式，供 system prompt 动态避雷。"""
    if not signs_of_ai:
        return
    patterns = signs_of_ai.get("patterns") or []
    if not patterns:
        return
    with _hits_lock:
        for p in patterns:
            key = str(p.get("key") or "").strip() or str(p.get("name") or "").strip()
            if not key:
                continue
            _pattern_hits[key] += max(1, int(p.get("count") or 1))
            bucket = _match_samples.setdefault(key, deque(maxlen=8))
            for m in p.get("matches") or []:
                text = " ".join(str(m).split()).strip()
                if text and text not in bucket:
                    bucket.append(text[:80])
        # 控制体积：只保留最高频的一批
        if len(_pattern_hits) > _MAX_PATTERN_KEYS:
            keep = {k for k, _ in _pattern_hits.most_common(_MAX_PATTERN_KEYS)}
            for k in list(_pattern_hits.keys()):
                if k not in keep:
                    del _pattern_hits[k]
                    _match_samples.pop(k, None)


def reset_detection_patterns() -> None:
    """测试用：清空累计。"""
    with _hits_lock:
        _pattern_hits.clear()
        _match_samples.clear()


def top_negative_examples(*, limit: int = 6) -> list[tuple[str, list[str]]]:
    with _hits_lock:
        return [
            (key, list(_match_samples.get(key, ())))
            for key, _ in _pattern_hits.most_common(limit)
        ]


def format_negative_examples_block(*, limit: int = 6) -> str:
    rows = top_negative_examples(limit=limit)
    if not rows:
        return ""
    lines = ["Hard avoid (recent AI tells from our own drafts):"]
    for key, samples in rows:
        line = f"- {key}"
        if samples:
            line += f" (e.g. {', '.join(samples[:3])})"
        lines.append(line)
    return "\n".join(lines)


def with_negative_examples(base_system: str) -> str:
    block = format_negative_examples_block()
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


def fetch_community_examples(client: Any, subreddit: str, *, limit: int = 3) -> list[dict[str, str]]:
    """Best-effort 拉该 sub feed；失败返回空，不阻断生成。"""
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
    return pick_community_examples(raw, limit=limit)
