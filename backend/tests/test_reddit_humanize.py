"""评论真人化：笔误次数受限，品牌名与数字不被改写；账号 seed 抽不同子集。"""
from app.services.reddit_humanize import (
    diversify_opener,
    humanize_comment,
    typo_pairs_for_seed,
)


def test_humanize_injects_at_most_two_typos_with_seed():
    text = (
        "I really think the night wakeups are the hardest part and you get used to it "
        "because their schedule keeps changing with every growth spurt."
    )
    out = humanize_comment(text, seed=7, brand_names=("Bebcare",))
    # 统计白名单替换造成的差异词，上限 2
    src_words = text.split()
    dst_words = out.split()
    changed = sum(1 for a, b in zip(src_words, dst_words) if a != b)
    assert changed <= 2
    assert "Bebcare" not in text or "Bebcare" in out


def test_humanize_never_alters_brand_or_numbers():
    text = "We tried Bebcare for 12 weeks and the range was about 300 feet."
    out = humanize_comment(text, seed=99, brand_names=("Bebcare",))
    assert "Bebcare" in out
    assert "12" in out
    assert "300" in out


def test_diversify_opener_avoids_yeah_honestly():
    assert not diversify_opener("Yeah I had the same issue last month.", seed=1).lower().startswith("yeah")
    assert not diversify_opener("Honestly this is exhausting.", seed=2).lower().startswith("honestly")


def test_strip_em_dashes():
    from app.services.reddit_humanize import strip_em_dashes

    assert "—" not in strip_em_dashes("Tried Bebcare — worked fine overnight.")
    assert "–" not in strip_em_dashes("Tried Bebcare – worked fine.")
    assert strip_em_dashes("Tried Bebcare — worked fine.") == "Tried Bebcare, worked fine."
    out = humanize_comment("Same here — nights are rough.", seed=3, max_typos=0)
    assert "—" not in out


def test_typo_pairs_differ_by_account_seed():
    a = set(typo_pairs_for_seed(1))
    b = set(typo_pairs_for_seed(99))
    assert len(a) == 4
    assert len(b) == 4
    assert a != b


def test_typo_pairs_none_seed_is_stable():
    assert typo_pairs_for_seed(None) == typo_pairs_for_seed(0)
    assert typo_pairs_for_seed(None) == typo_pairs_for_seed(None)
