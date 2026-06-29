from __future__ import annotations

import copy
import threading
import time
from typing import Any

from . import flow_readiness_runtime, flow_status_runtime


QUICK_NETWORK_STATUS_CACHE_TTL_SECONDS = 2.0
QUICK_NETWORK_STATUS_CACHE_LOCK = threading.RLock()
QUICK_NETWORK_STATUS_CACHE: dict[str, Any] = {"expires_at": 0.0, "key": None, "payload": None}
PASSIVE_PAGE_STATUS_CACHE_TTL_SECONDS = 0.5
PASSIVE_PAGE_STATUS_CACHE_LOCK = threading.RLock()
PASSIVE_PAGE_STATUS_CACHE: dict[str, Any] = {"expires_at": 0.0, "key": None, "payload": None}


def _callable(legacy_globals: dict[str, Any], name: str) -> Any:
    value = legacy_globals.get(name)
    if not callable(value):
        raise RuntimeError(f"Legacy callable {name} is unavailable.")
    return value


def auth_status(legacy_globals: dict[str, Any]) -> dict[str, Any]:
    return {"platforms": _callable(legacy_globals, "get_auth_status")()}


def chrome_status(legacy_globals: dict[str, Any]) -> dict[str, Any]:
    return _callable(legacy_globals, "flow_runtime_status")()


def network_status(legacy_globals: dict[str, Any], *, deep: bool = False) -> dict[str, Any]:
    if not deep:
        return quick_network_status(legacy_globals)
    return _callable(legacy_globals, "flow_network_status")(auto_select=False)


def clear_quick_network_status_cache() -> None:
    with QUICK_NETWORK_STATUS_CACHE_LOCK:
        QUICK_NETWORK_STATUS_CACHE.update({"expires_at": 0.0, "key": None, "payload": None})


def quick_network_status(legacy_globals: dict[str, Any]) -> dict[str, Any]:
    cache_key = id(legacy_globals)
    with QUICK_NETWORK_STATUS_CACHE_LOCK:
        now = time.monotonic()
        cached = QUICK_NETWORK_STATUS_CACHE.get("payload")
        if cached is not None and QUICK_NETWORK_STATUS_CACHE.get("key") == cache_key and float(QUICK_NETWORK_STATUS_CACHE.get("expires_at") or 0.0) > now:
            return copy.deepcopy(cached)
        payload = _read_quick_network_status(legacy_globals)
        QUICK_NETWORK_STATUS_CACHE.update({"expires_at": time.monotonic() + QUICK_NETWORK_STATUS_CACHE_TTL_SECONDS, "key": cache_key, "payload": copy.deepcopy(payload)})
        return payload


def _read_quick_network_status(legacy_globals: dict[str, Any]) -> dict[str, Any]:
    detected = _callable(legacy_globals, "detect_clash_controller")()
    if not detected:
        return {
            "ok": True,
            "controllerReady": False,
            "configured": False,
            "quick": True,
            "message": "未检测到 Clash/Mihomo 控制接口。可在 config.local.json 配置 clash.controller 和 clash.secret。",
            "usableCount": 0,
            "candidateCount": 0,
            "blockedCount": 0,
            "testedCount": 0,
            "bestLatencyMs": None,
            "selected": False,
            "flowValidation": None,
        }

    proxies = detected.get("proxies") if isinstance(detected, dict) else {}
    config = detected.get("config") if isinstance(detected, dict) else {}
    proxies = proxies if isinstance(proxies, dict) else {}
    config = config if isinstance(config, dict) else {}
    is_real_clash_node = _callable(legacy_globals, "is_real_clash_node")
    is_excluded_clash_node = _callable(legacy_globals, "is_excluded_clash_node")
    pick_clash_group = _callable(legacy_globals, "pick_clash_group")
    candidates = [name for name, proxy in proxies.items() if is_real_clash_node(name, proxy)]
    blocked_count = len([
        name
        for name, proxy in proxies.items()
        if name and is_excluded_clash_node(name) and not isinstance(proxy.get("all"), list)
    ])
    group = pick_clash_group(proxies, candidates, config.get("group") or "")
    return {
        "ok": True,
        "controllerReady": True,
        "configured": True,
        "quick": True,
        "controller": detected.get("controller"),
        "groupReady": bool(group),
        "groupName": group,
        "usableCount": len(candidates),
        "candidateCount": len(candidates),
        "blockedCount": blocked_count,
        "testedCount": 0,
        "bestLatencyMs": None,
        "selected": False,
        "selectedNode": None,
        "flowValidation": None,
        "message": (
            f"已检测到 {len(candidates)} 个候选节点；提交前会自动测速并验证 Flow/recaptcha 连通性。"
            if candidates
            else "已连接 Clash/Mihomo，但未检测到候选海外节点。"
        ),
    }


