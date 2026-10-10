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


def _rules_url(name: str) -> str:
    return f"https://www.reddit.com/r/{name}/about/rules.json"


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


@dataclass
class CommunityRulesResult:
    name: str
    rules_text: str | None
    error: str | None = None
    retry_after: int | None = None
    rate_limited: bool = False


RULES_CACHE_HOURS = 24
RULES_PROMPT_MAX_CHARS = 2500
_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_OAUTH_RULES_TMPL = "https://oauth.reddit.com/r/{name}/about/rules"

_oauth_token_cache: dict[str, Any] = {"token": None, "expires_at": 0.0}


def _reddit_user_agent() -> str:
    try:
        from app.core.config import settings

        return (settings.REDDIT_USER_AGENT or _USER_AGENT).strip() or _USER_AGENT
    except Exception:
        return _USER_AGENT


def reddit_app_credentials() -> tuple[str, str] | None:
    try:
        from app.core.config import settings

        cid = (settings.REDDIT_CLIENT_ID or "").strip()
        secret = (settings.REDDIT_CLIENT_SECRET or "").strip()
        if cid and secret:
            return cid, secret
    except Exception:
        pass
    return None


def get_reddit_app_access_token(*, force: bool = False) -> str | None:
    """client_credentials 应用令牌；无凭证则返回 None。"""
    import time

    creds = reddit_app_credentials()
    if not creds:
        return None
    now = time.time()
    if (
        not force
        and _oauth_token_cache.get("token")
        and float(_oauth_token_cache.get("expires_at") or 0) > now + 30
    ):
        return str(_oauth_token_cache["token"])
    cid, secret = creds
    with httpx.Client(timeout=_TIMEOUT_S, follow_redirects=True) as client:
        resp = client.post(
            _TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(cid, secret),
            headers={"User-Agent": _reddit_user_agent()},
        )
    if resp.status_code >= 400:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    token = str(data.get("access_token") or "").strip()
    if not token:
        return None
    expires_in = float(data.get("expires_in") or 3600)
    _oauth_token_cache["token"] = token
    _oauth_token_cache["expires_at"] = now + max(60.0, expires_in)
    return token


