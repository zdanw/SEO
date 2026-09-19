"""Reddit 社区校验：公开 about（弱）+ Zernio feed/search 取证 + LLM 判定。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

import httpx

from app.services.reddit_client import normalize_subreddit

MIN_SUBSCRIBERS = 1000
MIN_POSTS_7D = 3
VERIFY_CACHE_HOURS = 24
_FEED_LIMIT = 8
_USER_AGENT = "SEO-Ops/1.0 (community-verify; +https://localhost)"
_TIMEOUT_S = 15.0

HttpGet = Callable[[str], tuple[int, Any]]


class FeedClient(Protocol):
    def list_feed(self, subreddit: str, limit: int = 10) -> list[dict]: ...

    def search_posts(self, subreddit: str, keyword: str, limit: int = 10) -> list[dict]: ...


@dataclass
class CommunityVerifyResult:
    name: str
    exists: bool | None
    subscribers: int | None = None
    accounts_active: int | None = None
    posts_7d: int | None = None
    activity_score: float | None = None
    is_active_enough: bool = False
    persona_fit: bool | None = None
    error: str | None = None


def _default_http_get(url: str) -> tuple[int, Any]:
    with httpx.Client(
        timeout=_TIMEOUT_S,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
        },
        follow_redirects=True,
    ) as client:
        resp = client.get(url)
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text[:500]}
        return resp.status_code, body


def _about_url(name: str) -> str:
    return f"https://www.reddit.com/r/{name}/about.json"


def _new_url(name: str) -> str:
    return f"https://www.reddit.com/r/{name}/new.json?limit=25"


def _count_posts_7d_from_items(items: list[dict], *, now: datetime) -> int:
    cutoff = now.timestamp() - 7 * 24 * 3600
    count = 0
    for item in items:
        created = float(item.get("created_utc") or 0)
        if created >= cutoff:
            count += 1
    return count


def _count_posts_7d(payload: Any, *, now: datetime) -> int:
    children = ((payload or {}).get("data") or {}).get("children") or []
    cutoff = now.timestamp() - 7 * 24 * 3600
    count = 0
    for child in children:
        data = child.get("data") if isinstance(child, dict) else None
        if not isinstance(data, dict):
            continue
        created = float(data.get("created_utc") or 0)
        if created >= cutoff:
            count += 1
    return count


def _activity_score(subscribers: int | None, posts_7d: int | None) -> float:
    sub = float(subscribers or 0)
    posts = float(posts_7d or 0)
    sub_part = min(1.0, sub / 100_000.0) * 0.4
    post_part = min(1.0, posts / 21.0) * 0.6
    return round(sub_part + post_part, 4)


def verify_subreddit(
    name: str,
    *,
    http_get: HttpGet | None = None,
    now: datetime | None = None,
) -> CommunityVerifyResult:
    sr = normalize_subreddit(name)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    getter = http_get or _default_http_get

    try:
        status, about = getter(_about_url(sr))
    except Exception as exc:
        return CommunityVerifyResult(
            name=sr,
            exists=None,
            is_active_enough=True,
            error=f"about 请求失败: {exc}",
        )

    if status == 404 or (isinstance(about, dict) and about.get("error") == 404):
        return CommunityVerifyResult(name=sr, exists=False, error="subreddit 不存在")
    if status >= 400:
        return CommunityVerifyResult(
            name=sr,
            exists=None,
            is_active_enough=True,
            error=f"about HTTP {status}",
        )

    data = (about or {}).get("data") if isinstance(about, dict) else None
    if not isinstance(data, dict) or not data.get("display_name"):
        reason = None
        if isinstance(about, dict):
            reason = about.get("reason") or about.get("message")
        return CommunityVerifyResult(
            name=sr,
            exists=None,
            is_active_enough=True,
            error=str(reason or "无法解析 about 数据"),
        )

    subscribers = int(data.get("subscribers") or 0)
    accounts_active = data.get("accounts_active")
    if accounts_active is not None:
        try:
            accounts_active = int(accounts_active)
        except (TypeError, ValueError):
            accounts_active = None

    posts_7d = 0
    try:
        n_status, new_payload = getter(_new_url(sr))
        if n_status < 400:
            posts_7d = _count_posts_7d(new_payload, now=now)
        elif n_status >= 400:
            score = _activity_score(subscribers, None)
            return CommunityVerifyResult(
                name=sr,
                exists=True,
                subscribers=subscribers,
                accounts_active=accounts_active,
                posts_7d=None,
                activity_score=score,
                is_active_enough=subscribers >= MIN_SUBSCRIBERS,
                error=f"new HTTP {n_status}",
            )
    except Exception:
        posts_7d = 0

    score = _activity_score(subscribers, posts_7d)
    enough = subscribers >= MIN_SUBSCRIBERS and posts_7d >= MIN_POSTS_7D
    return CommunityVerifyResult(
        name=sr,
        exists=True,
        subscribers=subscribers,
        accounts_active=accounts_active,
        posts_7d=posts_7d,
        activity_score=score,
        is_active_enough=enough,
        error=None if enough else "订阅或近 7 日发帖未达门槛",
    )


def collect_subreddit_evidence(
    names: list[str],
    *,
    client: FeedClient,
    search_keyword: str = "discussion",
    now: datetime | None = None,
    feed_limit: int = _FEED_LIMIT,
) -> list[dict[str, Any]]:
    """经 Zernio list_feed + search_posts 收集判定证据。"""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    kw = (search_keyword or "discussion").strip() or "discussion"
    out: list[dict[str, Any]] = []
    for raw in names:
        sr = normalize_subreddit(raw)
        if not sr:
            continue
        feed: list[dict] = []
        search: list[dict] = []
        err: str | None = None
        try:
            feed = client.list_feed(sr, limit=feed_limit) or []
        except Exception as exc:
            err = str(exc)
            status = getattr(exc, "status_code", None)
            if status == 404 or "404" in err or "not found" in err.lower():
                out.append(
                    {
                        "name": sr,
                        "feed_count": 0,
                        "search_count": 0,
                        "posts_7d": 0,
                        "sample_titles": [],
                        "error": err,
                    }
                )
                continue
        try:
            search = client.search_posts(sr, kw, limit=feed_limit) or []
        except Exception as exc:
            if err:
                err = f"{err}; search: {exc}"
            else:
                err = f"search: {exc}"
        merged = feed + [p for p in search if p not in feed]
        titles = [(p.get("title") or "").strip() for p in merged if (p.get("title") or "").strip()]
        posts_7d = _count_posts_7d_from_items(merged, now=now)
        out.append(
            {
                "name": sr,
                "feed_count": len(feed),
                "search_count": len(search),
                "posts_7d": posts_7d,
                "sample_titles": titles[:8],
                "error": err,
            }
        )
    return out


def _parse_judge_json(raw: str) -> list[dict]:
    start = raw.find("[")
    end = raw.rfind("]")
    if start < 0 or end <= start:
        return []
    parsed = json.loads(raw[start : end + 1])
    return [x for x in parsed if isinstance(x, dict)]


def _heuristic_judge(evidence: list[dict[str, Any]], interests: list[str]) -> list[CommunityVerifyResult]:
    interest_blob = " ".join(i.lower() for i in interests)
    results: list[CommunityVerifyResult] = []
    for item in evidence:
        name = item["name"]
        titles = " ".join(item.get("sample_titles") or []).lower()
        posts_7d = int(item.get("posts_7d") or 0)
        feed_count = int(item.get("feed_count") or 0)
        err = item.get("error")
        hard_miss = (
            bool(err)
            and ("404" in str(err) or "not found" in str(err).lower())
            and feed_count == 0
        )
        if hard_miss:
            results.append(
                CommunityVerifyResult(
                    name=name,
                    exists=False,
                    posts_7d=0,
                    activity_score=0.0,
                    is_active_enough=False,
                    persona_fit=False,
                    error=str(err),
                )
            )
            continue
        exists = feed_count > 0 or int(item.get("search_count") or 0) > 0
        active = posts_7d >= MIN_POSTS_7D or feed_count >= 2
        tokens = [t for t in re.split(r"[\s,/]+", interest_blob) if len(t) >= 3]
        fit = any(t in titles or t in name.lower() for t in tokens) if tokens else exists
        if exists and active and titles and not fit and tokens:
            # 有真实讨论但词面不沾边 → 不匹配
            fit = False
        elif exists and active and not tokens:
            fit = True
        ok = bool(exists and active and fit)
        reason_parts: list[str] = []
        if not exists:
            reason_parts.append("无帖可证伪/空版")
        if exists and not active:
            reason_parts.append("活跃度不足")
        if exists and not fit:
            reason_parts.append("与人设兴趣匹配弱")
        if ok:
            reason_parts.append("启发式：有帖且标题/名称沾边兴趣")
        results.append(
            CommunityVerifyResult(
                name=name,
                exists=exists if exists else (None if err else False),
                posts_7d=posts_7d,
                activity_score=_activity_score(None, posts_7d),
                is_active_enough=ok,
                persona_fit=fit,
                error="; ".join(reason_parts) if reason_parts else None,
            )
        )
    return results


def judge_communities_batch(
    evidence: list[dict[str, Any]],
    *,
    interests: list[str],
    ai: Any | None = None,
) -> list[CommunityVerifyResult]:
    """根据 feed/search 证据批量判定：存在 + 活跃 + 人设匹配。"""
    if not evidence:
        return []
    if ai is None:
        return _heuristic_judge(evidence, interests)

    payload = {
        "interests": interests,
        "candidates": evidence,
    }
    prompt = (
        "You evaluate Reddit subreddits for a persona account.\n"
        "For each candidate, decide if it is usable: exists (real community with posts), "
        "active (recent discussion), and persona_fit (matches interests; hobby/life OK, "
        "not random finance/meme unless interests say so).\n"
        "ok=true only when exists AND active AND persona_fit.\n"
        "Return JSON array only, one object per candidate with keys: "
        "name, ok, exists, active, persona_fit, score (0-1), reason.\n"
        f"Input:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        raw = ai.chat(prompt, system_prompt="Return JSON only.", temperature=0.2, max_tokens=1200)
        parsed = _parse_judge_json(raw)
    except Exception:
        return _heuristic_judge(evidence, interests)

    by_name = {str(x.get("name") or "").lower(): x for x in parsed}
    results: list[CommunityVerifyResult] = []
    for item in evidence:
        name = item["name"]
        judged = by_name.get(name.lower())
        if not judged:
            results.extend(_heuristic_judge([item], interests))
            continue
        exists = judged.get("exists")
        if exists is not None:
            exists = bool(exists)
        active = bool(judged.get("active"))
        fit = bool(judged.get("persona_fit"))
        ok = bool(judged.get("ok")) if "ok" in judged else (bool(exists) and active and fit)
        score = judged.get("score")
        try:
            score_f = float(score) if score is not None else _activity_score(None, item.get("posts_7d"))
        except (TypeError, ValueError):
            score_f = _activity_score(None, item.get("posts_7d"))
        reason = str(judged.get("reason") or "").strip() or None
        results.append(
            CommunityVerifyResult(
                name=name,
                exists=exists,
                posts_7d=int(item.get("posts_7d") or 0),
                activity_score=score_f,
                is_active_enough=ok,
                persona_fit=fit,
                error=reason,
            )
        )
    return results


def verify_subreddits_with_feed_llm(
    names: list[str],
    *,
    interests: list[str],
    client: FeedClient,
    ai: Any | None = None,
    search_keyword: str | None = None,
    now: datetime | None = None,
) -> list[CommunityVerifyResult]:
    kw = search_keyword or (interests[0] if interests else "discussion")
    evidence = collect_subreddit_evidence(
        names,
        client=client,
        search_keyword=kw,
        now=now,
    )
    return judge_communities_batch(evidence, interests=interests, ai=ai)


def is_verify_fresh(row: Any, *, now: datetime | None = None) -> bool:
    verified_at = getattr(row, "verified_at", None)
    if verified_at is None:
        return False
    now = now or datetime.now(timezone.utc)
    if verified_at.tzinfo is None:
        verified_at = verified_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return verified_at >= now - timedelta(hours=VERIFY_CACHE_HOURS)


def apply_verify_to_community(row: Any, result: CommunityVerifyResult) -> None:
    row.exists = result.exists
    row.subscribers = result.subscribers
    row.accounts_active = result.accounts_active
    row.posts_7d = result.posts_7d
    row.activity_score = result.activity_score
    row.verify_error = result.error
    row.verified_at = datetime.now(timezone.utc)
    if result.exists is False:
        row.is_active = False
    elif result.is_active_enough:
        row.is_active = True
    elif result.exists is True:
        row.is_active = False
    else:
        row.is_active = bool(result.is_active_enough)


def verify_community_row(
    row: Any,
    *,
    force: bool = False,
    http_get: HttpGet | None = None,
    now: datetime | None = None,
) -> CommunityVerifyResult:
    now = now or datetime.now(timezone.utc)
    if not force and is_verify_fresh(row, now=now):
        return CommunityVerifyResult(
            name=row.name,
            exists=row.exists,
            subscribers=row.subscribers,
            accounts_active=row.accounts_active,
            posts_7d=row.posts_7d,
            activity_score=row.activity_score,
            is_active_enough=bool(row.is_active and row.exists is not False),
            error=row.verify_error,
        )
    result = verify_subreddit(row.name, http_get=http_get, now=now)
    apply_verify_to_community(row, result)
    return result
