"""DeepSeek / Agnes AI 写作服务封装。

支持两个模型：
  - DeepSeek（主力，DEEPSEEK_API_KEY 必须配置）
  - Agnes（可选，配置 AGNES_API_KEY 后生效）

调用方通过 model_provider="deepseek" | "agnes" 指定。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import settings
from app.services.reddit_humanize import strip_em_dashes
from app.services.seo_analyzer import (
    META_IDEAL_MAX,
    META_IDEAL_MIN,
    TITLE_IDEAL_MAX,
    TITLE_IDEAL_MIN,
    TITLE_MAX,
    _strip_markdown,
)


# ============ 异常 ============
class DeepSeekError(RuntimeError):
    """DeepSeek API 调用失败统一异常。"""


# ============ 数据结构 ============
@dataclass
class ArticleDraft:
    """AI 生成的文章初稿。"""
    title: str
    meta_description: str
    content: str  # Markdown 正文
    keywords_suggested: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class SocialCopy:
    """单平台社交差异化文案。"""
    platform: str
    title: str
    summary: str
    hashtags: list[str] = field(default_factory=list)


# ============ Prompt 模板 ============
SYSTEM_PROMPT = (
    "你是一位资深 SEO 专家和内容写手，精通 E-E-A-T 原则。"
    "你的输出必须是结构清晰、信息密度高、避免空话套话的长文，语言与关键词保持一致。"
    "禁止编造数据或引用不存在的来源。"
)

REDDIT_SYSTEM_PROMPT = (
    "You are a real Reddit commenter texting from your phone, not an SEO writer or marketer. "
    "Keep it short and messy like everyday chat: contractions, fragments, maybe one typo vibe. "
    "Never use numbered lists, bullet lists, TL;DR, section headers, or 'As an AI'. "
    "Never use em dashes or en dashes; use commas, periods, or plain hyphens instead. "
    "Do not invent sources or statistics."
)

REDDIT_POST_SYSTEM_PROMPT = (
    "You write Reddit posts like a regular person ranting or sharing in the moment, "
    "not a brand account and not a polished guide. First-person, short paragraphs, "
    "no bullet lists, no numbered steps, no TL;DR, no FAQ layout. "
    "Never use em dashes or en dashes; use commas, periods, or plain hyphens instead. "
    "Do not sound like an ad. Do not invent specs, prices, or sources."
)

ARTICLE_PROMPT_TEMPLATE = """请围绕核心关键词「{keyword}」撰写一篇 1500-2000 字的高质量 SEO 长文。

【语言】与关键词/大纲保持一致（英文关键词写英文，中文关键词写中文）。

【SEO 硬性指标 — 必须全部满足】
1. 标题（H1）：恰好使用 `# ` 开头，长度 50-60 字符，必须完整包含关键词「{keyword}」（大小写可调整，拼写不可改）
2. Meta Description：写在正文最前面，格式 `> Meta: ...`，长度 150-160 字符，必须含关键词
3. 关键词密度 1%-3%：正文中关键词「{keyword}」须自然出现 4-8 次（含标题与 H2），禁止堆砌
4. 首段：H1 后第一段的前 200 字内必须出现关键词
5. 标题层级：1 个 H1 + 4-6 个 H2（##），至少 1 个 H2 标题含关键词；H2 下可用 H3（###）
6. 配图：插入 2-3 张 Markdown 图片 `![含关键词的描述性 ALT](图片URL占位符)`，每张必须有描述性 ALT
7. 段落简短，每段不超过 4 句话；文末给出 3-5 条「关键要点」小结

【内容要求】
- 开头导语点明用户痛点与本文价值
- 根据受众类型组织内容：技术类可含代码/案例，产品推广类侧重功能对比、使用场景与选购建议
- 禁止编造数据或引用不存在的来源

附加信息：
- 用户给定的大纲：{outline}
- 目标受众：{audience}
- 语气风格：{tone}

