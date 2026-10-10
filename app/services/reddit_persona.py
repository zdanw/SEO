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
