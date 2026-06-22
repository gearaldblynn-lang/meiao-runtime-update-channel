from __future__ import annotations

import os
import traceback
from pathlib import Path
from typing import Any

from .route_helpers import append_debug_log


def audio_cache_dir(legacy_globals: dict[str, Any]) -> Path:
    configured = legacy_globals.get("capcut_audio_cache_dir")
    if callable(configured):
        configured = configured()
    if configured:
        return Path(configured)
    return Path.home() / "AppData" / "Local" / "JianyingPro" / "User Data" / "Cache" / "music"


def _open_path(legacy_globals: dict[str, Any], path: Path) -> None:
    opener = legacy_globals.get("open_capcut_audio_cache_dir")
    if callable(opener):
        opener(path)
        return
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]


def open_cache(legacy_globals: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        cache_dir = audio_cache_dir(legacy_globals)
        cache_dir.mkdir(parents=True, exist_ok=True)
        _open_path(legacy_globals, cache_dir)
        return 200, {"opened": True, "path": str(cache_dir)}
    except Exception as exc:
        append_debug_log(
            legacy_globals,
            "api.capcut_mate.audio_cache.open.error",
            {"errorType": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc()},
        )
        return 500, {"error": f"打开音乐缓存目录失败：{exc}"}


def auto_download(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    try:
        body = payload if isinstance(payload, dict) else {}
        raw_categories = body.get("categories") if isinstance(body.get("categories"), list) else []
        categories = [str(item).strip() for item in raw_categories if str(item).strip()]
        start_download = legacy_globals.get("start_jianying_audio_cache_download")
        if not callable(start_download):
            return 500, {"error": "剪映音乐缓存下载器不可用。"}
        result = start_download(
            kind=str(body.get("kind") or "music"),
            categories=categories,
            per_category=int(body.get("perCategory") or 3),
            commercial_only=bool(body.get("commercialOnly", True)),
            dry_run=bool(body.get("dryRun", False)),
        )
        if not isinstance(result, dict):
            result = {"result": result}
        return (500 if result.get("error") else 200), result
    except Exception as exc:
        append_debug_log(
            legacy_globals,
            "api.capcut_mate.audio_cache.auto_download.error",
            {"errorType": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc()},
        )
        return 500, {"error": f"启动自动缓存下载失败：{exc}"}
