"""Content Optimizer SERP 内容评分（移植自 content-optimizer-mcp 开源逻辑）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SerpScoreResult:
    """SERP 内容优化评分结果。"""

    overall_score: int
    categories: dict[str, dict[str, Any]]
    recommendations: list[str] = field(default_factory=list)
    serp_benchmark: dict[str, Any] = field(default_factory=dict)
    missing_topics: list[str] = field(default_factory=list)
    covered_topics: list[str] = field(default_factory=list)
    coverage_percentage: int = 0
    data_source: str = "simulated"
    top_competitors: list[dict[str, Any]] = field(default_factory=list)
    skipped_competitors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_score": self.overall_score,
            "categories": self.categories,
            "recommendations": self.recommendations,
            "serp_benchmark": self.serp_benchmark,
            "missing_topics": self.missing_topics,
            "covered_topics": self.covered_topics,
            "coverage_percentage": self.coverage_percentage,
            "data_source": self.data_source,
            "top_competitors": self.top_competitors,
            "skipped_competitors": self.skipped_competitors,
            "engine": "content_optimizer",
        }


_BASE_TOPICS = [
    "definition", "benefits", "best practices", "examples",
    "tools", "strategies", "common mistakes", "tips",
    "comparison", "how it works", "getting started", "advanced techniques",
    "case studies", "statistics", "trends", "FAQ",
    "cost", "alternatives", "implementation", "ROI",
]

_TOPIC_LABELS: dict[str, str] = {
    "Word Count": "字数",
    "Keyword Usage": "关键词使用",
    "Heading Structure": "标题结构",
    "Readability": "可读性",
    "Entity Coverage": "话题覆盖",
    "Content Depth": "内容深度",
    "Internal Structure": "内容结构",
}


def score_content_for_serp(
    content: str,
    keyword: str,
    *,
    target_word_count: int | None = None,
    region: str | None = None,
) -> SerpScoreResult:
    """对正文按目标关键词做 SERP 7 维评分。"""
    kw = keyword.strip()
    if not kw:
        raise ValueError("关键词不能为空")

    serp, data_source = analyze_serp(kw, region=region)
    topics = serp.get("commonTopics") or _get_keyword_topics(kw)
    target = target_word_count or serp["averages"]["recommendedWordCount"]
    scored = _score_content(content, kw, target, topics=topics)
    content_lower = content.lower()
    covered = [t for t in topics if _topic_in_content(t, content_lower)]
    missing = [t for t in topics if not _topic_in_content(t, content_lower)]
    coverage = round(len(covered) / len(topics) * 100) if topics else 0

    recs = list(scored["recommendations"])
    words = _count_words(content)
    source_label = "真实 SERP（ScrapingBee）" if data_source == "scrapingbee" else "模拟基准"
    if words < serp["averages"]["recommendedWordCount"] * 0.7:
        recs.insert(
            0,
            f"正文 {words} 词，{source_label}竞品均值 {serp['averages']['wordCount']} 词，"
            f"建议扩充至 {serp['averages']['recommendedWordCount']}+ 词。",
        )
    if missing:
        recs.append(f"补充缺失话题：{', '.join(missing[:5])}。")

    categories: dict[str, dict[str, Any]] = {}
    for name, cat in scored["categories"].items():
        label = _TOPIC_LABELS.get(name, name)
        categories[label] = {
            "score": cat["score"],
            "max_score": cat["maxScore"],
            "details": cat["details"],
            "key": name,
        }

    top_competitors = [
        {
            "position": r.get("googleRank", r.get("position", i + 1)),
            "title": r.get("title", ""),
            "domain": r.get("domain", ""),
            "word_count": r.get("wordCount", 0),
            "heading_count": r.get("headingCount", 0),
            "url": r.get("url", ""),
        }
        for i, r in enumerate(serp.get("topResults") or [])
    ]
    skipped_competitors = [
        {
            "position": s.get("google_rank"),
            "title": s.get("title", ""),
            "url": s.get("url", ""),
            "domain": s.get("domain", ""),
            "reason": s.get("reason", ""),
        }
        for s in (serp.get("skippedResults") or [])
    ]

    return SerpScoreResult(
        overall_score=int(scored["overallScore"]),
        categories=categories,
        recommendations=_dedupe(recs)[:12],
        serp_benchmark={
            "avg_word_count": serp["averages"]["wordCount"],
            "recommended_word_count": serp["averages"]["recommendedWordCount"],
            "avg_headings": serp["averages"]["headingCount"],
            "serp_features": serp.get("serpFeatures", {}),
            "pages_analyzed": serp.get("pagesAnalyzed", 0),
            "organic_count": serp.get("organicCount", 0),
        },
        missing_topics=missing[:10],
        covered_topics=covered[:10],
        coverage_percentage=coverage,
        data_source=data_source,
        top_competitors=top_competitors[:8],
        skipped_competitors=skipped_competitors[:8],
    )


def analyze_serp(keyword: str, region: str | None = None) -> tuple[dict[str, Any], str]:
    """获取 SERP 竞品基准。优先 ScrapingBee 真实数据，失败则回退模拟。"""
    from app.services.serp_benchmark import fetch_serp_benchmark

    real = fetch_serp_benchmark(keyword, region=region)
    if real:
        return real, "scrapingbee"
    return _analyze_serp_simulated(keyword), "simulated"


def _analyze_serp_simulated(keyword: str) -> dict[str, Any]:
    """模拟 SERP 竞品基准（无 ScrapingBee 时回退）。"""
    rng = _seeded_random(keyword.lower())
    topics = _get_keyword_topics(keyword)
    results = []
    titles_suffix = [
        "Complete Guide", "Everything You Need to Know", "Ultimate Guide",
        "Best Practices", "How to Get Started", "Expert Tips",
        "A Comprehensive Overview", "Step-by-Step Guide",
        "What You Should Know", "Top Strategies",
    ]
    kw_cap = keyword[:1].upper() + keyword[1:]
    for i in range(10):
        wc = 800 + int(rng() * 2500)
        hc = 3 + int(rng() * 12)
        results.append({
            "position": i + 1,
            "title": f"{kw_cap}: {titles_suffix[i]}",
            "wordCount": wc,
            "headingCount": hc,
        })
    avg_wc = round(sum(r["wordCount"] for r in results) / 10)
    avg_h = round(sum(r["headingCount"] for r in results) / 10)
    return {
        "keyword": keyword,
        "topResults": results,
        "averages": {
            "wordCount": avg_wc,
            "headingCount": avg_h,
            "recommendedWordCount": round(avg_wc * 1.1),
        },
        "commonTopics": topics,
        "serpFeatures": {
            "featuredSnippet": rng() > 0.4,
            "peopleAlsoAsk": rng() > 0.2,
            "videoResults": rng() > 0.5,
        },
    }


def _topic_in_content(topic: str, content_lower: str) -> bool:
    """话题是否出现在正文中（支持多词部分匹配）。"""
    t = topic.lower().strip()
    if t in content_lower:
        return True
    words = [w for w in re.findall(r"[a-z0-9]+", t) if len(w) > 3]
    if not words:
        return False
    matched = sum(1 for w in words if w in content_lower)
    return matched >= max(1, len(words) // 2)


def _score_content(
    content: str,
    keyword: str,
    target: int,
    *,
    topics: list[str] | None = None,
) -> dict[str, Any]:
    words = _count_words(content)
    headings = _extract_headings(content)
    readability_score, grade_level = _flesch_kincaid(content)
    topic_list = topics if topics else _get_keyword_topics(keyword)
    content_lower = content.lower()
    kw_lower = keyword.lower()
    categories: dict[str, dict[str, Any]] = {}
    recommendations: list[str] = []

    # 1. Word Count
    word_ratio = min(words / target, 1.5) if target else 0
    if word_ratio < 0.5:
        word_score = round(word_ratio * 10)
    elif word_ratio < 0.8:
        word_score = round(5 + (word_ratio - 0.5) * 20)
    elif word_ratio <= 1.2:
        word_score = round(11 + (min(word_ratio, 1.0) - 0.8) * 20)
    else:
        word_score = max(10, 15 - round((word_ratio - 1.2) * 10))
    word_score = min(15, max(0, word_score))
    categories["Word Count"] = {
        "score": word_score, "maxScore": 15,
        "details": f"{words} words (target: {target}). Ratio: {word_ratio * 100:.0f}%.",
    }
    if words < target * 0.8:
        recommendations.append(f"Increase content length from {words} to at least {target} words.")

    # 2. Keyword Usage
    kw_count = len(re.findall(re.escape(kw_lower), content_lower))
    density = (kw_count / words * 100) if words else 0
    lines = content.split("\n")
    first_para = " ".join(lines[:5]).lower()
    kw_in_first = kw_lower in first_para
    kw_in_headings = any(kw_lower in h["text"].lower() for h in headings)
    kw_score = 0
    if 0.5 <= density <= 2.5:
        kw_score += 8
    elif 0 < density < 0.5:
        kw_score += 3
    elif 2.5 < density <= 4:
        kw_score += 4
    elif density > 4:
        kw_score += 1
    if kw_in_first:
        kw_score += 5
    if kw_in_headings:
        kw_score += 5
    if kw_count >= 3:
        kw_score += 2
    kw_score = min(20, kw_score)
    categories["Keyword Usage"] = {
        "score": kw_score, "maxScore": 20,
        "details": (
            f'Keyword "{keyword}" x{kw_count} (density: {density:.2f}%). '
            f'First para: {"yes" if kw_in_first else "no"}. Headings: {"yes" if kw_in_headings else "no"}.'
        ),
    }
    if not kw_in_first:
        recommendations.append(f'Include "{keyword}" in the first paragraph.')
    if not kw_in_headings:
        recommendations.append(f'Add "{keyword}" to at least one heading.')

    # 3. Heading Structure
    h1s = [h for h in headings if h["level"] == 1]
    h2s = [h for h in headings if h["level"] == 2]
    h3s = [h for h in headings if h["level"] == 3]
    h_score = 0
    if len(h1s) == 1:
        h_score += 4
    elif len(h1s) > 1:
        h_score += 1
    if len(h2s) >= 3:
        h_score += 5
    elif len(h2s) >= 1:
        h_score += 3
    if len(h3s) >= 2:
        h_score += 3
    elif len(h3s) >= 1:
        h_score += 1
    if len(headings) >= 5:
        h_score += 3
    elif len(headings) >= 3:
        h_score += 2
    h_score = min(15, h_score)
    categories["Heading Structure"] = {
        "score": h_score, "maxScore": 15,
        "details": f"H1: {len(h1s)}, H2: {len(h2s)}, H3: {len(h3s)}. Total: {len(headings)}.",
    }
    if not h1s:
        recommendations.append("Add an H1 heading with the primary keyword.")
    if len(h2s) < 3:
        recommendations.append("Add more H2 subheadings (aim for 3+).")

    # 4. Readability
    if 60 <= readability_score <= 80:
        r_score = 15
    elif 50 <= readability_score < 60 or 80 < readability_score <= 90:
        r_score = 12
    elif 40 <= readability_score < 50:
        r_score = 8
    elif readability_score > 90:
        r_score = 8
    elif readability_score >= 30:
        r_score = 5
    else:
        r_score = 2
    categories["Readability"] = {
        "score": r_score, "maxScore": 15,
        "details": f"Flesch-Kincaid: {readability_score} (grade {grade_level:.1f}).",
    }

    # 5. Entity Coverage
    covered = [t for t in topic_list if t.lower() in content_lower]
    coverage = len(covered) / len(topic_list) if topic_list else 0
    e_score = min(15, round(coverage * 15))
    categories["Entity Coverage"] = {
        "score": e_score, "maxScore": 15,
        "details": f"{len(covered)}/{len(topic_list)} topics ({coverage * 100:.0f}%).",
    }

    # 6. Content Depth
    paragraphs = [p for p in re.split(r"\n\n+", content) if p.strip()]
    avg_para = words / len(paragraphs) if paragraphs else words
    has_list = bool(re.search(r"[-*]\s+\w|^\d+\.\s+\w", content, re.M))
    has_numbers = bool(re.search(r"\d+%|\d+\s*(million|billion|thousand|percent)", content, re.I))
    d_score = 0
    if len(paragraphs) >= 5:
        d_score += 3
    elif len(paragraphs) >= 3:
        d_score += 2
    if 30 <= avg_para <= 150:
        d_score += 3
    elif avg_para > 0:
        d_score += 1
    if has_list:
        d_score += 2
    if has_numbers:
        d_score += 2
    d_score = min(10, d_score)
    categories["Content Depth"] = {
        "score": d_score, "maxScore": 10,
        "details": f"{len(paragraphs)} paragraphs, lists: {'yes' if has_list else 'no'}.",
    }

    # 7. Internal Structure
    has_intro = words >= 50 and _count_words("\n".join(lines[:5])) >= 20
    has_conclusion = any(
        p in content_lower
        for p in ("conclusion", "summary", "key takeaways", "final thoughts", "wrapping up")
    )
    s_score = 0
    if has_intro:
        s_score += 3
    if has_conclusion:
        s_score += 3
    if headings and len(h2s) >= 2:
        s_score += 2
    if words > 300:
        s_score += 2
    s_score = min(10, s_score)
    categories["Internal Structure"] = {
        "score": s_score, "maxScore": 10,
        "details": f"Intro: {'yes' if has_intro else 'no'}. Conclusion: {'yes' if has_conclusion else 'no'}.",
    }
    if not has_conclusion:
        recommendations.append("Add a conclusion or key takeaways section.")

    overall = sum(c["score"] for c in categories.values())
    return {"overallScore": overall, "categories": categories, "recommendations": recommendations}


def _count_words(text: str) -> int:
    return len(re.findall(r"\S+", text))


def _count_syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if len(w) <= 3:
        return 1
    w = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", w)
    w = re.sub(r"^y", "", w)
    matches = re.findall(r"[aeiouy]{1,2}", w)
    return max(len(matches), 1)


def _flesch_kincaid(text: str) -> tuple[float, float]:
    words_list = re.findall(r"\S+", text)
    words = len(words_list)
    sentences = max(len(re.findall(r"[.!?]+", text)), 1)
    if words == 0:
        return 0.0, 0.0
    syllables = sum(_count_syllables(w) for w in words_list)
    score = 206.835 - 1.015 * (words / sentences) - 84.6 * (syllables / words)
    grade = 0.39 * (words / sentences) + 11.8 * (syllables / words) - 15.59
    return round(max(0, min(100, score)), 1), round(max(0, grade), 1)


def _extract_headings(content: str) -> list[dict[str, Any]]:
    headings: list[dict[str, Any]] = []
    for line in content.split("\n"):
        md = re.match(r"^(#{1,6})\s+(.+)", line.strip())
        if md:
            headings.append({"level": len(md.group(1)), "text": md.group(2).strip()})
            continue
        html = re.match(r"<h([1-6])[^>]*>(.*?)</h\1>", line.strip(), re.I)
        if html:
            text = re.sub(r"<[^>]+>", "", html.group(2)).strip()
            headings.append({"level": int(html.group(1)), "text": text})
    return headings


def _seeded_random(seed: str):
    h = 0
    for c in seed:
        h = ((h << 5) - h + ord(c)) & 0xFFFFFFFF
    state = abs(h) or 1

    def rng() -> float:
        nonlocal state
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        return state / 0x7FFFFFFF

    return rng


def _get_keyword_topics(keyword: str) -> list[str]:
    rng = _seeded_random(keyword.lower())
    shuffled = sorted(_BASE_TOPICS, key=lambda _: rng())
    count = 8 + int(rng() * 6)
    topics = shuffled[:count]
    words = keyword.split()
    if len(words) > 1:
        topics.extend([f"{words[0]} overview", f"{words[-1]} guide"])
    return topics


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
