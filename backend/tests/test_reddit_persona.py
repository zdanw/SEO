"""人设解析：短备注与 JSON 都能变成 prompt 片段。"""
from app.services.reddit_persona import parse_persona


def test_plain_persona_note_becomes_voice():
    p = parse_persona("海外宝妈")
    assert p.voice == "海外宝妈"
    assert p.interests == []


def test_json_persona_keeps_interests():
    p = parse_persona(
        None,
        config={"voice": "working mom", "interests": ["parenting", "cooking"], "quirks": "always tired", "never_say": "hard sell"},
    )
    assert p.voice == "working mom"
    assert p.interests == ["parenting", "cooking"]
    prompt = p.to_prompt()
    assert "working mom" in prompt
    assert "parenting" in prompt
    assert "hard sell" in prompt


# ============ 结构化字段（A）============
def _rich_config():
    return {
        "voice": "plainspoken dad",
        "interests": ["parenting", "home coffee"],
        "quirks": "always misplacing keys",
        "never_say": "buy now, limited time",
        "background": "Moved cities twice for work; raising two kids.",
        "life_facts": ["drives a 12-year-old hatchback", "brews coffee before 6am"],
        "signature_opinions": ["most parenting advice is overpriced"],
        "recurring_themes": ["budgeting", "weekend routines"],
    }


def test_parse_structured_fields():
    p = parse_persona(None, config=_rich_config())
    assert p.background.startswith("Moved cities")
    assert p.life_facts == ["drives a 12-year-old hatchback", "brews coffee before 6am"]
    assert p.signature_opinions == ["most parenting advice is overpriced"]
    assert p.recurring_themes == ["budgeting", "weekend routines"]


def test_to_dict_roundtrip_keeps_new_fields():
    p = parse_persona(None, config=_rich_config())
    d = p.to_dict()
    assert d["background"] == p.background
    assert d["life_facts"] == p.life_facts
    again = parse_persona(None, config=d)
    assert again.signature_opinions == p.signature_opinions
    assert again.recurring_themes == p.recurring_themes


def test_prompt_injects_exactly_one_life_fact():
    p = parse_persona(None, config=_rich_config())
    prompt = p.to_prompt(seed=1)
    hit = [f for f in p.life_facts if f in prompt]
    assert len(hit) == 1  # 每次只注入 1 条生活细节
    # 不同 seed 可轮换到不同 life_fact
    prompts = [p.to_prompt(seed=s) for s in range(10)]
    seen = {f for f in p.life_facts for text in prompts if f in text}
    assert seen == set(p.life_facts)


def test_prompt_excludes_given_facts():
    p = parse_persona(None, config=_rich_config())
    prompt = p.to_prompt(seed=1, exclude_facts=(p.life_facts[0],))
    assert p.life_facts[0] not in prompt
    assert p.life_facts[1] in prompt


# ============ 按社区适配（D）============
def test_subreddit_override_changes_voice_and_quirks():
    cfg = _rich_config()
    cfg["subreddit_overrides"] = {
        "r/Coffee": {"voice": "obsessive home barista", "quirks": "weighs every bean"},
    }
    p = parse_persona(None, config=cfg)
    assert p.override_for("coffee")["voice"] == "obsessive home barista"
    prompt = p.to_prompt(subreddit="r/Coffee")
    assert "obsessive home barista" in prompt
    assert "weighs every bean" in prompt
    assert "plainspoken dad" not in prompt  # 基础 voice 被覆盖
    base = p.to_prompt(subreddit="r/Other")
    assert "plainspoken dad" in base


def test_override_life_facts_replace_base():
    cfg = _rich_config()
    cfg["subreddit_overrides"] = {"coffee": {"life_facts": ["roasts beans on Sundays"]}}
    p = parse_persona(None, config=cfg)
    prompt = p.to_prompt(subreddit="coffee", seed=3)
    assert "roasts beans on Sundays" in prompt
    assert p.life_facts[0] not in prompt


# ============ 一致性校验 ============
def test_consistency_warnings_flag_never_say_conflict():
    cfg = _rich_config()
    cfg["never_say"] = "hatchback"
    p = parse_persona(None, config=cfg)
    warnings = p.consistency_warnings()
    assert any("hatchback" in w for w in warnings)


def test_consistency_warnings_clean_config():
    p = parse_persona(None, config=_rich_config())
    assert p.consistency_warnings() == []


def test_is_empty():
    assert parse_persona("").is_empty()
    assert not parse_persona(None, config=_rich_config()).is_empty()
