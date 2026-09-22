"""产品 description 进入 LLM brief。"""
from app.services.ai_writer import _reddit_post_extra_lines


def test_product_description_in_post_extra_lines():
    lines = _reddit_post_extra_lines(
        "",
        {
            "brand": "Bebcare",
            "product": "Baby Monitors",
            "description": "Analog low-EMF baby monitor without WiFi.",
            "talking_points": ["no wifi"],
        },
    )
    assert "Analog low-EMF baby monitor without WiFi." in lines
