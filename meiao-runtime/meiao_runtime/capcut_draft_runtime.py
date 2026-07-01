from __future__ import annotations

import time
import traceback
import json
import shutil
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from .route_helpers import append_debug_log


def create_adapter(legacy_globals: dict[str, Any]) -> Any:
    factory = legacy_globals.get("create_capcut_mate_adapter")
    if callable(factory):
        return factory()
    try:
        from integrations.capcut_mate.adapter import CapCutMateAdapter
    except Exception as exc:
        append_debug_log(legacy_globals, "api.capcut_mate.import.error", {"errorType": type(exc).__name__, "error": str(exc)})
        raise RuntimeError("剪映小助手适配器缺失，请检查 integrations/capcut_mate。") from exc
    return CapCutMateAdapter()


def _draft_write_gate(legacy_globals: dict[str, Any]) -> Any:
    semaphore = legacy_globals.get("CAPCUT_DRAFT_WRITE_SEMAPHORE")
    if hasattr(semaphore, "__enter__") and hasattr(semaphore, "__exit__"):
        return semaphore
    lock = legacy_globals.get("CAPCUT_DRAFT_WRITE_LOCK")
    return lock if hasattr(lock, "__enter__") and hasattr(lock, "__exit__") else nullcontext()


def _startup_delay_seconds(legacy_globals: dict[str, Any]) -> float:
    value = legacy_globals.get("capcut_export_startup_delay_seconds")
    if callable(value):
        try:
            return max(0.0, float(value()))
        except (TypeError, ValueError):
            return 3.0
    return 3.0


def _open_startup_delay_seconds(legacy_globals: dict[str, Any]) -> float:
    value = legacy_globals.get("capcut_open_startup_delay_seconds")
    if callable(value):
        try:
            return max(0.0, float(value()))
        except (TypeError, ValueError):
            return 3.0
    return 3.0


def _callable_helper(legacy_globals: dict[str, Any], name: str) -> Any:
    value = legacy_globals.get(name)
    if callable(value):
        return value
    try:
        from integrations.capcut_mate import adapter as capcut_adapter
    except Exception as exc:
        raise RuntimeError("剪映小助手适配器缺失，请检查 integrations/capcut_mate。") from exc
    value = getattr(capcut_adapter, name, None)
    if callable(value):
        return value
    raise RuntimeError(f"CapCut helper {name} is unavailable.")