def page_status(legacy_globals: dict[str, Any]) -> dict[str, Any]:
    return _callable(legacy_globals, "flow_page_status")()


def clear_passive_page_status_cache() -> None:
    with PASSIVE_PAGE_STATUS_CACHE_LOCK:
        PASSIVE_PAGE_STATUS_CACHE.update({"expires_at": 0.0, "key": None, "payload": None})


def passive_page_status(legacy_globals: dict[str, Any]) -> dict[str, Any]:
    cache_key = id(legacy_globals)
    with PASSIVE_PAGE_STATUS_CACHE_LOCK:
        now = time.monotonic()
        cached = PASSIVE_PAGE_STATUS_CACHE.get("payload")
        if cached is not None and PASSIVE_PAGE_STATUS_CACHE.get("key") == cache_key and float(PASSIVE_PAGE_STATUS_CACHE.get("expires_at") or 0.0) > now:
            return copy.deepcopy(cached)
        payload = flow_status_runtime.safe_page_status(lambda: page_status(legacy_globals))
        if payload.get("ok") is False and payload.get("code") == "FLOW_PAGE_NOT_READY":
            PASSIVE_PAGE_STATUS_CACHE.update({
                "expires_at": time.monotonic() + PASSIVE_PAGE_STATUS_CACHE_TTL_SECONDS,
                "key": cache_key,
                "payload": copy.deepcopy(payload),
            })
        else:
            PASSIVE_PAGE_STATUS_CACHE.update({"expires_at": 0.0, "key": None, "payload": None})
        return payload



def prompt_media_status(legacy_globals: dict[str, Any], expected_count: int) -> dict[str, Any]:
    return _callable(legacy_globals, "flow_prompt_media_status")(expected_count)


def prompt_input_snapshot(legacy_globals: dict[str, Any], label: str) -> dict[str, Any]:
    return _callable(legacy_globals, "flow_prompt_input_snapshot")(label)


def progress(legacy_globals: dict[str, Any], job_id: str | None) -> dict[str, Any]:
    return _callable(legacy_globals, "get_flow_progress")(job_id)


def click_targets(legacy_globals: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "targets": _callable(legacy_globals, "flow_page_click_targets")()}


def live_readiness(legacy_globals: dict[str, Any], runtime_url: str = "") -> dict[str, Any]:
    chrome_payload: dict[str, Any] = {}

    def read_chrome_status() -> dict[str, Any]:
        nonlocal chrome_payload
        chrome_payload = chrome_status(legacy_globals)
        return chrome_payload

    def read_page_status() -> dict[str, Any]:
        if not chrome_payload.get("cdpReady"):
            return {
                "ok": False,
                "skipped": True,
                "code": "FLOW_PAGE_NOT_READY",
                "message": "Skipped because Flow Chrome CDP is not ready.",
            }
        return page_status(legacy_globals)

    def read_network_status() -> dict[str, Any]:
        if not chrome_payload.get("cdpReady"):
            return {
                "ok": True,
                "skipped": True,
                "message": "Skipped because Flow Chrome CDP is not ready.",
            }
        return network_status(legacy_globals, deep=True)

    statuses = flow_status_runtime.safe_statuses(
        auth_status=lambda: auth_status(legacy_globals),
        chrome_status=read_chrome_status,
        page_status=read_page_status,
        network_status=read_network_status,
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
