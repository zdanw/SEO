"""社区官方版规拉取与 prompt 注入。"""
from datetime import datetime, timedelta, timezone

from app.services.reddit_community_verify import (
    apply_rules_to_community,
    fetch_subreddit_rules,
    fetch_subreddit_rules_via_zernio,
    format_rules_from_payload,
    format_rules_prompt_line,
    is_rules_fresh,
    truncate_rules_for_prompt,
)


def test_format_rules_from_zernio_camel_case():
    text = format_rules_from_payload(
        {
            "rules": [
                {
                    "shortName": "No self-promotion",
                    "description": "Posts that primarily promote your own product will be removed.",
                }
            ],
            "siteRules": ["Spam", "Personal info"],
        }
    )
    assert "No self-promotion" in text
    assert "Site-wide: Spam; Personal info" in text


def test_fetch_subreddit_rules_via_zernio():
    class FakeClient:
        def get_subreddit_rules(self, subreddit: str):
            assert subreddit == "Parenting"
            return {
                "rules": [{"shortName": "Be kind", "description": "Be nice."}],
                "siteRules": ["Spam"],
            }

    result = fetch_subreddit_rules_via_zernio(FakeClient(), "Parenting")
    assert result.rules_text
    assert "Be kind" in result.rules_text
    assert "Spam" in result.rules_text


def test_fetch_subreddit_rules_via_zernio_rate_limit():
    class FakeClient:
        def get_subreddit_rules(self, subreddit: str):
            err = type(
                "E",
                (Exception,),
                {"status_code": 429, "retry_after": 112},
            )("reddit rate limit reached. Quota resets in 112s.")
            raise err

    result = fetch_subreddit_rules_via_zernio(FakeClient(), "Parenting")
    assert result.rate_limited is True
    assert result.retry_after == 112
    assert "限流" in (result.error or "")
    assert result.rules_text is None


def test_fetch_subreddit_rules_403_mentions_oauth_config():
    result = fetch_subreddit_rules("Nope", http_get=lambda _u: (403, {"raw": "blocked"}))
    assert result.rules_text is None
    assert "REDDIT_CLIENT_ID" in (result.error or "")


def test_fetch_subreddit_rules_uses_injected_http_get():
    def http_get(url: str):
        assert url.endswith("/about/rules.json")
        return 200, {
            "rules": [
                {"short_name": "Self promo", "description": "Only on Fridays."},
            ]
        }

    result = fetch_subreddit_rules("Parenting", http_get=http_get)
    assert result.rules_text
    assert "Self promo" in result.rules_text
    assert result.error is None


def test_fetch_subreddit_rules_404():
    result = fetch_subreddit_rules("Nope", http_get=lambda _u: (404, {}))
    assert result.rules_text is None
    assert result.error


def test_apply_and_freshness():
    class Row:
        rules_text = None
        rules_fetched_at = None
        verify_error = None

    row = Row()
    apply_rules_to_community(
        row,
        fetch_subreddit_rules(
            "x",
            http_get=lambda _u: (200, {"rules": [{"short_name": "A", "description": "B"}]}),
        ),
    )
    assert row.rules_text
    assert is_rules_fresh(row, now=datetime.now(timezone.utc))
    assert not is_rules_fresh(row, now=datetime.now(timezone.utc) + timedelta(hours=25))


def test_rules_prompt_line_injected_into_post():
    from app.services import ai_writer

    captured: list[str] = []

    class FakeClient(ai_writer.DeepSeekClient):
        def __init__(self):
            pass

        def chat(self, user_prompt, system_prompt="", temperature=0.7, max_tokens=4096):
            captured.append(user_prompt)
            return '{"title": "t", "body": "b"}'

    FakeClient().generate_reddit_post(
        "auto",
        "Parenting",
        "",
        None,
        community_rules="1. No spam: Do not spam.",
        allow_product=False,
    )
    assert "subreddit rules" in captured[0].lower()
    assert "no spam" in captured[0].lower()


def test_truncate_rules_for_prompt():
    long = "x" * 3000
    out = truncate_rules_for_prompt(long, max_chars=100)
    assert len(out) <= 100
    assert "truncated" in out
    assert format_rules_prompt_line("") == ""
