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


def typo_pairs_for_seed(seed: int | None, *, count: int = ACCOUNT_TYPO_COUNT) -> tuple[tuple[str, str], ...]:
    """按账号/种子从总词表抽固定子集，各号错误风格不同。"""
    pairs = list(_TYPO_PAIRS)
    rng = random.Random(seed)
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
    text = strip_em_dashes(text or "")
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
