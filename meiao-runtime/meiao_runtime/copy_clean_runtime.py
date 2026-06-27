from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .route_helpers import append_debug_log as _append_debug_log
from .route_helpers import callable_or_raise as _callable


COPY_CLEAN_MAX_DIRECT_DURATION_SECONDS = 180
COPY_CLEAN_DETECT_MAX_CONCURRENCY = 4


def _item_local_media_path(legacy_globals: dict[str, Any], raw_item: dict[str, Any]) -> Any:
    resolver = legacy_globals.get("media_url_to_file_path")
    if not callable(resolver):
        return None
    for field in ("sourceVideoUrl", "remoteVideoUrl", "sourceUrl", "originalVideoUrl"):
        media_path = resolver(raw_item.get(field))
        if media_path:
            return media_path
    return None


def _uncertain_no_subtitle_detection(detected: dict[str, Any]) -> tuple[bool, str]:
    method = str(detected.get("method") or "").strip()
    return detected.get("hasSubtitle") is False and method != "confirmed-no-subtitle", method


def _detect_region_concurrency(item_count: int) -> int:
    if item_count <= 1:
        return 1
    raw_override = str(os.environ.get("MEIAO_COPY_CLEAN_DETECT_CONCURRENCY") or "").strip()
    if raw_override:
        try:
            override = int(raw_override)
        except ValueError:
            override = 1
        return max(1, min(COPY_CLEAN_DETECT_MAX_CONCURRENCY, item_count, override))
    cpu_count = os.cpu_count() or 2
    if cpu_count < 4:
        return 1
    return min(2, item_count)


def _client_state_helpers(legacy_globals: dict[str, Any]) -> tuple[Any, Any]:
    read_state = legacy_globals.get("read_client_state")
    write_state = legacy_globals.get("write_client_state")
    return read_state if callable(read_state) else None, write_state if callable(write_state) else None


def _stable_script_id(legacy_globals: dict[str, Any], item_id: str) -> str:
    helper = legacy_globals.get("stable_script_id_from_ingest_id")
    if callable(helper):
        return str(helper(item_id))
    normalized = "".join(char for char in str(item_id or "").replace("IN-", "") if char.isalnum())
    return f"S-{normalized or item_id}"


def _persist_auto_split_record(
    legacy_globals: dict[str, Any],
    raw_item: dict[str, Any],
    backend_media_id: str,
    split_result: dict[str, Any],
) -> str:
    read_state, write_state = _client_state_helpers(legacy_globals)
    if not read_state or not write_state:
        return ""

    item_id = str(raw_item.get("id") or "").strip()
    script_id = _stable_script_id(legacy_globals, item_id)
    now_ms = int(time.time() * 1000)
    segments: list[dict[str, Any]] = []
    for segment in split_result.get("segments") if isinstance(split_result.get("segments"), list) else []:
        if not isinstance(segment, dict):
            continue
        segments.append(
            {
                **segment,
                "mediaId": backend_media_id,
                "splitRunId": segment.get("splitRunId") or split_result.get("splitRunId"),
                "copyCleanStatus": "waiting",
                "copyCleanParentItemId": item_id,
            }
        )

    state = read_state()
    state = dict(state) if isinstance(state, dict) else {}
    records = state.get("meiao-scene-split-records") if isinstance(state.get("meiao-scene-split-records"), dict) else {}
    records = dict(records)
    records[script_id] = {
        "status": "done",
        "segments": len(segments),
        "mediaId": backend_media_id,
        "scriptId": script_id,
        "ingestId": item_id,
        "source": str(raw_item.get("title") or raw_item.get("source") or backend_media_id),
        "splitRunId": split_result.get("splitRunId"),
        "sceneSegments": segments,
        "updatedAt": now_ms,
    }
    state["meiao-scene-split-records"] = records
    write_state(state)
    return script_id


