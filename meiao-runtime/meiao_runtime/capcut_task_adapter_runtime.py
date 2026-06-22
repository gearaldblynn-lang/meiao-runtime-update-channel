from __future__ import annotations

from typing import Any

from . import capcut_draft_runtime


CAPCUT_LIVE_GUARD = "CAPCUT_LIVE_GUARD"
CAPCUT_PLAN_MISSING = "CAPCUT_PLAN_MISSING"
CAPCUT_DRAFT_URL_MISSING = "CAPCUT_DRAFT_URL_MISSING"


def generate_draft_task(legacy_globals: dict[str, Any], payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    plan = payload.get("plan")
    if not isinstance(plan, dict):
        return 400, {"error": "plan is required", "code": CAPCUT_PLAN_MISSING}
    if payload.get("allowLive") is not True:
        return 409, {"error": "live CapCut draft generation requires allowLive=true", "code": CAPCUT_LIVE_GUARD}
    return capcut_draft_runtime.generate_draft(legacy_globals, payload)


def export_video_task(legacy_globals: dict[str, Any], payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    draft_url = str(payload.get("draftUrl") or payload.get("capcutDraftUrl") or "").strip()
    if not draft_url:
        return 400, {"error": "draftUrl is required", "code": CAPCUT_DRAFT_URL_MISSING}
    if payload.get("allowLive") is not True:
        return 409, {"error": "live CapCut export requires allowLive=true", "code": CAPCUT_LIVE_GUARD}
    return capcut_draft_runtime.export_video(legacy_globals, {**payload, "draftUrl": draft_url})
