"""评论生成流水线：AI 写稿 → 真人化 → AI 检测（失败则带诊断重写）。"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.ai_detector import AiDetectionResult, detect_ai_content
from app.services.ai_writer import DeepSeekClient
from app.services.reddit_humanize import humanize_comment
from app.services.reddit_mix import casual_mentions_brand
from app.services.reddit_style import record_detection_patterns


@dataclass
class GeneratedComment:
    body: str
    ai_risk: str  # ok | mixed | likely_ai
    rewrite_count: int = 0
    ai_score_before_humanize: float | None = None
    ai_score_after_humanize: float | None = None


def build_revision_notes(detection: AiDetectionResult) -> str:
    """把检测诊断拼成重写指导，供下一轮 generate_reddit_comment 使用。"""
    parts: list[str] = [
        f"Your previous draft was flagged as {detection.verdict} "
        f"(score {float(detection.score):.0f}). Rewrite in plainer everyday words."
    ]
    tips: list[str] = []
    for p in (detection.signs_of_ai or {}).get("patterns") or []:
        suggestion = (p.get("suggestion") or "").strip()
        name = (p.get("name") or p.get("key") or "").strip()
        matches = [str(m).strip() for m in (p.get("matches") or []) if m][:2]
        if suggestion:
            tips.append(suggestion)
        elif name:
            tip = f"Avoid {name}"
            if matches:
                tip += f" (e.g. {', '.join(matches)})"
            tips.append(tip)
        if len(tips) >= 5:
            break
    if tips:
        parts.append("Avoid: " + "; ".join(tips))
    sentences = [
        str(s.get("text") or "").strip()
        for s in (detection.lmscan or {}).get("high_risk_sentences") or []
        if s.get("text")
    ][:3]
    if sentences:
        parts.append(
            "Rewrite these specific sentences in plainer words: " + " | ".join(sentences)
        )
    return "\n".join(parts)


def _verdict_to_risk(verdict: str) -> str:
    if verdict == "likely_ai":
        return "likely_ai"
    if verdict == "mixed":
        return "mixed"
    return "ok"


def generate_comment_pipeline(
    ai: DeepSeekClient,
    *,
    post_title: str,
    post_body: str,
    subreddit: str,
    intent: str = "casual",
    persona_prompt: str = "",
    product_brief: dict | None = None,
    site_url: str | None = None,
    community_rules: str | None = None,
    seed: int | None = None,
    community_examples: list[dict[str, str]] | None = None,
) -> GeneratedComment:
    brands = []
    if product_brief and product_brief.get("brand"):
        brands.append(str(product_brief["brand"]))
    brief_for_model = product_brief if intent == "promo" else None
    url = site_url if intent == "promo" else None
    max_typos = 2 if intent == "casual" else 1

    body = ""
    risk = "ok"
    rewrites = 0
    revision_notes = ""
    score_before: float | None = None
    score_after: float | None = None

    for attempt in range(2):
        body = ai.generate_reddit_comment(
            post_title=post_title,
            post_body=post_body,
            subreddit=subreddit,
            site_url=url,
            intent=intent,
            persona_prompt=persona_prompt,
            product_brief=brief_for_model,
            community_rules=community_rules,
            revision_notes=revision_notes or None,
            community_examples=community_examples,
        )
        rewrites = attempt
        if intent == "casual" and casual_mentions_brand(body, brands):
            continue

        raw = body
        before = detect_ai_content(raw)
        score_before = float(before.score)
        if before.verdict in {"mixed", "likely_ai"}:
            record_detection_patterns(before.signs_of_ai)

        body = humanize_comment(
            raw,
            seed=None if seed is None else seed + attempt,
            brand_names=tuple(brands),
            max_typos=max_typos,
        )
        after = detect_ai_content(body)
        score_after = float(after.score)
        verdict = after.verdict
        if verdict in {"mixed", "likely_ai"}:
            record_detection_patterns(after.signs_of_ai)

        if verdict != "likely_ai":
            risk = _verdict_to_risk(verdict)
            break
        risk = "likely_ai"
        revision_notes = build_revision_notes(after)
    else:
        risk = "likely_ai"

    return GeneratedComment(
        body=body,
        ai_risk=risk,
        rewrite_count=rewrites,
        ai_score_before_humanize=score_before,
        ai_score_after_humanize=score_after,
    )
