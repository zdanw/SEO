"""Reddit 社区轮转 / 冷却 / 内容角度去重 / 频次自适应 / 表现反馈校准 / 扩池扩散。

设计要点：
- **加权轮转**：weight ∝ 1/(1+近 7 日使用次数) × 1/(1+表现惩罚)；越少用、表现越好越优先。
- **相邻去重**：同账号相邻动作尽量不落在同一社区（硬规则，池 >1 时生效）。
- **社区冷却**：cooldown_until 之前不参与轮转；池过小（账号×社区 ≤ 阈值）时降级为
  仅内容角度冷却（apply_cooldown=False），避免无社区可用而停摆。
- **内容角度轮转**：同一社区内轮换叙事角度（personal_incident/hard_lesson/...），
  近期已用角度自动回避；重复角度作为负面信号不再选用。
- **频次自适应**：per_sub_daily_cap 按 配额/(账号数×社区数) 计算，池小时收紧上限。
- **表现反馈**：用 RedditPostMetric 的每帖最新分数对社区降权 + 冷却（bad 内容沉底）。
"""
from __future__ import annotations

import json
import logging
import math
import random
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.timeutil import as_bj_naive, now_bj
from app.models.reddit import (
    RedditComment,
    RedditCommunity,
    RedditCommunityActivity,
    RedditPost,
    RedditPostMetric,
)
from app.services.reddit_client import normalize_subreddit

logger = logging.getLogger(__name__)

# ============ 内容角度（叙事姿态）============
ANGLE_LIBRARY: dict[str, str] = {
    "personal_incident": "Open with one specific real-life incident (a time, place or object) and stay in that scene.",
    "hard_lesson": "Frame it as a mistake you personally made and what it actually cost you.",
    "ask_advice": "You are genuinely unsure and asking the community for their take, not lecturing.",
    "compare_options": "Compare two options you actually weighed, with honest trade-offs.",
    "small_tip": "Share one small concrete thing that worked for you; no life philosophy.",
    "unpopular_take": "State a mildly unpopular opinion and defend it calmly with one reason.",
}
_DEFAULT_ANGLES: tuple[str, ...] = tuple(ANGLE_LIBRARY)

# 发帖类型轮转（避免同一社区反复 pitfall/guide）
POST_TYPE_ROTATION: tuple[str, ...] = ("pitfall", "vent", "unpopular", "guide", "help_seek")

_ANGLES_KEPT = 8


def angle_hint(angle: str | None) -> str:
    """角度 → 注入 prompt 的一句话指令。"""
    key = str(angle or "").strip()
    text = ANGLE_LIBRARY.get(key)
    return text or ""


def pick_angle(recent: list[str] | None, *, rng: random.Random | None = None) -> str:
    """从未用过的角度里挑一个；全部用过则挑最久未用的（recent 为最近优先）。"""
    rng = rng or random
    used = [str(a) for a in (recent or []) if str(a) in ANGLE_LIBRARY]
    unused = [a for a in _DEFAULT_ANGLES if a not in used]
    if unused:
        return rng.choice(unused)
    for a in reversed(used):  # 最久未用的排最后
        return a
    return rng.choice(_DEFAULT_ANGLES)


def pick_post_type(recent: list[str] | None, *, rng: random.Random | None = None) -> str:
    """同社区内轮换发帖类型；全用过则挑最久未用的。"""
    rng = rng or random
    used = [str(t) for t in (recent or [])]
    unused = [t for t in POST_TYPE_ROTATION if t not in used]
    if unused:
        return rng.choice(unused)
    for t in reversed(used):
        if t in POST_TYPE_ROTATION:
            return t
    return rng.choice(POST_TYPE_ROTATION)


