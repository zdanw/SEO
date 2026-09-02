"""AI 内容混合检测：lmscan（统计特征）+ Signs of AI（ai-text-audit 模式规则）。

评分约定：0-100，越高越像 AI 生成（与现有 ai_detected_score 一致）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.core.config import settings
from app.services.seo_analyzer import _strip_markdown

logger = logging.getLogger(__name__)

# Signs of AI 模式名 → 中文展示
_PATTERN_LABELS: dict[str, str] = {
    "ai_vocabulary": "AI 高频词汇",
    "boast_language": "夸张褒扬用语",
    "promotional": "营销套话",
    "filler_phrase": "空洞填充短语",
    "conjunctive_adverb_overuse": "连接副词滥用",
    "rule_of_three": "三段式排比",
    "negative_parallelism": "否定平行结构",
    "em_dash_overuse": "破折号过度使用",
    "vague_attribution": "模糊归因",
    "chatbot_phrase": "聊天机器人套话",
    "ing_superficial": "肤浅 -ing 分析",
    "sycophantic": "谄媚式表达",
}


@dataclass
class AiDetectionResult:
    """AI 检测结果。"""

    score: Decimal
    verdict: str  # human_like | mixed | likely_ai | insufficient_text
    engine: str  # hybrid | lmscan | signs_of_ai | heuristic
    lmscan: dict[str, Any] = field(default_factory=dict)
    signs_of_ai: dict[str, Any] = field(default_factory=dict)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": float(self.score),
            "verdict": self.verdict,
            "engine": self.engine,
            "message": self.message,
            "lmscan": self.lmscan,
            "signs_of_ai": self.signs_of_ai,
        }


def detect_ai_content(content: str) -> AiDetectionResult:
    """对正文执行混合 AI 检测，不可用时回退启发式规则。"""
    plain = _strip_markdown(content or "")
    min_chars = settings.AI_DETECTOR_MIN_CHARS

    if len(plain) < min_chars:
        return AiDetectionResult(
            score=Decimal("0"),
            verdict="insufficient_text",
            engine="none",
            message=f"正文不足 {min_chars} 字，跳过 AI 检测",
        )

    mode = (settings.AI_DETECTOR or "hybrid").lower()
    lmscan_data: dict[str, Any] | None = None
    signs_data: dict[str, Any] | None = None

    if mode in ("hybrid", "lmscan"):
        lmscan_data = _run_lmscan(plain)
    if mode in ("hybrid", "signs_of_ai", "signs"):
        signs_data = _run_signs_of_ai(plain)

    if lmscan_data or signs_data:
        return _combine_results(lmscan_data, signs_data, mode)

    # 兜底：旧版启发式
    from app.services.seo_analyzer import _estimate_ai_content

    score = _estimate_ai_content(content)
    return AiDetectionResult(
        score=score,
        verdict=_score_to_verdict(float(score)),
        engine="heuristic",
        message="未安装 lmscan / ai-text-audit，使用内置启发式规则",
    )


def _run_lmscan(text: str) -> dict[str, Any] | None:
    try:
        from lmscan import scan
    except ImportError:
        logger.warning("lmscan 未安装，跳过统计检测")
        return None

    try:
        result = scan(text)
    except Exception as exc:
        logger.warning("lmscan 检测失败: %s", exc)
        return None

    high_risk = [
        {
            "text": s.text[:120],
            "score": round(s.ai_probability * 100, 1),
        }
        for s in (result.sentence_scores or [])
        if s.ai_probability >= 0.5
    ]
    high_risk.sort(key=lambda x: x["score"], reverse=True)
    # 去重相同句子
    seen: set[str] = set()
    unique_risk: list[dict[str, Any]] = []
    for item in high_risk:
        if item["text"] in seen:
            continue
        seen.add(item["text"])
        unique_risk.append(item)

    attribution = []
    for m in (result.model_attribution or [])[:3]:
        attribution.append({
            "model": m.model,
            "confidence": round(m.confidence * 100, 1),
            "evidence": list(m.evidence[:5]) if m.evidence else [],
        })

    score = round(result.ai_probability * 100, 2)
    return {
        "score": score,
        "verdict": result.verdict,
        "confidence": result.confidence,
        "model_attribution": attribution,
        "high_risk_sentences": unique_risk[:5],
    }


def _run_signs_of_ai(text: str) -> dict[str, Any] | None:
    try:
        from ai_text_audit import Auditor
    except ImportError:
        logger.warning("ai-text-audit 未安装，跳过 Signs of AI 检测")
        return None

    try:
        auditor = Auditor()
        result = auditor.analyze(text)
    except Exception as exc:
        logger.warning("ai-text-audit 检测失败: %s", exc)
        return None

    patterns = []
    for p in (result.patterns or []):
        patterns.append({
            "key": p.name,
            "name": _PATTERN_LABELS.get(p.name, p.name),
            "severity": p.severity,
            "count": p.count,
            "matches": list(p.matches[:8]) if p.matches else [],
            "suggestion": p.suggestion or "",
        })

    return {
        "score": round(float(result.score), 2),
        "verdict": result.verdict,
        "patterns_matched": len(patterns),
        "patterns": patterns[:8],
        "suggestions": list(result.suggestions[:5]) if result.suggestions else [],
    }


def _combine_results(
    lmscan: dict[str, Any] | None,
    signs: dict[str, Any] | None,
    mode: str,
) -> AiDetectionResult:
    scores: list[tuple[float, float]] = []  # (score, weight)

    if lmscan and lmscan.get("score") is not None:
        scores.append((float(lmscan["score"]), settings.AI_DETECTOR_LMSCAN_WEIGHT))
    if signs and signs.get("score") is not None:
        scores.append((float(signs["score"]), settings.AI_DETECTOR_SIGNS_WEIGHT))

    if not scores:
        from app.services.seo_analyzer import _estimate_ai_content

        score = _estimate_ai_content("")
        return AiDetectionResult(
            score=score,
            verdict="mixed",
            engine="heuristic",
            message="检测引擎均不可用",
        )

    total_weight = sum(w for _, w in scores)
    combined = sum(s * w for s, w in scores) / total_weight
    combined = round(combined, 2)

    engine = mode
    if lmscan and signs:
        engine = "hybrid"
    elif lmscan:
        engine = "lmscan"
    else:
        engine = "signs_of_ai"

    return AiDetectionResult(
        score=Decimal(str(combined)),
        verdict=_score_to_verdict(combined),
        engine=engine,
        lmscan=lmscan or {},
        signs_of_ai=signs or {},
        message=_build_summary(combined, lmscan, signs),
    )


def _score_to_verdict(score: float) -> str:
    if score < 30:
        return "human_like"
    if score < 60:
        return "mixed"
    return "likely_ai"


def _build_summary(
    combined: float,
    lmscan: dict[str, Any] | None,
    signs: dict[str, Any] | None,
) -> str:
    parts: list[str] = []
    if lmscan:
        parts.append(f"lmscan {lmscan.get('score', 0):.0f}分")
    if signs:
        matched = signs.get("patterns_matched", 0)
        parts.append(f"Signs of AI {signs.get('score', 0):.0f}分（命中 {matched} 类模式）")
    summary = "，".join(parts)
    if combined < 30:
        return f"综合 {combined:.0f} 分，文风接近真人（{summary}）"
    if combined < 60:
        return f"综合 {combined:.0f} 分，存在部分 AI 痕迹（{summary}）"
    return f"综合 {combined:.0f} 分，AI 特征明显（{summary}）"
