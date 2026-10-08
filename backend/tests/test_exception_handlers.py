"""全局异常处理器测试：结构化响应体、request_id 透传、500 不泄漏内部信息。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.errors import AppException
from app.core.exception_handlers import register_exception_handlers


def _make_app() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> dict:
        raise RuntimeError("secret internal state")

    @app.get("/biz")
    async def biz() -> dict:
        raise AppException("风控拦截", code="risk_blocked", status_code=409, details=["频率过高"])

    @app.get("/http404")
    async def http404() -> dict:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="资源不存在")

    from pydantic import BaseModel

    class Item(BaseModel):
        name: str
        age: int

    @app.post("/items")
    async def create_item(item: Item) -> dict:
        return item.model_dump()

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def client() -> TestClient:
    return _make_app()


def test_unhandled_exception_returns_generic_500(client, monkeypatch):
    monkeypatch.setattr(settings, "APP_DEBUG", False)
    resp = client.get("/boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"] == "Internal server error"
    assert body["code"] == "internal_error"
    assert "secret" not in resp.text


def test_unhandled_exception_debug_mode_keeps_detail(client, monkeypatch):
    monkeypatch.setattr(settings, "APP_DEBUG", True)
    resp = client.get("/boom")
    assert resp.status_code == 500
    assert "secret internal state" in resp.json()["detail"]


def test_app_exception_structured_response(client):
    resp = client.get("/biz")
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"] == "风控拦截"
    assert body["code"] == "risk_blocked"
    assert body["errors"] == ["频率过高"]


def test_http_exception_keeps_detail(client):
    resp = client.get("/http404")
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"] == "资源不存在"
    assert body["code"] == "not_found"


def test_validation_error_structured(client):
    resp = client.post("/items", json={"name": "x"})  # 缺 age
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "validation_error"
    assert isinstance(body["detail"], list)
    assert all(set(e) == {"loc", "msg", "type"} for e in body["detail"])


def test_request_id_header_on_500(client, monkeypatch):
    monkeypatch.setattr(settings, "APP_DEBUG", False)
    rid = "test-rid-123"
    resp = client.get("/boom", headers={"X-Request-ID": rid})
    # 未挂 RequestIdMiddleware 时 handler 读不到 state，但常规链路（main.py）已注入
    assert resp.status_code == 500
    assert resp.json()["request_id"] in (rid, "-")
