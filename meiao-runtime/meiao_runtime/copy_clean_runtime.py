from __future__ import annotations

import time
from typing import Any

from .route_helpers import append_debug_log as _append_debug_log
from .route_helpers import callable_or_raise as _callable


def submit(legacy_globals: dict[str, Any], items: list[Any]) -> dict[str, Any]:
    store = _callable(legacy_globals, "read_copy_clean_store")()
    submitted: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        item_id = str(raw_item.get("id") or "").strip()
        backend_media_id = str(raw_item.get("backendMediaId") or "").strip()
        source_url = _callable(legacy_globals, "build_copy_clean_source_url")(raw_item)
        if not item_id:
            failed.append({"error": "Missing media item id"})
            continue

        local_media_dir = legacy_globals["MEDIA_ROOT"] / backend_media_id if backend_media_id else None
        local_video_path = _callable(legacy_globals, "find_original_media_video")(local_media_dir) if local_media_dir else None
        duration_seconds = _callable(legacy_globals, "parse_duration_seconds")(str(raw_item.get("duration") or "0:00"))
        width = 720
        height = 1280
        file_size_mb = None
        if local_video_path:
            duration_seconds = int(_callable(legacy_globals, "probe_video_duration")(local_video_path) or duration_seconds or 0)
            resolution_info = _callable(legacy_globals, "probe_video_resolution")(local_video_path)
            if resolution_info:
                width, height = resolution_info
            file_size_mb = round(local_video_path.stat().st_size / (1024 * 1024), 2)
        else:
            file_size_mb = float(raw_item.get("fileSize") or 0) or None
            if not source_url or not source_url.startswith(("http://", "https://")):
                failed.append({"itemId": item_id, "error": "Missing accessible video URL"})
                continue

        if (not source_url or _callable(legacy_globals, "is_local_media_url")(source_url)) and local_video_path:
            upload_path = str(_callable(legacy_globals, "get_file_upload_config")()["upload_path"] or "copy-clean/videos").strip()
            upload_name = f"{_callable(legacy_globals, 'unique_file_token')('copy-clean-upload', backend_media_id or item_id)}{local_video_path.suffix.lower() or '.mp4'}"
            source_url = _callable(legacy_globals, "upload_file_to_kie")(local_video_path, upload_name, upload_path)
        if not source_url:
            failed.append({"itemId": item_id, "error": "Missing public video URL"})
            continue

        resolution = f"{width}x{height}"
        region = _callable(legacy_globals, "normalize_copy_clean_region")(raw_item.get("copyCleanRegion"), width, height)
        video_name = str(raw_item.get("videoName") or _callable(legacy_globals, "copy_clean_video_name")(region))
        request_body: dict[str, Any] = {
            "biz": "aiRemoveSubtitleSubmitTask",
            "fileSize": file_size_mb or 0,
            "duration": duration_seconds or 0,
            "resolution": resolution,
            "videoName": video_name,
            "coverUrl": str(raw_item.get("remotePosterUrl") or raw_item.get("posterUrl") or ""),
            "url": source_url,
        }
        notify_url = raw_item.get("notifyUrl")
        if isinstance(notify_url, str) and notify_url.strip():
            request_body["notifyUrl"] = notify_url.strip()

        try:
            api_result = _callable(legacy_globals, "call_copy_clean_api")(request_body)
            api_code = api_result.get("code")
            if int(api_code if api_code is not None else -1) != 0:
                failed.append({"itemId": item_id, "error": str(api_result.get("msg") or "Submit failed"), "code": api_result.get("code")})
                continue
            task_id = str((api_result.get("data") or {}).get("taskId") or "").strip()
            if not task_id:
                failed.append({"itemId": item_id, "error": "API did not return taskId"})
                continue
            task = {
                "itemId": item_id,
                "taskId": task_id,
                "status": "waiting",
                "emsg": "Submitted",
                "progress": _callable(legacy_globals, "copy_clean_progress_for_status")("waiting"),
                "stage": _callable(legacy_globals, "copy_clean_stage_for_status")("waiting"),
                "sourceUrl": source_url,
                "originalVideoUrl": str(source_url if _callable(legacy_globals, "is_local_media_url")(raw_item.get("originalVideoUrl")) else (raw_item.get("originalVideoUrl") or source_url)),
                "originalPosterUrl": str(raw_item.get("originalPosterUrl") or raw_item.get("remotePosterUrl") or raw_item.get("posterUrl") or ""),
                "backendMediaId": backend_media_id,
                "resolution": resolution,
                "duration": duration_seconds,
                "fileSize": file_size_mb,
                "videoName": video_name,
                "region": region,
                "resultUrl": "",
                "resultLocalUrl": "",
                "resultPosterUrl": "",
                "createdAt": int(time.time() * 1000),
                "updatedAt": int(time.time() * 1000),
            }
            _callable(legacy_globals, "save_copy_clean_task")(store, task)
            submitted.append(task)
        except Exception as error:
            failed.append({"itemId": item_id, "error": str(error)})

    _callable(legacy_globals, "write_copy_clean_store")(store)
    _append_debug_log(legacy_globals, "api.copy_clean.submit", {"submitted": len(submitted), "failed": len(failed)})
    return {"tasks": submitted, "failed": failed}