请直接输出 Markdown，不要任何额外解释。
"""

SUMMARY_PROMPT_TEMPLATE = """基于以下文章正文，生成一段 Meta Description，用于搜索引擎结果页摘要。
要求：
- 长度严格控制在 150-160 字符（含标点）
- 必须完整包含核心关键词「{keyword}」
- 有吸引力，激发点击欲望
- 客观陈述，避免夸张营销词
- 语言与关键词一致

文章正文：
{content}

只输出 Meta Description 文本本身，不要任何前缀或解释。
"""

SOCIAL_PROMPT_TEMPLATE = """基于以下文章，为 {platform} 平台生成 1 条引流文案。

文章标题：{title}
文章核心关键词：{keyword}
文章正文摘要：{summary}

平台特性要求：
- LinkedIn：专业、理性、突出行业洞察，200-300 字，结尾抛出 1 个开放问题
- Twitter/X：简短、有钩子、< 200 字符，至少 2 个 hashtag
- Facebook：亲和、互动性强、120-200 字，结尾号召评论
- PulseForge：综合风格，150 字左右，附 1-2 个 hashtag
- Reddit：真诚、像真人分享经验、避免硬广，150-200 字

请按 JSON 格式输出：
{{"title": "<文案标题>", "summary": "<文案正文>", "hashtags": ["#标签1", "#标签2"]}}
"""

REDDIT_CONSULTATION_PROMPT = """Write a Reddit post for r/{subreddit} answering a common question like you're chatting with someone, not publishing a guide.

Question/keyword: {keyword}
{site_line}

Requirements:
- English, first-person, like a tired parent typing on their phone
- Title: casual question or "what worked for us" vibe, under 300 characters (not "Here's what I learned" essay energy)
- Body: 80-180 words, 1-3 short paragraphs only
- Give 1-2 concrete tips woven into sentences. NO numbered lists, NO bullets, NO section headers, NO TL;DR
- Never use em dashes or en dashes
- NO links, NO hard product promotion; if a site URL is provided, one soft mention at most
- Sound unfinished / conversational, not like a prepared answer

Output JSON only:
{{"title": "...", "body": "..."}}
"""

REDDIT_EXPERIENCE_PROMPT = """Write a Reddit post for r/{subreddit} sharing a personal experience like a quick vent or update.

Topic/keyword: {keyword}
{site_line}

Requirements:
- English, first-person, messy and brief
- Title: under 300 characters, casual
- Body: 80-160 words, short paragraphs. No pros/cons sections, no bullets, no numbered steps
- Never use em dashes or en dashes
- Mention at most one product naturally if it fits; no hard-sell
- If site URL provided, one soft mention max at the end
- End with a simple question or shrug, not a call-to-action

Output JSON only:
{{"title": "...", "body": "..."}}
"""

REDDIT_COMPARISON_PROMPT = """Write a Reddit post for r/{subreddit} casually comparing a few options, like telling a friend what you tried, not a buying guide.

Topic/keyword: {keyword}
{site_line}

Requirements:
- English, first-person, fair but informal
- Title: under 300 characters, not "2026 guide / Compared vs vs"
- Body: 100-200 words in flowing paragraphs only
- Mention 2-3 options in prose ("we tried X then Y..."). NO bullet lists, NO scorecards, NO TL;DR, NO "How to choose" sections
- Never use em dashes or en dashes
- Do not invent fake specs or prices
- NO links unless site URL provided; then one soft mention max

Output JSON only:
{{"title": "...", "body": "..."}}
"""

REDDIT_COMMENT_PROMPT = """You are a real Reddit user casually replying in r/{subreddit}:

Title: {post_title}
Post body:
{post_body}

{persona_line}
{intent_line}
{site_line}

Write ONE short comment in English, 25-80 words:
- Reply ONLY to this post. No unrelated product pitch.
- Sound like everyday chat, maybe one concrete detail or a quick question
- Contractions, fragments OK. Do not start with Yeah / Honestly / As someone
- No lists, no "hope this helps", no essay structure, no marketing voice
- Never use em dashes or en dashes

