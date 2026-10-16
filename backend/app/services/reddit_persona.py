"""账号人设：短备注或结构化 JSON → 生成用 prompt。

结构化字段（persona_config）：
- voice / interests / quirks / never_say：基础口吻
- background：背景故事（一两句）
- life_facts：可复用的具体生活细节，每次生成轮换注入 1 条
- signature_opinions：固定立场（用自己的话表达，不做口号）
- recurring_themes：可自然带出的长期主题
- subreddit_overrides：{subreddit: {voice/quirks/background/themes/life_facts}} 按社区微调

一致性：life_facts / signature_opinions 与 never_say 冲突时给出警告（不阻断生成）。
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field


def _str_list(value) -> list[str]:
    if isinstance(value, str):
        value = [s for s in value.split(",")]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = " ".join(str(item or "").split()).strip()
        if text and text not in out:
            out.append(text)
    return out


def _norm_sub(name) -> str:
    return str(name or "").removeprefix("r/").strip().lower()


@dataclass
class PersonaConfig:
    voice: str = ""
    interests: list[str] = field(default_factory=list)
    quirks: str = ""
    never_say: str = ""
    background: str = ""
    life_facts: list[str] = field(default_factory=list)
    signature_opinions: list[str] = field(default_factory=list)
    recurring_themes: list[str] = field(default_factory=list)
    subreddit_overrides: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "voice": self.voice,
            "interests": list(self.interests),
            "quirks": self.quirks,
            "never_say": self.never_say,
            "background": self.background,
            "life_facts": list(self.life_facts),
            "signature_opinions": list(self.signature_opinions),
            "recurring_themes": list(self.recurring_themes),
            "subreddit_overrides": {k: dict(v) for k, v in self.subreddit_overrides.items()},
        }

    def is_empty(self) -> bool:
        return not any(
            (
                self.voice,
                self.interests,
                self.quirks,
                self.never_say,
                self.background,
                self.life_facts,
                self.signature_opinions,
                self.recurring_themes,
                self.subreddit_overrides,
            )
        )

    def override_for(self, subreddit: str | None) -> dict:
        """返回该社区的人设覆盖（不存在则空 dict）。"""
        key = _norm_sub(subreddit)
        if not key:
            return {}
        for raw_key, value in (self.subreddit_overrides or {}).items():
            if _norm_sub(raw_key) == key and isinstance(value, dict):
                return value
        return {}

    def pick_life_fact(self, *, seed: int | None = None, exclude: tuple[str, ...] = ()) -> str:
        facts = [f for f in self.life_facts if f and f not in set(exclude)]
        if not facts:
            return ""
        rng = random.Random(seed) if seed is not None else random
        return rng.choice(facts)

    def consistency_warnings(self) -> list[str]:
        """life_facts / signature_opinions 与 never_say 词面冲突提示。"""
        banned = [
            t
            for t in (s.strip().lower() for s in (self.never_say or "").replace("，", ",").split(","))
            if len(t) >= 3
        ]
        if not banned:
            return []
        warnings: list[str] = []
        for kind, items in (
            ("life_fact", self.life_facts),
            ("signature_opinion", self.signature_opinions),
        ):
            for text in items:
                low = text.lower()
                for token in banned:
                    if token in low:
                        warnings.append(f"{kind}「{text}」与 never_say 冲突（包含「{token}」）")
                        break
        return warnings

    def to_prompt(
        self,
        *,
        subreddit: str | None = None,
        seed: int | None = None,
        exclude_facts: tuple[str, ...] = (),
    ) -> str:
        ov = self.override_for(subreddit)
        voice = str(ov.get("voice") or self.voice).strip()
        quirks = str(ov.get("quirks") or self.quirks).strip()
        background = str(ov.get("background") or self.background).strip()
        themes = _str_list(ov.get("themes")) or list(self.recurring_themes)
        facts = _str_list(ov.get("life_facts")) or list(self.life_facts)

        parts: list[str] = []
        if voice:
            parts.append(f"Voice: {voice}.")
        if background:
            parts.append(f"Background: {background}")
        if self.interests:
            parts.append("Interests: " + ", ".join(self.interests) + ".")
        if self.signature_opinions:
            parts.append(
                "Opinions you hold (state them as your own take in your own words, never as slogans): "
                + "; ".join(self.signature_opinions[:4])
                + "."
            )
        if themes:
            parts.append("Recurring themes you may bring up naturally: " + ", ".join(themes[:6]) + ".")
        fact = ""
        if facts:
            pool = [f for f in facts if f not in set(exclude_facts)] or facts
            rng = random.Random(seed) if seed is not None else random
            fact = rng.choice(pool)
        if fact:
            parts.append(f"One concrete life detail you may mention at most once if it fits: {fact}.")
        if quirks:
            parts.append(f"Personal quirks / life details you might mention: {quirks}.")
        if self.never_say:
            parts.append(f"Never sound like: {self.never_say}.")
        return " ".join(parts)


ROLE_HINTS: dict[str, str] = {
    "warmup": "a pure warm-up account that only interacts (comments/votes), never posts links",
    "seeding": "an ordinary real user who occasionally shares genuine experiences and may mention products naturally",
    "expert": "a knowledgeable practitioner who answers questions with practical, experience-based advice",
}

# 每站点每日人设生成次数限额（Redis INCRBY，成本控制）
PERSONA_AI_DAILY_LIMIT = 20

PERSONA_GENERATE_PROMPT = """Design a believable Reddit persona for an account.

Account role: {role_hint}
User requirements: {requirements}

