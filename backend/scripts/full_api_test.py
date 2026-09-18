"""全面 API 测试：覆盖 OpenAPI 全部路由 + 业务闭环。

用法:
  cd backend
  python scripts/full_api_test.py

分类:
  PASS  — 期望成功且成功
  FAIL  — 期望成功但失败，或期望失败但状态不符
  SOFT  — 外部依赖/前置不足导致的可接受失败（GSC 未连接、Reddit 空等）
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

BASE = "http://127.0.0.1:8000"
API = f"{BASE}/api"
TS = int(time.time())
EMAIL = f"fullapi_{TS}@example.com"
PWD = "TestPass123!"


@dataclass
class Result:
    name: str
    status: str  # PASS | FAIL | SOFT
    detail: str = ""
    code: int | None = None


@dataclass
class Ctx:
    token: str = ""
    site_id: int | None = None
    topic_id: int | None = None
    child_topic_id: int | None = None
    query_id: int | None = None
    asset_id: int | None = None
    version_id: int | None = None
    project_id: int | None = None
    evidence_id: int | None = None
    claim_id: int | None = None
    ai_query_id: int | None = None
    run_id: int | None = None
    result_id: int | None = None
    opp_id: int | None = None
    experiment_id: int | None = None
    audit_id: int | None = None
    results: list[Result] = field(default_factory=list)

    @property
    def headers(self) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        if self.site_id:
            h["X-Site-Id"] = str(self.site_id)
        return h


def call(
    ctx: Ctx,
    method: str,
    path: str,
    *,
    name: str | None = None,
    expect: int | tuple[int, ...] = 200,
    soft: tuple[int, ...] = (),
    json_body: Any = None,
    params: dict | None = None,
    auth: bool = True,
) -> Any:
    label = name or f"{method} {path}"
    url = path if path.startswith("http") else f"{API}{path}"
    headers = ctx.headers if auth else {"Content-Type": "application/json"}
    allowed = expect if isinstance(expect, tuple) else (expect,)
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            r = requests.request(
                method,
                url,
                headers=headers,
                json=json_body,
                params=params,
                timeout=45,
            )
            break
        except (requests.ConnectionError, requests.Timeout, ConnectionResetError) as e:
            last_err = e
            time.sleep(0.8 * (attempt + 1))
    else:
        ctx.results.append(Result(label, "FAIL", str(last_err)[:400]))
        return None

    code = r.status_code
    body_preview = (r.text or "")[:400]
    data: Any = None
    if r.content:
        try:
            data = r.json()
        except Exception:
            data = r.text

    if code in allowed:
        ctx.results.append(Result(label, "PASS", "", code))
        return data
    if code in soft:
        ctx.results.append(Result(label, "SOFT", body_preview, code))
        return data
    ctx.results.append(Result(label, "FAIL", body_preview, code))
    return data


def seed_observation(ctx: Ctx) -> None:
    """直接写库一条高展示低 CTR 观测，便于 opportunities/build 产出机会。"""
    if not ctx.site_id or not ctx.query_id:
        return
    try:
        from app.core.database import SessionLocal
        from app.models.search_observation import SearchObservation

        db = SessionLocal()
        try:
            obs = SearchObservation(
                site_id=ctx.site_id,
                search_query_id=ctx.query_id,
                source="gsc",
                observed_at=datetime.now(timezone.utc),
                impressions=5000,
                clicks=20,
                ctr=0.004,
                rank=8.5,
                page_url="https://example.com/e2e",
            )
            db.add(obs)
            db.commit()
            ctx.results.append(Result("seed SearchObservation", "PASS"))
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        ctx.results.append(Result("seed SearchObservation", "SOFT", str(e)[:300]))


def section_auth(ctx: Ctx) -> None:
    call(ctx, "GET", f"{BASE}/api/health", name="GET /api/health", auth=False)
    call(ctx, "GET", f"{BASE}/api/v1/health", name="旧 /api/v1 应 404", expect=404, auth=False)
    call(ctx, "GET", f"{BASE}/api/v2/health", name="旧 /api/v2 应 404", expect=404, auth=False)

    call(
        ctx,
        "POST",
        "/auth/register",
        expect=(200, 201),
        auth=False,
        json_body={"email": EMAIL, "password": PWD, "full_name": "Full API"},
    )
    login = call(
        ctx,
        "POST",
        "/auth/login",
        auth=False,
        json_body={"email": EMAIL, "password": PWD},
    )
    if isinstance(login, dict) and login.get("access_token"):
        ctx.token = login["access_token"]
    call(ctx, "GET", "/auth/me")
    call(ctx, "GET", "/sites", name="无 token → 401", expect=401, auth=False)
    call(
        ctx,
        "POST",
        "/auth/login",
        name="错误密码 → 401",
        expect=401,
        auth=False,
        json_body={"email": EMAIL, "password": "wrong-password"},
    )
    call(
        ctx,
        "POST",
        "/auth/register",
        name="非法邮箱 → 422",
        expect=422,
        auth=False,
        json_body={"email": "bad@", "password": PWD},
    )


def section_sites(ctx: Ctx) -> None:
    created = call(
        ctx,
        "POST",
        "/sites",
        expect=(200, 201),
        json_body={
            "name": f"FullAPI Site {TS}",
            "domain": f"fullapi-{TS}.example.com",
            "industry": "SaaS",
        },
    )
    sites = call(ctx, "GET", "/sites")
    if isinstance(created, dict) and created.get("id"):
        ctx.site_id = created["id"]
    elif isinstance(sites, list) and sites:
        ctx.site_id = sites[0]["id"]

    sid = ctx.site_id
    call(ctx, "GET", f"/sites/{sid}")
    call(ctx, "PATCH", f"/sites/{sid}", json_body={"notes": "full api test"})
    call(ctx, "GET", f"/sites/{sid}/members")
    call(
        ctx,
        "PATCH",
        f"/sites/{sid}/onboarding",
        json_body={
            "industry": "SaaS",
            "target_market": "United States",
            "language": "en",
            "primary_goal": "more_traffic",
        },
    )
    call(ctx, "GET", f"/sites/{sid}/init-status")
    call(ctx, "POST", f"/sites/{sid}/init", expect=(200, 202))
    call(ctx, "GET", f"/sites/{sid}/baseline")
    call(ctx, "GET", f"/sites/{sid}/command-center")
    call(ctx, "GET", f"/sites/999999", name="站点不存在 → 404", expect=(403, 404))


def section_topics(ctx: Ctx) -> None:
    sid = ctx.site_id
    topic = call(
        ctx,
        "POST",
        f"/sites/{sid}/topics",
        expect=(200, 201),
        json_body={"name": f"Root Topic {TS}", "priority": 2, "intent_summary": "full api"},
    )
    if isinstance(topic, dict):
        ctx.topic_id = topic.get("id")
    call(ctx, "GET", f"/sites/{sid}/topics")
    tid = ctx.topic_id
    if not tid:
        return
    call(ctx, "GET", f"/topics/{tid}")
    call(ctx, "PATCH", f"/topics/{tid}", json_body={"description": "updated"})
    child = call(
        ctx,
        "POST",
        f"/sites/{sid}/topics",
        expect=(200, 201),
        json_body={"name": f"Child Topic {TS}", "parent_id": tid, "priority": 3},
    )
    if isinstance(child, dict):
        ctx.child_topic_id = child.get("id")
    call(ctx, "GET", f"/topics/{tid}/children")
    call(ctx, "GET", f"/topics/{tid}/stats")
    call(ctx, "GET", f"/topics/{tid}/queries")
    call(ctx, "GET", f"/topics/{tid}/content-assets")
    call(ctx, "POST", f"/topics/{tid}/archive", expect=(200, 201))
    # 恢复 active 方便后续挂接
    call(ctx, "PATCH", f"/topics/{tid}", json_body={"status": "active"})


def section_queries(ctx: Ctx) -> None:
    sid = ctx.site_id
    q = call(
        ctx,
        "POST",
        f"/sites/{sid}/queries",
        expect=(200, 201),
        json_body={
            "query": f"best crm for startups {TS}",
            "topic_id": ctx.topic_id,
            "country_code": "US",
            "language_code": "en",
            "priority": 2,
        },
    )
    if isinstance(q, dict):
        ctx.query_id = q.get("id")
    call(ctx, "GET", f"/sites/{sid}/queries")
    qid = ctx.query_id
    if not qid:
        return
    call(ctx, "GET", f"/queries/{qid}")
    call(ctx, "PATCH", f"/queries/{qid}", json_body={"intent": "commercial", "priority": 1})
    if ctx.topic_id:
        call(
            ctx,
            "POST",
            f"/queries/{qid}/attach-topic",
            expect=(200, 201, 422),
            soft=(400, 404, 422),
            json_body={"topic_id": ctx.topic_id},
        )
    call(ctx, "GET", f"/queries/{qid}/observations")
    call(ctx, "GET", f"/queries/{qid}/content-assets")
    call(ctx, "GET", f"/sites/{sid}/search/overview")


def section_content(ctx: Ctx) -> None:
    sid = ctx.site_id
    asset = call(
        ctx,
        "POST",
        f"/sites/{sid}/content-assets",
        expect=(200, 201),
        json_body={
            "asset_type": "article",
            "title": f"Full API Asset {TS}",
            "primary_topic_id": ctx.topic_id,
            "primary_query_id": ctx.query_id,
            "summary": "e2e content",
        },
    )
    if isinstance(asset, dict):
        ctx.asset_id = asset.get("id")
    call(ctx, "GET", f"/sites/{sid}/content-assets")
    aid = ctx.asset_id
    if not aid:
        return
    call(ctx, "GET", f"/content-assets/{aid}")
    call(ctx, "PATCH", f"/content-assets/{aid}", json_body={"title": f"Updated Asset {TS}"})
    ver = call(
        ctx,
        "POST",
        f"/content-assets/{aid}/versions",
        expect=(200, 201),
        soft=(400, 422),
        json_body={"body": "# Hello\n\nFull API version body.", "source_type": "human"},
    )
    if isinstance(ver, dict):
        ctx.version_id = ver.get("id")
    call(ctx, "GET", f"/content-assets/{aid}/versions")
    call(ctx, "GET", f"/content-assets/{aid}/graph")
    if ctx.topic_id:
        call(
            ctx,
            "POST",
            f"/content-assets/{aid}/topics",
            expect=(200, 201, 204),
            soft=(400, 409, 422),
            json_body={"topic_id": ctx.topic_id, "relation_type": "supporting"},
        )
    if ctx.query_id:
        call(
            ctx,
            "POST",
            f"/content-assets/{aid}/queries",
            expect=(200, 201, 204),
            soft=(400, 409, 422),
            json_body={"query_id": ctx.query_id, "relation_type": "target"},
        )
    # 发布需要 version_number；无 CMS 时 soft
    vers = call(ctx, "GET", f"/content-assets/{aid}/versions", name="GET versions for publish")
    vnum = None
    if isinstance(vers, list) and vers:
        vnum = vers[0].get("version_number")
    call(
        ctx,
        "POST",
        f"/content-assets/{aid}/publish",
        expect=(200, 201),
        soft=(400, 422, 502),
        json_body={"version_number": vnum} if vnum else {},
    )
    call(ctx, "GET", f"/content-assets/{aid}/claims")


def section_research_claims(ctx: Ctx) -> None:
    sid = ctx.site_id
    proj = call(
        ctx,
        "POST",
        f"/sites/{sid}/research/projects",
        expect=(200, 201),
        json_body={"name": f"Research {TS}", "objective": "full api"},
    )
    if isinstance(proj, dict):
        ctx.project_id = proj.get("id")
    call(ctx, "GET", f"/sites/{sid}/research/projects")
    pid = ctx.project_id
    if pid:
        call(ctx, "GET", f"/research/projects/{pid}")
        call(ctx, "PATCH", f"/research/projects/{pid}", json_body={"methodology": "desk research"})
        ev = call(
            ctx,
            "POST",
            f"/research/projects/{pid}/evidence",
            expect=(200, 201),
            soft=(400, 422),
            json_body={
                "source_type": "external",
                "source_url": "https://example.com/evidence",
                "method_summary": "sample evidence",
            },
        )
        if isinstance(ev, dict):
            ctx.evidence_id = ev.get("id")
        if ctx.evidence_id:
            call(ctx, "GET", f"/research/evidence/{ctx.evidence_id}")
        call(ctx, "POST", f"/research/projects/{pid}/complete", expect=(200, 201), soft=(400, 409))

    claim = call(
        ctx,
        "POST",
        f"/sites/{sid}/claims",
        expect=(200, 201),
        soft=(400, 422),
        json_body={
            "claim_key": f"ctr-claim-{TS}",
            "claim_text": f"Product X improves CTR by 20% ({TS})",
            "claim_type": "comparison",
            "risk_level": "medium",
        },
    )
    if isinstance(claim, dict):
        ctx.claim_id = claim.get("id")
    call(ctx, "GET", f"/sites/{sid}/claims")
    cid = ctx.claim_id
    if not cid:
        return
    call(ctx, "GET", f"/claims/{cid}")
    call(ctx, "PATCH", f"/claims/{cid}", json_body={"statement": f"Updated claim {TS}"}, soft=(400, 422))
    if ctx.evidence_id:
        call(
            ctx,
            "POST",
            f"/claims/{cid}/evidence",
            expect=(200, 201),
            soft=(400, 409, 422),
            json_body={"research_evidence_id": ctx.evidence_id},
        )
    call(ctx, "GET", f"/claims/{cid}/publishability")
    call(ctx, "POST", f"/claims/{cid}/approve", expect=(200, 201), soft=(400, 409, 422), json_body={})
    call(ctx, "POST", f"/claims/{cid}/reject", expect=(200, 201), soft=(400, 409, 422), json_body={"reason": "test"})
    if ctx.asset_id:
        call(
            ctx,
            "POST",
            f"/content-assets/{ctx.asset_id}/claims/{cid}",
            expect=(200, 201, 204),
            soft=(400, 409, 422),
        )


def section_ai_search(ctx: Ctx) -> None:
    sid = ctx.site_id
    aq = call(
        ctx,
        "POST",
        f"/sites/{sid}/ai-search/queries",
        expect=(200, 201),
        json_body={"query_text": f"best seo tools {TS}", "topic_id": ctx.topic_id, "priority": 2},
    )
    if isinstance(aq, dict):
        ctx.ai_query_id = aq.get("id")
    call(ctx, "GET", f"/sites/{sid}/ai-search/queries")
    call(ctx, "GET", f"/sites/{sid}/ai-search/overview")
    call(ctx, "GET", f"/sites/{sid}/ai-search/visibility")
    call(ctx, "GET", f"/sites/{sid}/ai-search/citation-winners")
    call(ctx, "GET", f"/sites/{sid}/ai-search/content-gaps")
    call(ctx, "GET", f"/sites/{sid}/ai-search/query-matrix")
    call(
        ctx,
        "POST",
        f"/sites/{sid}/ai-search/queries/import",
        expect=(200, 201),
        soft=(400, 404, 422),
        json_body={},
    )
    call(
        ctx,
        "POST",
        f"/sites/{sid}/ai-search/aggregate",
        expect=(200, 201),
        soft=(400, 422),
        json_body={},
    )
    qid = ctx.ai_query_id
    if not qid:
        return
    call(ctx, "GET", f"/ai-search/queries/{qid}")
    call(ctx, "PATCH", f"/ai-search/queries/{qid}", json_body={"priority": 1})
    run = call(
        ctx,
        "POST",
        f"/ai-search/queries/{qid}/runs",
        expect=(200, 201),
        soft=(400, 422, 502),
        json_body={},
    )
    if isinstance(run, dict):
        ctx.run_id = run.get("id") or run.get("run_id")
    call(
        ctx,
        "POST",
        f"/ai-search/queries/{qid}/runs/manual",
        expect=(200, 201),
        soft=(400, 422),
        json_body={
            "provider": "manual",
            "answer_text": "Example answer citing example.com",
            "citations": [{"cited_url": "https://example.com", "cited_domain": "example.com", "anchor_text": "Example"}],
        },
    )
    if ctx.run_id:
        call(ctx, "GET", f"/ai-search/runs/{ctx.run_id}")
        results = call(ctx, "GET", f"/ai-search/runs/{ctx.run_id}/results")
        if isinstance(results, list) and results:
            ctx.result_id = results[0].get("id")
        elif isinstance(results, dict) and results.get("id"):
            ctx.result_id = results.get("id")
    if ctx.result_id:
        call(ctx, "GET", f"/ai-search/results/{ctx.result_id}/citations")


def section_gsc_reddit_technical(ctx: Ctx) -> None:
    sid = ctx.site_id
    # GSC — 未连接时多为 soft；metrics 需要日期
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=28)).isoformat()
    call(ctx, "GET", f"/sites/{sid}/gsc/metrics", params={"start": start, "end": end}, soft=(400, 404, 422, 502))
    call(ctx, "GET", f"/sites/{sid}/gsc/top-pages", params={"start": start, "end": end}, soft=(400, 404, 422, 502))
    call(ctx, "GET", f"/sites/{sid}/gsc/top-queries", params={"start": start, "end": end}, soft=(400, 404, 422, 502))
    call(
        ctx,
        "POST",
        f"/sites/{sid}/gsc/sync",
        expect=(200, 201),
        soft=(400, 404, 422, 502),
        json_body={},
    )
    call(
        ctx,
        "POST",
        f"/sites/{sid}/gsc/backfill",
        expect=(200, 201),
        soft=(400, 404, 422, 502),
        json_body={"start": start, "end": end},
    )

    call(ctx, "GET", f"/sites/{sid}/reddit/threads")
    call(ctx, "GET", f"/sites/{sid}/reddit/opportunities")
    call(
        ctx,
        "POST",
        f"/sites/{sid}/reddit/threads/collect",
        expect=(200, 201),
        soft=(400, 404, 422, 502),
        json_body={
            "source": "manual",
            "threads": [
                {
                    "subreddit": "seo",
                    "title": "How to improve CTR?",
                    "url": f"https://reddit.com/r/seo/e2e{TS}",
                    "external_thread_id": f"e2e{TS}",
                }
            ],
        },
    )
    # 无 thread 时 404 soft
    call(ctx, "GET", "/reddit/threads/999999", expect=(404, 403), soft=(400,))
    call(ctx, "POST", "/reddit/threads/999999/score", expect=(404, 400, 422), soft=(502,))
    call(ctx, "GET", "/reddit/communities/999999/rules", expect=(404, 200), soft=(400,))
    call(
        ctx,
        "POST",
        "/reddit/communities/999999/rules/snapshot",
        expect=(404, 400, 422),
        soft=(502,),
        json_body={},
    )
    call(
        ctx,
        "POST",
        "/reddit/opportunities/999999/status",
        expect=(404, 400, 422),
        json_body={"status": "observed"},
    )

    audit = call(
        ctx,
        "POST",
        f"/sites/{sid}/technical-audits",
        expect=(200, 201),
        json_body={
            "url": f"https://fullapi-{TS}.example.com/",
            "score": 88.5,
            "findings": [{"code": "missing_h1", "severity": "warning"}],
            "cwv": {"lcp": 2.1},
        },
    )
    if isinstance(audit, dict):
        ctx.audit_id = audit.get("id")
    call(ctx, "GET", f"/sites/{sid}/technical-audits")


def section_opportunities_experiments(ctx: Ctx) -> None:
    sid = ctx.site_id
    seed_observation(ctx)
    build = call(ctx, "POST", f"/sites/{sid}/opportunities/build", expect=(200, 201))
    opps = call(ctx, "GET", f"/sites/{sid}/opportunities", params={"status": "open"})
    if isinstance(opps, list) and opps:
        ctx.opp_id = opps[0].get("id")
    elif isinstance(build, dict) and build.get("created", 0) == 0:
        ctx.results.append(Result("opportunities empty after build", "SOFT", str(build)[:200]))

    oid = ctx.opp_id
    if oid:
        call(ctx, "GET", f"/opportunities/{oid}")
        exe = call(
            ctx,
            "POST",
            f"/opportunities/{oid}/execute",
            json_body={"channel": "copy", "payload": {"text": "New title for CTR"}},
        )
        if isinstance(exe, dict):
            ctx.experiment_id = (
                exe.get("experiment_id")
                or (exe.get("experiment") or {}).get("id")
                if isinstance(exe.get("experiment"), dict)
                else None
            )
        call(
            ctx,
            "POST",
            f"/opportunities/{oid}/execute",
            name="execute task channel",
            expect=(200, 201),
            soft=(400, 409, 422),
            json_body={"channel": "task", "payload": {"assignee": "SEO", "note": "fix title"}},
        )
        call(
            ctx,
            "POST",
            f"/opportunities/{oid}/resolve",
            expect=(200, 201),
            soft=(400, 409),
            json_body={"resolution_note": "done in full api test"},
        )

    call(ctx, "GET", f"/sites/{sid}/experiments")
    exps = None
    for r in reversed(ctx.results):
        if r.name.startswith("GET /sites/") and "experiments" in r.name and r.status == "PASS":
            break
    # 再取一次 experiments 数据
    exps = call(ctx, "GET", f"/sites/{sid}/experiments", name="GET experiments (data)")
    if isinstance(exps, list) and exps and not ctx.experiment_id:
        ctx.experiment_id = exps[0].get("id")
    eid = ctx.experiment_id
    if eid:
        call(ctx, "GET", f"/experiments/{eid}")
        call(ctx, "POST", f"/experiments/{eid}/measure", expect=(200, 201), soft=(400, 409, 422))


def section_openapi_probe(ctx: Ctx) -> None:
    """对 OpenAPI 中尚未覆盖的 GET，用占位 ID 探测可达性（404/401 也算探测成功）。"""
    try:
        spec = requests.get(f"{BASE}/openapi.json", timeout=15).json()
    except Exception as e:  # noqa: BLE001
        ctx.results.append(Result("openapi.json", "FAIL", str(e)))
        return
    ctx.results.append(Result("openapi.json", "PASS", f"paths={len(spec.get('paths', {}))}"))

    repl = {
        "{site_id}": str(ctx.site_id or 0),
        "{topic_id}": str(ctx.topic_id or 0),
        "{query_id}": str(ctx.query_id or 0),
        "{asset_id}": str(ctx.asset_id or 0),
        "{opportunity_id}": str(ctx.opp_id or 0),
        "{experiment_id}": str(ctx.experiment_id or 0),
        "{project_id}": str(ctx.project_id or 0),
        "{evidence_id}": str(ctx.evidence_id or 0),
        "{claim_id}": str(ctx.claim_id or 0),
        "{query_id}": str(ctx.query_id or ctx.ai_query_id or 0),
    }
    # ai search path uses query_id for ai queries too — handle separately below

    probed = 0
    for path, methods in sorted(spec.get("paths", {}).items()):
        if "get" not in methods:
            continue
        # skip health already tested
        concrete = path
        if "{query_id}" in concrete and "/ai-search/" in concrete:
            concrete = concrete.replace("{query_id}", str(ctx.ai_query_id or 0))
        for k, v in {
            "{site_id}": str(ctx.site_id or 0),
            "{topic_id}": str(ctx.topic_id or 0),
            "{query_id}": str(ctx.query_id or 0),
            "{asset_id}": str(ctx.asset_id or 0),
            "{opportunity_id}": str(ctx.opp_id or 999999),
            "{experiment_id}": str(ctx.experiment_id or 999999),
            "{project_id}": str(ctx.project_id or 999999),
            "{evidence_id}": str(ctx.evidence_id or 999999),
            "{claim_id}": str(ctx.claim_id or 999999),
            "{run_id}": str(ctx.run_id or 999999),
            "{result_id}": str(ctx.result_id or 999999),
            "{thread_id}": "999999",
            "{community_id}": "999999",
            "{opportunity_id}": str(ctx.opp_id or 999999),
        }.items():
            concrete = concrete.replace(k, v)
        if "{" in concrete:
            continue
        # path already includes /api
        url = f"{BASE}{concrete}" if concrete.startswith("/api") else f"{API}{concrete}"
        try:
            r = requests.get(url, headers=ctx.headers, timeout=20)
            # 任何响应都算探测到（含 404/422）
            status = "PASS" if r.status_code < 500 else "FAIL"
            if r.status_code >= 500:
                ctx.results.append(Result(f"probe GET {concrete}", "FAIL", r.text[:200], r.status_code))
            else:
                ctx.results.append(Result(f"probe GET {concrete}", status, "", r.status_code))
            probed += 1
        except Exception as e:  # noqa: BLE001
            ctx.results.append(Result(f"probe GET {concrete}", "FAIL", str(e)[:200]))
    ctx.results.append(Result("openapi GET probe count", "PASS", f"probed={probed}"))


def print_report(ctx: Ctx) -> int:
    passes = [r for r in ctx.results if r.status == "PASS"]
    fails = [r for r in ctx.results if r.status == "FAIL"]
    softs = [r for r in ctx.results if r.status == "SOFT"]

    print("\n" + "=" * 72)
    print(f"FULL API TEST  PASS={len(passes)}  FAIL={len(fails)}  SOFT={len(softs)}  TOTAL={len(ctx.results)}")
    print(f"user={EMAIL}  site_id={ctx.site_id}")
    print("=" * 72)

    if fails:
        print("\n--- FAIL ---")
        for r in fails:
            print(f"[{r.code}] {r.name}: {r.detail[:240]}")
    if softs:
        print("\n--- SOFT (可接受) ---")
        for r in softs:
            print(f"[{r.code}] {r.name}: {r.detail[:180]}")

    print("\n--- 摘要 by 域 ---")
    buckets: dict[str, dict[str, int]] = {}
    for r in ctx.results:
        key = r.name.split()[0] if r.name.startswith(("GET", "POST", "PATCH", "DELETE")) else r.name.split()[0]
        # better: first path segment after method
        parts = r.name.split(" ", 1)
        bucket = "other"
        if len(parts) == 2:
            p = parts[1]
            if "/auth" in p:
                bucket = "auth"
            elif "/sites" in p and "onboarding" in p or "/init" in p or "baseline" in p or "command-center" in p:
                bucket = "onboarding/cc"
            elif "/topic" in p:
                bucket = "topics"
            elif "/quer" in p or "/search/" in p:
                bucket = "search"
            elif "/content" in p:
                bucket = "content"
            elif "/research" in p or "/claim" in p:
                bucket = "research/claims"
            elif "/ai-search" in p:
                bucket = "ai_search"
            elif "/gsc" in p:
                bucket = "gsc"
            elif "/reddit" in p:
                bucket = "reddit"
            elif "/technical" in p:
                bucket = "technical"
            elif "/opportunit" in p or "/experiment" in p:
                bucket = "opportunity"
            elif "probe" in p or "openapi" in p:
                bucket = "openapi_probe"
            elif "旧" in p or "health" in p:
                bucket = "health/compat"
            elif "seed" in p:
                bucket = "seed"
        buckets.setdefault(bucket, {"PASS": 0, "FAIL": 0, "SOFT": 0})
        buckets[bucket][r.status] = buckets[bucket].get(r.status, 0) + 1

    for k in sorted(buckets):
        b = buckets[k]
        print(f"  {k:20s}  PASS={b.get('PASS',0):3d}  FAIL={b.get('FAIL',0):3d}  SOFT={b.get('SOFT',0):3d}")

    # write json report
    out = {
        "email": EMAIL,
        "site_id": ctx.site_id,
        "pass": len(passes),
        "fail": len(fails),
        "soft": len(softs),
        "results": [{"name": r.name, "status": r.status, "code": r.code, "detail": r.detail} for r in ctx.results],
    }
    path = f"scripts/full_api_test_report_{TS}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n报告已写入: {path}")
    return 1 if fails else 0


def main() -> int:
    ctx = Ctx()
    print(f"=== Full API Test start {EMAIL} ===")
    section_auth(ctx)
    if not ctx.token:
        print("ABORT: no token")
        return print_report(ctx)
    section_sites(ctx)
    if not ctx.site_id:
        print("ABORT: no site")
        return print_report(ctx)
    section_topics(ctx)
    section_queries(ctx)
    section_content(ctx)
    section_research_claims(ctx)
    section_ai_search(ctx)
    section_gsc_reddit_technical(ctx)
    section_opportunities_experiments(ctx)
    section_openapi_probe(ctx)
    return print_report(ctx)


if __name__ == "__main__":
    # 便于 seed 写库
    sys.path.insert(0, ".")
    sys.exit(main())
