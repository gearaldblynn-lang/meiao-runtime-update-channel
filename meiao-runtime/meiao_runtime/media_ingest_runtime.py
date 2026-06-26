from __future__ import annotations

import shutil
import time
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .route_helpers import append_debug_log as _append_debug_log
from .route_helpers import callable_or_raise as _callable
from .route_helpers import payload_dict as _payload
from .state_store import StateStore


def _state_store(legacy_globals: dict[str, Any]) -> StateStore:
    cached = legacy_globals.get("_meiao_state_store")
    if isinstance(cached, StateStore):
        return cached
    store = StateStore(legacy_globals)
    legacy_globals["_meiao_state_store"] = store
    return store


def _media_item_payload(item: Any) -> dict[str, Any]:
    return {
        "title": item.title,
        "duration": item.duration,
        "source": item.source,
        "platformKey": item.platform_key,
        "sourceUrl": item.source_url,
        "sourceVideoUrl": item.source_video_url,
        "remotePosterUrl": item.remote_poster_url,
        "remoteVideoUrl": item.remote_video_url,
        "backendMediaId": item.media_id,
        "notes": item.notes,
        "basicInfo": item.basic_info,
        "author": item.author,
        "metrics": item.metrics,
        "contentTags": item.content_tags,
        "commerce": item.commerce,
        "platformRaw": item.platform_raw,
    }


