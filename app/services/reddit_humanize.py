"""评论口语化后处理：按账号 seed 抽笔误子集；不改品牌名和数字；去掉破折号。"""
from __future__ import annotations

import random
import re

_BANNED_OPENERS = ("yeah", "honestly", "as someone")
_OPENER_SWAPS = (
    "I ",
    "We ",
    "Same here, ",
    "Not gonna lie, ",
    "Idk, ",
)

# 扩大词表；实际每次只按 seed 抽 ACCOUNT_TYPO_COUNT 对，避免矩阵指纹
_TYPO_PAIRS: tuple[tuple[str, str], ...] = (
    ("the", "teh"),
    ("and", "adn"),
    ("really", "realy"),
    ("because", "becuase"),
    ("which", "whcih"),
    ("with", "wiht"),
    ("their", "thier"),
    ("don't", "dont"),
    ("it's", "its"),
    ("you're", "your"),
    ("about", "abotu"),
    ("would", "woudl"),
    ("should", "shoudl"),
    ("could", "coudl"),
    ("before", "befroe"),
    ("after", "afetr"),
    ("something", "someting"),
    ("everything", "everyting"),
    ("nothing", "nothign"),
    ("someone", "someon"),
    ("people", "peopel"),
    ("friend", "freind"),
    ("night", "nigth"),
    ("morning", "mroning"),
    ("tomorrow", "tomorow"),
    ("yesterday", "yesteday"),
    ("definitely", "definately"),
    ("probably", "probaly"),
    ("maybe", "mayb"),
    ("always", "alwyas"),
    ("never", "neevr"),
    ("going", "goign"),
    ("trying", "tryign"),
    ("until", "untill"),
    ("through", "throuhg"),
    ("though", "thoguh"),
    ("enough", "enoguh"),
    ("different", "diffrent"),
    ("between", "betwen"),
    ("without", "withotu"),
    ("around", "aroudn"),
    ("pretty", "prety"),
    ("little", "littel"),
    ("better", "beter"),
    ("happened", "happend"),
    ("experience", "experiance"),
)

ACCOUNT_TYPO_COUNT = 4

_EM_DASH_RE = re.compile(r"[—–]+")
_EM_DASH_SPACED_RE = re.compile(r"\s*[—–]+\s*")

# 高频 AI 套话 → 口语化替换（英文帖子/评论通用，保护大小写无关）
_AI_TELL_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (r"\bit(?:'s| is) (?:important|worth) (?:to note|noting) that\b", "worth noting,"),
    (r"\bin conclusion,?\s*", "so, "),
    (r"\bfurthermore,?\s*", "also, "),
    (r"\bmoreover,?\s*", "plus, "),
    (r"\badditionally,?\s*", "also, "),
    (r"\bhowever,?\s*", "but "),
    (r"\btherefore,?\s*", "so "),
    (r"\bin today'?s fast[- ]paced world,?\s*", ""),
    (r"\bit goes without saying that\s*", ""),
    (r"\bwhen it comes to\b", "with"),
    (r"\ba wide range of\b", "many"),
    (r"\bin order to\b", "to"),
    (r"\bdelve into\b", "dig into"),
    (r"\bleverage\b", "use"),
    (r"\butilize\b", "use"),
    (r"\bplethora of\b", "lot of"),
    (r"\bmyriad of\b", "ton of"),
    (r"\bgame[- ]changer\b", "big deal"),
    (r"\bseamless(?:ly)?\b", "smooth"),
    (r"\brobust\b", "solid"),
    (r"\belevate\b", "improve"),
    (r"\bempower\b", "help"),
    (r"\bcrucial\b", "key"),
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_LONG_SENTENCE_WORDS = 30
_SHORT_SENTENCE_WORDS = 7


def replace_ai_tells(text: str) -> str:
    """替换高频 AI 套话/营销词，压缩空话，收敛多余空白。"""
    if not text:
        return text
    out = text
    for pattern, replacement in _AI_TELL_REPLACEMENTS:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r",\s*,+", ",", out)
    out = re.sub(r"([.!?])\s*([a-z])", lambda m: m.group(1) + " " + m.group(2).upper(), out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def _capitalize(text: str) -> str:
    text = text.lstrip()
    if not text:
        return text
    return text[0].upper() + text[1:]


def _split_long_sentence(sentence: str) -> list[str]:
    """在靠中部的逗号处断开长句，制造长短交替。"""
    words = sentence.split()
    if len(words) <= _LONG_SENTENCE_WORDS:
        return [sentence]
    mid = len(words) // 2
    best = -1
    for i in range(len(words)):
        if words[i].endswith(","):
            if best == -1 or abs(i - mid) < abs(best - mid):
                best = i
    if best <= 2 or best >= len(words) - 3:
        return [sentence]
    left = " ".join(words[: best + 1]).rstrip(",") + "."
    right = _capitalize(" ".join(words[best + 1:]))
    return [left, right]


def vary_sentence_rhythm(text: str, *, seed: int | None = None) -> str:
    """打散句长波动（burstiness）：长句拆分 + 相邻短句合并。"""
    if not text:
        return text
    rng = random.Random(seed)
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]
    if not sentences:
        return text

    expanded: list[str] = []
    for s in sentences:
        expanded.extend(_split_long_sentence(s))

    merged: list[str] = []
    i = 0
    while i < len(expanded):
        cur = expanded[i]
        if i + 1 < len(expanded):
            nxt = expanded[i + 1]
            cur_words = len(cur.split())
            nxt_words = len(nxt.split())
            if (
                cur_words <= _SHORT_SENTENCE_WORDS
                and nxt_words <= _SHORT_SENTENCE_WORDS
                and rng.random() < 0.6
            ):
                joined = cur.rstrip().rstrip(".!?") + ", " + nxt.lstrip()[0].lower() + nxt.lstrip()[1:]
                merged.append(joined)
                i += 2
                continue
        merged.append(cur)
        i += 1
    return " ".join(merged).strip()



