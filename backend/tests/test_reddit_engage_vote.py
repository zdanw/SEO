"""养号互动：评论映射与投票入参。"""
from app.services.zernio_client import ZernioClient


def test_map_comment_items_prefixes_t1_and_flattens_replies():
    raw = [
        {
            "id": "abc",
            "message": "top level",
            "from": {"username": "alice"},
            "likeCount": 4,
            "replies": [
                {"id": "t1_def", "message": "reply", "from": {"name": "bob"}, "score": 1},
            ],
        }
    ]
    mapped = ZernioClient._map_comment_items(raw)
    assert mapped[0]["thing_id"] == "t1_abc"
    assert mapped[0]["body"] == "top level"
    assert mapped[0]["author"] == "alice"
    assert mapped[0]["score"] == 4
    assert mapped[1]["thing_id"] == "t1_def"
    assert mapped[1]["author"] == "bob"


def test_vote_mock_ok():
    client = ZernioClient(account_id=0, api_key=None)
    out = client.vote_reddit_thing("mock_acc", "t3_xyz", 1)
    assert out["ok"] is True
    assert out["thingId"] == "t3_xyz"


def test_list_post_comments_mock():
    client = ZernioClient(account_id=0, api_key=None)
    items = client.list_post_comments("mock_acc", "t3_post1", "Parenting", limit=3)
    assert len(items) == 3
    assert all(i["thing_id"].startswith("t1_") for i in items)