Output the comment text only, no prefix or quotes.
"""


# ============ 客户端 ============
class DeepSeekClient:
    """统一聊天客户端，支持 DeepSeek 和 Agnes（Sapiens AI）。"""

    PROVIDERS = {
        "deepseek": ("DEEPSEEK", "https://api.deepseek.com/v1", "deepseek-chat", 120),
        "agnes":    ("AGNES",    "https://api.sapiens.ai/v1",    "agnes-2.5-flash", 60),
    }

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self.api_key = api_key or settings.DEEPSEEK_API_KEY
        self.base_url = (base_url or settings.DEEPSEEK_BASE_URL).rstrip("/")
        self.model = model or settings.DEEPSEEK_MODEL
        self.timeout = timeout or settings.DEEPSEEK_TIMEOUT
        self.provider = self._detect_provider()
        if not self.api_key:
            raise DeepSeekError(
                "未配置 AI API Key：请设置 DEEPSEEK_API_KEY 或 AGNES_API_KEY，并在 backend/.env 中填写后重启服务。"
            )

    def _detect_provider(self) -> str:
        """根据 base_url 判断当前使用的是哪个提供商。"""
        if "sapiens" in self.base_url or "agnes" in self.base_url.lower():
            return "agnes"
        return "deepseek"

    # ---------- 底层调用 ----------
    def chat(
        self,
        user_prompt: str,
        system_prompt: str = SYSTEM_PROMPT,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """调用 AI Chat Completions（兼容 OpenAI 格式），返回 assistant 文本。"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                raise DeepSeekError(
                    f"{self.provider} API 返回 {resp.status_code}: {resp.text[:500]}"
                )
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except httpx.HTTPError as e:
            raise DeepSeekError(f"网络请求失败: {e}") from e

    # ---------- 业务方法 ----------
    def generate_article(
        self,
        keyword: str,
        outline: str = "",
        audience: str = "技术从业者和内容创作者",
        tone: str = "专业、务实、易懂",
    ) -> ArticleDraft:
        """根据关键词生成文章初稿。"""
        prompt = ARTICLE_PROMPT_TEMPLATE.format(
            keyword=keyword,
            outline=outline or "（无）",
            audience=audience,
            tone=tone,
        )
        raw_text = self.chat(prompt, temperature=0.75, max_tokens=4096)

        # 解析标题、Meta、正文
        title, meta, content, keywords = _parse_article_draft(raw_text, keyword)

        # SEO 后处理：对齐检查器评分标准
        title = _ensure_title_seo(title, keyword)
        content = _ensure_h1_in_content(content, title)
        content = _ensure_first_para_keyword(content, keyword)
        content = _ensure_keyword_in_h2(content, keyword)
        content = _ensure_keyword_occurrences(content, keyword)
        content = _add_image_placeholders(content, keyword)
        meta = _ensure_meta_seo(meta, keyword, content, self)

        return ArticleDraft(
            title=title,
            meta_description=meta,
            content=content,
            keywords_suggested=keywords,
            raw={"model": self.model, "raw_text": raw_text},
        )

    def generate_meta_description(
        self, keyword: str, content: str
    ) -> str:
        """基于正文重新生成 Meta Description。"""
        prompt = SUMMARY_PROMPT_TEMPLATE.format(
            keyword=keyword,
            content=content[:3000],  # 防止 token 超限
        )
        meta = self.chat(prompt, temperature=0.5, max_tokens=200)
        return meta.strip().strip('"').strip()

    def generate_social_copy(
        self,
        platform: str,
        article_title: str,
        keyword: str,
        summary: str,
    ) -> SocialCopy:
        """为指定平台生成社交分发文案。"""
        prompt = SOCIAL_PROMPT_TEMPLATE.format(
            platform=platform,
            title=article_title,
            keyword=keyword,
            summary=summary[:800],
        )
        raw = self.chat(prompt, temperature=0.8, max_tokens=600)
        try:
            obj = json.loads(_extract_json(raw))
            return SocialCopy(
                platform=platform,
                title=str(obj.get("title", article_title)),
                summary=str(obj.get("summary", "")),
                hashtags=list(obj.get("hashtags", [])),
            )
        except (json.JSONDecodeError, ValueError) as e:
            raise DeepSeekError(f"社交文案 JSON 解析失败: {e}; raw={raw[:300]}") from e

    def generate_reddit_post(
        self,
        post_type: str,
        subreddit: str,
        keyword: str,
        site_url: str | None = None,
        *,
        persona_prompt: str = "",
        product_brief: dict | None = None,
    ) -> dict[str, str]:
        """Generate Reddit post: consultation / experience / comparison (English)."""
        sr = subreddit.removeprefix("r/").strip()
        extra = _reddit_post_extra_lines(persona_prompt, product_brief)
        if post_type == "consultation":
            site_line = (
                f"You may softly reference this site as optional reading if relevant: {site_url}"
                if site_url
                else "Do not include any links."
            )
            prompt = REDDIT_CONSULTATION_PROMPT.format(subreddit=sr, keyword=keyword, site_line=site_line + extra)
        elif post_type == "comparison":
            site_line = (
                f"You may mention this site once at the end as 'full data/write-up': {site_url}"
                if site_url
                else "Do not include any links."
            )
            prompt = REDDIT_COMPARISON_PROMPT.format(subreddit=sr, keyword=keyword, site_line=site_line + extra)
        else:
            site_line = f"Site URL to soft-mention if natural: {site_url}" if site_url else "Do not include any links."
            prompt = REDDIT_EXPERIENCE_PROMPT.format(
                subreddit=sr, keyword=keyword, site_line=site_line + extra
            )
        raw = self.chat(prompt, system_prompt=REDDIT_POST_SYSTEM_PROMPT, temperature=0.9, max_tokens=700)
        try:
            obj = json.loads(_extract_json(raw))
            return {
                "title": strip_em_dashes(str(obj.get("title", keyword)))[:300],
                "body": strip_em_dashes(str(obj.get("body", ""))),
            }
        except (json.JSONDecodeError, ValueError) as e:
            raise DeepSeekError(f"Reddit post JSON parse failed: {e}; raw={raw[:300]}") from e

    def generate_reddit_comment(
        self,
        post_title: str,
        post_body: str,
        subreddit: str,
        site_url: str | None = None,
        *,
        intent: str = "casual",
        persona_prompt: str = "",
        product_brief: dict | None = None,
    ) -> str:
        """Generate a contextual Reddit comment (English)."""
        sr = subreddit.removeprefix("r/").strip()
        persona_line = f"Persona: {persona_prompt}" if persona_prompt else ""
        if intent == "promo" and product_brief:
            brand = str(product_brief.get("brand") or "").strip()
            product = str(product_brief.get("product") or "").strip()
            points = product_brief.get("talking_points") or []
            never = str(product_brief.get("never_claim") or "").strip()
            intent_line = (
                "Intent: lightly helpful product mention ONLY if the post is already about this category. "
                f"Brand you may mention once if natural: {brand or '(none)'}. "
                f"Product to lean toward: {product or '(none)'}. "
                f"Talking points: {', '.join(str(p) for p in points[:3]) or '(none)'}. "
                f"Never claim: {never or '(none)'}. No hard sell, no links unless a site URL is provided."
            )
            site_line = (
                f"You may softly reference this site if relevant: {site_url}"
                if site_url
                else "Do not include links."
            )
        else:
            intent_line = (
                "Intent: casual community reply. Do not mention any brand, product, company, "
                "or website. Must not mention marketing talking points."
            )
            site_line = "Do not include links."
        prompt = REDDIT_COMMENT_PROMPT.format(
            subreddit=sr,
            post_title=post_title[:500] or "(untitled)",
            post_body=(post_body or "").strip()[:1500] or "(no body; link/image post; reply based on the title only)",
            persona_line=persona_line,
            intent_line=intent_line,
            site_line=site_line,
        )
        return strip_em_dashes(
            self.chat(
                prompt,
                system_prompt=REDDIT_SYSTEM_PROMPT,
                temperature=0.95,
                max_tokens=180,
            )
        ).strip()


