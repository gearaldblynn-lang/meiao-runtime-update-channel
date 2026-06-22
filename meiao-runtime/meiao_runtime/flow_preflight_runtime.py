from __future__ import annotations

from typing import Any

from . import flow_context_runtime

FLOW_CDP_NOT_READY = "FLOW_CDP_NOT_READY"
FLOW_PAGE_NOT_READY = "FLOW_PAGE_NOT_READY"
FLOW_AUTH_NOT_READY = "FLOW_AUTH_NOT_READY"
FLOW_NETWORK_NOT_READY = "FLOW_NETWORK_NOT_READY"
FLOW_PROJECT_NOT_READY = "FLOW_PROJECT_NOT_READY"
FLOW_PROJECT_CONTEXT_REQUIRED = "FLOW_PROJECT_CONTEXT_REQUIRED"


def _preflight_error(
    code: str,
    message: str,
    action: str,
    field: str,
    status: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    return 409, {
        "error": f"[{code}] {message}",
        "code": code,
        "action": action,
        field: status,
    }


def evaluate_live_preflight(
    runtime_status: dict[str, Any],
    page_status: dict[str, Any],
    network_status: dict[str, Any],
    action: str,
) -> tuple[int, dict[str, Any]] | None:
    if not bool(runtime_status.get("cdpReady") or runtime_status.get("cdp_ready")):
        return _preflight_error(
            FLOW_CDP_NOT_READY,
            "Flow Chrome CDP is not ready.",
            action,
            "runtimeStatus",
            runtime_status,
        )

    url = str(page_status.get("url") or "").strip()
    title = str(page_status.get("title") or "").strip()
    if page_status.get("ok") is False or (not url and not title):
        return _preflight_error(
            FLOW_PAGE_NOT_READY,
            "Flow page is not ready.",
            action,
            "pageStatus",
            page_status,
        )

    if page_status.get("loginRequired") or page_status.get("verificationRequired"):
        return _preflight_error(
            FLOW_AUTH_NOT_READY,
            "Flow login or verification is required.",
            action,
            "pageStatus",
            page_status,
        )

    if (
        network_status.get("ok") is False
        and (network_status.get("configured") is True or network_status.get("controllerReady") is True)
    ):
        return _preflight_error(
            FLOW_NETWORK_NOT_READY,
            "Flow network route is not ready.",
            action,
            "networkStatus",
            network_status,
        )

    if action == "flow-submit-prompt":
        if not flow_context_runtime.flow_page_submission_ready(page_status):
            return _preflight_error(
                FLOW_PROJECT_NOT_READY,
                "Flow project prompt page is not ready.",
                action,
                "pageStatus",
                page_status,
            )
    elif action == "flow-collect-results":
        if not flow_context_runtime.flow_url_is_project_context(url):
            return _preflight_error(
                FLOW_PROJECT_CONTEXT_REQUIRED,
                "Flow collect requires a project context page.",
                action,
                "pageStatus",
                page_status,
            )

    return None
