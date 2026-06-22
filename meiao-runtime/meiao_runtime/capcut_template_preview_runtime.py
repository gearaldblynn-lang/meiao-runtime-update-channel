from __future__ import annotations

import json
import traceback
from io import BytesIO
from typing import Any

from .route_helpers import append_debug_log


class _RequestHeaders:
    def __init__(self, headers: dict[str, str]) -> None:
        self._headers = {key.lower(): value for key, value in headers.items()}

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._headers.get(key.lower(), default)


def template_preview(
    legacy_globals: dict[str, Any],
    payload: Any,
    request_base_url: str = "http://127.0.0.1:8787",
) -> tuple[int, dict[str, Any]]:
    try:
        handler_cls = legacy_globals.get("Handler")
        if not isinstance(handler_cls, type):
            return 500, {"error": "CapCut template preview handler is unavailable."}
        handler = object.__new__(handler_cls)
        body = json.dumps(payload if isinstance(payload, dict) else {}).encode("utf-8")
        captured: dict[str, Any] = {"status": 500, "payload": {"error": "CapCut template preview returned no response."}}

        def write_json(status: int, response_payload: dict[str, Any]) -> None:
            captured["status"] = int(status)
            captured["payload"] = response_payload if isinstance(response_payload, dict) else {"result": response_payload}

        handler.headers = _RequestHeaders({"Content-Length": str(len(body))})
        handler.rfile = BytesIO(body)
        handler.write_json = write_json
        handler.request_base_url = lambda: request_base_url.rstrip("/")
        handler.handle_capcut_mate_template_preview()
        return int(captured["status"]), captured["payload"]
    except Exception as exc:
        append_debug_log(
            legacy_globals,
            "api.capcut_mate.template_preview.runtime_error",
            {"errorType": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc()},
        )
        return 500, {"error": f"真实预览生成失败：{exc}"}
