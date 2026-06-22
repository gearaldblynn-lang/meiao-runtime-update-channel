from __future__ import annotations

import threading
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from starlette.responses import Response

from .route_helpers import append_debug_log as _append_debug_log
from .route_helpers import callable_or_raise as _callable
from .route_helpers import payload_dict as _payload


def create(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    data = _payload(payload)
    text = str(data.get("text") or "").strip()
    voice = str(data.get("voice") or "EkK5I93UQWFDigLMpZcX").strip()
    language_code = str(data.get("language_code") or "zh").strip()
    try:
        stability = float(data.get("stability", 0.5))
    except Exception:
        stability = 0.5
    if stability not in {0.0, 0.5, 1.0}:
        stability = 0.5
    if not text:
        return 400, {"error": "Missing voice text"}
    if len(text) > 5000:
        return 400, {"error": "Voice text exceeds 5000 characters"}

    config = _callable(legacy_globals, "get_audio_model_config")()
    profile = config.get("active_model") or {}
    default_model = str(legacy_globals.get("DEFAULT_AUDIO_MODEL") or "elevenlabs/text-to-dialogue-v3")
    model_name = str(profile.get("model") or default_model).strip() or default_model
    request_body: dict[str, Any] = {
        "model": model_name,
        "input": {
            "dialogue": [{"text": text, "voice": voice}],
            "stability": stability,
            "language_code": language_code,
        },
    }
    callback_url = str(data.get("callBackUrl") or "").strip()
    if callback_url:
        request_body["callBackUrl"] = callback_url

    api_result = _callable(legacy_globals, "call_kie_market_create_task")(request_body, profile)
    result_data = api_result.get("data") if isinstance(api_result.get("data"), dict) else {}
    task_id = str(result_data.get("taskId") or "").strip()
    record_id = str(result_data.get("recordId") or "").strip()
    if not task_id:
        ingest_error = legacy_globals.get("IngestError")
        if isinstance(ingest_error, type):
            raise ingest_error("Kie voice API did not return taskId")
        raise RuntimeError("Kie voice API did not return taskId")

    _append_debug_log(
        legacy_globals,
        "api.voice.elevenlabs.create",
        {"taskId": task_id, "recordId": record_id, "model": model_name, "textLength": len(text), "voice": voice},
    )
    return 200, {
        "taskId": task_id,
        "recordId": record_id,
        "status": "submitted",
        "model": model_name,
        "voice": voice,
        "stability": stability,
        "language_code": language_code,
        "raw": api_result,
    }


def status(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    data = _payload(payload)
    task_id = str(data.get("taskId") or data.get("task_id") or "").strip()
    if not task_id:
        return 400, {"error": "Missing taskId"}

    api_result = _callable(legacy_globals, "call_kie_market_task_detail")(task_id)
    result_data = api_result.get("data") if isinstance(api_result.get("data"), dict) else api_result
    status_value = _callable(legacy_globals, "normalize_kie_task_state")(str(result_data.get("state") or result_data.get("status") or ""))
    audio_url = _callable(legacy_globals, "extract_kie_result_audio_url")(result_data)
    error = str(result_data.get("failMsg") or result_data.get("errorMessage") or result_data.get("msg") or "").strip()
    return 200, {
        "taskId": task_id,
        "recordId": str(data.get("recordId") or result_data.get("recordId") or "").strip(),
        "status": "success" if audio_url and status_value != "failed" else status_value,
        "audioUrl": audio_url,
        "error": error,
        "raw": api_result,
    }


def preview_target(legacy_globals: dict[str, Any], voice_id: str) -> Path | None:
    target = _callable(legacy_globals, "ensure_voice_preview_cached")(str(voice_id or "").strip())
    return Path(target) if target is not None else None


def preview_file_response(target: Path, range_header: str | None, headers: dict[str, str] | None = None) -> Response:
    path = Path(target)
    file_size = path.stat().st_size
    base_headers = {
        **(headers or {}),
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=31536000, immutable",
        "Content-Type": "audio/mpeg",
    }

    if range_header:
        match = re.match(r"bytes=(\d*)-(\d*)", str(range_header))
        if match and file_size > 0:
            start_text, end_text = match.groups()
            start = int(start_text) if start_text else 0
            end = int(end_text) if end_text else file_size - 1
            start = max(0, min(start, file_size - 1))
            end = max(start, min(end, file_size - 1))
            length = end - start + 1
            with path.open("rb") as file:
                file.seek(start)
                body = file.read(length)
            return Response(
                body,
                status_code=206,
                media_type="audio/mpeg",
                headers={
                    **base_headers,
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Content-Length": str(length),
                },
            )

    return Response(
        path.read_bytes(),
        status_code=200,
        media_type="audio/mpeg",
        headers={**base_headers, "Content-Length": str(file_size)},
    )


def preview_prefetch(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    raw_ids = _payload(payload).get("voiceIds")
    voice_ids = [str(item).strip() for item in raw_ids if str(item).strip()] if isinstance(raw_ids, list) else []
    voice_ids = list(dict.fromkeys(voice_ids))
    if not voice_ids:
        return 400, {"error": "Missing voiceIds"}

    failed: list[str] = []
    lock = threading.Lock()

    def worker(voice_id: str) -> None:
        if _callable(legacy_globals, "ensure_voice_preview_cached")(voice_id) is None:
            with lock:
                failed.append(voice_id)

    thread_pool = legacy_globals.get("ThreadPoolExecutor")
    executor_factory = thread_pool if callable(thread_pool) else ThreadPoolExecutor
    with executor_factory(max_workers=6) as pool:
        list(pool.map(worker, voice_ids))

    cached = len(voice_ids) - len(failed)
    _append_debug_log(
        legacy_globals,
        "api.voice.elevenlabs.preview.prefetch",
        {"total": len(voice_ids), "cached": cached, "failed": len(failed)},
    )
    return 200, {"total": len(voice_ids), "cached": cached, "failed": failed}
