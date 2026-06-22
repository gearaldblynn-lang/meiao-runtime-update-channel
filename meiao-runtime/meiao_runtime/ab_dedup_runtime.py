from __future__ import annotations

from typing import Any

from .route_helpers import callable_or_raise as _callable
from .route_helpers import payload_dict as _payload


def run(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    data = _payload(payload)
    shots = data.get("shots")
    options = data.get("options")
    if not isinstance(shots, list):
        return 400, {"error": "Missing shots"}
    result = _callable(legacy_globals, "run_ab_dedup_batch")(shots, options if isinstance(options, dict) else {})
    return 200, result