# ============ 工具函数 ============
def _reddit_post_extra_lines(persona_prompt: str, product_brief: dict | None) -> str:
    chunks: list[str] = []
    if persona_prompt:
        chunks.append(f"Author persona: {persona_prompt}")
    if product_brief:
        brand = str(product_brief.get("brand") or "").strip()
        category = str(product_brief.get("category") or "").strip()
        points = product_brief.get("talking_points") or []
        comps = product_brief.get("competitors") or []
        never = str(product_brief.get("never_claim") or "").strip()
        if brand:
            chunks.append(f"Our product brand (mention naturally at most once if relevant): {brand}")
        product = str(product_brief.get("product") or "").strip()
        if product:
            chunks.append(f"Focus this reply around the product: {product}")
        if category:
            chunks.append(f"Category: {category}")
        if points:
            chunks.append("Allowed talking points: " + "; ".join(str(p) for p in points[:3]))
        if comps:
            chunks.append("Other options you may compare fairly: " + ", ".join(str(c) for c in comps[:5]))
        if never:
            chunks.append(f"Never claim: {never}")
    if not chunks:
        return ""
    return "\n" + "\n".join(chunks)


def _parse_article_draft(
    raw_text: str, keyword: str
) -> tuple[str, str, str, list[str]]:
    """从 AI 返回的 Markdown 文本中解析出标题/Meta/正文/关键词建议。"""
    lines = raw_text.strip().splitlines()

    # Meta Description（> Meta: ... 或单独一行）
    meta = ""
    meta_pattern = re.compile(r"^>\s*Meta:\s*(.+)$", re.IGNORECASE)
    body_start_idx = 0
    for i, line in enumerate(lines):
        m = meta_pattern.match(line.strip())
        if m:
            meta = m.group(1).strip()
            body_start_idx = i + 1
            break

    # 标题：第一个 H1
    title = ""
    for i, line in enumerate(lines[body_start_idx:], start=body_start_idx):
        if line.strip().startswith("# "):
            title = line.strip()[2:].strip()
            body_start_idx = i + 1
            break
    if not title:
        title = f"关于「{keyword}」的深度解析"

    # 正文（去掉 meta 行之后的内容）
    content = "\n".join(lines[body_start_idx:]).strip()
    if not content:
        content = raw_text

    # 关键词建议：简单提取 H2 文本
    keywords: list[str] = []
    for line in lines:
        if line.strip().startswith("## "):
            kw = line.strip()[3:].strip()
            if kw:
                keywords.append(kw)

    # 若 Meta 为空，取正文前 160 字符兜底
    if not meta:
        meta = content.replace("\n", " ").strip()[:META_IDEAL_MAX]
    if len(meta) > META_IDEAL_MAX:
        meta = meta[:META_IDEAL_MAX]

    return title, meta, content, keywords


