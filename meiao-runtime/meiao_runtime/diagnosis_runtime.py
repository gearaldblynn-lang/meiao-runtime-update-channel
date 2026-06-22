from __future__ import annotations

from typing import Any

from .route_helpers import append_debug_log as _append_debug_log
from .route_helpers import callable_or_raise as _callable
from .route_helpers import payload_dict as _payload


def _is_ingest_error(legacy_globals: dict[str, Any], error: BaseException) -> bool:
    ingest_error_type = legacy_globals.get("IngestError")
    return isinstance(ingest_error_type, type) and isinstance(error, ingest_error_type)


def _ingest_error_payload(error: BaseException) -> dict[str, Any]:
    return {
        "error": str(error),
        "code": getattr(error, "code", ""),
        "action": getattr(error, "action", ""),
    }


def extract(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    data = _payload(payload)
    target_url = str(data.get("url") or "").strip()
    requested_platform = str(data.get("platform") or "").strip() or None
    if not target_url.startswith(("http://", "https://")):
        return 400, {"error": "Missing accessible video URL"}

    platform_key = _callable(legacy_globals, "detect_platform_key")(target_url) or requested_platform
    _append_debug_log(legacy_globals, "api.diagnosis.extract.start", {"url": target_url, "platform": platform_key})

    info: dict[str, Any] | None = None
    browser_result: dict[str, Any] | None = None
    extract_error: BaseException | None = None
    try:
        extracted = _callable(legacy_globals, "extract_info_with_login_retry")(target_url, legacy_globals["MEDIA_ROOT"], False)
        info = extracted if isinstance(extracted, dict) else None
    except Exception as error:
        extract_error = error
        if not (_is_ingest_error(legacy_globals, error) and platform_key == "douyin"):
            if platform_key != "douyin":
                raise

    if platform_key == "douyin" and (not info or not _callable(legacy_globals, "first_nonempty")(info.get("url"), info.get("title"))):
        browser_result = _callable(legacy_globals, "resolve_douyin_with_browser")(target_url)
        browser_info = (
            _callable(legacy_globals, "browser_result_to_info")(target_url, legacy_globals["MEDIA_ROOT"], False, browser_result)
            if browser_result
            else None
        )
        if browser_info:
            merged = dict(info or {})
            for key, value in browser_info.items():
                if value not in (None, "", [], {}):
                    merged[key] = value
            info = merged

    if not info:
        if extract_error and _is_ingest_error(legacy_globals, extract_error):
            return 409, _ingest_error_payload(extract_error)
        if extract_error:
            raise extract_error
        return 409, {"error": "No usable video fields extracted", "code": "DIAGNOSIS_EXTRACT_EMPTY"}

    result = _callable(legacy_globals, "build_diagnosis_extract_result")(target_url, requested_platform, info, browser_result)
    if not result.get("title") and not result.get("directVideoUrl") and not result.get("rawFields"):
        return 409, {"error": "No usable video fields extracted", "code": "DIAGNOSIS_EXTRACT_EMPTY"}

    _append_debug_log(
        legacy_globals,
        "api.diagnosis.extract.success",
        {
            "url": target_url,
            "platform": platform_key,
            "title": result.get("title"),
            "hasDirectVideoUrl": bool(result.get("directVideoUrl")),
            "rawFieldCount": len(result.get("rawFields") or {}),
        },
    )
    return 200, result