def generate_draft(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    try:
        if not isinstance(payload, dict):
            return 400, {"error": "请求体格式错误"}
        plan = payload.get("plan")
        if not isinstance(plan, dict):
            return 400, {"error": "缺少草稿计划"}
        width = int(payload.get("width") or 1080)
        height = int(payload.get("height") or 1920)
        if width <= 0 or height <= 0:
            return 400, {"error": "画布尺寸无效"}

        try:
            adapter = create_adapter(legacy_globals)
        except RuntimeError as exc:
            return 500, {"error": str(exc)}

        with _draft_write_gate(legacy_globals):
            result = adapter.generate_draft_from_plan(plan, width=width, height=height)
        draft_url = str(result.get("draft_url") or "").strip() if isinstance(result, dict) else ""
        if not draft_url:
            return 500, {"error": "剪映小助手未返回草稿地址。"}

        add_videos_result = result.get("add_videos_result") or {}
        append_debug_log(
            legacy_globals,
            "api.capcut_mate.generate_draft.success",
            {
                "planId": plan.get("id"),
                "clipCount": result.get("clip_count"),
                "draftUrl": draft_url,
                "voiceDurationResult": result.get("voice_duration_result"),
                "timingResult": result.get("timing_result"),
                "stageTimingsMs": result.get("stage_timings_ms"),
                "addVideosResult": {
                    "total_duration": add_videos_result.get("total_duration"),
                    "source_duration_total": add_videos_result.get("source_duration_total"),
                    "segment_count": len(add_videos_result.get("segment_ids") or []),
                },
            },
        )
        return 200, {
            "draftUrl": draft_url,
            "clipCount": int(result.get("clip_count") or 0),
            "timingResult": result.get("timing_result"),
            "voiceDurationResult": result.get("voice_duration_result"),
            "stageTimingsMs": result.get("stage_timings_ms"),
            "createResult": result.get("create_result"),
            "addVideosResult": result.get("add_videos_result"),
            "addAudiosResult": result.get("add_audios_result"),
            "addCaptionsResult": result.get("add_captions_result"),
            "addFiltersResult": result.get("add_filters_result"),
            "addEffectsResult": result.get("add_effects_result"),
            "addStickersResult": result.get("add_stickers_result"),
            "addDedupeVideoResult": result.get("add_dedupe_video_result"),
            "addMasksResult": result.get("add_masks_result"),
            "addKeyframesResult": result.get("add_keyframes_result"),
            "saveResult": result.get("save_result"),
            "importResult": result.get("import_result"),
            "recognizeSubtitlesResult": result.get("recognize_subtitles_result"),
        }
    except Exception as exc:
        error_text = str(exc)
        append_debug_log(
            legacy_globals,
            "api.capcut_mate.generate_draft.error",
            {"errorType": type(exc).__name__, "error": error_text, "traceback": traceback.format_exc()},
        )
        status = 409 if "capcut-mate service unavailable" in error_text or "Connection refused" in error_text else 500
        hint = "请先启动剪映小助手服务 http://127.0.0.1:30000。" if status == 409 else ""
        return status, {"error": f"剪映草稿写入失败：{error_text}{('。' + hint) if hint else ''}"}


def open_draft(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    try:
        if not isinstance(payload, dict):
            return 400, {"error": "请求体格式错误"}
        draft_path_text = str(payload.get("draftPath") or "").strip()
        if not draft_path_text:
            return 400, {"error": "缺少草稿路径"}
        draft_path = Path(draft_path_text)
        if not draft_path.exists():
            capcut_draft_url = str(payload.get("capcutDraftUrl") or "").strip()
            if not capcut_draft_url:
                return 404, {"error": "草稿目录不存在"}
            try:
                adapter = create_adapter(legacy_globals)
                import_result = adapter.import_draft_to_jianying(capcut_draft_url, target_draft_id=draft_path.name)
                restored_path = Path(str(import_result.get("target_dir") or draft_path))
                if restored_path.exists():
                    draft_path = restored_path
                elif not draft_path.exists():
                    return 404, {"error": "草稿目录不存在，且自动恢复失败。"}
                append_debug_log(
                    legacy_globals,
                    "api.capcut_mate.open_draft.restored",
                    {"draftPath": draft_path_text, "capcutDraftUrl": capcut_draft_url, "restoredPath": str(draft_path)},
                )
            except Exception as restore_exc:
                return 404, {"error": f"草稿目录不存在，自动恢复失败：{restore_exc}"}
        launch_fixed_jianying = legacy_globals.get("launch_fixed_jianying")
        if not callable(launch_fixed_jianying):
            return 500, {"error": "剪映启动器不可用。"}
        launch_result = launch_fixed_jianying()
        capcut_path = str(launch_result.get("path") or "").strip() if isinstance(launch_result, dict) else ""
        if isinstance(launch_result, dict) and launch_result.get("opened") and capcut_path:
            read_draft_name = legacy_globals.get("read_jianying_draft_name")
            activate_draft = legacy_globals.get("try_activate_jianying_draft")
            if not callable(read_draft_name) or not callable(activate_draft):
                return 500, {"error": "剪映草稿激活器不可用。"}
            draft_name = read_draft_name(draft_path)
            opened, automation_error = activate_draft(draft_name)
            append_debug_log(
                legacy_globals,
                "api.capcut_mate.open_draft",
                {
                    "draftPath": str(draft_path),
                    "draftName": draft_name,
                    "capcutPath": capcut_path,
                    "opened": opened,
                    "automationError": automation_error,
                },
            )
            if opened:
                return 200, {
                    "opened": True,
                    "draftPath": str(draft_path),
                    "draftName": draft_name,
                    "capcutPath": capcut_path,
                }
            return 409, {
                "error": f"剪映已启动，但没有自动进入草稿《{draft_name}》：{automation_error or '未找到草稿卡片'}",
                "draftPath": str(draft_path),
                "draftName": draft_name,
                "capcutPath": capcut_path,
            }
        return 409, {
            "error": (launch_result or {}).get("error") if isinstance(launch_result, dict) else "未找到固定剪映版本",
            "draftPath": str(draft_path),
            "capcutPath": "",
            "capcut": (launch_result or {}).get("capcut") if isinstance(launch_result, dict) else None,
        }
    except Exception as exc:
        append_debug_log(
            legacy_globals,
            "api.capcut_mate.open_draft.error",
            {"errorType": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc()},
        )
        return 500, {"error": f"打开剪映失败：{exc}"}


def delete_draft(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    try:
        if not isinstance(payload, dict):
            return 400, {"error": "请求体格式错误"}
        draft_path_raw = str(payload.get("draftPath") or "").strip()
        draft_id_from_url = _callable_helper(legacy_globals, "draft_id_from_url")
        capcut_mate_output_draft_dir = _callable_helper(legacy_globals, "capcut_mate_output_draft_dir")
        jianying_draft_root = _callable_helper(legacy_globals, "jianying_draft_root")
        jianying_metadata_root = _callable_helper(legacy_globals, "jianying_metadata_root")

        draft_path = None
        if draft_path_raw and not draft_path_raw.startswith("http://") and not draft_path_raw.startswith("https://"):
            draft_path = Path(draft_path_raw).expanduser()

        draft_ids: list[str] = []
        if draft_path and draft_path.name:
            draft_ids.append(draft_path.name)
        parsed_draft_id = draft_id_from_url(str(payload.get("capcutDraftUrl") or "")) if payload.get("capcutDraftUrl") else ""
        if parsed_draft_id:
            draft_ids.append(parsed_draft_id)
        draft_ids = list(dict.fromkeys([item for item in draft_ids if item]))

        jianying_root = Path(jianying_draft_root()).resolve()
        output_roots = [Path(capcut_mate_output_draft_dir(item)).parent.resolve() for item in draft_ids]
        allowed_roots = [jianying_root, *output_roots]
        delete_targets: list[Path] = []

        if draft_path:
            if draft_path.exists():
                if not draft_path.is_dir():
                    return 400, {"error": "草稿路径不是目录"}
                resolved = draft_path.resolve()
                if not any(resolved == root or root in resolved.parents for root in allowed_roots):
                    return 400, {"error": "草稿路径不在允许删除的剪映草稿目录内"}
                delete_targets.append(resolved)
            else:
                append_debug_log(
                    legacy_globals,
                    "api.capcut_mate.delete_draft.missing_primary",
                    {"draftPath": str(draft_path)},
                )

        for draft_id in draft_ids:
            source_dir = Path(capcut_mate_output_draft_dir(draft_id))
            if source_dir.exists() and source_dir.is_dir():
                resolved_source = source_dir.resolve()
                source_root = source_dir.parent.resolve()
                if resolved_source != source_root and source_root in resolved_source.parents:
                    delete_targets.append(resolved_source)

        unique_targets: list[Path] = []
        seen_targets: set[str] = set()
        for target in delete_targets:
            key = str(target).lower()
            if key not in seen_targets:
                seen_targets.add(key)
                unique_targets.append(target)

        if not unique_targets:
            return 200, {
                "deleted": False,
                "draftPath": str(draft_path) if draft_path else "",
                "message": "草稿目录不存在，记录可直接删除。",
            }

        root_meta_path = Path(jianying_metadata_root()) / "root_meta_info.json"
        if root_meta_path.exists() and draft_path:
            try:
                root_meta = json.loads(root_meta_path.read_text(encoding="utf-8"))
                store = root_meta.get("all_draft_store")
                if isinstance(store, list):
                    draft_path_key = str(draft_path.resolve()).replace("\\", "/") if draft_path.exists() else str(draft_path).replace("\\", "/")
                    draft_name = draft_path.resolve().name if draft_path.exists() else draft_path.name
                    root_meta["all_draft_store"] = [
                        item for item in store
                        if not (
                            isinstance(item, dict)
                            and (
                                str(item.get("draft_fold_path") or "").replace("\\", "/") == draft_path_key
                                or str(item.get("draft_name") or "") == draft_name
                            )
                        )
                    ]
                    root_meta["draft_ids"] = len(root_meta["all_draft_store"])
                    root_meta_path.write_text(json.dumps(root_meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            except Exception as meta_exc:
                append_debug_log(
                    legacy_globals,
                    "api.capcut_mate.delete_draft.metadata_warning",
                    {"error": str(meta_exc), "draftPath": str(draft_path)},
                )

        deleted_paths: list[str] = []
        for target in unique_targets:
            shutil.rmtree(target, ignore_errors=False)
            deleted_paths.append(str(target))
        append_debug_log(legacy_globals, "api.capcut_mate.delete_draft", {"deletedPaths": deleted_paths, "draftIds": draft_ids})
        return 200, {"deleted": True, "draftPath": str(draft_path) if draft_path else "", "deletedPaths": deleted_paths}
    except Exception as exc:
        append_debug_log(
            legacy_globals,
            "api.capcut_mate.delete_draft.error",
            {"errorType": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc()},
        )
        return 500, {"error": f"删除剪映草稿失败：{exc}"}


def archive_exported_video(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    try:
        if not isinstance(payload, dict):
            return 400, {"error": "请求体格式错误"}
        video_url = str(payload.get("videoUrl") or "").strip()
        output_dir_text = str(payload.get("outputDir") or "").strip()
        project_name = str(payload.get("projectName") or "").strip()
        if not output_dir_text:
            return 400, {"error": "缺少导出文件夹"}

        assert_writable_directory = legacy_globals.get("assert_writable_directory")
        describe_file_operation_error = legacy_globals.get("describe_file_operation_error")
        resolve_capcut_export_source = legacy_globals.get("resolve_capcut_export_source")
        next_export_target_path = legacy_globals.get("next_export_target_path")
        if not all(callable(item) for item in [assert_writable_directory, describe_file_operation_error, resolve_capcut_export_source, next_export_target_path]):
            return 500, {"error": "导出归档依赖不可用。"}

        output_dir = Path(output_dir_text).expanduser().resolve()
        try:
            assert_writable_directory(output_dir, "导出文件夹")
        except Exception as exc:
            return 400, {"error": describe_file_operation_error("检查导出文件夹", output_dir, exc), "path": str(output_dir)}

        try:
            source = Path(resolve_capcut_export_source(video_url))
        except Exception as exc:
            return 400, {
                "error": describe_file_operation_error("检查导出视频", Path(video_url or "."), exc),
                "videoUrl": video_url,
            }

        target, index = next_export_target_path(output_dir, project_name)
        target = Path(target)
        try:
            shutil.move(str(source), str(target))
        except Exception as exc:
            return 500, {
                "error": describe_file_operation_error("移动导出视频", target, exc),
                "sourcePath": str(source),
                "targetPath": str(target),
            }
        append_debug_log(
            legacy_globals,
            "api.capcut_mate.archive_exported_video",
            {"source": str(source), "target": str(target), "index": index, "projectName": project_name},
        )
        return 200, {
            "moved": True,
            "sourcePath": str(source),
            "localPath": str(target),
            "fileName": target.name,
            "index": index,
        }
    except Exception as exc:
        append_debug_log(
            legacy_globals,
            "api.capcut_mate.archive_exported_video.error",
            {"errorType": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc()},
        )
        return 500, {"error": f"移动导出视频失败：{exc}"}


def export_status(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    try:
        if not isinstance(payload, dict):
            return 400, {"error": "请求体格式错误"}
        draft_url = str(payload.get("draftUrl") or "").strip()
        if not draft_url:
            return 400, {"error": "缺少草稿 URL"}
        try:
            adapter = create_adapter(legacy_globals)
        except RuntimeError as exc:
            return 500, {"error": str(exc)}

        result = adapter.query_video_status(draft_url)
        append_debug_log(
            legacy_globals,
            "api.capcut_mate.export_status",
            {"draftUrl": draft_url, "result": result},
        )
        return 200, result if isinstance(result, dict) else {"result": result}
    except Exception as exc:
        append_debug_log(
            legacy_globals,
            "api.capcut_mate.export_status.error",
            {"errorType": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc()},
        )
        return 500, {"error": f"查询导出状态失败：{exc}"}


def export_video(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    try:
        if not isinstance(payload, dict):
            return 400, {"error": "请求体格式错误"}
        draft_url = str(payload.get("draftUrl") or "").strip()
        if not draft_url:
            return 400, {"error": "缺少草稿 URL"}
        try:
            adapter = create_adapter(legacy_globals)
        except RuntimeError as exc:
            return 500, {"error": str(exc)}

        launch_fixed_jianying = legacy_globals.get("launch_fixed_jianying")
        if not callable(launch_fixed_jianying):
            return 500, {"error": "剪映启动器不可用。"}
        launch_result = launch_fixed_jianying()
        capcut_path = str(launch_result.get("path") or "").strip() if isinstance(launch_result, dict) else ""
        if not isinstance(launch_result, dict) or not launch_result.get("opened") or not capcut_path:
            return 409, {
                "error": (launch_result or {}).get("error") or "未找到固定剪映版本" if isinstance(launch_result, dict) else "未找到固定剪映版本",
                "capcut": (launch_result or {}).get("capcut") if isinstance(launch_result, dict) else None,
            }
        delay = _startup_delay_seconds(legacy_globals)
        if delay > 0:
            time.sleep(delay)
        recognize_result = None
        if should_recognize_subtitles_before_export(payload):
            recognize_result = adapter.recognize_subtitles(draft_url)
        result = adapter.generate_video(draft_url, str(payload.get("apiKey") or "").strip() or None)
        if isinstance(result, dict) and recognize_result is not None:
            result = {**result, "recognizeSubtitlesResult": recognize_result}
        append_debug_log(
            legacy_globals,
            "api.capcut_mate.export_video",
            {
                "draftUrl": draft_url,
                "capcutPath": capcut_path,
                "stoppedOtherVersions": launch_result.get("stoppedOtherVersions", []),
                "recognizeSubtitlesResult": recognize_result,
                "result": result,
            },
        )
        return 200, result if isinstance(result, dict) else {"result": result}
    except Exception as exc:
        append_debug_log(
            legacy_globals,
            "api.capcut_mate.export_video.error",
            {"errorType": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc()},
        )
        return 500, {"error": f"提交导出失败：{exc}"}
def should_recognize_subtitles_before_export(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    return str(payload.get("subtitlePreset") or "").strip() == "jianying_recognize"