# ============ 内容级表现反馈加权 ============
CONTENT_FEEDBACK_LOOKBACK_DAYS = 30
CONTENT_FEEDBACK_MIN_SAMPLES = 2   # 单维度样本不足 → 中性权重（回退纯轮转）
CONTENT_FEEDBACK_FLOOR = 0.3       # 权重下限：表现再差也保底探索，不为 0
CONTENT_FEEDBACK_CEIL = 4.0        # 权重上限：防止单类型/角度垄断
CONTENT_FEEDBACK_FULL_SCORE = 8.0  # 均分达到该值视为满表现（映射到权重上限）


def content_performance_weights(
    db: Session, *, account_id: int, keys: list[str], field: str = "post_type"
) -> dict[str, float]:
    """按内容维度（post_type / narrative_angle）聚合近 30 天已发布帖子的最新分数。

    返回 {key: weight}；weight = clamp(FLOOR + 均分线性映射到 [FLOOR, CEIL])，
    均分 0 → FLOOR、≥ FULL_SCORE → CEIL、无数据/样本不足的 key 返回中性 1.0。
    与社区级 apply_feedback_calibration 同源数据（RedditPostMetric 每帖最新快照），
    但作用在内容维度：表现差的发帖类型/叙事角度被渐进降权。
    """
    if not keys:
        return {}
    score_col = getattr(RedditPost, field, None)
    if score_col is None:
        return {k: 1.0 for k in keys}
    cutoff = now_bj() - timedelta(days=CONTENT_FEEDBACK_LOOKBACK_DAYS)
    rows = (
        db.query(score_col, RedditPost.id, RedditPostMetric.score)
        .join(RedditPostMetric, RedditPostMetric.post_id == RedditPost.id)
        .filter(
            RedditPost.account_id == account_id,
            RedditPost.status == "posted",
            RedditPost.published_at.isnot(None),
            RedditPost.published_at >= cutoff,
            score_col.in_(keys),
        )
        # synced_at + id 双升序：后写覆盖 = 每帖最新快照（id 兜底同秒快照的确定性）
        .order_by(RedditPostMetric.synced_at.asc(), RedditPostMetric.id.asc())
        .all()
    )
    latest: dict[int, tuple[str, int]] = {}
    for key, pid, score in rows:
        latest[pid] = (str(key), int(score or 0))
    buckets: dict[str, list[int]] = {}
    for key, score in latest.values():
        buckets.setdefault(key, []).append(score)
    span = CONTENT_FEEDBACK_CEIL - CONTENT_FEEDBACK_FLOOR
    weights: dict[str, float] = {}
    for k in keys:
        scores = buckets.get(k, [])
        if len(scores) < CONTENT_FEEDBACK_MIN_SAMPLES:
            weights[k] = 1.0
        else:
            mean = sum(scores) / len(scores)
            weights[k] = max(
                CONTENT_FEEDBACK_FLOOR,
                min(CONTENT_FEEDBACK_CEIL, CONTENT_FEEDBACK_FLOOR + mean * span / CONTENT_FEEDBACK_FULL_SCORE),
            )
    return weights


def _weighted_rotation_pick(
    candidates: tuple[str, ...],
    recent: list[str] | None,
    weights: dict[str, float],
    *,
    rng: random.Random,
) -> str:
    """轮转规则（优先未用过）之上叠加表现权重；全用过则按权重挑最久未用的。"""
    used = [str(x) for x in (recent or [])]
    unused = [c for c in candidates if c not in used]
    if unused:
        return rng.choices(unused, weights=[weights.get(c, 1.0) for c in unused], k=1)[0]
    for c in reversed(used):
        if c in candidates:
            return c
    return rng.choice(candidates)


def pick_post_type_weighted(
    db: Session, account_id: int, recent: list[str] | None, *, rng: random.Random | None = None
) -> str:
    """发帖类型轮转 + 近 30 天表现加权（样本不足自动退化为纯轮转）。"""
    rng = rng or random
    weights = content_performance_weights(
        db, account_id=account_id, keys=list(POST_TYPE_ROTATION), field="post_type"
    )
    return _weighted_rotation_pick(POST_TYPE_ROTATION, recent, weights, rng=rng)


