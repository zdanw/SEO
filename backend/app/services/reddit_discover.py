"""Reddit 讨论发现：人设/产品双池抽样、新帖过滤、问句优先。"""
from __future__ import annotations

import json
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy.orm import Session

from app.services.reddit_client import normalize_subreddit

QUESTION_RE = re.compile(r"\?|\bhelp\b|\banyone\b|\badvice\b", re.IGNORECASE)
MAX_AGE_HOURS = 48
CASUAL_SLOTS = 3
PROMO_SLOTS = 1
FEED_PULL_LIMIT = 5

INTEREST_FALLBACK: dict[str, list[str]] = {
    "parenting": ["Parenting", "NewParents", "Mommit", "daddit", "beyondthebump"],
    "cooking": ["Cooking", "EatCheapAndHealthy", "MealPrepSunday"],
    "remote work": ["remotework", "WorkFromHome", "overemployed"],
    "home": ["HomeImprovement", "declutter", "CozyPlaces"],
    "safety": ["HomeSafety", "TwoXChromosomes"],
    "fitness": ["xxfitness", "bodyweightfitness"],
}


def promo_names_for_product(
    active_promo: list[str],
    bound_names: set[str] | None,
) -> list[str]:
    """Intersect site promo communities with product-bound names. No binding → empty."""
    if not bound_names:
        return []
    bound_l = {normalize_subreddit(n).lower() for n in bound_names if n}
    out: list[str] = []
    seen: set[str] = set()
    for name in active_promo:
        sr = normalize_subreddit(name)
        key = sr.lower()
        if key in bound_l and key not in seen:
            seen.add(key)
            out.append(sr)
    return out


def pick_discover_targets(
    persona: list[str],
    promo: list[str],
    *,
    seed: int | None = None,
    allow_promo: bool = True,
    casual_slots: int = CASUAL_SLOTS,
    promo_slots: int = PROMO_SLOTS,
) -> list[tuple[str, str]]:
    rng = random.Random(seed)
    persona_names = [normalize_subreddit(n) for n in persona if n]
    promo_names = [normalize_subreddit(n) for n in promo if n]
    picked: list[tuple[str, str]] = []
    if persona_names and casual_slots > 0:
        chosen = rng.sample(persona_names, k=min(casual_slots, len(persona_names)))
        picked.extend((name, "casual") for name in chosen)
    if allow_promo and promo_names and promo_slots > 0:
        extra = rng.sample(promo_names, k=min(promo_slots, len(promo_names)))
        picked.extend((name, "promo") for name in extra)
    rng.shuffle(picked)
    return picked


def filter_commentable(
    items: list[dict],
    *,
    already_commented: set[str],
    now: datetime | None = None,
    max_age_hours: int = MAX_AGE_HOURS,
    min_score: int = 0,
) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = int((now - timedelta(hours=max_age_hours)).timestamp())
    seen: set[str] = set()
    kept: list[dict] = []
    for item in items:
        url = (item.get("url") or "").strip()
        if not url or url in already_commented or url in seen:
            continue
        created = int(item.get("created_utc") or 0)
        if created and created < cutoff:
            continue
        if int(item.get("score") or 0) < min_score:
            continue
        if item.get("locked"):
            continue
        seen.add(url)
        kept.append(item)
    return kept


def prefer_questions(items: list[dict]) -> list[dict]:
    questions = [i for i in items if QUESTION_RE.search(i.get("title") or "")]
    rest = [i for i in items if i not in questions]
    return questions + rest


def suggest_persona_subreddits(interests: list[str], *, ai=None, limit: int = 12) -> list[str]:
    names: list[str] = []
    if ai is not None:
        try:
            raw = ai.chat(
                "Suggest Reddit subreddits (name only, no r/ prefix) for a real person whose "
                f"interests are: {', '.join(interests)}. Return JSON array of 8-15 strings. "
                "Prefer hobby/life communities, not product marketing subs.",
                system_prompt="Return JSON only.",
                temperature=0.4,
                max_tokens=400,
            )
            parsed = json.loads(raw[raw.find("[") : raw.rfind("]") + 1])
            names.extend(str(x) for x in parsed)
        except Exception:
            names = []
    if not names:
        for interest in interests:
            key = interest.strip().lower()
            names.extend(INTEREST_FALLBACK.get(key, []))
            for mapped, subs in INTEREST_FALLBACK.items():
                if key in mapped or mapped in key:
                    names.extend(subs)
    cleaned: list[str] = []
    seen: set[str] = set()
    for name in names:
        sr = normalize_subreddit(str(name))
        key = sr.lower()
        if not sr or key in seen:
            continue
        seen.add(key)
        cleaned.append(sr)
        if len(cleaned) >= limit:
            break
    return cleaned


