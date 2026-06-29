from __future__ import annotations

from typing import Any


def _callable(legacy_globals: dict[str, Any], name: str) -> Any:
    value = legacy_globals.get(name)
    if not callable(value):
        raise RuntimeError(f"Legacy callable {name} is unavailable.")
    return value


def get_model_registry(legacy_globals: dict[str, Any]) -> dict[str, Any]:
    return _callable(legacy_globals, "get_model_registry_config")()


def save_model_registry(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    if not isinstance(payload, dict):
        return 400, {"error": "请求体格式错误"}
    return 200, _callable(legacy_globals, "save_model_registry_config")(payload)


def get_analysis_models(legacy_globals: dict[str, Any]) -> dict[str, Any]:
    return _callable(legacy_globals, "get_analysis_model_config")()


def save_analysis_models(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    if not isinstance(payload, dict):
        return 400, {"error": "请求体格式错误"}
    return 200, _callable(legacy_globals, "save_analysis_model_config")(payload)


def chat(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    if not isinstance(payload, dict):
        return 400, {"error": "请求体格式错误"}

    messages = _callable(legacy_globals, "normalize_chat_messages")(payload.get("messages"))
    if not messages:
        return 400, {"error": "缺少 messages 或消息内容为空"}

    config = _callable(legacy_globals, "get_analysis_model_config")()
    raw_timeout = payload.get("timeoutSeconds") or payload.get("timeout_seconds")
    try:
        timeout_seconds = float(raw_timeout)
    except Exception:
        timeout_seconds = 120.0
    timeout_seconds = max(15.0, min(240.0, timeout_seconds))
    requested_model_id = str(payload.get("modelId") or payload.get("model_id") or "").strip()
    profiles = _callable(legacy_globals, "select_analysis_profiles_for_request")(config, requested_model_id, messages)
    if not profiles:
        return 409, {"error": "未配置可用分析模型。请在设置里补充。", "code": "ANALYSIS_CONFIG_MISSING", "action": "CONFIGURE_ANALYSIS_MODEL"}

    response_data = None
    profile = profiles[0]
    last_error: BaseException | None = None
    for index, candidate in enumerate(profiles):
        profile = candidate
        try:
            response_data = _callable(legacy_globals, "openai_compatible_chat_completion")(
                profile,
                messages,
                stream=bool(payload.get("stream")) if isinstance(payload.get("stream"), bool) else False,
                tools=payload.get("tools"),
                include_thoughts=payload.get("include_thoughts"),
                reasoning_effort=str(payload.get("reasoning_effort") or "").strip() or None,
                response_format=payload.get("response_format"),
                timeout_seconds=timeout_seconds,
            )
            break
        except Exception as exc:
            last_error = exc
            if index + 1 >= len(profiles):
                raise
            append_debug_log = legacy_globals.get("append_debug_log")
            if callable(append_debug_log):
                append_debug_log(
                    "api.analysis.chat.retry",
                    {
                        "failedModelId": profile.get("id"),
                        "nextModelId": profiles[index + 1].get("id"),
                        "error": str(exc),
                        "code": getattr(exc, "code", ""),
                    },
                )
    if response_data is None:
        if last_error:
            raise last_error
        ingest_error = legacy_globals.get("IngestError")
        if isinstance(ingest_error, type):
            raise ingest_error("分析模型调用失败。", "ANALYSIS_REQUEST_FAILED", "RETRY")
        raise RuntimeError("分析模型调用失败。")

    assistant_content = _callable(legacy_globals, "extract_chat_message_content")(response_data)
    append_debug_log = legacy_globals.get("append_debug_log")
    if callable(append_debug_log):
        append_debug_log(
            "api.analysis.chat.success",
            {
                "modelId": profile["id"],
                "model": profile.get("model"),
                "messageCount": len(messages),
                "contentLength": len(assistant_content),
            },
        )
    return 200, {
        "model": profile.get("model"),
        "modelId": profile["id"],
        "provider": profile.get("provider"),
        "content": assistant_content,
        "response": response_data,
    }


def vector_status(legacy_globals: dict[str, Any]) -> dict[str, Any]:
    get_vector_status_payload = legacy_globals.get("get_vector_status_payload")
    if callable(get_vector_status_payload):
        return get_vector_status_payload()

    store = _callable(legacy_globals, "read_vector_store")()
    store, _changed = _callable(legacy_globals, "sync_vector_store_profile")(store)
    for item in store.get("items", {}).values():
        _callable(legacy_globals, "ensure_product_structure_tags")(item)
    items = store.get("items", {})
    return {
        "provider": "doubao",
        "model": store.get("model") or _callable(legacy_globals, "get_ark_config")()["model"],
        "profile": store.get("profile") or legacy_globals.get("VECTOR_PROFILE_ID"),
        "count": len(items),
        "items": [
            {
                "key": key,
                "projectId": item.get("projectId"),
                "mediaId": item.get("mediaId"),
                "scriptId": item.get("scriptId"),
                "segmentIndex": item.get("segmentIndex"),
                "segmentId": item.get("segmentId"),
                "splitRunId": item.get("splitRunId"),
                "filename": item.get("filename"),
                "url": item.get("url"),
                "updatedAt": item.get("updatedAt"),
                "cost": _callable(legacy_globals, "normalize_vector_cost")(item),
                "inputModalities": item.get("inputModalities"),
                "embeddingProfile": item.get("embeddingProfile"),
                "dimensions": item.get("dimensions"),
                "videoFps": item.get("videoFps"),
                "inputStrategy": item.get("inputStrategy"),
                "visualTags": item.get("visualTags"),
                "voiceTags": item.get("voiceTags"),
                "productTags": item.get("productTags"),
                "actionTags": item.get("actionTags"),
                "sceneTags": item.get("sceneTags"),
                "freeTags": item.get("freeTags"),
                "categoryTags": item.get("categoryTags"),
                "styleTags": item.get("styleTags"),
                "colorTags": item.get("colorTags"),
                "specTags": item.get("specTags"),
                "materialTags": item.get("materialTags"),
                "sellingPointTags": item.get("sellingPointTags"),
                "usageSceneTags": item.get("usageSceneTags"),
                "voiceFitTags": item.get("voiceFitTags"),
                "aliasTags": item.get("aliasTags"),
                "tagAnalysisStatus": item.get("tagAnalysisStatus"),
                "tagAnalysisError": item.get("tagAnalysisError"),
                "tagAnalysisModelId": item.get("tagAnalysisModelId"),
                "tagFallbackApplied": item.get("tagFallbackApplied"),
            }
            for key, item in items.items()
        ],
    }


def vector_tag_tasks(legacy_globals: dict[str, Any], project_id: str) -> dict[str, Any]:
    store = _callable(legacy_globals, "read_vector_tag_task_store")()
    has_pending = any(
        isinstance(task, dict) and str(task.get("status") or "") in {"queued", "running", "canceling"}
        for task in store.get("tasks", {}).values()
    )
    if has_pending:
        _callable(legacy_globals, "reconcile_vector_tag_task_runtime")()
        store = _callable(legacy_globals, "read_vector_tag_task_store")()
    tasks = []
    for task in store.get("tasks", {}).values():
        if not isinstance(task, dict):
            continue
        if project_id and str(task.get("projectId") or "") != project_id:
            continue
        tasks.append(_callable(legacy_globals, "summarize_vector_tag_task")(dict(task)))
    tasks.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
    light_tasks = [_callable(legacy_globals, "vector_tag_task_summary_view")(task) for task in tasks[:20]]
    return {"tasks": light_tasks}


def vector_search(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    if not isinstance(payload, dict):
        payload = {}
    query = str(payload.get("query") or "").strip()
    if not query:
        return 400, {"error": "缺少 query"}

    project_id = str(payload.get("projectId") or "").strip()
    matching_mode = str(payload.get("matchingMode") or "hybrid").strip()
    limit = int(payload.get("limit") or 12)
    valid_segments_raw = payload.get("validSegments")
    valid_segments = None
    if isinstance(valid_segments_raw, list):
        valid_segments = {
            (str(item.get("mediaId") or ""), str(item.get("scriptId")), int(item.get("segmentIndex"))): {
                "url": str(item.get("url") or ""),
                "filename": str(item.get("filename") or ""),
                "segmentId": str(item.get("segmentId") or ""),
                "splitRunId": str(item.get("splitRunId") or ""),
            }
            for item in valid_segments_raw
            if isinstance(item, dict)
            and item.get("scriptId")
            and isinstance(item.get("segmentIndex"), (int, float, str))
            and str(item.get("segmentIndex")).strip().isdigit()
        }

    query_inputs = [{"type": "text", "text": query}]
    query_vector, usage = _callable(legacy_globals, "call_doubao_embedding")(query_inputs, _callable(legacy_globals, "query_instruction")())
    query_cost = _callable(legacy_globals, "estimate_input_cost")(query_inputs, usage)
    store = _callable(legacy_globals, "read_vector_store")()
    store, changed = _callable(legacy_globals, "sync_vector_store_profile")(store)
    items = store.get("items", {})
    results = []
    shared_scene_library_project_id = legacy_globals.get("SHARED_SCENE_LIBRARY_PROJECT_ID")
    vector_profile_id = legacy_globals.get("VECTOR_PROFILE_ID")
    for item in items.values():
        item, item_changed = _callable(legacy_globals, "ensure_product_structure_tags")(item)
        if item_changed:
            changed = True
        item_project_id = str(item.get("projectId") or "")
        if project_id and item_project_id != project_id and item_project_id != shared_scene_library_project_id:
            continue
        if item.get("embeddingProfile") != vector_profile_id:
            continue
        try:
            segment_index = int(item.get("segmentIndex"))
        except (TypeError, ValueError):
            continue
        script_id = str(item.get("scriptId") or "")
        media_id = str(item.get("mediaId") or "")
        if valid_segments is not None:
            valid_meta = valid_segments.get((media_id, script_id, segment_index))
            if valid_meta is None:
                continue
            if valid_meta.get("url") and valid_meta["url"] != str(item.get("url") or ""):
                continue
            if valid_meta.get("filename") and valid_meta["filename"] != str(item.get("filename") or ""):
                continue
            if valid_meta.get("segmentId") and valid_meta["segmentId"] != str(item.get("segmentId") or ""):
                continue
            if valid_meta.get("splitRunId") and valid_meta["splitRunId"] != str(item.get("splitRunId") or ""):
                continue
        vector = item.get("vector")
        if not isinstance(vector, list):
            continue
        base_score = _callable(legacy_globals, "cosine_similarity")(query_vector, [float(value) for value in vector])
        profile_vectors = item.get("profileVectors") if isinstance(item.get("profileVectors"), dict) else {}
        profile_scores = {}
        for profile_key, profile_vector in profile_vectors.items():
            if isinstance(profile_vector, list):
                profile_scores[profile_key] = _callable(legacy_globals, "cosine_similarity")(query_vector, [float(value) for value in profile_vector])
        if matching_mode == "visual":
            weights = {"base": 0.72, "actionScene": 0.18, "product": 0.07, "voice": 0.03}
        elif matching_mode == "voice":
            weights = {"base": 0.12, "voice": 0.55, "product": 0.23, "actionScene": 0.10}
        else:
            weights = {"base": 0.18, "voice": 0.34, "product": 0.28, "actionScene": 0.20}
        weighted_sum = base_score * weights["base"]
        used_weight = weights["base"]
        for profile_key in ("voice", "product", "actionScene"):
            if profile_key in profile_scores:
                weighted_sum += profile_scores[profile_key] * weights[profile_key]
                used_weight += weights[profile_key]
        weighted_score = weighted_sum / used_weight if used_weight > 0 else base_score
        floor_ratio = 0.94 if matching_mode == "visual" else 0.9
        score = max(weighted_score, base_score * floor_ratio)
        results.append(
            {
                "score": score,
                "baseScore": base_score,
                "profileScores": profile_scores,
                "matchingMode": matching_mode,
                "key": item.get("key"),
                "projectId": item.get("projectId"),
                "mediaId": media_id,
                "scriptId": script_id,
                "segmentIndex": segment_index,
                "segmentId": item.get("segmentId"),
                "splitRunId": item.get("splitRunId"),
                "filename": item.get("filename"),
                "url": item.get("url"),
                "posterUrl": item.get("posterUrl"),
                "text": item.get("text"),
                "embeddingText": item.get("embeddingText"),
                "visualTags": item.get("visualTags"),
                "voiceTags": item.get("voiceTags"),
                "productTags": item.get("productTags"),
                "actionTags": item.get("actionTags"),
                "sceneTags": item.get("sceneTags"),
                "freeTags": item.get("freeTags"),
                "categoryTags": item.get("categoryTags"),
                "styleTags": item.get("styleTags"),
                "colorTags": item.get("colorTags"),
                "specTags": item.get("specTags"),
                "materialTags": item.get("materialTags"),
                "sellingPointTags": item.get("sellingPointTags"),
                "usageSceneTags": item.get("usageSceneTags"),
                "voiceFitTags": item.get("voiceFitTags"),
                "aliasTags": item.get("aliasTags"),
                "duration": item.get("duration"),
                "updatedAt": item.get("updatedAt"),
                "inputModalities": item.get("inputModalities"),
                "cost": _callable(legacy_globals, "normalize_vector_cost")(item),
                "dimensions": len(vector),
                "embeddingProfile": item.get("embeddingProfile"),
                "videoFps": item.get("videoFps"),
                "inputStrategy": item.get("inputStrategy"),
            }
        )
    results.sort(key=lambda item: item["score"], reverse=True)
    append_debug_log = legacy_globals.get("append_debug_log")
    if callable(append_debug_log):
        append_debug_log("api.vectors.search", {"projectId": project_id, "query": query, "resultCount": len(results), "cost": query_cost})
    return 200, {"query": query, "cost": query_cost, "results": results[: max(1, min(limit, 50))]}
