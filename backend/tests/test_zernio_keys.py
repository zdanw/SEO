import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.social import SocialAccount
from app.models.zernio_key import ZernioApiKey
from app.services.reddit_client import (
    RedditApiError,
    get_reddit_client_for_account,
    resolve_zernio_api_credentials,
)
from app.services.reddit_oauth import sync_zernio_accounts
from app.services.zernio_client import ZernioClient, ZernioError
from app.services.zernio_keys import is_zernio_ready, mask_api_key


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    ZernioApiKey.__table__.create(engine)
    SocialAccount.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_mask_api_key():
    assert mask_api_key("") == ""
    assert mask_api_key("short") == "****"
    assert mask_api_key("sk_live_abcdefgh") == "sk_l…efgh"


def test_client_uses_injected_key():
    z = ZernioClient(api_key="sk_pool", profile_id="prof_a")
    assert z.api_key == "sk_pool"
    assert z.profile_id == "prof_a"
    assert z._mock_mode is False


def test_client_without_key_is_mock():
    z = ZernioClient()
    assert z.api_key is None
    assert z._mock_mode is True


def test_resolve_missing_key_id_is_mock_path():
    api_key, profile_id = resolve_zernio_api_credentials({})
    assert api_key is None
    assert profile_id is None


def test_resolve_deleted_key_raises(db):
    with pytest.raises(RedditApiError) as exc:
        resolve_zernio_api_credentials({"zernio_key_id": 99}, db)
    assert exc.value.status_code == 400


def test_get_client_routes_by_account_key(db):
    key = ZernioApiKey(label="A", api_key="sk_account_a", profile_id=None, is_enabled=True)
    db.add(key)
    db.commit()
    acc = SocialAccount(
        user_id=1,
        site_id=1,
        platform="reddit",
        account_name="u/alice",
        access_token="acc_a",
        config={"zernio_account_id": "acc_a", "zernio_key_id": key.id},
        is_active=True,
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    client = get_reddit_client_for_account(acc)
    assert client._zernio.api_key == "sk_account_a"
    assert client.zernio_account_id == "acc_a"


def test_sync_requires_web_key(db):
    with pytest.raises(ZernioError) as exc:
        sync_zernio_accounts(db, user_id=1, site_id=1)
    assert "社交账号" in str(exc.value)
    assert is_zernio_ready(db) is False


def test_list_hides_accounts_without_enabled_key(db):
    from app.services.reddit_oauth import display_reddit_name, list_reddit_accounts, upsert_from_zernio

    assert display_reddit_name("u/OilDeep4528 · 771490571@qq.com(GitHub)") == "u/OilDeep4528"

    orphan = SocialAccount(
        user_id=1,
        site_id=1,
        platform="reddit",
        account_name="u/orphan",
        access_token="acc_old",
        config={"zernio_account_id": "acc_old"},
        is_active=True,
    )
    db.add(orphan)
    db.commit()
    assert list_reddit_accounts(db, site_id=1) == []
    db.refresh(orphan)
    assert orphan.is_active is False

    taken = SocialAccount(
        user_id=1,
        site_id=1,
        platform="reddit",
        account_name="u/OilDeep4528",
        access_token="acc_other",
        config={"zernio_account_id": "acc_other", "zernio_key_id": 9},
        is_active=True,
    )
    db.add(taken)
    db.commit()
    row = upsert_from_zernio(
        db, 1, 1, "acc_new", "OilDeep4528", zernio_key_id=3, key_label="771490571@qq.com(GitHub)",
    )
    assert row.account_name == "u/OilDeep4528"
    assert "qq.com" not in row.account_name


def test_sync_polls_each_key(db, monkeypatch):
    k1 = ZernioApiKey(label="KeyA", api_key="sk_aaa_aaaa", is_enabled=True)
    k2 = ZernioApiKey(label="KeyB", api_key="sk_bbb_bbbb", is_enabled=True)
    db.add_all([k1, k2])
    db.commit()

    def fake_list(self):
        if self.api_key == "sk_aaa_aaaa":
            return [{"_id": "acc_a", "username": "alice"}]
        if self.api_key == "sk_bbb_bbbb":
            return [{"_id": "acc_b", "username": "bob"}]
        return []

    monkeypatch.setattr(ZernioClient, "list_reddit_accounts", fake_list)
    accounts, errors = sync_zernio_accounts(db, user_id=1, site_id=1)
    assert errors == []
    names = {a.account_name: a.config for a in accounts}
    assert names["u/alice"]["zernio_key_id"] == k1.id
    assert names["u/bob"]["zernio_key_id"] == k2.id
    assert is_zernio_ready(db) is True


def test_sync_continues_after_one_key_fails(db, monkeypatch):
    k1 = ZernioApiKey(label="Bad", api_key="sk_bad_bbbb", is_enabled=True)
    k2 = ZernioApiKey(label="Good", api_key="sk_good_bbbb", is_enabled=True)
    db.add_all([k1, k2])
    db.commit()

    def fake_list(self):
        if self.api_key == "sk_bad_bbbb":
            raise ZernioError("boom", status_code=500)
        return [{"_id": "acc_ok", "username": "okuser"}]

    monkeypatch.setattr(ZernioClient, "list_reddit_accounts", fake_list)
    accounts, errors = sync_zernio_accounts(db, user_id=1, site_id=1)
    assert len(errors) == 1
    assert "Bad" in errors[0]
    assert len(accounts) == 1
    assert accounts[0].account_name == "u/okuser"


def test_sync_all_keys_fail_raises(db, monkeypatch):
    db.add(ZernioApiKey(label="Bad", api_key="sk_bad_only1", is_enabled=True))
    db.commit()

    def fake_list(self):
        raise ZernioError("down", status_code=502)

    monkeypatch.setattr(ZernioClient, "list_reddit_accounts", fake_list)
    with pytest.raises(ZernioError):
        sync_zernio_accounts(db, user_id=1, site_id=1)


def test_publish_uses_account_key(db, monkeypatch):
    key = ZernioApiKey(label="A", api_key="sk_post_key1", is_enabled=True)
    db.add(key)
    db.commit()
    acc = SocialAccount(
        user_id=1,
        site_id=1,
        platform="reddit",
        account_name="u/alice",
        access_token="acc_a",
        config={"zernio_account_id": "acc_a", "zernio_key_id": key.id},
        is_active=True,
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)

    seen: list[str] = []

    def fake_submit(self, zernio_account_id, subreddit, title, body):
        seen.append(self.api_key)
        return {"id": "t3_x", "permalink": "https://reddit.com/r/x"}

    monkeypatch.setattr(ZernioClient, "submit_post", fake_submit)
    client = get_reddit_client_for_account(acc)
    client.submit_post("test", "t", "b")
    assert seen == ["sk_post_key1"]


def test_publish_missing_key_400(db):
    acc = SocialAccount(
        user_id=1,
        site_id=1,
        platform="reddit",
        account_name="u/ghost",
        config={"zernio_account_id": "acc_g", "zernio_key_id": 12345},
        is_active=True,
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    with pytest.raises(RedditApiError) as exc:
        get_reddit_client_for_account(acc)
    assert exc.value.status_code == 400
