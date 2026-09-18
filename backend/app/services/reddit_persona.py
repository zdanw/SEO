"""账号人设：短备注或结构化 JSON → 生成用 prompt。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class PersonaConfig:
    voice: str = ""
    interests: list[str] = field(default_factory=list)
    quirks: str = ""
    never_say: str = ""

    def to_dict(self) -> dict:
        return {
            "voice": self.voice,
            "interests": list(self.interests),
            "quirks": self.quirks,
            "never_say": self.never_say,
        }

    def to_prompt(self) -> str:
        parts: list[str] = []
        if self.voice:
            parts.append(f"Voice: {self.voice}.")
        if self.interests:
            parts.append("Interests: " + ", ".join(self.interests) + ".")
        if self.quirks:
            parts.append(f"Personal quirks / life details you might mention: {self.quirks}.")
        if self.never_say:
            parts.append(f"Never sound like: {self.never_say}.")
        return " ".join(parts)


def parse_persona(raw: str | dict | None = None, config: dict | None = None) -> PersonaConfig:
    """兼容旧的单行人设备注与新的 persona_config JSON。"""
    if isinstance(config, dict) and any(config.get(k) for k in ("voice", "interests", "quirks", "never_say")):
        interests = config.get("interests") or []
        if isinstance(interests, str):
            interests = [s.strip() for s in interests.split(",") if s.strip()]
        return PersonaConfig(
            voice=str(config.get("voice") or "").strip(),
            interests=[str(i).strip() for i in interests if str(i).strip()],
            quirks=str(config.get("quirks") or "").strip(),
            never_say=str(config.get("never_say") or "").strip(),
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