Return ONE JSON object only (no markdown fences), all text content in English, sounding like a real native Reddit user:
{{
  "voice": "one-line identity, e.g. '32yo night-shift nurse in a midwest town'",
  "interests": ["3-6 short lowercase-ish tags"],
  "quirks": "1-2 sentence habits / speech texture, casual",
  "never_say": "tones to avoid, comma separated, e.g. 'sales pitch, expert lecture, corporate PR'",
  "background": "1-2 sentences of backstory that explains the interests",
  "life_facts": ["4-6 ultra-concrete reusable details, e.g. 'drives a 12-year-old hatchback'"],
  "signature_opinions": ["3-5 firm personal takes, stated casually, never slogans"],
  "recurring_themes": ["3-5 topics this person naturally returns to"],
  "subreddit_overrides": {{"optional": "only if requirements mention specific communities; keys are subreddit names without r/, values like {{\\"voice\\": \\"...\\", \\"life_facts\\": [...]}}; return empty object if unsure"}},
  "suggested_daily_post_limit": 0-3,
  "suggested_daily_comment_limit": 3-15
}}
Rules: concrete over generic (no 'loves long walks'); opinions must be specific and mildly opinionated; life_facts must be verifiable-feeling micro details; never mention brands unless the requirements demand it; stay consistent with each other."""


def generate_persona_config(
    requirements: str,
    *,
    role: str | None = None,
    ai=None,
) -> dict:
    """调用 LLM 生成结构化 persona_config（不落库，返回 dict 供确认后保存）。"""
    if ai is None:
        raise ValueError("ai client is required")
    text = (requirements or "").strip()
    if not text:
        raise ValueError("requirements is required")
    role_hint = ROLE_HINTS.get((role or "").strip(), ROLE_HINTS["seeding"])
    prompt = PERSONA_GENERATE_PROMPT.format(role_hint=role_hint, requirements=text[:2000])
    raw = ai.chat(
        prompt,
        system_prompt="Return JSON only.",
        temperature=0.9,
        max_tokens=1200,
    )
    from app.services.ai_writer import _extract_json

    try:
        data = json.loads(_extract_json(raw))
    except json.JSONDecodeError as e:
        raise ValueError(f"AI 返回的不是合法 JSON：{raw[:200]}") from e
    if not isinstance(data, dict):
        raise ValueError("AI 返回的 JSON 不是对象")
    return normalize_generated_persona(data)


def normalize_generated_persona(data: dict) -> dict:
    """清洗 LLM 输出的 persona 字段，超出 schema 的键丢弃。"""
    overrides: dict[str, dict] = {}
    raw_overrides = data.get("subreddit_overrides")
    if isinstance(raw_overrides, dict):
        for key, value in raw_overrides.items():
            name = _norm_sub(key)
            if name and isinstance(value, dict):
                overrides[name] = value

    def _int_between(value, low: int, high: int, default: int) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return default
        return max(low, min(high, n))

    post_limit = _int_between(data.get("suggested_daily_post_limit"), 0, 20, 0)
    comment_limit = _int_between(data.get("suggested_daily_comment_limit"), 0, 100, 10)
    out: dict = {
        "voice": str(data.get("voice") or "").strip()[:200],
        "interests": _str_list(data.get("interests"))[:10],
        "quirks": str(data.get("quirks") or "").strip(),
        "never_say": str(data.get("never_say") or "").strip(),
        "background": str(data.get("background") or "").strip(),
        "life_facts": _str_list(data.get("life_facts"))[:10],
        "signature_opinions": _str_list(data.get("signature_opinions"))[:8],
        "recurring_themes": _str_list(data.get("recurring_themes"))[:10],
        "subreddit_overrides": overrides,
        "suggested_daily_post_limit": post_limit,
        "suggested_daily_comment_limit": comment_limit,
    }
    if not out["voice"] and not out["interests"] and not out["background"]:
        raise ValueError("AI 返回的人设内容为空")
    return out


def parse_persona(raw: str | dict | None = None, config: dict | None = None) -> PersonaConfig:
    """兼容旧的单行人设备注与新的 persona_config JSON。"""
    if isinstance(config, dict) and any(
        config.get(k)
        for k in (
            "voice",
            "interests",
            "quirks",
            "never_say",
            "background",
            "life_facts",
            "signature_opinions",
            "recurring_themes",
            "subreddit_overrides",
        )
    ):
        overrides_raw = config.get("subreddit_overrides")
        overrides: dict[str, dict] = {}
        if isinstance(overrides_raw, dict):
            for key, value in overrides_raw.items():
                if isinstance(value, dict):
                    overrides[str(key)] = value
        return PersonaConfig(
            voice=str(config.get("voice") or "").strip(),
            interests=_str_list(config.get("interests")),
            quirks=str(config.get("quirks") or "").strip(),
            never_say=str(config.get("never_say") or "").strip(),
            background=str(config.get("background") or "").strip(),
            life_facts=_str_list(config.get("life_facts")),
            signature_opinions=_str_list(config.get("signature_opinions")),
            recurring_themes=_str_list(config.get("recurring_themes")),
            subreddit_overrides=overrides,
        )
    if isinstance(raw, dict):
        return parse_persona(None, config=raw)
    text = (raw or "").strip()
    if not text:
        return PersonaConfig()
    if text.startswith("{") and text.endswith("}"):
        try:
            return parse_persona(None, config=json.loads(text))
        except json.JSONDecodeError:
            pass
    return PersonaConfig(voice=text)
