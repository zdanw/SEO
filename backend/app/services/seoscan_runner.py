"""通过 seoscan CLI 对 URL 执行技术 SEO 审计。"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

_CATEGORY_LABELS: dict[str, str] = {
    "meta": "Meta / 页面标签",
    "performance": "性能",
    "links": "链接",
    "images": "图片",
    "headers": "安全头 / 缓存",
    "sitemap": "Sitemap",
    "robots": "Robots.txt",
    "structured": "结构化数据",
    "content": "内容质量",
}


class SeoscanError(RuntimeError):
    """seoscan 执行失败。"""


def audit_url(url: str) -> dict[str, Any]:
    """对单个 URL 运行 seoscan，返回解析后的 JSON 报告。"""
    raw = _run_seoscan(url)
    data = _parse_stdout_json(raw)
    return _normalize_report(data)


def _run_seoscan(url: str) -> str:
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        raise SeoscanError("未找到 npx，请安装 Node.js 18+")

    cmd = [npx, "seoscan", url, "--export", "json"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=settings.SEOSCAN_TIMEOUT,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SeoscanError(f"seoscan 超时（>{settings.SEOSCAN_TIMEOUT}s）") from exc

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    if proc.returncode != 0 and not stdout.strip():
        raise SeoscanError(f"seoscan 退出码 {proc.returncode}: {stderr[:500]}")

    if "JSON output printed" not in stdout and '"overall"' not in stdout:
        logger.warning("seoscan stderr: %s", stderr[:300])
    return stdout


def _parse_stdout_json(stdout: str) -> dict[str, Any]:
    """从 seoscan 混合输出中提取 JSON 对象。"""
    marker = stdout.find('{\n  "url"')
    if marker == -1:
        marker = stdout.find('{"url"')
    if marker == -1:
        # 尝试从最后一个 { 开始匹配
        marker = stdout.rfind("{")
    if marker == -1:
        raise SeoscanError("seoscan 输出中未找到 JSON")

    fragment = stdout[marker:]
    # 截断 JSON 后的提示行
    end = fragment.rfind("}")
    if end == -1:
        raise SeoscanError("seoscan JSON 不完整")
    try:
        return json.loads(fragment[: end + 1])
    except json.JSONDecodeError as exc:
        raise SeoscanError(f"seoscan JSON 解析失败: {exc}") from exc


def _normalize_report(data: dict[str, Any]) -> dict[str, Any]:
    """整理为平台统一结构。"""
    overall = data.get("overall") or {}
    score = float(overall.get("score", 0))
    grade = overall.get("grade", "")
    breakdown = overall.get("breakdown") or {}
    categories: list[dict[str, Any]] = []
    top_issues: list[str] = []

    for key, block in breakdown.items():
        if not isinstance(block, dict):
            continue
        cat_score = block.get("score", 0)
        label = _CATEGORY_LABELS.get(key, key)
        categories.append({
            "key": key,
            "name": label,
            "score": cat_score,
            "weight": block.get("weight"),
            "weighted": block.get("weighted"),
        })

    results = data.get("results") or {}
    for cat_key, cat_data in results.items():
        if not isinstance(cat_data, dict):
            continue
        for check in cat_data.get("checks") or []:
            if check.get("status") in ("fail", "error"):
                name = check.get("name", "")
                top_issues.append(f"[{_CATEGORY_LABELS.get(cat_key, cat_key)}] {name}")

    meta_block = (results.get("meta") or {}).get("meta") or {}
    title = meta_block.get("title") or data.get("url", "")

    return {
        "engine": "seoscan",
        "url": data.get("url", ""),
        "score": score,
        "grade": grade,
        "title": title,
        "categories": categories,
        "top_issues": top_issues[:15],
        "raw": data,
    }


def seoscan_to_check_items(report: dict[str, Any]) -> list[dict[str, Any]]:
    """将 seoscan 分类转为 SeoCheckItem 兼容格式（供 URL 检查 UI）。"""
    items: list[dict[str, Any]] = []
    for cat in report.get("categories") or []:
        score = float(cat.get("score", 0))
        status = "pass" if score >= 80 else ("warning" if score >= 60 else "fail")
        items.append({
            "key": f"seoscan_{cat['key']}",
            "name": cat["name"],
            "score": score,
            "max_score": 100.0,
            "status": status,
            "message": f"seoscan 评分 {score:.0f}/100",
            "suggestion": "",
        })
    return items
