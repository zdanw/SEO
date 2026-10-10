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


def normalize_product_keywords(raw: list[str] | None, *, limit: int = 20) -> list[str]:
    """trim / 去空 / 大小写去重，保留首次出现的原文大小写。"""
    out: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        term = " ".join(str(item or "").split()).strip()
        if not term:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(term)
        if len(out) >= limit:
            break
    return out


def product_search_terms(*, keywords: list[str] | None = None) -> list[str]:
    """仅产品绑定关键词，不用名称/品类/卖点。"""
    return normalize_product_keywords(keywords)


def pick_promo_search_keyword(terms: list[str], *, seed: int | None = None) -> str:
    ordered = shuffled_promo_keywords(terms, seed=seed)
    return ordered[0] if ordered else ""


def shuffled_promo_keywords(terms: list[str], *, seed: int | None = None) -> list[str]:
    """产品关键词随机顺序，供搜索失败时依次重试。"""
    clean = [t for t in terms if t]
    if not clean:
        return []
    out = list(clean)
    rng = random.Random(seed) if seed is not None else random
    rng.shuffle(out)
    return out


def relevance_score(item: dict, terms: list[str]) -> int:
    """词边界匹配，避免短词误伤（如 car ⊂ career）。多词短语仍用子串。"""
    if not terms:
        return 0
    text = f"{item.get('title') or ''} {item.get('body') or ''}".lower()
    hits = 0
    for t in terms:
        term = (t or "").strip().lower()
        if not term:
            continue
        if " " in term:
            if term in text:
                hits += 1
        elif re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text):
            hits += 1
    return hits


def opportunity_score(item: dict, *, now: datetime | None = None) -> float:
    """问句 + 新鲜度加分；评论很多的热帖强扣分，避免抢高楼。"""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    score = 0.0
    title = item.get("title") or ""
    if QUESTION_RE.search(title):
        score += 3.0
    created = int(item.get("created_utc") or 0)
    if created:
        age_h = max(0.0, (now.timestamp() - created) / 3600.0)
        score += max(0.0, 3.0 - age_h / 12.0)
    n_comments = int(item.get("num_comments") or item.get("comment_count") or 0)
    if n_comments >= 50:
        score -= 6.0
    elif n_comments >= 15:
        score -= 3.0
    elif n_comments < 5:
        score += 2.0
    else:
        score += 1.0
    return score


def rank_by_opportunity(items: list[dict], *, now: datetime | None = None) -> list[dict]:
    return sorted(items, key=lambda i: opportunity_score(i, now=now), reverse=True)