def _is_mostly_ascii(text: str) -> bool:
    """判断文本是否以英文/ASCII 为主。"""
    if not text:
        return False
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    return ascii_chars / len(text) > 0.7


def _ensure_title_seo(title: str, keyword: str) -> str:
    """确保标题 50-60 字符且包含关键词。"""
    title = title.strip()
    kw = keyword.strip()
    if kw and kw.lower() not in title.lower():
        title = f"{kw}: {title}" if _is_mostly_ascii(kw) else f"{title}｜{kw}"

    length = len(title)
    if TITLE_IDEAL_MIN <= length <= TITLE_IDEAL_MAX:
        return title

    if length < TITLE_IDEAL_MIN:
        if _is_mostly_ascii(kw or title):
            suffix = " — Complete Guide, Features & Buying Tips"
        else:
            suffix = "：完整指南、核心要点与实用建议"
        padded = title + suffix
        if len(padded) > TITLE_IDEAL_MAX:
            return padded[:TITLE_IDEAL_MAX].rstrip(" -—：|｜,，")
        return padded

    if length > TITLE_MAX:
        return title[:TITLE_MAX].rstrip(" -—：|｜,，")
    return title


def _ensure_h1_in_content(content: str, title: str) -> str:
    """解析时 H1 被提取为 title，补回正文以满足 H1 层级检查。"""
    content = content.strip()
    if not title:
        return content
    h1_list = re.findall(r"^#\s+(.+)$", content, re.MULTILINE)
    if len(h1_list) == 1:
        return content
    if h1_list:
        # 多个 H1 时保留第一个，降级其余
        lines = content.splitlines()
        h1_seen = False
        fixed: list[str] = []
        for line in lines:
            if line.strip().startswith("# ") and not h1_seen:
                fixed.append(f"# {title}")
                h1_seen = True
            elif line.strip().startswith("# "):
                fixed.append("## " + line.strip()[2:].strip())
            else:
                fixed.append(line)
        return "\n".join(fixed).strip()
    return f"# {title}\n\n{content}"