def pick_angle_weighted(
    db: Session, account_id: int, recent: list[str] | None, *, rng: random.Random | None = None
) -> str:
    """叙事角度轮转 + 近 30 天表现加权（样本不足自动退化为纯轮转）。"""
    rng = rng or random
    weights = content_performance_weights(
        db, account_id=account_id, keys=list(_DEFAULT_ANGLES), field="narrative_angle"
    )
    return _weighted_rotation_pick(_DEFAULT_ANGLES, recent, weights, rng=rng)


def max_ngram_similarity(text: str, corpus: list[str], *, n: int = 3) -> float:
    """text 与语料的最大字符 n-gram Jaccard 相似度（0-1）；用于评论雷同检测。"""
    def grams(s: str) -> set[str]:
        s = (s or "").strip().lower()
        if len(s) < n:
            return {s} if s else set()
        return {s[i : i + n] for i in range(len(s) - n + 1)}

    g1 = grams(text)
    if not g1:
        return 0.0
    best = 0.0
    for other in corpus or []:
        g2 = grams(other)
        if not g2:
            continue
        jac = len(g1 & g2) / len(g1 | g2)
        if jac > best:
            best = jac
    return best


# 评论与本账号近期已发布评论的雷同阈值：超过则换角度重生成一次
COMMENT_SIMILARITY_REGEN_THRESHOLD = 0.55
COMMENT_SIMILARITY_CORPUS_SIZE = 10


# ============ 频次自适应 ============
def per_sub_daily_cap(
    *,
    daily_total: int,
    n_accounts: int,
    n_subs: int,
    pool_small_threshold: int = 3,
    hard_max: int = 4,
) -> int:
    """单账号单社区每日内容上限（0 = 不限制）。

    上限 ≈ 配额 / (账号数 × 社区数)；账号×社区 过小时收紧到 2，避免同社区刷屏。
    """
    if daily_total <= 0:
        return 0
    denom = max(1, int(n_accounts)) * max(1, int(n_subs))
    cap = math.ceil(daily_total / denom)
    if denom <= pool_small_threshold:
        cap = min(cap, 2)
    return max(1, min(cap, hard_max))


def pool_is_small(*, n_accounts: int, n_subs: int, threshold: int = 3) -> bool:
    return max(1, int(n_accounts)) * max(1, int(n_subs)) <= threshold


# ============ 活动账本（upsert）============
def _activity_row(db: Session, account_id: int, subreddit: str) -> RedditCommunityActivity | None:
    name = normalize_subreddit(subreddit)
    if not name:
        return None
    return (
        db.query(RedditCommunityActivity)
        .filter(
            RedditCommunityActivity.account_id == account_id,
            func.lower(RedditCommunityActivity.subreddit) == name.lower(),
        )
        .first()
    )


def recent_angles(db: Session, account_id: int, subreddit: str) -> list[str]:
    row = _activity_row(db, account_id, subreddit)
    if not row or not isinstance(row.recent_angles, list):
        return []
    return [str(a) for a in row.recent_angles]


def last_subreddit(db: Session, account_id: int) -> str | None:
    row = (
        db.query(RedditCommunityActivity)
        .filter(RedditCommunityActivity.account_id == account_id)
        .order_by(RedditCommunityActivity.last_action_at.desc())
        .first()
    )
    return row.subreddit if row and row.subreddit else None


