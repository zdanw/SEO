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
