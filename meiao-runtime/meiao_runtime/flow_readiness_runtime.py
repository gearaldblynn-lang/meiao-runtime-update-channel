from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return bool(str(value).strip())


def _add_unique(items: list[str], code: str) -> None:
    if code not in items:
        items.append(code)


def _int_value(value: Any) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def evaluate_live_readiness(
    *,
    health: dict[str, Any],
    auth_status: dict[str, Any],
    chrome_status: dict[str, Any],
    page_status: dict[str, Any],
    network_status: dict[str, Any],
    runtime_url: str = "",
) -> dict[str, Any]:
    missing: list[str] = []
    warnings: list[str] = []

    if str(health.get("status")) != "ok":
        _add_unique(missing, "health-not-ok")
    if not _truthy(chrome_status.get("cdpReady")):
        _add_unique(missing, "flow-cdp-not-ready")
    if page_status.get("ok") is False or (
        not _truthy(page_status.get("url")) and not _truthy(page_status.get("title"))
    ):
        _add_unique(missing, "flow-page-not-ready")
    if page_status.get("loginRequired") or page_status.get("verificationRequired"):
        _add_unique(missing, "flow-auth-not-ready")
    if page_status.get("appErrorDetected"):
        _add_unique(missing, "flow-page-app-error")
    if (
        network_status.get("ok") is False
        and (network_status.get("configured") is True or network_status.get("controllerReady") is True)
    ):
        _add_unique(missing, "flow-network-not-ready")
    elif network_status.get("controllerReady") is False or network_status.get("configured") is False:
        warnings.append("flow-network-controller-not-configured")

    url = str(page_status.get("url") or "")
    prompt_input_count = _int_value(page_status.get("promptInputCount"))
    project_context = bool(re.search(r"/project/[^/?#]+", url))
    edit_context = bool(re.search(r"/project/[^/?#]+/edit/[^/?#]+", url))
    builder_context = project_context and not edit_context and not bool(re.search(r"/project/[^/?#]+/[^?#]+", url))
    submit_ready = len(missing) == 0 and prompt_input_count > 0 and (builder_context or edit_context)
    collect_ready = len(missing) == 0 and project_context

    if not submit_ready:
        _add_unique(missing, "flow-submit-context-not-ready")
    if not collect_ready:
        _add_unique(missing, "flow-collect-context-not-ready")

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "mode": "flow-live-readiness-only",
        "runtime": runtime_url,
        "readyForConfirmedFlowLive": submit_ready,
        "submitReady": submit_ready,
        "collectReady": collect_ready,
        "missing": missing,
        "warnings": warnings,
        "sideEffects": "readiness-only; no Chrome launch, no login, no Flow prompt submit, no result collection",
        "samples": {
            "health": health,
            "authStatus": auth_status,
            "chromeStatus": chrome_status,
            "pageStatus": page_status,
            "networkStatus": network_status,
        },
    }
