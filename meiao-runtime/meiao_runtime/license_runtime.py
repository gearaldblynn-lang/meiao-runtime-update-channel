from __future__ import annotations

from typing import Any


def _callable(legacy_globals: dict[str, Any], name: str) -> Any:
    value = legacy_globals.get(name)
    if not callable(value):
        raise RuntimeError(f"Legacy callable {name} is unavailable.")
    return value


def activate(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    if not isinstance(payload, dict):
        payload = {}
    account = str(payload.get("account") or "").strip()
    license_key = str(payload.get("licenseKey") or payload.get("license_key") or "").strip()
    if not account or not license_key:
        return 400, {"error": "请输入账号和授权码。"}
    return 200, _callable(legacy_globals, "activate_license")(account, license_key)


def rebind(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    if not isinstance(payload, dict):
        payload = {}
    account = str(payload.get("account") or "").strip()
    license_key = str(payload.get("licenseKey") or payload.get("license_key") or "").strip()
    if not account or not license_key:
        return 400, {"error": "请输入账号和授权码。"}
    usage = _callable(legacy_globals, "get_rebind_usage")(account)
    if usage["used"] >= usage["limit"]:
        return 429, {
            "error": f"本月换绑次数已达上限（{usage['limit']} 次）。",
            "code": "REBIND_LIMIT_REACHED",
            "rebindUsedThisMonth": usage["used"],
            "rebindLimitPerMonth": usage["limit"],
            "rebindRemainingThisMonth": usage["remaining"],
        }
    result = _callable(legacy_globals, "activate_license")(account, license_key)
    status = _callable(legacy_globals, "get_license_status")(force_online=False)
    if not status.get("active"):
        return 409, {
            "error": "换绑未生效，请检查账号、授权码和设备绑定。",
            "code": "LICENSE_REBIND_FAILED",
            "rebindUsedThisMonth": usage["used"],
            "rebindLimitPerMonth": usage["limit"],
            "rebindRemainingThisMonth": usage["remaining"],
        }
    usage_next = _callable(legacy_globals, "record_rebind_usage")(account)
    status.update(
        {
            "rebindUsedThisMonth": usage_next["used"],
            "rebindLimitPerMonth": usage_next["limit"],
            "rebindRemainingThisMonth": usage_next["remaining"],
        }
    )
    return 200, status


def verify(legacy_globals: dict[str, Any]) -> dict[str, Any]:
    return _callable(legacy_globals, "get_license_status")(force_online=True, allow_throttle=False)


def logout(legacy_globals: dict[str, Any]) -> dict[str, Any]:
    _callable(legacy_globals, "clear_license_state")()
    return {"active": False, "loggedOut": True, **_callable(legacy_globals, "get_license_status")(force_online=False)}
