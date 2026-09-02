"""SEO 检查器：基于 Markdown/HTML 内容静态分析。

覆盖：
- P1.4 基础指标：Title / Meta / 关键词密度 / H1-H3 层级 / 图片 ALT
- P1.5 AI 检测标记：重复句、AI 典型句式启发式评分
- P1.6 Core Web Vitals 预估：LCP/CLS 风险

输出统一为 SeoReport，包含每项打分 + 总分 + 文字建议。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass
class CheckItem:
    """单项检查结果。"""
    key: str
    name: str
    score: float  # 0-100
    max_score: float
    status: str  # pass / warning / fail
    message: str
    suggestion: str = ""


@dataclass
class SeoReport:
    """SEO 检查总报告。"""
    total_score: Decimal
    items: list[CheckItem] = field(default_factory=list)
    ai_detected_score: Decimal = Decimal("0")
    ai_detail: dict[str, Any] = field(default_factory=dict)
    serp_detail: dict[str, Any] = field(default_factory=dict)
    technical_audit: dict[str, Any] = field(default_factory=dict)
    cwv_estimate: dict[str, Any] = field(default_factory=dict)
    internal_links_recommended: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_score": float(self.total_score),
            "items": [
                {
                    "key": i.key,
                    "name": i.name,
                    "score": i.score,
                    "max_score": i.max_score,
                    "status": i.status,
                    "message": i.message,
                    "suggestion": i.suggestion,
                }
                for i in self.items
            ],
            "ai_detected_score": float(self.ai_detected_score),
            "ai_detail": self.ai_detail,
            "serp_detail": self.serp_detail,
            "technical_audit": self.technical_audit,
            "cwv_estimate": self.cwv_estimate,
            "internal_links_recommended": self.internal_links_recommended,
        }


# ============ 阈值常量 ============
TITLE_MIN, TITLE_MAX, TITLE_IDEAL_MIN, TITLE_IDEAL_MAX = 30, 70, 50, 60
META_MIN, META_MAX, META_IDEAL_MIN, META_IDEAL_MAX = 80, 200, 150, 160
DENSITY_MIN, DENSITY_MAX = 0.01, 0.03  # 1% - 3%


def analyze(
    title: str,
    content: str,
    meta_description: str | None = None,
    keyword: str | None = None,
    cover_image_url: str | None = None,
) -> SeoReport:
    """主入口：对一篇文章做全量 SEO 静态检查。"""
    items: list[CheckItem] = []
    meta = (meta_description or "").strip()

    items.append(_check_title(title or "", keyword))
    items.append(_check_meta(meta, keyword))
    items.append(_check_keyword_density(content or "", keyword))
    items.append(_check_heading_hierarchy(content or "", keyword))
    items.append(_check_images_alt(content or ""))
    items.append(_check_content_length(content or ""))
    items.append(_check_first_paragraph(content or "", keyword))

    # 总分 = 各项 score 加权求和（按 max_score 归一化到 100）
    total_max = sum(i.max_score for i in items)
    total_got = sum(i.score for i in items)
    total_score = Decimal(str(round(total_got / total_max * 100, 2))) if total_max else Decimal("0")

    report = SeoReport(total_score=total_score, items=items)
    # AI 检测（lmscan + Signs of AI 混合）
    from app.services.ai_detector import detect_ai_content

    ai_result = detect_ai_content(content or "")
    report.ai_detected_score = ai_result.score
    report.ai_detail = ai_result.to_dict()

    # SERP 内容优化评分（有关键词时）
    if keyword and keyword.strip():
        from app.services.content_optimizer import score_content_for_serp

        try:
            serp = score_content_for_serp(content or "", keyword.strip())
            report.serp_detail = serp.to_dict()
        except Exception:
            report.serp_detail = {}

    # CWV 预估
    report.cwv_estimate = _estimate_cwv(content or "", cover_image_url)
    return report


# ============ 单项检查 ============
def _check_title(title: str, keyword: str | None) -> CheckItem:
    """标题长度 + 关键词包含。"""
    length = len(title)
    max_score = 15.0
    if not title:
        return CheckItem("title", "标题 Title", 0, max_score, "fail", "标题为空", "请填写 50-60 字符的标题")
    score = 0.0
    msgs: list[str] = []
    if TITLE_IDEAL_MIN <= length <= TITLE_IDEAL_MAX:
        score += 8
    elif TITLE_MIN <= length <= TITLE_MAX:
        score += 5
        msgs.append(f"标题长度 {length} 字符，建议 50-60 之间")
    else:
        msgs.append(f"标题长度 {length} 字符，超出推荐范围 30-70")

    if keyword and keyword.lower() in title.lower():
        score += 7
    elif keyword:
        msgs.append(f"标题未包含关键词「{keyword}」")
    else:
        score += 5  # 无关键词约束时只看长度

    status = "pass" if score >= 12 else ("warning" if score >= 7 else "fail")
    return CheckItem(
        "title", "标题 Title", min(score, max_score), max_score, status,
        "；".join(msgs) if msgs else f"标题长度 {length}，符合规范",
        "标题应 50-60 字符且包含核心关键词" if status != "pass" else "",
    )


def _check_meta(meta: str, keyword: str | None) -> CheckItem:
    """Meta Description 长度 + 关键词。"""
    length = len(meta)
    max_score = 10.0
    if not meta:
        return CheckItem("meta", "Meta Description", 0, max_score, "fail", "Meta Description 为空", "请填写 150-160 字符的描述")
    score = 0.0
    msgs: list[str] = []
    if META_IDEAL_MIN <= length <= META_IDEAL_MAX:
        score += 6
    elif META_MIN <= length <= META_MAX:
        score += 4
        msgs.append(f"Meta 长度 {length}，建议 150-160")
    else:
        msgs.append(f"Meta 长度 {length}，超出 80-200 范围")

    if keyword and keyword.lower() in meta.lower():
        score += 4
    else:
        msgs.append("Meta 未包含关键词" if keyword else "")

    status = "pass" if score >= 8 else ("warning" if score >= 5 else "fail")
    return CheckItem(
        "meta", "Meta Description", min(score, max_score), max_score, status,
        "；".join([m for m in msgs if m]) or f"Meta 长度 {length}，符合规范",
        "Meta 应 150-160 字符且含关键词" if status != "pass" else "",
    )


def _check_keyword_density(content: str, keyword: str | None) -> CheckItem:
    """关键词密度（正文出现次数 / 总词数）。"""
    max_score = 15.0
    if not keyword:
        return CheckItem("density", "关键词密度", 8, max_score, "warning", "未提供关键词，跳过密度检查", "建议传入关键词以获得准确评分")
    plain = _strip_markdown(content)
    char_count = len(plain)
    if char_count < 50:
        return CheckItem("density", "关键词密度", 0, max_score, "fail", "正文过短，无法计算密度", "正文至少 800 字")
    # 中文按字符计数；关键词出现次数 / 总字符
    kw_count = plain.lower().count(keyword.lower())
    density = kw_count * len(keyword) / char_count if char_count else 0
    if DENSITY_MIN <= density <= DENSITY_MAX:
        score = 15.0
        status = "pass"
        msg = f"关键词密度 {density*100:.2f}%（出现 {kw_count} 次），符合 1%-3%"
    elif density < DENSITY_MIN:
        score = 6.0
        status = "warning"
        msg = f"关键词密度 {density*100:.2f}% 偏低，建议自然增加出现频次"
    else:
        score = 4.0
        status = "fail"
        msg = f"关键词密度 {density*100:.2f}% 过高，有堆砌风险"
    return CheckItem("density", "关键词密度", score, max_score, status, msg, "" if status == "pass" else "目标 1%-3%")


def _check_heading_hierarchy(content: str, keyword: str | None) -> CheckItem:
    """H1 唯一、H2/H3 层级不跳级、H2 含关键词。"""
    max_score = 20.0
    h1_list = re.findall(r"^#\s+(.+)$", content, re.MULTILINE)
    h2_list = re.findall(r"^##\s+(.+)$", content, re.MULTILINE)
    h3_list = re.findall(r"^###\s+(.+)$", content, re.MULTILINE)

    if not h1_list and not h2_list:
        return CheckItem("heading", "标题层级 H1-H3", 0, max_score, "fail", "未检测到 H1/H2 标题", "请使用 Markdown #/##/### 划分结构")

    score = 0.0
    msgs: list[str] = []
    # H1
    if len(h1_list) == 1:
        score += 6
    elif len(h1_list) == 0:
        msgs.append("缺少 H1 主标题")
    else:
        msgs.append(f"检测到 {len(h1_list)} 个 H1，应仅 1 个")
        score += 2

    # H2 数量
    if 3 <= len(h2_list) <= 8:
        score += 6
    elif len(h2_list) > 0:
        score += 4
    else:
        msgs.append("缺少 H2 二级小节")

    # H3 层级检查（H3 应出现在 H2 之后）
    if h3_list and not h2_list:
        msgs.append("存在 H3 但缺少 H2，层级跳级")
        score += 1
    elif h3_list:
        score += 3

    # H2 含关键词
    if keyword and h2_list:
        h2_with_kw = sum(1 for h in h2_list if keyword.lower() in h.lower())
        if h2_with_kw >= 1:
            score += 5
        else:
            msgs.append("H2 中未出现关键词")

    status = "pass" if score >= 15 else ("warning" if score >= 10 else "fail")
    return CheckItem(
        "heading", "标题层级 H1-H3", min(score, max_score), max_score, status,
        "；".join(msgs) if msgs else f"H1×{len(h1_list)} H2×{len(h2_list)} H3×{len(h3_list)} 结构合理",
        "建议 1 个 H1 + 3-6 个 H2，H2 中自然含关键词" if status != "pass" else "",
    )


def _check_images_alt(content: str) -> CheckItem:
    """图片 ALT 标签检测。"""
    max_score = 10.0
    # Markdown 图片语法 ![alt](url)
    images = re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", content)
    if not images:
        return CheckItem("image_alt", "图片 ALT", 8, max_score, "pass", "文章无图片（建议适当配图提升可读性）", "")
    total = len(images)
    missing = sum(1 for alt, _ in images if not alt.strip())
    if missing == 0:
        return CheckItem("image_alt", "图片 ALT", 10, max_score, "pass", f"共 {total} 张图片，全部有 ALT", "")
    score = max(0, 10 - missing * 3)
    status = "fail" if missing > total / 2 else "warning"
    return CheckItem(
        "image_alt", "图片 ALT", score, max_score, status,
        f"{missing}/{total} 张图片缺少 ALT 标签",
        "为每张图片添加描述性 ALT 文本（含关键词更佳）",
    )


def _check_content_length(content: str) -> CheckItem:
    """正文长度（建议 800-2500 字）。"""
    max_score = 15.0
    plain = _strip_markdown(content)
    length = len(plain)
    if length >= 2500:
        score = 13.0
        status = "pass"
        msg = f"正文字符数 {length}，内容充实"
    elif 1500 <= length < 2500:
        score = 15.0
        status = "pass"
        msg = f"正文字符数 {length}，长度理想"
    elif 800 <= length < 1500:
        score = 11.0
        status = "warning"
        msg = f"正文字符数 {length}，建议扩充到 1500+ 字"
    elif length < 800:
        score = 5.0
        status = "fail"
        msg = f"正文字符数 {length} 过短，SEO 偏好 1500+ 字长文"
    else:
        score = 8.0
        status = "warning"
        msg = f"正文字符数 {length}"
    return CheckItem("content_length", "正文长度", score, max_score, status, msg, "目标 1500-2500 字" if status != "pass" else "")


def _check_first_paragraph(content: str, keyword: str | None) -> CheckItem:
    """首段是否含关键词（前 200 字）。"""
    max_score = 15.0
    plain = _strip_markdown(content)
    first_para = plain[:200]
    if not first_para:
        return CheckItem("first_para", "首段关键词", 0, max_score, "fail", "无正文内容", "首段应点明主题并含关键词")
    if keyword and keyword.lower() in first_para.lower():
        return CheckItem("first_para", "首段关键词", 15, max_score, "pass", "首段包含关键词", "")
    if keyword:
        return CheckItem("first_para", "首段关键词", 5, max_score, "warning", "首段未包含关键词", "在开头 200 字内自然提及关键词")
    return CheckItem("first_para", "首段关键词", 10, max_score, "pass", "首段内容存在", "")


# ============ AI 内容检测（启发式） ============
_AI_PHRASES = [
    "首先", "其次", "再次", "最后", "综上所述", "总而言之", "值得注意的是",
    "需要指出的是", "在这个过程中", "不得不说", "毫无疑问", "众所周知",
    "在当今", "随着.*的发展", "作为一个", "深入探讨", "让我们", "在这篇文章中",
]
_REPETITIVE_PATTERNS = [
    r"(.{8,30})\1{2,}",  # 同一段话重复 3 次以上
]


def _estimate_ai_content(content: str) -> Decimal:
    """启发式 AI 内容检测，返回 0-100 分（越高越像 AI 生成）。"""
    plain = _strip_markdown(content)
    if len(plain) < 100:
        return Decimal("0")
    score = 0.0
    # 1. AI 高频短语
    ai_phrase_hits = 0
    for phrase in _AI_PHRASES:
        if re.search(phrase, plain):
            ai_phrase_hits += 1
    score += min(ai_phrase_hits * 8, 40)
    # 2. 重复句式
    for pattern in _REPETITIVE_PATTERNS:
        if re.search(pattern, plain):
            score += 15
    # 3. 段落长度均匀度（AI 生成的段落往往长度接近）
    paragraphs = [p for p in plain.split("\n\n") if len(p) > 20]
    if len(paragraphs) >= 4:
        lengths = [len(p) for p in paragraphs]
        avg = sum(lengths) / len(lengths)
        if avg > 0:
            variance = sum((l - avg) ** 2 for l in lengths) / len(lengths)
            cv = (variance ** 0.5) / avg  # 变异系数
            if cv < 0.15:  # 高度均匀
                score += 20
            elif cv < 0.3:
                score += 10
    # 4. 缺少具体数字/案例
    numbers = re.findall(r"\d+", plain)
    if len(numbers) < 2:
        score += 10
    return Decimal(str(min(round(score, 2), 100)))


# ============ Core Web Vitals 预估 ============
def _estimate_cwv(content: str, cover_image_url: str | None) -> dict[str, Any]:
    """基于内容静态估算 CWV 风险（非实测）。"""
    images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", content)
    image_count = len(images)
    # 简单启发：无尺寸声明的图片数 → CLS 风险
    # Markdown 默认不写尺寸，每张图都有 CLS 风险
    cls_risk_images = image_count
    if cover_image_url:
        cls_risk_images += 1

    # LCP 估算：首屏大图数量
    lcp_risk = "low"
    if image_count >= 3:
        lcp_risk = "medium"
    if image_count >= 6 or (cover_image_url and image_count >= 3):
        lcp_risk = "high"

    # CLS 估算
    cls_risk = "low"
    if cls_risk_images >= 2:
        cls_risk = "medium"
    if cls_risk_images >= 5:
        cls_risk = "high"

    # INP 估算（静态无法精确，按交互元素数量粗估）
    interactive = len(re.findall(r"\[.+?\]\(.+?\)", content))  # 链接数
    inp_risk = "low" if interactive < 10 else ("medium" if interactive < 30 else "high")

    # 估算分数（毫秒，粗略）
    lcp_ms = 1200 + image_count * 400 + (800 if cover_image_url else 0)
    cls_score = round(0.05 + cls_risk_images * 0.05, 3)
    inp_ms = 100 + interactive * 15

    return {
        "source": "estimated",
        "data_type": "estimated",
        "lcp_ms": lcp_ms,
        "lcp_risk": lcp_risk,
        "cls": cls_score,
        "cls_risk": cls_risk,
        "inp_ms": inp_ms,
        "inp_risk": inp_risk,
        "image_count": image_count,
        "cover_image": bool(cover_image_url),
        "note": "静态预估值，实际请用 Lighthouse / CrUX 实测",
    }


# ============ 工具 ============
def _strip_markdown(content: str) -> str:
    """简单去除 Markdown 标记，保留纯文本用于统计。"""
    text = content
    # 去图片
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    # 去链接 [text](url) -> text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # 去标题井号
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # 去粗体/斜体
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    # 去代码块
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # 去引用
    text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)
    # 去列表标记
    text = re.sub(r"^[\s]*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[\s]*\d+\.\s+", "", text, flags=re.MULTILINE)
    # 去多余空白
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()