def _media_id_from_url(legacy_globals: dict[str, Any], value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    helper = legacy_globals.get("media_id_from_media_url")
    if callable(helper):
        return str(helper(value) or "").strip()
    marker = "/media/"
    if marker not in value:
        return ""
    tail = value.split(marker, 1)[1]
    return unquote(tail.split("/", 1)[0]).strip()


def _stable_script_id_from_ingest_id(legacy_globals: dict[str, Any], ingest_id: str) -> str:
    helper = legacy_globals.get("stable_script_id_from_ingest_id")
    if callable(helper):
        return str(helper(ingest_id))
    normalized = re.sub(r"[^A-Za-z0-9]", "", str(ingest_id or "").replace("IN-", ""))
    return f"S-{normalized or ingest_id}"


def _item_matches_media(legacy_globals: dict[str, Any], item: dict[str, Any], media_id: str) -> bool:
    if str(item.get("backendMediaId") or "").strip() == media_id:
        return True
    for field in ("remoteVideoUrl", "remotePosterUrl", "sourceVideoUrl", "sourceUrl"):
        if _media_id_from_url(legacy_globals, item.get(field)) == media_id:
            return True
    return False


def _record_matches_media(legacy_globals: dict[str, Any], record: Any, media_id: str) -> bool:
    if not isinstance(record, dict):
        return False
    if str(record.get("mediaId") or "").strip() == media_id:
        return True
    for segment in record.get("sceneSegments") if isinstance(record.get("sceneSegments"), list) else []:
        if not isinstance(segment, dict):
            continue
        if str(segment.get("mediaId") or "").strip() == media_id:
            return True
        if _media_id_from_url(legacy_globals, segment.get("url")) == media_id:
            return True
        if _media_id_from_url(legacy_globals, segment.get("posterUrl")) == media_id:
            return True
    return False


def _delete_dict_entries(value: Any, keys: set[str], *, media_id: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    next_value: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if key_text in keys:
            continue
        if media_id and isinstance(item, dict) and str(item.get("backendMediaId") or "").strip() == media_id:
            continue
        next_value[key_text] = item
    return next_value


def _cleanup_media_state(legacy_globals: dict[str, Any], media_id: str) -> dict[str, Any]:
    store = _state_store(legacy_globals)

    def update() -> dict[str, Any]:
        state = store.read_client_state()
        state_items = state.get("meiao-ingest-items") if isinstance(state.get("meiao-ingest-items"), list) else []
        sidecar_items = store.read_sidecar("media-library")

        removed_ingest_ids = {
            str(item.get("id") or "").strip()
            for item in [*state_items, *sidecar_items]
            if isinstance(item, dict) and _item_matches_media(legacy_globals, item, media_id) and str(item.get("id") or "").strip()
        }
        removed_script_ids = {
            _stable_script_id_from_ingest_id(legacy_globals, ingest_id)
            for ingest_id in removed_ingest_ids
        }

        next_state_items = [
            item for item in state_items
            if not (isinstance(item, dict) and _item_matches_media(legacy_globals, item, media_id))
        ]
        next_sidecar_items = [
            item for item in sidecar_items
            if not _item_matches_media(legacy_globals, item, media_id)
        ]

        split_records = state.get("meiao-scene-split-records") if isinstance(state.get("meiao-scene-split-records"), dict) else {}
        next_split_records: dict[str, Any] = {}
        for key, record in split_records.items():
            key_text = str(key)
            if key_text in removed_script_ids or _record_matches_media(legacy_globals, record, media_id):
                removed_script_ids.add(key_text)
                continue
            next_split_records[key_text] = record

        state["meiao-ingest-items"] = next_state_items
        if media_id:
            deleted_media = state.get("meiao-deleted-media") if isinstance(state.get("meiao-deleted-media"), dict) else {}
            deleted_media[str(media_id)] = int(time.time() * 1000)
            state["meiao-deleted-media"] = deleted_media
        state["meiao-copy-clean-review"] = _delete_dict_entries(state.get("meiao-copy-clean-review"), removed_ingest_ids)
        state["meiao-copy-clean-tasks"] = _delete_dict_entries(state.get("meiao-copy-clean-tasks"), removed_ingest_ids, media_id=media_id)
        state["meiao-copy-clean-regions"] = _delete_dict_entries(state.get("meiao-copy-clean-regions"), removed_ingest_ids)
        state["meiao-scene-split-queue"] = [
            item for item in (state.get("meiao-scene-split-queue") if isinstance(state.get("meiao-scene-split-queue"), list) else [])
            if str(item).strip() not in removed_ingest_ids
        ]
        state["meiao-scene-split-records"] = next_split_records
        state["meiao-scene-split-hidden-records"] = [
            item for item in (state.get("meiao-scene-split-hidden-records") if isinstance(state.get("meiao-scene-split-hidden-records"), list) else [])
            if str(item).strip() not in removed_script_ids
        ]
        for key in ("meiao-scene-vector-status", "meiao-script-quality", "meiao-script-renames"):
            state[key] = _delete_dict_entries(state.get(key), removed_script_ids)

        store.write_sidecar("media-library", next_sidecar_items)
        store.write_client_state(state, snapshot_reason=f"before-delete-media-{media_id}")
        return {"ingestIds": sorted(removed_ingest_ids), "scriptIds": sorted(removed_script_ids)}

    return store.run_exclusive(update)


def ingest_url(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    data = _payload(payload)
    url = str(data.get("url") or "").strip()
    if not url:
        return 400, {"error": "Missing url"}

    _append_debug_log(legacy_globals, "api.ingest_url.request", {"url": url, "platform": _callable(legacy_globals, "detect_platform_key")(url)})
    items = _callable(legacy_globals, "download_single_url")(url)
    _append_debug_log(legacy_globals, "api.ingest_url.success", {"url": url, "itemCount": len(items)})
    return 200, {"items": [_media_item_payload(item) for item in items]}


def ingest_local(
    legacy_globals: dict[str, Any],
    *,
    filename: str,
    file_bytes: bytes,
    project_id: str = "",
    client_item_id: str = "",
    upload_for_analysis: bool = False,
) -> tuple[int, dict[str, Any]]:
    if not file_bytes:
        return 400, {"error": "Missing local video file"}

    item = _callable(legacy_globals, "ingest_local_file")(filename or "local-video.mp4", file_bytes)
    project_id = str(project_id or "").strip()
    client_item_id = str(client_item_id or "").strip()
    analysis_video_url = ""

    if upload_for_analysis:
        media_dir = legacy_globals["MEDIA_ROOT"] / item.media_id
        local_video_path = _callable(legacy_globals, "find_original_media_video")(media_dir) or _callable(legacy_globals, "find_primary_media_video")(media_dir)
        if not local_video_path:
            ingest_error = legacy_globals.get("IngestError")
            if isinstance(ingest_error, type):
                raise ingest_error("Local video was ingested but no uploadable analysis video file was found.")
            raise RuntimeError("Local video was ingested but no uploadable analysis video file was found.")
        upload_path = f"analysis-media/{datetime.now().strftime('%Y%m%d')}"
        upload_name = f"{_callable(legacy_globals, 'unique_file_token')('analysis-upload', item.media_id)}{local_video_path.suffix.lower() or '.mp4'}"
        analysis_video_url = _callable(legacy_globals, "upload_file_to_kie")(local_video_path, upload_name, upload_path)

    library_item = {
        "id": client_item_id or f"REC-{item.media_id}",
        "projectId": project_id,
        "title": item.title,
        "duration": item.duration,
        "source": item.source,
        "platformKey": item.platform_key,
        "sourceUrl": item.source_url,
        "sourceVideoUrl": analysis_video_url or item.source_video_url,
        "remotePosterUrl": item.remote_poster_url,
        "remoteVideoUrl": item.remote_video_url,
        "backendMediaId": item.media_id,
        "status": "ingested",
        "progress": 100,
        "time": "just now",
        "failed": False,
        "createdAt": int(time.time() * 1000),
        "ingestMode": "local",
        "notes": item.notes,
    }

    store = _state_store(legacy_globals)
    existing_items = store.read_sidecar("media-library")
    library_items = _callable(legacy_globals, "merge_media_library_items")([library_item], existing_items)
    store.write_sidecar("media-library", library_items)
    store.sync_client_state_value("meiao-ingest-items", library_items)

    _append_debug_log(
        legacy_globals,
        "api.ingest_local.success",
        {
            "filename": filename,
            "mediaId": item.media_id,
            "projectId": project_id,
            "size": len(file_bytes),
            "analysisUpload": bool(analysis_video_url),
        },
    )
    return 200, {
        "item": {
            "id": library_item["id"],
            "projectId": project_id,
            "title": item.title,
            "duration": item.duration,
            "source": item.source,
            "platformKey": item.platform_key,
            "sourceUrl": item.source_url,
            "sourceVideoUrl": analysis_video_url or item.source_video_url,
            "remotePosterUrl": item.remote_poster_url,
            "remoteVideoUrl": item.remote_video_url,
            "backendMediaId": item.media_id,
            "notes": item.notes,
        }
    }


def delete_media(legacy_globals: dict[str, Any], media_id: str) -> tuple[int, dict[str, Any]]:
    media_id = unquote(str(media_id or "").strip())
    target = Path(legacy_globals["MEDIA_ROOT"]) / media_id
    cleanup = _cleanup_media_state(legacy_globals, media_id) if media_id else {"ingestIds": [], "scriptIds": []}
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    pruned = _callable(legacy_globals, "prune_vector_items")(media_id=media_id)
    clear_media_storage_cache = legacy_globals.get("clear_media_storage_cache")
    if callable(clear_media_storage_cache):
        clear_media_storage_cache()
    clear_client_state_recover_cache = legacy_globals.get("clear_client_state_recover_cache")
    if callable(clear_client_state_recover_cache):
        clear_client_state_recover_cache()
    _append_debug_log(
        legacy_globals,
        "api.media.delete",
        {"mediaId": media_id, "vectorRemoved": pruned["removed"], **cleanup},
    )
    return 200, {"deleted": True, "vectorRemoved": pruned["removed"], **cleanup}


def scene_split(legacy_globals: dict[str, Any], media_id: str, payload: Any) -> tuple[int, dict[str, Any]]:
    data = _payload(payload)
    threshold = float(data.get("threshold") or 0.3)
    min_scene_seconds = float(data.get("minSceneSeconds") or 1.2)
    result = _callable(legacy_globals, "build_scene_segments_for_media")(unquote(media_id), threshold, min_scene_seconds)
    _append_debug_log(
        legacy_globals,
        "api.scene_split.success",
        {
            "mediaId": media_id,
            "threshold": threshold,
            "minSceneSeconds": min_scene_seconds,
            "segments": len(result.get("segments") or []),
        },
    )
    return 200, result