def run_smart_discover(
    *,
    persona_communities: list[str],
    promo_communities: list[str],
    already_commented: set[str],
    search_fn: Callable[[str, str, int], list[dict]],
    generate_fn: Callable[[dict, str], None],
    allow_promo: bool,
    remaining_slots: int,
    seed: int | None = None,
    now: datetime | None = None,
    posts_per_community: int = 1,
    feed_limit: int = FEED_PULL_LIMIT,
) -> dict:
    """无 DB 的发现循环，供单测与任务复用。"""
    targets = pick_discover_targets(
        persona_communities,
        promo_communities,
        seed=seed,
        allow_promo=allow_promo,
    )
    queued = 0
    skipped = 0
    errors = 0
    last_error = ""
    used_urls: set[str] = set(already_commented)
    if not targets:
        return {"queued": 0, "skipped": 0, "errors": 0, "last_error": "", "reason": "no_communities"}
    for subreddit, intent in targets:
        if queued >= remaining_slots:
            break
        keyword = "discussion" if intent == "casual" else subreddit
        try:
            raw = search_fn(subreddit, keyword, feed_limit)
        except Exception as exc:
            errors += 1
            last_error = str(exc)
            status = getattr(exc, "status_code", None)
            if status == 429 or "429" in last_error:
                break
            continue
        candidates = prefer_questions(
            filter_commentable(raw, already_commented=used_urls, now=now)
        )
        for item in candidates[:posts_per_community]:
            if queued >= remaining_slots:
                break
            try:
                generate_fn(item, intent)
                used_urls.add(item["url"])
                queued += 1
            except Exception as exc:
                skipped += 1
                detail = getattr(exc, "detail", None)
                last_error = (str(detail) if detail else str(exc)) or last_error
                continue
    reason = ""
    if queued == 0:
        if errors:
            reason = "upstream"
        elif skipped:
            reason = "generate_failed"
        else:
            reason = "no_fresh_posts"
    return {"queued": queued, "skipped": skipped, "errors": errors, "last_error": last_error, "reason": reason}


def _refresh_stale_communities(db: Session, rows: list) -> list:
    """缓存过期则同步刷新；只返回仍 is_active 的行。"""
    from app.services.reddit_community_verify import is_verify_fresh, verify_community_row

    kept = []
    dirty = False
    for row in rows:
        if not is_verify_fresh(row):
            verify_community_row(row, force=True)
            dirty = True
        if row.is_active and row.exists is not False:
            kept.append(row)
    if dirty:
        db.commit()
    return kept


def smart_discover_for_account(
    db: Session,
    *,
    site_id: int,
    account_id: int,
    generate_comment,
    search_posts,
    remaining_slots: int,
    seed: int | None = None,
    product_id: int | None = None,
) -> dict:
    from app.models.reddit import RedditComment, RedditCommunity, RedditProduct

    persona_rows = _refresh_stale_communities(
        db,
        db.query(RedditCommunity)
        .filter(
            RedditCommunity.site_id == site_id,
            RedditCommunity.is_active.is_(True),
            RedditCommunity.purpose == "persona",
            RedditCommunity.account_id == account_id,
        )
        .all(),
    )
    promo_rows = _refresh_stale_communities(
        db,
        db.query(RedditCommunity)
        .filter(
            RedditCommunity.site_id == site_id,
            RedditCommunity.is_active.is_(True),
            RedditCommunity.purpose == "promo",
        )
        .all(),
    )
    promo_names = [r.name for r in promo_rows]
    if product_id:
        product = (
            db.query(RedditProduct)
            .filter(RedditProduct.id == product_id, RedditProduct.is_active.is_(True))
            .first()
        )
        bound = {c.name for c in (product.communities or [])} if product else set()
        promo_names = promo_names_for_product(promo_names, bound)
    else:
        # 未指定产品：不抽产品社区，避免社区与卖点错配
        promo_names = []

    commented = {
        row[0]
        for row in db.query(RedditComment.target_post_url)
        .filter(RedditComment.site_id == site_id, RedditComment.account_id == account_id)
        .all()
        if row[0]
    }
    # 生成阶段不拦配额；发布时由 publish 路径 enforce_promo_quota
    allow_promo = bool(promo_names)

    def _gen(item: dict, intent: str) -> None:
        generate_comment(item, intent)

    return run_smart_discover(
        persona_communities=[r.name for r in persona_rows],
        promo_communities=promo_names,
        already_commented=commented,
        search_fn=search_posts,
        generate_fn=_gen,
        allow_promo=allow_promo,
        remaining_slots=remaining_slots,
        seed=seed,
    )
