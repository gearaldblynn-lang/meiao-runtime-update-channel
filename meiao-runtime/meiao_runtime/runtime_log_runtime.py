from __future__ import annotations

from typing import Any

from .route_helpers import append_debug_log as _append_debug_log
from .route_helpers import payload_dict as _payload


def ingest_logs(legacy_globals: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    log_file = legacy_globals["INGEST_LOG_FILE"]
    if not log_file.exists():
        return 200, {"lines": []}
    lines = log_file.read_text(encoding="utf-8", errors="ignore").splitlines()[-300:]
    return 200, {"path": str(log_file), "lines": lines}


def client_log(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    try:
        data = _payload(payload)
        _append_debug_log(legacy_globals, "client." + str(data.get("event") or "event"), {"payload": data})
        return 200, {"logged": True}
    except Exception as error:
        _append_debug_log(legacy_globals, "client.log_error", {"errorType": type(error).__name__, "error": str(error)})
        return 200, {"logged": False}