def record_activity(
    db: Session,
    *,
    site_id: int,
    account_id: int,
    subreddit: str,
    angle: str | None = None,
    at=None,
) -> None:
    """记录一次动作：更新 last_action_at / 计数 / 追加最近角度（失败静默）。"""
    name = normalize_subreddit(subreddit)
    if not name:
        return
    when = as_bj_naive(at or now_bj())
    row = _activity_row(db, account_id, name)
    try:
        if row is None:
            angles = [angle] if angle in ANGLE_LIBRARY else []
            db.add(
                RedditCommunityActivity(
                    site_id=site_id,
                    account_id=account_id,
                    subreddit=name,
                    last_action_at=when,
                    action_count_7d=1,
                    action_count_30d=1,
                    recent_angles=angles,
                )
            )
        else:
            row.last_action_at = when
            row.action_count_7d = int(row.action_count_7d or 0) + 1
            row.action_count_30d = int(row.action_count_30d or 0) + 1
            if angle in ANGLE_LIBRARY:
                angles = [a for a in (row.recent_angles or []) if a != angle]
                angles.insert(0, angle)
                row.recent_angles = angles[:_ANGLES_KEPT]
        db.commit()
    except Exception:  # noqa: BLE001 — 轮转记账失败不影响主流程
        db.rollback()
        logger.debug("record_activity failed account=%s sub=%s", account_id, name, exc_info=True)


def _today_count(db: Session, account_id: int, subreddit: str, now) -> int:
    day = as_bj_naive(now).replace(hour=0, minute=0, second=0, microsecond=0)
    name = normalize_subreddit(subreddit)
    posts = (
        db.query(func.count(RedditPost.id))
        .filter(
            RedditPost.account_id == account_id,
            func.lower(RedditPost.subreddit) == name.lower(),
            RedditPost.created_at >= day,
        )
        .scalar()
    )
    comments = (
        db.query(func.count(RedditComment.id))
        .filter(
            RedditComment.account_id == account_id,
            func.lower(RedditComment.subreddit) == name.lower(),
            RedditComment.created_at >= day,
        )
        .scalar()
    )
    return int(posts or 0) + int(comments or 0)


# ============ 轮转选择 ============
def _weight(row: RedditCommunityActivity | None) -> float:
    recent = int(row.action_count_7d or 0) if row else 0
    penalty = float(row.penalty or 0) if row else 0.0
    return 1.0 / ((1.0 + recent) * (1.0 + penalty))


def select_subreddit(
    db: Session,
    *,
    account_id: int,
    candidates: list[str],
    now=None,
    rng: random.Random | None = None,
    daily_cap: int = 0,
    apply_cooldown: bool = True,
) -> str:
    """加权轮转选社区：冷却/超频过滤 → 相邻去重 → 按权重随机。

    全部候选都被冷却/超频时回退到全集，保证有内容可发（宁可重复也不停摆）。
    """
    rng = rng or random
    now_n = as_bj_naive(now or now_bj())
    names: list[str] = []
    seen: set[str] = set()
    for raw in candidates or []:
        name = normalize_subreddit(raw)
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)
    if not names:
        return ""

    rows = {
        r.subreddit.lower(): r
        for r in db.query(RedditCommunityActivity)
        .filter(RedditCommunityActivity.account_id == account_id)
        .all()
    }
    pool: list[str] = []
    for name in names:
        row = rows.get(name.lower())
        if apply_cooldown and row and row.cooldown_until:
            if as_bj_naive(row.cooldown_until) > now_n:
                continue
        if daily_cap and _today_count(db, account_id, name, now_n) >= daily_cap:
            continue
        pool.append(name)
    if not pool:
        pool = list(names)

    last = last_subreddit(db, account_id)
    if last and len(pool) > 1:
        filtered = [n for n in pool if n.lower() != last.lower()]
        if filtered:
            pool = filtered

    weights = [_weight(rows.get(n.lower())) for n in pool]
    return rng.choices(pool, weights=weights, k=1)[0]


def order_subreddits(
    db: Session,
    *,
    account_id: int,
    candidates: list[str],
    now=None,
    rng: random.Random | None = None,
    limit: int = 0,
    daily_cap: int = 0,
    apply_cooldown: bool = True,
) -> list[str]:
    """按轮转权重给出有序社区列表（发现抽样等需要多个社区的场景）。"""
    remaining = list(candidates or [])
    out: list[str] = []
    while remaining and (not limit or len(out) < limit):
        pick = select_subreddit(
            db,
            account_id=account_id,
            candidates=remaining,
            now=now,
            rng=rng,
            daily_cap=daily_cap,
            apply_cooldown=apply_cooldown,
        )
        if not pick:
            break
        out.append(pick)
        remaining = [c for c in remaining if normalize_subreddit(c).lower() != pick.lower()]
    return out