def _build_auto_split_items(
    legacy_globals: dict[str, Any],
    raw_item: dict[str, Any],
    backend_media_id: str,
    duration_seconds: int,
) -> list[dict[str, Any]]:
    threshold = float(raw_item.get("sceneSplitThreshold") or raw_item.get("threshold") or 0.3)
    min_scene_seconds = float(raw_item.get("minSceneSeconds") or 1.2)
    split_result = _callable(legacy_globals, "build_scene_segments_for_media")(backend_media_id, threshold, min_scene_seconds)
    script_id = _persist_auto_split_record(legacy_globals, raw_item, backend_media_id, split_result)

    parent_item_id = str(raw_item.get("id") or "").strip()
    segments = split_result.get("segments") if isinstance(split_result.get("segments"), list) else []
    segment_items: list[dict[str, Any]] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        segment_id = str(segment.get("segmentId") or f"segment-{segment.get('index') or len(segment_items) + 1}").strip()
        segment_url = str(segment.get("url") or "").strip()
        if not segment_id or not segment_url:
            continue
        segment_items.append(
            {
                **raw_item,
                "id": f"{parent_item_id}::scene::{segment_id}",
                "duration": str(segment.get("duration") or ""),
                "sourceVideoUrl": segment_url,
                "remoteVideoUrl": segment_url,
                "remotePosterUrl": segment.get("posterUrl") or raw_item.get("remotePosterUrl"),
                "originalVideoUrl": segment_url,
                "originalPosterUrl": segment.get("posterUrl") or raw_item.get("originalPosterUrl") or raw_item.get("remotePosterUrl"),
                "backendMediaId": backend_media_id,
                "copyCleanSegment": {
                    "parentItemId": parent_item_id,
                    "scriptId": script_id,
                    "segmentId": segment_id,
                    "segmentIndex": segment.get("index"),
                    "splitRunId": segment.get("splitRunId") or split_result.get("splitRunId"),
                    "originalUrl": segment_url,
                    "originalPosterUrl": segment.get("posterUrl"),
                    "parentDuration": duration_seconds,
                },
            }
        )
    return segment_items


def _update_scene_segment_from_copy_clean_task(legacy_globals: dict[str, Any], task: dict[str, Any]) -> None:
    segment_meta = task.get("copyCleanSegment")
    if not isinstance(segment_meta, dict):
        return
    read_state, write_state = _client_state_helpers(legacy_globals)
    if not read_state or not write_state:
        return

    script_id = str(segment_meta.get("scriptId") or "").strip()
    segment_id = str(segment_meta.get("segmentId") or "").strip()
    segment_index = segment_meta.get("segmentIndex")
    state = read_state()
    if not isinstance(state, dict):
        return
    state = dict(state)
    records = state.get("meiao-scene-split-records") if isinstance(state.get("meiao-scene-split-records"), dict) else {}
    records = dict(records)
    state["meiao-scene-split-records"] = records
    candidate_records = [records.get(script_id)] if script_id and isinstance(records.get(script_id), dict) else records.values()
    changed = False
    for record in candidate_records:
        if not isinstance(record, dict):
            continue
        segments = record.get("sceneSegments") if isinstance(record.get("sceneSegments"), list) else []
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            matches_segment_id = segment_id and str(segment.get("segmentId") or "").strip() == segment_id
            matches_index = segment_index is not None and str(segment.get("index") or "") == str(segment_index)
            if not matches_segment_id and not matches_index:
                continue
            if not segment.get("originalUrl"):
                segment["originalUrl"] = segment.get("url") or segment_meta.get("originalUrl")
            if not segment.get("originalPosterUrl"):
                segment["originalPosterUrl"] = segment.get("posterUrl") or segment_meta.get("originalPosterUrl")
            segment["copyCleanStatus"] = task.get("status")
            segment["copyCleanTaskId"] = task.get("taskId")
            segment["copyCleanItemId"] = task.get("itemId")
            if task.get("status") == "success" and task.get("resultLocalUrl"):
                segment["url"] = task.get("resultLocalUrl")
                if task.get("resultPosterUrl"):
                    segment["posterUrl"] = task.get("resultPosterUrl")
            if task.get("status") == "failed":
                segment["copyCleanError"] = task.get("emsg") or task.get("stage") or "Copy-clean failed"
            segment["updatedAt"] = int(time.time() * 1000)
            record["updatedAt"] = segment["updatedAt"]
            changed = True
            break
    if changed:
        write_state(state)