def prefer_product_relevant(items: list[dict], terms: list[str], *, now: datetime | None = None) -> list[dict]:
    """只保留标题/正文命中产品词的帖；无命中则返回空。按机会分排序，相关度作次键。"""
    if not terms or not items:
        return list(items)
    matched = [i for i in items if relevance_score(i, terms) > 0]
    if not matched:
        return []
    return sorted(
        matched,
        key=lambda i: (opportunity_score(i, now=now), relevance_score(i, terms)),
        reverse=True,
    )


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
    targets: list[tuple[str, str]] | None = None,
    product_terms: list[str] | None = None,
) -> dict:
    """无 DB 的发现循环，供单测与任务复用。"""
    if targets is None:
        targets = pick_discover_targets(
            persona_communities,
            promo_communities,
            seed=seed,
            allow_promo=allow_promo,
        )
    terms = [t for t in (product_terms or []) if t]
    promo_keywords = shuffled_promo_keywords(terms, seed=seed)
    pull_limit = max(feed_limit, 15) if terms else feed_limit
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
        if intent == "promo":
            if not promo_keywords:
                continue
            candidates: list[dict] = []
            rate_limited = False
            for keyword in promo_keywords:
                try:
                    raw = search_fn(subreddit, keyword, pull_limit)
                except Exception as exc:
                    errors += 1
                    last_error = str(exc)
                    status = getattr(exc, "status_code", None)
                    if status == 429 or "429" in last_error:
                        rate_limited = True
                        break
                    continue
                candidates = rank_by_opportunity(
                    filter_commentable(raw, already_commented=used_urls, now=now),
                    now=now,
                )
                candidates = prefer_product_relevant(candidates, terms, now=now)
                if candidates:
                    break
            if rate_limited:
                break
            if not candidates:
                continue
        elif intent == "casual":
            keyword = "discussion"
            try:
                raw = search_fn(subreddit, keyword, pull_limit)
            except Exception as exc:
                errors += 1
                last_error = str(exc)
                status = getattr(exc, "status_code", None)
                if status == 429 or "429" in last_error:
                    break
                continue
            candidates = rank_by_opportunity(
                filter_commentable(raw, already_commented=used_urls, now=now),
                now=now,
            )
        else:
            keyword = subreddit
            try:
                raw = search_fn(subreddit, keyword, pull_limit)
            except Exception as exc:
                errors += 1
                last_error = str(exc)
                status = getattr(exc, "status_code", None)
                if status == 429 or "429" in last_error:
                    break
                continue
            candidates = rank_by_opportunity(
                filter_commentable(raw, already_commented=used_urls, now=now),
                now=now,
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
    subreddits: list[str] | None = None,
) -> dict:
    from app.models.reddit import RedditComment, RedditCommunity, RedditProduct

    commented = {
        row[0]
        for row in db.query(RedditComment.target_post_url)
        .filter(RedditComment.site_id == site_id, RedditComment.account_id == account_id)
        .all()
        if row[0]
    }

    product = None
    product_terms: list[str] = []
    if product_id:
        product = (
            db.query(RedditProduct)
            .filter(RedditProduct.id == product_id, RedditProduct.is_active.is_(True))
            .first()
        )
        if product:
            rows = getattr(product, "keyword_rows", None) or []
            product_terms = product_search_terms(
                keywords=[getattr(r, "keyword", "") for r in rows],
            )

    def _gen(item: dict, intent: str) -> None:
        generate_comment(item, intent)

    # 手选社区：每个社区 1 条，意图由社区库 purpose 决定（未知则 casual）
    if subreddits:
        manual_targets: list[tuple[str, str]] = []
        seen: set[str] = set()
        for raw in subreddits:
            sr = normalize_subreddit(raw)
            key = sr.lower()
            if not sr or key in seen:
                continue
            seen.add(key)
            row = (
                db.query(RedditCommunity)
                .filter(RedditCommunity.site_id == site_id, RedditCommunity.name == sr)
                .first()
            )
            intent = "promo" if row and row.purpose == "promo" else "casual"
            manual_targets.append((sr, intent))
        if not manual_targets:
            return {"queued": 0, "skipped": 0, "errors": 0, "last_error": "", "reason": "no_communities"}
        return run_smart_discover(
            persona_communities=[],
            promo_communities=[],
            already_commented=commented,
            search_fn=search_posts,
            generate_fn=_gen,
            allow_promo=True,
            remaining_slots=remaining_slots,
            seed=seed,
            targets=manual_targets,
            product_terms=product_terms,
        )

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
        bound = {c.name for c in (product.communities or [])} if product else set()
        promo_names = promo_names_for_product(promo_names, bound)
    else:
        # 未指定产品：不抽产品社区，避免社区与卖点错配
        promo_names = []

    # 生成阶段不拦配额；发布时由 publish 路径 enforce_promo_quota
    allow_promo = bool(promo_names)

    return run_smart_discover(
        persona_communities=[r.name for r in persona_rows],
        promo_communities=promo_names,
        already_commented=commented,
        search_fn=search_posts,
        generate_fn=_gen,
        allow_promo=allow_promo,
        remaining_slots=remaining_slots,
        seed=seed,
        product_terms=product_terms,
    )