def _ensure_first_para_keyword(content: str, keyword: str) -> str:
    """确保首段前 200 字含关键词。"""
    kw = keyword.strip()
    if not kw:
        return content
    plain = _strip_markdown(content)
    if kw.lower() in plain[:200].lower():
        return content

    if _is_mostly_ascii(kw):
        intro = (
            f"When researching {kw}, you need clear, practical information "
            f"to make the right choice. This guide covers what matters most.\n\n"
        )
    else:
        intro = (
            f"关于「{kw}」，许多读者最关心的是真实体验与实用价值。"
            f"本文将梳理核心要点与选购建议，帮助你快速做出判断。\n\n"
        )
    return intro + content


def _ensure_keyword_occurrences(content: str, keyword: str) -> str:
    """确保正文中出现足够次数的精确关键词（密度不低于 1%）。"""
    kw = keyword.strip()
    if not kw:
        return content
    plain = _strip_markdown(content)
    char_count = len(plain)
    if char_count < 50:
        return content

    kw_count = plain.lower().count(kw.lower())
    target_count = max(2, int(0.015 * char_count / len(kw)))  # 约 1.5% 密度
    if kw_count >= target_count:
        return content

    missing = target_count - kw_count
    if _is_mostly_ascii(kw):
        extra = f" Throughout this guide, {kw} is discussed in practical, real-world terms."
    else:
        extra = f" 下文将结合场景，多次围绕{kw}展开说明。"
    # 在最后一个 H2 小节末尾补充
    h2_matches = list(re.finditer(r"^##\s+.+$", content, re.MULTILINE))
    if h2_matches:
        last_h2 = h2_matches[-1]
        next_h2 = re.search(r"^##\s+", content[last_h2.end():], re.MULTILINE)
        insert_at = last_h2.end() + (next_h2.start() if next_h2 else len(content[last_h2.end():]))
        insert_text = (extra * missing).strip() + "\n"
        return content[:insert_at] + "\n" + insert_text + content[insert_at:]
    return content.rstrip() + "\n\n" + extra.strip() + "\n"


def _ensure_keyword_in_h2(content: str, keyword: str) -> str:
    """确保至少一个 H2 含关键词。"""
    kw = keyword.strip()
    if not kw:
        return content
    h2_matches = list(re.finditer(r"^##\s+(.+)$", content, re.MULTILINE))
    if not h2_matches:
        return content
    if any(kw.lower() in m.group(1).lower() for m in h2_matches):
        return content

    first = h2_matches[0]
    old_line = first.group(0)
    h2_text = first.group(1).strip()
    if _is_mostly_ascii(kw):
        new_line = f"## {kw}: {h2_text}"
    else:
        new_line = f"## {h2_text}：{kw}深度解析"
    return content.replace(old_line, new_line, 1)