# ============ 表现反馈校准 ============
def note_community_feedback(
    db: Session,
    *,
    site_id: int,
    account_id: int,
    subreddit: str,
    penalty: float = 0.5,
    cooldown_days: int = 3,
    now=None,
) -> None:
    name = normalize_subreddit(subreddit)
    if not name:
        return
    when = as_bj_naive(now or now_bj())
    row = _activity_row(db, account_id, name)
    if row is None:
        row = RedditCommunityActivity(
            site_id=site_id,
            account_id=account_id,
            subreddit=name,
            penalty=0.0,
        )
        db.add(row)
    row.penalty = float(row.penalty or 0.0) + float(penalty)
    if cooldown_days > 0:
        row.cooldown_until = when + timedelta(days=cooldown_days)
    db.commit()


def apply_feedback_calibration(
    db: Session,
    *,
    site_id: int,
    now=None,
    min_avg_score: float = 1.0,
    min_samples: int = 2,
    penalty_step: float = 0.5,
    cooldown_days: int = 3,
    lookback_days: int = 30,
) -> int:
    """低表现（account×社区）降权 + 冷却，返回被降权组合数。

    以每帖最新一次 metric 快照为准，均值低于阈值且样本足够时触发。
    只回看近 lookback_days 天的快照：社区表现是近期信号，且限定窗口
    避免随历史数据增长全量扫描（校准现由每日 beat 任务执行）。
    """
    now_n = as_bj_naive(now or now_bj())
    rows = (
        db.query(
            RedditPost.id,
            RedditPost.account_id,
            RedditPost.subreddit,
            RedditPostMetric.score,
            RedditPostMetric.synced_at,
        )
        .join(RedditPostMetric, RedditPostMetric.post_id == RedditPost.id)
        .filter(
            RedditPost.site_id == site_id,
            RedditPostMetric.synced_at >= now_n - timedelta(days=lookback_days),
        )
        .order_by(RedditPostMetric.synced_at.asc(), RedditPostMetric.id.asc())
        .all()
    )
    if not rows:
        return 0
    latest: dict[int, tuple[int, str, int]] = {}
    for pid, account_id, subreddit, score, _synced in rows:
        latest[pid] = (account_id, subreddit, int(score or 0))

    groups: dict[tuple[int, str], list[int]] = {}
    for account_id, subreddit, score in latest.values():
        groups.setdefault((account_id, normalize_subreddit(subreddit)), []).append(score)

    affected = 0
    for (account_id, subreddit), scores in groups.items():
        if not subreddit or len(scores) < min_samples:
            continue
        if sum(scores) / len(scores) >= min_avg_score:
            continue
        existing = _activity_row(db, account_id, subreddit)
        if existing and existing.cooldown_until and as_bj_naive(existing.cooldown_until) > now_n:
            continue  # 冷却期内不重复降权，避免罚分失控
        note_community_feedback(
            db,
            site_id=site_id,
            account_id=account_id,
            subreddit=subreddit,
            penalty=penalty_step,
            cooldown_days=cooldown_days,
            now=now_n,
        )
        affected += 1
    return affected


