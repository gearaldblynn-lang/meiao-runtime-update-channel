from __future__ import annotations

from typing import Any

from .route_helpers import callable_or_raise as _callable


def _admin_payload(legacy_globals: dict[str, Any], payload: Any) -> tuple[dict[str, Any], str, str]:
    data = payload if isinstance(payload, dict) else {}
    token = str(data.get("adminToken") or data.get("admin_token") or "").strip()
    device = _callable(legacy_globals, "get_license_device")()
    device_id = str(data.get("deviceId") or device.get("deviceId") or "").strip()
    return data, token, device_id


def _admin_license_items(result: Any) -> list[Any]:
    if isinstance(result, list):
        return result
    if not isinstance(result, dict):
        return []
    for key in ("licenses", "items", "data"):
        value = result.get(key)
        if isinstance(value, list):
            return value
    return []


def login(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    data = payload if isinstance(payload, dict) else {}
    account = str(data.get("account") or "").strip()
    admin_key = str(data.get("adminKey") or data.get("admin_key") or "").strip()
    if not account or not admin_key:
        return 400, {"error": "请输入主管理账号和管理密钥。"}

    device = _callable(legacy_globals, "get_license_device")()
    result = _callable(legacy_globals, "login_admin_with_auto_rebind")(account, admin_key, device)
    token = _callable(legacy_globals, "admin_result_token")(result)
    if not token:
        code = str(result.get("code") or "INVALID_ADMIN")
        message = str(result.get("message") or "主管理账号或管理密钥无效。")
        if code == "admin_device_mismatch":
            message = "主管理密钥已通过校验，但当前设备和云端绑定的主管理设备不一致。已尝试自动换绑，如仍失败请检查云端是否已部署 admin_rebind_device。"
        return 409, {
            "error": message,
            "code": code,
            "deviceId": device.get("deviceId", ""),
            "deviceName": device.get("deviceName", ""),
        }

    _callable(legacy_globals, "write_admin_session_state")(
        {
            "adminToken": token,
            "account": result.get("account") or account,
            "expiresAt": result.get("expires_at") or result.get("expiresAt") or "",
            "deviceId": device.get("deviceId", ""),
            "rebound": bool(result.get("rebound")),
            "updatedAt": _callable(legacy_globals, "utc_now_iso")(),
        }
    )
    return 200, result


def session(legacy_globals: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    session_state = _callable(legacy_globals, "read_admin_session_state")()
    token = str(session_state.get("adminToken") or "").strip()
    device_id = str(session_state.get("deviceId") or _callable(legacy_globals, "get_license_device")().get("deviceId") or "").strip()
    if not token:
        return 200, {"active": False}

    try:
        result = _callable(legacy_globals, "call_supabase_rpc")("admin_list_licenses", {"p_admin_token": token, "p_device_id": device_id})
        licenses = _admin_license_items(result)
        try:
            _callable(legacy_globals, "write_admin_session_state")(
                {
                    **session_state,
                    "adminToken": token,
                    "account": session_state.get("account", ""),
                    "expiresAt": session_state.get("expiresAt", ""),
                    "deviceId": device_id,
                    "licenses": licenses,
                    "updatedAt": _callable(legacy_globals, "utc_now_iso")(),
                }
            )
        except Exception:
            pass
        return 200, {"active": True, "adminToken": token, "account": session_state.get("account", ""), "expiresAt": session_state.get("expiresAt", ""), "licenses": licenses}
    except Exception as error:
        return 200, {
            "active": True,
            "adminToken": token,
            "account": session_state.get("account", ""),
            "expiresAt": session_state.get("expiresAt", ""),
            "licenses": session_state.get("licenses", []),
            "syncError": str(error),
        }


def logout(legacy_globals: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    _callable(legacy_globals, "clear_admin_session_state")()
    return 200, {"active": False, "loggedOut": True}


def list_licenses(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    _data, token, device_id = _admin_payload(legacy_globals, payload)
    result = _callable(legacy_globals, "call_supabase_rpc")("admin_list_licenses", {"p_admin_token": token, "p_device_id": device_id})
    return 200, result


def create(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    data, token, device_id = _admin_payload(legacy_globals, payload)
    account = str(data.get("account") or "").strip()
    if not account:
        return 400, {"error": "请输入被授权账号。"}
    result = _callable(legacy_globals, "call_supabase_rpc")(
        "admin_create_license",
        {
            "p_admin_token": token,
            "p_device_id": device_id,
            "p_account": account,
            "p_label": str(data.get("label") or "").strip(),
            "p_expires_at": data.get("expiresAt") or data.get("expires_at") or None,
            "p_max_devices": int(data.get("maxDevices") or data.get("max_devices") or 1),
            "p_offline_grace_hours": int(data.get("offlineGraceHours") or data.get("offline_grace_hours") or 72),
            "p_license_key": str(data.get("licenseKey") or data.get("license_key") or "").strip() or None,
        },
    )
    return 200, result


def set_status(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    data, token, device_id = _admin_payload(legacy_globals, payload)
    license_id = str(data.get("licenseId") or data.get("license_id") or "").strip()
    status = str(data.get("status") or "").strip()
    if not license_id or status not in {"active", "disabled"}:
        return 400, {"error": "授权 ID 或状态无效。"}
    result = _callable(legacy_globals, "call_supabase_rpc")(
        "admin_set_license_status",
        {"p_admin_token": token, "p_device_id": device_id, "p_license_id": license_id, "p_status": status},
    )
    return 200, result


def reset_devices(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    data, token, device_id = _admin_payload(legacy_globals, payload)
    license_id = str(data.get("licenseId") or data.get("license_id") or "").strip()
    if not license_id:
        return 400, {"error": "缺少授权 ID。"}
    result = _callable(legacy_globals, "call_supabase_rpc")(
        "admin_reset_license_devices",
        {"p_admin_token": token, "p_device_id": device_id, "p_license_id": license_id},
    )
    return 200, result
