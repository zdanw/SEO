"""评论生成流水线：AI 写稿 → 品牌闸 → 真人化 → AI 检测再写一轮。"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.ai_detector import detect_ai_content
from app.services.ai_writer import DeepSeekClient
from app.services.reddit_humanize import humanize_comment
from app.services.reddit_mix import casual_mentions_brand


@dataclass
class GeneratedComment:
    body: str
    ai_risk: str  # ok | likely_ai
    rewrite_count: int = 0


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
    seed: int | None = None,
) -> GeneratedComment:
    brands = []
    if product_brief and product_brief.get("brand"):
        brands.append(str(product_brief["brand"]))
    brief_for_model = product_brief if intent == "promo" else None
    url = site_url if intent == "promo" else None

    body = ""
    risk = "ok"
    rewrites = 0
    for attempt in range(2):
        body = ai.generate_reddit_comment(
            post_title=post_title,
            post_body=post_body,
            subreddit=subreddit,
            site_url=url,
            intent=intent,
            persona_prompt=persona_prompt,
            product_brief=brief_for_model,
        )
        rewrites = attempt
        if intent == "casual" and casual_mentions_brand(body, brands):
            continue
        verdict = detect_ai_content(body).verdict
        if verdict != "likely_ai":
            risk = "ok" if verdict != "mixed" else "ok"
            break
        risk = "likely_ai"
    else:
        risk = "likely_ai"

    max_typos = 2 if intent == "casual" else 1
    body = humanize_comment(body, seed=seed, brand_names=tuple(brands), max_typos=max_typos)
    return GeneratedComment(body=body, ai_risk=risk, rewrite_count=rewrites)
