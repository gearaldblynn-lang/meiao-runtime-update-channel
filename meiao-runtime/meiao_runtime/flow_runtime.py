from __future__ import annotations

from typing import Any

from . import flow_readiness_runtime, flow_status_runtime


def _callable(legacy_globals: dict[str, Any], name: str) -> Any:
    value = legacy_globals.get(name)
    if not callable(value):
        raise RuntimeError(f"Legacy callable {name} is unavailable.")
    return value


def auth_status(legacy_globals: dict[str, Any]) -> dict[str, Any]:
    return {"platforms": _callable(legacy_globals, "get_auth_status")()}


def chrome_status(legacy_globals: dict[str, Any]) -> dict[str, Any]:
    return _callable(legacy_globals, "flow_runtime_status")()


def network_status(legacy_globals: dict[str, Any]) -> dict[str, Any]:
    return _callable(legacy_globals, "flow_network_status")(auto_select=False)


def page_status(legacy_globals: dict[str, Any]) -> dict[str, Any]:
    return _callable(legacy_globals, "flow_page_status")()


def prompt_media_status(legacy_globals: dict[str, Any], expected_count: int) -> dict[str, Any]:
    return _callable(legacy_globals, "flow_prompt_media_status")(expected_count)


def prompt_input_snapshot(legacy_globals: dict[str, Any], label: str) -> dict[str, Any]:
    return _callable(legacy_globals, "flow_prompt_input_snapshot")(label)


def progress(legacy_globals: dict[str, Any], job_id: str | None) -> dict[str, Any]:
    return _callable(legacy_globals, "get_flow_progress")(job_id)


def click_targets(legacy_globals: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "targets": _callable(legacy_globals, "flow_page_click_targets")()}


def live_readiness(legacy_globals: dict[str, Any], runtime_url: str = "") -> dict[str, Any]:
    statuses = flow_status_runtime.safe_statuses(
        auth_status=lambda: auth_status(legacy_globals),
        chrome_status=lambda: chrome_status(legacy_globals),
        page_status=lambda: page_status(legacy_globals),
        network_status=lambda: network_status(legacy_globals),
    )
    return flow_readiness_runtime.evaluate_live_readiness(
        health={"status": "ok"},
        auth_status=statuses["authStatus"],
        chrome_status=statuses["chromeStatus"],
        page_status=statuses["pageStatus"],
        network_status=statuses["networkStatus"],
        runtime_url=runtime_url,
    )


def collect_live_preflight_statuses(legacy_globals: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return flow_status_runtime.collect_live_preflight_statuses(
        _callable(legacy_globals, "flow_runtime_status"),
        _callable(legacy_globals, "flow_page_status"),
        _callable(legacy_globals, "flow_network_status"),
    )
