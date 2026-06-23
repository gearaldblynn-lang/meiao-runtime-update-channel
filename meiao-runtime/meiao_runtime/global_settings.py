from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _callable(legacy_globals: dict[str, Any], name: str) -> Any:
    value = legacy_globals.get(name)
    if not callable(value):
        raise RuntimeError(f"Legacy callable {name} is unavailable.")
    return value


def get_bundle(legacy_globals: dict[str, Any], light: bool = False) -> dict[str, Any]:
    if light:
        return _callable(legacy_globals, "get_global_settings_light_bundle")()
    return _callable(legacy_globals, "get_global_settings_bundle")()


def save_bundle(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    if not isinstance(payload, dict):
        return 400, {"error": "请求体格式错误"}
    return 200, _callable(legacy_globals, "save_global_settings_bundle")(payload)


def backup(legacy_globals: dict[str, Any]) -> dict[str, Any]:
    return _callable(legacy_globals, "create_settings_backup")()


def restore(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    snapshot = payload.get("snapshot") if isinstance(payload, dict) else None
    if not isinstance(snapshot, dict):
        return 400, {"error": "缺少 snapshot"}
    return 200, _callable(legacy_globals, "restore_settings_backup")(snapshot)


def check_export_folder(legacy_globals: dict[str, Any], state_store: Any, payload: Any) -> tuple[int, dict[str, Any]]:
    folder_path: Path | None = None
    raw_path = str(payload.get("path") or "").strip() if isinstance(payload, dict) else ""
    project_id = str(payload.get("projectId") or "").strip() if isinstance(payload, dict) else ""
    if not raw_path:
        return 400, {"error": "缺少导出文件夹路径。"}

    state = state_store.read_client_state()
    export_folders = state.get("meiao-export-folders") if isinstance(state.get("meiao-export-folders"), dict) else {}
    saved_paths = [str(value or "").strip() for value in export_folders.values() if str(value or "").strip()]
    if project_id:
        expected_path = str(export_folders.get(project_id) or "").strip()
        if not expected_path:
            return 403, {"error": "当前项目没有已登记的导出文件夹，请重新选择导出目录。"}
        saved_paths = [expected_path]

    def same_folder(left: str, right: str) -> bool:
        try:
            return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))
        except Exception:
            return left.strip().rstrip("\\/") == right.strip().rstrip("\\/")

    if not any(same_folder(raw_path, saved_path) for saved_path in saved_paths):
        return 403, {"error": "导出文件夹未在当前工作台配置中登记，请重新选择导出目录。"}

    try:
        folder_path = Path(raw_path)
        _callable(legacy_globals, "assert_writable_directory")(folder_path, "导出文件夹")
        return 200, {
            "ok": True,
            "path": str(folder_path),
            "name": folder_path.name,
            "writable": True,
            "networkPath": _callable(legacy_globals, "path_is_network_location")(folder_path),
        }
    except Exception as exc:
        return 400, {
            "error": _callable(legacy_globals, "describe_file_operation_error")("检查导出文件夹", folder_path or Path("."), exc),
            "path": str(folder_path or ""),
        }