def submit(legacy_globals: dict[str, Any], items: list[Any]) -> dict[str, Any]:
    store = _callable(legacy_globals, "read_copy_clean_store")()
    submitted: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    pending_items = [item for item in items if isinstance(item, dict)]
    index = 0
    while index < len(pending_items):
        raw_item = pending_items[index]
        index += 1
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
        source_local_path = None
        if source_url and _callable(legacy_globals, "is_local_media_url")(source_url):
            source_local_path = _callable(legacy_globals, "media_url_to_file_path")(source_url)
        upload_video_path = source_local_path or local_video_path
        duration_seconds = _callable(legacy_globals, "parse_duration_seconds")(str(raw_item.get("duration") or "0:00"))
        width = 720
        height = 1280
        file_size_mb = None
        if upload_video_path:
            duration_seconds = int(_callable(legacy_globals, "probe_video_duration")(upload_video_path) or duration_seconds or 0)
            resolution_info = _callable(legacy_globals, "probe_video_resolution")(upload_video_path)
            if resolution_info:
                width, height = resolution_info
            file_size_mb = round(upload_video_path.stat().st_size / (1024 * 1024), 2)
        else:
            file_size_mb = float(raw_item.get("fileSize") or 0) or None
            if not source_url or not source_url.startswith(("http://", "https://")):
                failed.append({"itemId": item_id, "error": "Missing accessible video URL"})
                continue

        if (
            not isinstance(raw_item.get("copyCleanSegment"), dict)
            and backend_media_id
            and local_video_path
            and upload_video_path == local_video_path
            and duration_seconds > COPY_CLEAN_MAX_DIRECT_DURATION_SECONDS
        ):
            try:
                segment_items = _build_auto_split_items(legacy_globals, raw_item, backend_media_id, duration_seconds)
                if not segment_items:
                    failed.append({"itemId": item_id, "error": "Scene split did not produce copy-clean segments"})
                    continue
                pending_items[index:index] = segment_items
                _append_debug_log(
                    legacy_globals,
                    "api.copy_clean.auto_split",
                    {"itemId": item_id, "mediaId": backend_media_id, "duration": duration_seconds, "segments": len(segment_items)},
                )
                continue
            except Exception as error:
                failed.append({"itemId": item_id, "error": str(error)})
                continue

        if (not source_url or _callable(legacy_globals, "is_local_media_url")(source_url)) and upload_video_path:
            upload_path = str(_callable(legacy_globals, "get_file_upload_config")()["upload_path"] or "copy-clean/videos").strip()
            upload_name = f"{_callable(legacy_globals, 'unique_file_token')('copy-clean-upload', backend_media_id or item_id)}{upload_video_path.suffix.lower() or '.mp4'}"
            source_url = _callable(legacy_globals, "upload_file_to_kie")(upload_video_path, upload_name, upload_path)
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
            if isinstance(raw_item.get("copyCleanSegment"), dict):
                task["copyCleanSegment"] = raw_item["copyCleanSegment"]
                _update_scene_segment_from_copy_clean_task(legacy_globals, task)
            _callable(legacy_globals, "save_copy_clean_task")(store, task)
            submitted.append(task)
        except Exception as error:
            failed.append({"itemId": item_id, "error": str(error)})

    _callable(legacy_globals, "write_copy_clean_store")(store)
    _append_debug_log(legacy_globals, "api.copy_clean.submit", {"submitted": len(submitted), "failed": len(failed)})
    return {"tasks": submitted, "failed": failed}


def detect_region(legacy_globals: dict[str, Any], items: list[Any]) -> dict[str, Any]:
    valid_items = [raw_item for raw_item in items if isinstance(raw_item, dict)]
    concurrency = _detect_region_concurrency(len(valid_items))
    if concurrency <= 1:
        result = _detect_region_serial(legacy_globals, valid_items)
        _append_debug_log(
            legacy_globals,
            "api.copy_clean.detect_region",
            {"detected": len(result.get("regions") or []), "failed": len(result.get("failed") or []), "concurrency": concurrency},
        )
        return result

    ordered_results: list[tuple[int, dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(_detect_region_serial, legacy_globals, [raw_item]): index for index, raw_item in enumerate(valid_items)}
        for future in as_completed(futures):
            ordered_results.append((futures[future], future.result()))

    regions: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for _index, result in sorted(ordered_results, key=lambda item: item[0]):
        regions.extend(result.get("regions") if isinstance(result.get("regions"), list) else [])
        failed.extend(result.get("failed") if isinstance(result.get("failed"), list) else [])

    _append_debug_log(legacy_globals, "api.copy_clean.detect_region", {"detected": len(regions), "failed": len(failed), "concurrency": concurrency})
    return {"regions": regions, "failed": failed}


def _detect_region_serial(legacy_globals: dict[str, Any], items: list[Any]) -> dict[str, Any]:
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
        video_path = _item_local_media_path(legacy_globals, raw_item) or _callable(legacy_globals, "find_original_media_video")(media_dir)
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
            uncertain, method = _uncertain_no_subtitle_detection(detected)
            if uncertain:
                failed.append({"itemId": item_id, "error": "字幕区域未识别出来", "method": method or "unknown"})
                continue
            regions.append(
                {
                    "itemId": item_id,
                    "backendMediaId": backend_media_id,
                    "hasSubtitle": bool(detected.get("hasSubtitle", True)),
                    "region": region,
                    "confidence": detected.get("confidence"),
                    "method": method,
                }
            )
        except Exception as error:
            failed.append({"itemId": item_id, "error": str(error)})

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
        _update_scene_segment_from_copy_clean_task(legacy_globals, task)
        updated.append(task)

    _callable(legacy_globals, "write_copy_clean_store")(store)
    _append_debug_log(legacy_globals, "api.copy_clean.progress", {"count": len(updated)})
    return 200, {"tasks": updated}
