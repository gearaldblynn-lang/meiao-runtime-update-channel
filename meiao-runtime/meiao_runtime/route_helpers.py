from __future__ import annotations

from typing import Any

from starlette.responses import JSONResponse


def callable_or_raise(legacy_globals: dict[str, Any], name: str) -> Any:
    value = legacy_globals.get(name)
    if not callable(value):
        raise RuntimeError(f"Legacy callable {name} is unavailable.")
    return value


def json_response(status_code: int, payload: dict[str, Any], headers: dict[str, str]) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code, headers=headers)


def payload_dict(payload: Any) -> dict[str, Any]:
    return payload if isinstance(payload, dict) else {}


def append_debug_log(legacy_globals: dict[str, Any], event: str, payload: dict[str, Any]) -> None:
    append_debug_log_fn = legacy_globals.get("append_debug_log")
    if callable(append_debug_log_fn):
        append_debug_log_fn(event, payload)


def ingest_error_response(error: BaseException, headers: dict[str, str]) -> JSONResponse:
    return json_response(
        409,
        {
            "error": str(error),
            "code": getattr(error, "code", ""),
            "action": getattr(error, "action", ""),
        },
        headers,
    )
