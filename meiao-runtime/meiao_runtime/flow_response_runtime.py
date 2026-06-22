from __future__ import annotations

from typing import Any


def slot_index(payload: dict[str, Any]) -> int | None:
    raw_value = str(payload.get("slotIndex") or "")
    return int(raw_value) if raw_value.isdigit() else None


def submit_stage(payload: dict[str, Any], config: Any) -> str:
    explicit = str(payload.get("stage") or "").strip()
    if explicit:
        return explicit
    return "瑙嗛" if isinstance(config, dict) and config.get("type") == "video" else "鐢熷浘"


def write_submit_error_progress(
    legacy_globals: dict[str, Any],
    payload: dict[str, Any],
    config: Any,
    message: str,
    error_code: str,
) -> None:
    write_flow_progress = legacy_globals.get("write_flow_progress")
    if not callable(write_flow_progress):
        return
    write_flow_progress(
        {
            "jobId": str(payload.get("jobId") or "").strip(),
            "stage": submit_stage(payload, config),
            "slotId": str(payload.get("slotId") or "").strip() or None,
            "slotIndex": slot_index(payload),
            "phase": "error",
            "status": "failed",
            "message": message,
            "errorCode": error_code,
        }
    )


def is_ingest_error(legacy_globals: dict[str, Any], error: BaseException) -> bool:
    ingest_error_type = legacy_globals.get("IngestError")
    return isinstance(ingest_error_type, type) and isinstance(error, ingest_error_type)


def ingest_error_payload(error: BaseException) -> dict[str, Any]:
    return {
        "error": str(error),
        "code": getattr(error, "code", ""),
        "action": getattr(error, "action", ""),
    }


def log_flow_error(legacy_globals: dict[str, Any], event: str, error: BaseException) -> None:
    append_debug_log = legacy_globals.get("append_debug_log")
    if callable(append_debug_log):
        append_debug_log(
            event,
            {"error": str(error), "code": getattr(error, "code", "")}
            if is_ingest_error(legacy_globals, error)
            else {"errorType": type(error).__name__, "error": str(error)},
        )


def payload_dict(payload: Any) -> dict[str, Any]:
    return payload if isinstance(payload, dict) else {}


def ok_result(result: Any) -> dict[str, Any]:
    return result if isinstance(result, dict) else {"result": result}