def typo_pairs_for_seed(seed: int | None, *, count: int = ACCOUNT_TYPO_COUNT) -> tuple[tuple[str, str], ...]:
    """按账号/种子从总词表抽固定子集；seed=None 时用 0，保证可复现。"""
    pairs = list(_TYPO_PAIRS)
    rng = random.Random(0 if seed is None else seed)
    rng.shuffle(pairs)
    return tuple(pairs[: max(1, min(count, len(pairs)))])


def strip_em_dashes(text: str) -> str:
    """把 em/en dash 换成逗号或空格，避免 AI 破折号痕迹。"""
    if not text:
        return text
    out = _EM_DASH_SPACED_RE.sub(", ", text)
    out = _EM_DASH_RE.sub(",", out)
    out = re.sub(r",\s*,+", ",", out)
    out = re.sub(r" +", " ", out)
    return out.strip()


def diversify_opener(text: str, seed: int | None = None) -> str:
    stripped = (text or "").lstrip()
    if not stripped:
        return text
    lower = stripped.lower()
    matched = next((p for p in _BANNED_OPENERS if lower.startswith(p)), None)
    if not matched:
        return text
    rng = random.Random(seed)
    rest = stripped[len(matched):].lstrip(" ,.-–—")
    if rest:
        rest = rest[0].upper() + rest[1:]
    return rng.choice(_OPENER_SWAPS) + rest


def humanize_comment(
    text: str,
    *,
    seed: int | None = None,
    brand_names: tuple[str, ...] | list[str] = (),
    max_typos: int = 2,
) -> str:
    """注入 0–max_typos 处白名单笔误；保护品牌与含数字的 token；去掉破折号。"""
    text = replace_ai_tells(strip_em_dashes(text or ""))
    if not text or max_typos <= 0:
        return diversify_opener(text, seed=seed)

    rng = random.Random(seed)
    typo_pairs = typo_pairs_for_seed(seed)
    protected = {b.lower() for b in brand_names if b}
    tokens = re.findall(r"\S+|\s+", text)
    word_indexes = [
        i
        for i, tok in enumerate(tokens)
        if tok.strip() and not tok.isspace()
    ]
    candidates: list[tuple[int, str, str]] = []
    for idx in word_indexes:
        raw = tokens[idx]
        core, prefix, suffix = _split_punct(raw)
        if not core or any(ch.isdigit() for ch in core):
            continue
        if core.lower() in protected:
            continue
        for src, dst in typo_pairs:
            if core.lower() == src:
                replacement = _match_case(core, dst)
                candidates.append((idx, prefix + replacement + suffix, src))
                break

    rng.shuffle(candidates)
    used_src: set[str] = set()
    n = rng.randint(0, min(max_typos, len(candidates)))
    applied = 0
    for idx, new_tok, src in candidates:
        if applied >= n:
            break
        if src in used_src:
            continue
        tokens[idx] = new_tok
        used_src.add(src)
        applied += 1
    return diversify_opener("".join(tokens), seed=None if seed is None else seed + 17)


def _split_punct(token: str) -> tuple[str, str, str]:
    m = re.match(r"^([\"'(]*)(.*?)([\"').,!?:;]*)$", token)
    if not m:
        return token, "", ""
    return m.group(2), m.group(1), m.group(3)


def _match_case(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def humanize_post(
    text: str,
    *,
    seed: int | None = None,
    brand_names: tuple[str, ...] | list[str] = (),
) -> str:
    """帖子口语化后处理：去破折号 + 替换 AI 套话 + 打散句长节奏。

    与评论不同，长帖不注入笔误（观感差、易被识破）；品牌名原样保留不脱敏。
    """
    text = strip_em_dashes(text or "")
    if not text:
        return text
    text = replace_ai_tells(text)
    return vary_sentence_rhythm(text, seed=seed)

