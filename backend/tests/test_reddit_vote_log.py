"""Reddit 投票日志：unique(account, thing) 约束 + upsert 语义。"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.reddit import RedditVoteLog

_TABLES = [RedditVoteLog.__table__]


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    session = sessionmaker(bind=engine, autoflush=False)()
    Base.metadata.create_all(engine, tables=_TABLES)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _log(**kwargs):
    defaults = dict(
        site_id=1,
        account_id=1,
        thing_id="t3_abc",
        direction=1,
        source="campaign",
        campaign_action_id=None,
        error_message=None,
    )
    defaults.update(kwargs)
    return RedditVoteLog(**defaults)


def test_vote_log_requires_unique_account_thing(db):
    db.add(_log())
    db.commit()
    db.add(_log(source="manual"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_vote_log_allows_same_thing_across_accounts(db):
    db.add(_log(account_id=1))
    db.add(_log(account_id=2))
    db.commit()
    rows = db.query(RedditVoteLog).all()
    assert len(rows) == 2


def test_vote_log_direction_values(db):
    db.add(_log(direction=-1))
    db.add(_log(account_id=2, thing_id="t3_x2", direction=0))
    db.commit()
    rows = {r.thing_id: r.direction for r in db.query(RedditVoteLog).all()}
    assert rows == {"t3_abc": -1, "t3_x2": 0}
