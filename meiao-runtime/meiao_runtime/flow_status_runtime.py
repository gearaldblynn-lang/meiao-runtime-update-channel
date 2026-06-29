from __future__ import annotations

from typing import Any, Callable


StatusReader = Callable[[], dict[str, Any]]
NetworkStatusReader = Callable[..., dict[str, Any]]


def status_error(error: BaseException, message: str, ok: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": ok,
        "message": str(error) or message,
        "error": str(error),
    }
    code = getattr(error, "code", "")
    action = getattr(error, "action", "")
    if code:
        payload["code"] = code
    if action:
        payload["action"] = action
    return payload


def _safe_auth_status(auth_status: StatusReader) -> dict[str, Any]:
    try:
        return auth_status()
    except Exception as error:
        return {"platforms": {}, **status_error(error, "Flow auth status returned an invalid payload.")}


def _safe_chrome_status(chrome_status: StatusReader) -> dict[str, Any]:
    try:
        payload = chrome_status()
        return payload if isinstance(payload, dict) else {"ok": False, "message": "Flow runtime status returned an invalid payload."}
    except Exception as error:
        return status_error(error, "Flow runtime status returned an invalid payload.")


def _safe_page_status(page_status: StatusReader) -> dict[str, Any]:
    try:
        payload = page_status()
        return payload if isinstance(payload, dict) else {"ok": False, "message": "Flow page status returned an invalid payload."}
    except Exception as error:
        return status_error(error, "Flow page status returned an invalid payload.")


def _safe_network_status(network_status: NetworkStatusReader) -> dict[str, Any]:
    try:
        payload = network_status()
        return payload if isinstance(payload, dict) else {"ok": True, "message": "Flow network status returned an invalid payload."}
    except Exception as error:
        return status_error(error, "Flow network status returned an invalid payload.", ok=True)


def safe_auth_status(auth_status: StatusReader) -> dict[str, Any]:
    return _safe_auth_status(auth_status)


def safe_chrome_status(chrome_status: StatusReader) -> dict[str, Any]:
    return _safe_chrome_status(chrome_status)


def safe_page_status(page_status: StatusReader) -> dict[str, Any]:
    return _safe_page_status(page_status)


def safe_network_status(network_status: NetworkStatusReader) -> dict[str, Any]:
    return _safe_network_status(network_status)


def safe_statuses(
    *,
    auth_status: StatusReader,
    chrome_status: StatusReader,
    page_status: StatusReader,
    network_status: NetworkStatusReader,
) -> dict[str, dict[str, Any]]:
    return {
        "authStatus": _safe_auth_status(auth_status),
        "chromeStatus": _safe_chrome_status(chrome_status),
        "pageStatus": _safe_page_status(page_status),
        "networkStatus": _safe_network_status(network_status),
    }


def collect_live_preflight_statuses(
    runtime_status: StatusReader,
    page_status: StatusReader,
    network_status: NetworkStatusReader,
) -> dict[str, dict[str, Any]]:
    runtime_payload = runtime_status()
    if not isinstance(runtime_payload, dict):
        runtime_payload = {"ok": False, "message": "Flow runtime status returned an invalid payload."}

    page_payload = page_status()
    if not isinstance(page_payload, dict):
        page_payload = {"ok": False, "message": "Flow page status returned an invalid payload."}

    network_payload = network_status(auto_select=False)
    if not isinstance(network_payload, dict):
        network_payload = {"ok": True, "message": "Flow network status returned an invalid payload."}

    return {
        "runtimeStatus": runtime_payload,
        "pageStatus": page_payload,
        "networkStatus": network_payload,
    }