# ============ 扩池：相似社区扩散 ============
def _suggest_related_names(ai, seeds: list[str], interests: list[str], *, limit: int = 8) -> list[str]:
    if ai is None:
        return []
    try:
        raw = ai.chat(
            "A Reddit persona with interests "
            f"({', '.join(interests) or 'general'}) already posts in: "
            f"{', '.join('r/' + s for s in seeds)}. "
            "Suggest other subreddits with a similar audience and active discussion "
            "(name only, no r/ prefix). Avoid product/marketing subs. "
            f"Return a JSON array of {limit} strings.",
            system_prompt="Return JSON only.",
            temperature=0.5,
            max_tokens=400,
        )
        parsed = json.loads(raw[raw.find("[") : raw.rfind("]") + 1])
        return [str(x) for x in parsed if str(x).strip()]
    except Exception:  # noqa: BLE001
        return []


def _community_quality(db: Session, site_id: int, rows: list[RedditCommunity]) -> dict[str, float]:
    """社区质量分：优先用已发帖的 metric 均分，否则退回 activity_score。"""
    names = [normalize_subreddit(r.name).lower() for r in rows]
    if not names:
        return {}
    metric_rows = (
        db.query(RedditPost.subreddit, RedditPostMetric.score)
        .join(RedditPostMetric, RedditPostMetric.post_id == RedditPost.id)
        .filter(
            RedditPost.site_id == site_id,
            func.lower(RedditPost.subreddit).in_(names),
        )
        .all()
    )
    buckets: dict[str, list[int]] = {}
    for sub, score in metric_rows:
        buckets.setdefault(normalize_subreddit(sub).lower(), []).append(int(score or 0))
    quality: dict[str, float] = {}
    for row in rows:
        key = normalize_subreddit(row.name).lower()
        scores = buckets.get(key)
        if scores:
            quality[key] = sum(scores) / len(scores)
        else:
            quality[key] = float(row.activity_score or 0.0)
    return quality


def expand_similar_communities(
    db: Session,
    *,
    site_id: int,
    account_id: int,
    client,
    ai=None,
    interests: list[str] | None = None,
    seed_limit: int = 2,
    max_new: int = 3,
) -> list[str]:
    """从优质人设社区出发扩散相似社区 → 校验 → 入库（返回新增社区名）。"""
    from app.services.reddit_community_verify import (
        apply_rules_to_community,
        apply_verify_to_community,
        fetch_subreddit_rules_via_zernio,
        verify_subreddits_with_feed_llm,
    )

    rows = (
        db.query(RedditCommunity)
        .filter(
            RedditCommunity.site_id == site_id,
            RedditCommunity.account_id == account_id,
            RedditCommunity.purpose == "persona",
            RedditCommunity.is_active.is_(True),
            RedditCommunity.exists.isnot(False),
        )
        .all()
    )
    if not rows:
        return []
    quality = _community_quality(db, site_id, rows)
    seeds = sorted(
        rows,
        key=lambda r: quality.get(normalize_subreddit(r.name).lower(), 0.0),
        reverse=True,
    )[:seed_limit]
    seed_names = [normalize_subreddit(r.name) for r in seeds if r.name]
    if not seed_names:
        return []

    existing = {normalize_subreddit(r.name).lower() for r in rows}
    related = _suggest_related_names(ai, seed_names, interests or [], limit=max_new * 3)
    to_check = []
    seen: set[str] = set()
    for raw in related:
        name = normalize_subreddit(raw)
        key = name.lower()
        if not name or key in existing or key in seen:
            continue
        seen.add(key)
        to_check.append(name)
    if not to_check:
        return []

    judged = verify_subreddits_with_feed_llm(
        to_check,
        interests=interests or seed_names,
        client=client,
        ai=ai,
    )
    added: list[str] = []
    for result in judged:
        if len(added) >= max_new:
            break
        if result.exists is False or not result.is_active_enough:
            continue
        row = RedditCommunity(
            site_id=site_id,
            account_id=account_id,
            name=result.name,
            category="longtail",
            purpose="persona",
            discovered_from=seed_names[0],
        )
        apply_verify_to_community(row, result)
        rules = fetch_subreddit_rules_via_zernio(client, result.name)
        apply_rules_to_community(row, rules)
        db.add(row)
        added.append(result.name)
    if added:
        db.commit()
    return added