def detect_region(legacy_globals: dict[str, Any], items: list[Any]) -> dict[str, Any]:
    regions: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        item_id = str(raw_item.get("id") or "").strip()
        backend_media_id = str(raw_item.get("backendMediaId") or "").strip()
        if not backend_media_id:
            backend_media_id = (
                _callable(legacy_globals, "media_id_from_media_url")(raw_item.get("remoteVideoUrl"))
                or _callable(legacy_globals, "media_id_from_media_url")(raw_item.get("sourceVideoUrl"))
                or _callable(legacy_globals, "media_id_from_media_url")(raw_item.get("sourceUrl"))
            )
        if not item_id:
            failed.append({"error": "Missing media item id"})
            continue
        if not backend_media_id:
            failed.append({"itemId": item_id, "error": "Missing local media id"})
            continue

        media_dir = legacy_globals["MEDIA_ROOT"] / backend_media_id
        video_path = _callable(legacy_globals, "find_original_media_video")(media_dir)
        if not video_path:
            failed.append({"itemId": item_id, "error": "Original local video not found"})
            continue
        try:
            detected = _callable(legacy_globals, "detect_copy_clean_subtitle_region")(video_path)
            region = detected.get("region") if isinstance(detected, dict) else None
            if not isinstance(region, dict):
                failed.append({"itemId": item_id, "error": "Subtitle region not detected"})
                continue
            region["updatedAt"] = int(time.time() * 1000)
            regions.append(
                {
                    "itemId": item_id,
                    "backendMediaId": backend_media_id,
                    "hasSubtitle": bool(detected.get("hasSubtitle", True)),
                    "region": region,
                    "confidence": detected.get("confidence"),
                    "method": detected.get("method"),
                }
            )
        except Exception as error:
            failed.append({"itemId": item_id, "error": str(error)})

    _append_debug_log(legacy_globals, "api.copy_clean.detect_region", {"detected": len(regions), "failed": len(failed)})
    return {"regions": regions, "failed": failed}


def progress(legacy_globals: dict[str, Any], payload: dict[str, Any], task_ids: list[Any]) -> tuple[int, dict[str, Any]]:
    store = _callable(legacy_globals, "read_copy_clean_store")()
    tasks = store.setdefault("tasks", {})
    client_tasks = payload.get("tasks")
    if isinstance(client_tasks, list):
        for client_task in client_tasks:
            if not isinstance(client_task, dict):
                continue
            client_task_id = str(client_task.get("taskId") or "").strip()
            if not client_task_id:
                continue
            current_task = tasks.get(client_task_id) if isinstance(tasks.get(client_task_id), dict) else {}
            tasks[client_task_id] = {**client_task, **current_task}

    query_task_ids = ",".join(str(task_id) for task_id in task_ids if str(task_id).strip())
    if not query_task_ids:
        return 400, {"error": "Missing taskIds"}
    api_result = _callable(legacy_globals, "call_copy_clean_api")({"biz": "aiRemoveSubtitleProgress", "taskId": query_task_ids})
    api_code = api_result.get("code")
    if int(api_code if api_code is not None else -1) != 0:
        return 409, {"error": str(api_result.get("msg") or "Query failed"), "code": api_result.get("code")}

    remote_items = api_result.get("data") if isinstance(api_result, dict) else []
    if not isinstance(remote_items, list):
        remote_items = []
    updated: list[dict[str, Any]] = []

    for remote_item in remote_items:
        if not isinstance(remote_item, dict):
            continue
        task_id = str(remote_item.get("taskId") or "").strip()
        if not task_id:
            continue
        task = tasks.get(task_id) or {}
        status = str(remote_item.get("status") or task.get("status") or "waiting")
        result_url = str(remote_item.get("resultUrl") or "").strip()
        result_local_url = str(task.get("resultLocalUrl") or "").strip()
        result_poster_url = str(task.get("resultPosterUrl") or "").strip()
        backend_media_id = str(task.get("backendMediaId") or "").strip()
        if status == "success" and result_url and backend_media_id:
            media_dir = legacy_globals["MEDIA_ROOT"] / backend_media_id
            media_dir.mkdir(parents=True, exist_ok=True)
            clean_path = media_dir / f"copy-clean-{_callable(legacy_globals, 'sanitize_filename')(task_id)}.mp4"
            if not clean_path.exists() or clean_path.stat().st_size == 0:
                try:
                    _callable(legacy_globals, "download_remote_file")(result_url, clean_path)
                    result_local_url = _callable(legacy_globals, "get_public_media_url")(backend_media_id, clean_path.name)
                    poster_path = _callable(legacy_globals, "generate_poster")(clean_path, media_dir)
                    if poster_path:
                        result_poster_url = _callable(legacy_globals, "get_public_media_url")(backend_media_id, poster_path.name)
                except Exception as error:
                    remote_item["downloadError"] = str(error)
            else:
                result_local_url = _callable(legacy_globals, "get_public_media_url")(backend_media_id, clean_path.name)

        task = {
            **task,
            "taskId": task_id,
            "itemId": task.get("itemId") or remote_item.get("itemId"),
            "status": status,
            "emsg": str(remote_item.get("emsg") or remote_item.get("msg") or remote_item.get("message") or ""),
            "progress": _callable(legacy_globals, "copy_clean_progress_for_status")(status),
            "stage": _callable(legacy_globals, "copy_clean_stage_for_status")(status),
            "resultUrl": result_url,
            "resultLocalUrl": result_local_url,
            "resultPosterUrl": result_poster_url,
            "updateTime": remote_item.get("updateTime"),
            "raw": remote_item,
            "updatedAt": int(time.time() * 1000),
        }
        tasks[task_id] = task
        updated.append(task)

    _callable(legacy_globals, "write_copy_clean_store")(store)
    _append_debug_log(legacy_globals, "api.copy_clean.progress", {"count": len(updated)})
    return 200, {"tasks": updated}
