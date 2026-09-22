"""P4：Dashboard 汇总口径改读 Reddit 主路径。"""
from __future__ import annotations

from pathlib import Path


def test_dashboard_summary_uses_reddit_models():
    source = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "dashboard.py"
    text = source.read_text(encoding="utf-8")
    assert "RedditPost" in text
    assert "RedditComment" in text
    assert "from app.models.social import" not in text
    assert "SocialPost.id" not in text