def _oauth_http_get(url: str, token: str) -> tuple[int, Any]:
    with httpx.Client(
        timeout=_TIMEOUT_S,
        headers={
            "User-Agent": _reddit_user_agent(),
            "Authorization": f"Bearer {token}",
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


def format_rules_from_payload(payload: Any) -> str:
    """Normalize Reddit/Zernio rules payload into plain text."""
    if not isinstance(payload, dict):
        return ""
    rules = payload.get("rules")
    if not isinstance(rules, list):
        return ""
    lines: list[str] = []
    for idx, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            continue
        title = str(
            rule.get("short_name")
            or rule.get("shortName")
            or rule.get("violation_reason")
            or rule.get("violationReason")
            or f"Rule {idx}"
        ).strip()
        desc = str(rule.get("description") or "").strip()
        # strip simple HTML leftovers if any
        desc = re.sub(r"<[^>]+>", " ", desc)
        desc = re.sub(r"\s+", " ", desc).strip()
        if title and desc:
            lines.append(f"{idx}. {title}: {desc}")
        elif title:
            lines.append(f"{idx}. {title}")
        elif desc:
            lines.append(f"{idx}. {desc}")
    site_rules = payload.get("siteRules") or payload.get("site_rules") or []
    if isinstance(site_rules, list) and site_rules:
        site_bits = [str(x).strip() for x in site_rules if str(x).strip()]
        if site_bits:
            lines.append("Site-wide: " + "; ".join(site_bits))
    return "\n".join(lines).strip()


def fetch_subreddit_rules_via_zernio(client: Any, name: str) -> CommunityRulesResult:
    """通过已绑定的 Zernio Reddit 账号拉取版规（推荐路径）。"""
    sr = normalize_subreddit(name)
    try:
        payload = client.get_subreddit_rules(sr)
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        retry_after = getattr(exc, "retry_after", None)
        detail = str(exc)
        if retry_after is None:
            m = re.search(r"(?:Quota resets|Retry) in (\d+)\s*s", detail, re.IGNORECASE)
            if m:
                retry_after = int(m.group(1))
        if status == 429 or "rate limit" in detail.lower():
            wait = f"约 {retry_after} 秒后" if retry_after else "稍后"
            return CommunityRulesResult(
                name=sr,
                rules_text=None,
                error=f"Reddit/Zernio 接口限流，请{wait}再刷新版规",
                retry_after=retry_after,
                rate_limited=True,
            )
        if status:
            return CommunityRulesResult(
                name=sr,
                rules_text=None,
                error=f"Zernio rules HTTP {status}: {detail}",
                retry_after=retry_after,
            )
        return CommunityRulesResult(name=sr, rules_text=None, error=f"Zernio rules fetch failed: {detail}")
    text = format_rules_from_payload(payload if isinstance(payload, dict) else {})
    if not text:
        return CommunityRulesResult(name=sr, rules_text=None, error="empty rules from Zernio")
    return CommunityRulesResult(name=sr, rules_text=text)


def _rules_error_for_status(status: int, *, used_oauth: bool) -> str:
    if status == 403 and not used_oauth:
        return (
            "rules HTTP 403：Reddit 已关闭未登录 JSON 接口。"
            "请在 backend/.env 配置 REDDIT_CLIENT_ID 与 REDDIT_CLIENT_SECRET"
            "（https://www.reddit.com/prefs/apps 创建 script 应用）后重试"
        )
    if status == 403 and used_oauth:
        return "rules HTTP 403：OAuth 被拒，请检查 REDDIT_CLIENT_ID/SECRET 与 User-Agent"
    if status == 401:
        return "rules HTTP 401：Reddit 应用凭证无效"
    return f"rules HTTP {status}"


def fetch_subreddit_rules(
    name: str,
    *,
    http_get: HttpGet | None = None,
) -> CommunityRulesResult:
    """优先 OAuth 官方 API；测试可注入 http_get 走公开 URL。"""
    sr = normalize_subreddit(name)
    if http_get is not None:
        try:
            status, body = http_get(_rules_url(sr))
        except Exception as exc:
            return CommunityRulesResult(name=sr, rules_text=None, error=f"rules fetch failed: {exc}")
        if status == 404:
            return CommunityRulesResult(name=sr, rules_text=None, error="rules endpoint 404")
        if status >= 400:
            return CommunityRulesResult(
                name=sr,
                rules_text=None,
                error=_rules_error_for_status(status, used_oauth=False),
            )
        text = format_rules_from_payload(body)
        if not text:
            return CommunityRulesResult(name=sr, rules_text=None, error="empty rules")
        return CommunityRulesResult(name=sr, rules_text=text)

    token = None
    try:
        token = get_reddit_app_access_token()
    except Exception as exc:
        return CommunityRulesResult(name=sr, rules_text=None, error=f"oauth token failed: {exc}")

    if token:
        try:
            status, body = _oauth_http_get(_OAUTH_RULES_TMPL.format(name=sr), token)
        except Exception as exc:
            return CommunityRulesResult(name=sr, rules_text=None, error=f"rules oauth fetch failed: {exc}")
        if status >= 400:
            return CommunityRulesResult(
                name=sr,
                rules_text=None,
                error=_rules_error_for_status(status, used_oauth=True),
            )
        text = format_rules_from_payload(body)
        if not text:
            return CommunityRulesResult(name=sr, rules_text=None, error="empty rules")
        return CommunityRulesResult(name=sr, rules_text=text)

    # 无凭证：再试公开 URL（多数环境已 403，用于兼容旧网络）
    try:
        status, body = _default_http_get(_rules_url(sr))
    except Exception as exc:
        return CommunityRulesResult(name=sr, rules_text=None, error=f"rules fetch failed: {exc}")
    if status >= 400:
        return CommunityRulesResult(
            name=sr,
            rules_text=None,
            error=_rules_error_for_status(status, used_oauth=False),
        )
    text = format_rules_from_payload(body)
    if not text:
        return CommunityRulesResult(name=sr, rules_text=None, error="empty rules")
    return CommunityRulesResult(name=sr, rules_text=text)


def is_rules_fresh(row: Any, *, now: datetime | None = None) -> bool:
    fetched = getattr(row, "rules_fetched_at", None)
    if fetched is None or not getattr(row, "rules_text", None):
        return False
    now = now or datetime.now(timezone.utc)
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return fetched >= now - timedelta(hours=RULES_CACHE_HOURS)


def apply_rules_to_community(row: Any, result: CommunityRulesResult) -> None:
    if result.rules_text:
        row.rules_text = result.rules_text
        row.rules_fetched_at = datetime.now(timezone.utc)
        # keep verify_error for existence; do not overwrite success with empty rules noise
    elif result.error:
        # only annotate if we have no rules yet
        if not getattr(row, "rules_text", None):
            prev = (getattr(row, "verify_error", None) or "").strip()
            note = f"rules: {result.error}"
            row.verify_error = f"{prev}; {note}".strip("; ") if prev else note


def refresh_community_existence_and_rules(
    row: Any,
    *,
    force: bool = True,
    http_get: HttpGet | None = None,
    now: datetime | None = None,
    zernio_client: Any | None = None,
    skip_public_verify: bool = False,
) -> tuple[CommunityVerifyResult, CommunityRulesResult]:
    """Refresh official rules (prefer Zernio). Public about.json is optional."""
    now = now or datetime.now(timezone.utc)
    if skip_public_verify or zernio_client is not None:
        # 有 Zernio 时不必再打公开 about.json（多数环境 403，且浪费时机）
        verify = CommunityVerifyResult(
            name=row.name,
            exists=True if getattr(row, "exists", None) is not False else False,
            subscribers=getattr(row, "subscribers", None),
            accounts_active=getattr(row, "accounts_active", None),
            posts_7d=getattr(row, "posts_7d", None),
            activity_score=getattr(row, "activity_score", None),
            is_active_enough=bool(getattr(row, "is_active", True)),
            error=None,
        )
        if verify.exists is False:
            return verify, CommunityRulesResult(name=row.name, rules_text=None, error="subreddit missing")
    else:
        verify = verify_community_row(row, force=force, http_get=http_get, now=now)
        if verify.exists is False:
            return verify, CommunityRulesResult(name=row.name, rules_text=None, error="subreddit missing")
    if not force and is_rules_fresh(row, now=now):
        return verify, CommunityRulesResult(name=row.name, rules_text=row.rules_text)
    if zernio_client is not None:
        rules = fetch_subreddit_rules_via_zernio(zernio_client, row.name)
    else:
        rules = fetch_subreddit_rules(row.name, http_get=http_get)
    apply_rules_to_community(row, rules)
    return verify, rules


def truncate_rules_for_prompt(rules_text: str | None, *, max_chars: int = RULES_PROMPT_MAX_CHARS) -> str:
    text = (rules_text or "").strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n...[truncated]"


def format_rules_prompt_line(rules_text: str | None) -> str:
    clipped = truncate_rules_for_prompt(rules_text)
    if not clipped:
        return ""
    return (
        "Subreddit rules (must follow; if conflict with other instructions, these rules win):\n"
        f"{clipped}"
    )
