"""Reddit 讨论发现：人设/产品双池抽样、新帖过滤、问句优先。"""
from __future__ import annotations

import json
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy.orm import Session

from app.services.reddit_client import normalize_subreddit
from app.services.reddit_mix import can_enqueue_promo, count_mix_window

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


def smart_discover_for_account(
    db: Session,
    *,
    site_id: int,
    account_id: int,
    generate_comment,
    search_posts,
    remaining_slots: int,
    seed: int | None = None,
) -> dict:
    from app.models.reddit import RedditComment, RedditCommunity

    persona_rows = (
        db.query(RedditCommunity)
        .filter(
            RedditCommunity.site_id == site_id,
            RedditCommunity.is_active.is_(True),
            RedditCommunity.purpose == "persona",
            RedditCommunity.account_id == account_id,
        )
        .all()
    )
    promo_rows = (
        db.query(RedditCommunity)
        .filter(
            RedditCommunity.site_id == site_id,
            RedditCommunity.is_active.is_(True),
            RedditCommunity.purpose == "promo",
        )
        .all()
    )
    commented = {
        row[0]
        for row in db.query(RedditComment.target_post_url)
        .filter(RedditComment.site_id == site_id, RedditComment.account_id == account_id)
        .all()
        if row[0]
    }
    mix = count_mix_window(db, site_id)
    allow_promo = can_enqueue_promo(casual_count=mix.casual, promo_count=mix.promo)

    def _gen(item: dict, intent: str) -> None:
        generate_comment(item, intent)

    return run_smart_discover(
        persona_communities=[r.name for r in persona_rows],
        promo_communities=[r.name for r in promo_rows],
        already_commented=commented,
        search_fn=search_posts,
        generate_fn=_gen,
        allow_promo=allow_promo,
        remaining_slots=remaining_slots,
        seed=seed,
    )
