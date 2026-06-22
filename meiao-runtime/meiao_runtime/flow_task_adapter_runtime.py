from __future__ import annotations

from typing import Any

from . import flow_action_runtime, flow_preflight_runtime, flow_runtime


def flow_live_preflight(legacy_globals: dict[str, Any], action: str) -> tuple[int, dict[str, Any]] | None:
    statuses = flow_runtime.collect_live_preflight_statuses(legacy_globals)
    return flow_preflight_runtime.evaluate_live_preflight(
        statuses["runtimeStatus"],
        statuses["pageStatus"],
        statuses["networkStatus"],
        action,
    )


def submit_prompt_task(legacy_globals: dict[str, Any], payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        return 400, {"error": "prompt is required", "code": "FLOW_PROMPT_MISSING"}
    if payload.get("allowLive") is not True:
        return 409, {"error": "live Flow execution requires allowLive=true", "code": "FLOW_LIVE_GUARD"}
    preflight = flow_live_preflight(legacy_globals, "flow-submit-prompt")
    if preflight is not None:
        return preflight
    config = payload.get("config")
    reference_images = payload.get("referenceImages")
    result = flow_action_runtime.submit_prompt(
        legacy_globals,
        prompt,
        config if isinstance(config, dict) else None,
        reference_images if isinstance(reference_images, list) else None,
        str(payload.get("jobId") or "").strip() or None,
        str(payload.get("stage") or "").strip() or None,
        str(payload.get("slotId") or "").strip() or None,
        int(payload.get("slotIndex")) if str(payload.get("slotIndex") or "").isdigit() else None,
    )
    return 200, result if isinstance(result, dict) else {"result": result}


def collect_results_task(legacy_globals: dict[str, Any], payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    job_id = str(payload.get("jobId") or "").strip()
    if not job_id:
        return 400, {"error": "jobId is required", "code": "FLOW_JOB_ID_MISSING"}
    if payload.get("allowLive") is not True:
        return 409, {"error": "live Flow collection requires allowLive=true", "code": "FLOW_LIVE_GUARD"}
    preflight = flow_live_preflight(legacy_globals, "flow-collect-results")
    if preflight is not None:
        return preflight
    result = flow_action_runtime.collect_results(
        legacy_globals,
        job_id,
        str(payload.get("prompt") or "").strip() or None,
        str(payload.get("assetType") or "image").strip(),
    )
    return 200, result if isinstance(result, dict) else {"result": result}