def _add_image_placeholders(content: str, keyword: str) -> str:
    """若无配图，在首个 H2 后插入带 ALT 的占位图。"""
    if re.search(r"!\[[^\]]+\]\([^)]+\)", content):
        return content
    kw = keyword.strip() or "product"
    slug = re.sub(r"[^\w-]+", "-", kw.lower()).strip("-")
    if _is_mostly_ascii(kw):
        alt = f"{kw} feature overview"
    else:
        alt = f"{kw}产品功能展示"
    placeholder = f"\n\n![{alt}](https://placeholder.seo-platform.local/{slug}-1.jpg)\n\n"

    h2_match = re.search(r"^##\s+.+$", content, re.MULTILINE)
    if not h2_match:
        return content + placeholder
    insert_at = h2_match.end()
    return content[:insert_at] + placeholder + content[insert_at:]


def _ensure_meta_seo(
    meta: str,
    keyword: str,
    content: str,
    client: DeepSeekClient,
) -> str:
    """确保 Meta 150-160 字符且含关键词，不满足则调用 AI 重新生成。"""
    kw = keyword.strip()
    meta = meta.strip()
    length = len(meta)
    has_kw = not kw or kw.lower() in meta.lower()
    if META_IDEAL_MIN <= length <= META_IDEAL_MAX and has_kw:
        return meta
    try:
        return client.generate_meta_description(kw, content)
    except DeepSeekError:
        # AI 失败时用规则兜底
        if not meta and content:
            meta = _strip_markdown(content)[:META_IDEAL_MAX]
        if kw and kw.lower() not in meta.lower():
            meta = f"{kw} — {meta}"
        if len(meta) < META_IDEAL_MIN:
            meta = meta + " " + _strip_markdown(content)[:80]
        return meta[:META_IDEAL_MAX]


def _extract_json(text: str) -> str:
    """从可能包含前后杂音的文本中提取首个 JSON 对象。"""
    text = text.strip()
    if text.startswith("```"):
        # 去掉 ```json ... ``` 包裹
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return text
    return text[start : end + 1]


# ============ 单例 ============
_client_cache: dict[str, DeepSeekClient] = {}


def preferred_ai_provider() -> str:
    """有 Agnes Key 时优先 Agnes，否则 DeepSeek。"""
    if settings.AGNES_API_KEY:
        return "agnes"
    return "deepseek"


def get_ai_client(provider: str | None = None) -> DeepSeekClient:
    """获取 AI 客户端实例，支持 deepseek / agnes。

    provider 为 None 时：优先 Agnes（已配 AGNES_API_KEY），否则 DeepSeek。
    provider="deepseek" / "agnes" 时强制使用对应配置。
    """
    resolved = provider or preferred_ai_provider()
    if resolved in _client_cache:
        return _client_cache[resolved]

    prefix = resolved.upper()
    key_env = f"{prefix}_API_KEY"
    url_env = f"{prefix}_BASE_URL"
    model_env = f"{prefix}_MODEL"
    timeout_env = f"{prefix}_TIMEOUT"

    api_key = getattr(settings, key_env, None)
    base_url = getattr(settings, url_env, None)
    model = getattr(settings, model_env, None)
    timeout = getattr(settings, timeout_env, None)

    if not api_key:
        raise DeepSeekError(
            f"{resolved.upper()} API Key 未配置，请在 backend/.env 中设置 {key_env} 后重启服务。"
        )

    client = DeepSeekClient(api_key=api_key, base_url=base_url, model=model, timeout=timeout)
    _client_cache[resolved] = client
    return client
