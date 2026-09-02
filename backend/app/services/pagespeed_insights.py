"""Google PageSpeed Insights API — 真实 Core Web Vitals 测量。"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

PSI_ENDPOINT = "https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed"

# Google CWV 阈值（https://web.dev/articles/vitals）
_LCP_GOOD_MS = 2500
_LCP_POOR_MS = 4000
_CLS_GOOD = 0.1
_CLS_POOR = 0.25
_INP_GOOD_MS = 200
_INP_POOR_MS = 500

_FIELD_METRIC_KEYS = {
    "lcp_ms": "LARGEST_CONTENTFUL_PAINT_MS",
    "inp_ms": "INTERACTION_TO_NEXT_PAINT",
    "cls": "CUMULATIVE_LAYOUT_SHIFT_SCORE",
}

_LAB_AUDIT_KEYS = {
    "lcp_ms": "largest-contentful-paint",
    "inp_ms": "interaction-to-next-paint",
    "cls": "cumulative-layout-shift",
}


class PageSpeedError(RuntimeError):
    """PageSpeed Insights API 调用失败。"""


def is_configured() -> bool:
    return bool(settings.PAGESPEED_API_KEY)


def fetch_cwv(url: str, *, strategy: str | None = None) -> dict[str, Any]:
    """调用 PSI API，返回统一 cwv_estimate 结构。"""
    if not settings.PAGESPEED_API_KEY:
        raise PageSpeedError("未配置 PAGESPEED_API_KEY")

    strategy = (strategy or settings.PAGESPEED_STRATEGY or "mobile").lower()
    params: dict[str, str] = {
        "url": url,
        "strategy": strategy,
        "category": "performance",
        "key": settings.PAGESPEED_API_KEY,
    }

    try:
        with httpx.Client(timeout=float(settings.PAGESPEED_TIMEOUT)) as client:
            resp = client.get(PSI_ENDPOINT, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException as exc:
        raise PageSpeedError(f"PageSpeed API 超时（>{settings.PAGESPEED_TIMEOUT}s）") from exc
    except httpx.HTTPStatusError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("error", {}).get("message", "")
        except Exception:
            detail = exc.response.text[:200]
        raise PageSpeedError(f"PageSpeed API HTTP {exc.response.status_code}: {detail}") from exc
    except Exception as exc:
        raise PageSpeedError(str(exc)) from exc

    return _parse_psi_response(data, strategy=strategy)


def _parse_psi_response(data: dict[str, Any], *, strategy: str) -> dict[str, Any]:
    """解析 PSI 响应，优先 CrUX 字段数据，回退 Lighthouse 实验室数据。"""
    loading = data.get("loadingExperience") or {}
    origin_loading = data.get("originLoadingExperience") or {}
    lighthouse = data.get("lighthouseResult") or {}
    audits = lighthouse.get("audits") or {}
    categories = lighthouse.get("categories") or {}
    perf = categories.get("performance") or {}

    data_type = "lab"
    metrics_source = loading.get("metrics") or {}
    if not metrics_source:
        metrics_source = origin_loading.get("metrics") or {}
        if metrics_source:
            data_type = "field_origin"
    else:
        data_type = "field"

    lcp_ms, lcp_risk = _extract_metric(
        metrics_source, _FIELD_METRIC_KEYS["lcp_ms"], audits, _LAB_AUDIT_KEYS["lcp_ms"],
        kind="lcp",
    )
    inp_ms, inp_risk = _extract_metric(
        metrics_source, _FIELD_METRIC_KEYS["inp_ms"], audits, _LAB_AUDIT_KEYS["inp_ms"],
        kind="inp",
    )
    cls_score, cls_risk = _extract_metric(
        metrics_source, _FIELD_METRIC_KEYS["cls"], audits, _LAB_AUDIT_KEYS["cls"],
        kind="cls",
    )

    performance_score = perf.get("score")
    if performance_score is not None:
        performance_score = round(float(performance_score) * 100, 1)

    final_url = lighthouse.get("finalUrl") or data.get("id") or ""
    cwv_passed = all(r == "low" for r in (lcp_risk, cls_risk, inp_risk) if r)

    type_label = {
        "field": "CrUX 真实用户数据（页面级）",
        "field_origin": "CrUX 真实用户数据（整站）",
        "lab": "Lighthouse 实验室数据",
    }[data_type]

    return {
        "source": "psi",
        "data_type": data_type,
        "strategy": strategy,
        "lcp_ms": lcp_ms,
        "lcp_risk": lcp_risk,
        "cls": cls_score,
        "cls_risk": cls_risk,
        "inp_ms": inp_ms,
        "inp_risk": inp_risk,
        "performance_score": performance_score,
        "cwv_passed": cwv_passed,
        "final_url": final_url,
        "image_count": 0,
        "cover_image": False,
        "note": f"PageSpeed Insights 实测（{type_label}，{strategy}）",
    }


def _extract_metric(
    field_metrics: dict[str, Any],
    field_key: str,
    lab_audits: dict[str, Any],
    lab_key: str,
    *,
    kind: str,
) -> tuple[float | int, str]:
    """从字段数据或 Lighthouse 审计中提取指标值与风险等级。"""
    if field_key in field_metrics:
        raw = field_metrics[field_key].get("percentile")
        if raw is not None:
            if kind == "cls":
                value = round(float(raw) / 100, 3)
            else:
                value = int(raw)
            return value, _risk_for(kind, value)

    audit = lab_audits.get(lab_key) or {}
    numeric = audit.get("numericValue")
    if numeric is not None:
        if kind == "cls":
            value = round(float(numeric), 3)
        else:
            value = int(numeric)
        return value, _risk_for(kind, value)

    # INP 在部分 Lighthouse 版本不可用，用 TBT 粗估
    if kind == "inp":
        tbt = lab_audits.get("total-blocking-time") or {}
        tbt_val = tbt.get("numericValue")
        if tbt_val is not None:
            value = int(tbt_val)
            risk = "low" if value <= 200 else ("medium" if value <= 600 else "high")
            return value, risk

    return 0, "low"


def _risk_for(kind: str, value: float | int) -> str:
    if kind == "lcp":
        if value <= _LCP_GOOD_MS:
            return "low"
        if value <= _LCP_POOR_MS:
            return "medium"
        return "high"
    if kind == "cls":
        if value <= _CLS_GOOD:
            return "low"
        if value <= _CLS_POOR:
            return "medium"
        return "high"
    if kind == "inp":
        if value <= _INP_GOOD_MS:
            return "low"
        if value <= _INP_POOR_MS:
            return "medium"
        return "high"
    return "low"
