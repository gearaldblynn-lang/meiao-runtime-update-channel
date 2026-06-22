from __future__ import annotations

from pathlib import Path
from typing import Any

from . import flow_action_runtime, flow_control_runtime, flow_response_runtime


def auth_open_login_http(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    data = flow_response_runtime.payload_dict(payload)
    try:
        source_url = str(data.get("url") or "").strip() or None
        platform_key = str(data.get("platformKey") or "").strip() or None
        if platform_key is None:
            platform_key = flow_control_runtime.detect_platform_key(legacy_globals, source_url)
        result = flow_control_runtime.open_login(
            legacy_globals,
            platform_key,
            source_url,
            str(data.get("mode") or "dedicated").strip(),
        )
        return 200, flow_response_runtime.ok_result(result)
    except Exception as exc:
        flow_response_runtime.log_flow_error(legacy_globals, "api.open_login.error", exc)
        if flow_response_runtime.is_ingest_error(legacy_globals, exc):
            return 409, flow_response_runtime.ingest_error_payload(exc)
        return 500, {"error": str(exc)}


def chrome_start_http(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    data = flow_response_runtime.payload_dict(payload)
    try:
        result = flow_control_runtime.start_chrome(
            legacy_globals,
            bool(data.get("forceRestart")),
            str(data.get("profileDirectory") or legacy_globals.get("FLOW_CHROME_PROFILE") or ""),
        )
        return 200, flow_response_runtime.ok_result(result)
    except Exception as exc:
        flow_response_runtime.log_flow_error(legacy_globals, "api.flow.chrome.start.error", exc)
        if flow_response_runtime.is_ingest_error(legacy_globals, exc):
            return 409, flow_response_runtime.ingest_error_payload(exc)
        return 500, {"error": str(exc)}


def chrome_open_http(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    data = flow_response_runtime.payload_dict(payload)
    try:
        result = flow_control_runtime.open_url(
            legacy_globals,
            bool(data.get("forceRestart")),
            str(data.get("profileDirectory") or legacy_globals.get("FLOW_CHROME_PROFILE") or ""),
            str(data.get("url") or "").strip() or None,
        )
        return 200, flow_response_runtime.ok_result(result)
    except Exception as exc:
        flow_response_runtime.log_flow_error(legacy_globals, "api.flow.chrome.open.error", exc)
        if flow_response_runtime.is_ingest_error(legacy_globals, exc):
            return 409, flow_response_runtime.ingest_error_payload(exc)
        return 500, {"error": str(exc)}


def account_login_http(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    data = flow_response_runtime.payload_dict(payload)
    try:
        result = flow_control_runtime.account_login(
            legacy_globals,
            str(data.get("email") or ""),
            str(data.get("password") or ""),
            str(data.get("profileDirectory") or legacy_globals.get("FLOW_CHROME_PROFILE") or ""),
            bool(data.get("forceRestart")),
        )
        return 200, flow_response_runtime.ok_result(result)
    except Exception as exc:
        flow_response_runtime.log_flow_error(legacy_globals, "api.flow.account.login.error", exc)
        if flow_response_runtime.is_ingest_error(legacy_globals, exc):
            return 409, flow_response_runtime.ingest_error_payload(exc)
        return 500, {"error": str(exc)}


def account_continue_http(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    data = flow_response_runtime.payload_dict(payload)
    try:
        result = flow_control_runtime.account_continue(
            legacy_globals,
            str(data.get("email") or ""),
            str(data.get("profileDirectory") or legacy_globals.get("FLOW_CHROME_PROFILE") or ""),
        )
        return 200, flow_response_runtime.ok_result(result)
    except Exception as exc:
        flow_response_runtime.log_flow_error(legacy_globals, "api.flow.account.continue.error", exc)
        if flow_response_runtime.is_ingest_error(legacy_globals, exc):
            return 409, flow_response_runtime.ingest_error_payload(exc)
        return 500, {"error": str(exc)}


def network_select_http(legacy_globals: dict[str, Any], payload: Any = None) -> tuple[int, dict[str, Any]]:
    try:
        result = flow_control_runtime.select_network(legacy_globals)
        return 200, flow_response_runtime.ok_result(result)
    except Exception as exc:
        flow_response_runtime.log_flow_error(legacy_globals, "api.flow.network.select.error", exc)
        if flow_response_runtime.is_ingest_error(legacy_globals, exc):
            return 409, flow_response_runtime.ingest_error_payload(exc)
        return 500, {"error": str(exc)}


def start_project_http(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    data = flow_response_runtime.payload_dict(payload)
    try:
        result = flow_control_runtime.start_project(legacy_globals, bool(data.get("forceNew")))
        if isinstance(result, dict) and not result.get("ok"):
            ingest_error_type = legacy_globals.get("IngestError")
            if isinstance(ingest_error_type, type):
                raise ingest_error_type(
                    str(result.get("message") or "Flow project page is not ready."),
                    "FLOW_PROJECT_NOT_READY",
                    "OPEN_FLOW",
                )
        return 200, flow_response_runtime.ok_result(result)
    except Exception as exc:
        flow_response_runtime.log_flow_error(legacy_globals, "api.flow.page.start_project.error", exc)
        if flow_response_runtime.is_ingest_error(legacy_globals, exc):
            return 409, flow_response_runtime.ingest_error_payload(exc)
        return 500, {"error": str(exc)}


def prepare_normal_dialog_http(legacy_globals: dict[str, Any], payload: Any = None) -> tuple[int, dict[str, Any]]:
    try:
        result = flow_control_runtime.require_normal_dialog(legacy_globals, "prepare-normal-dialog")
        return 200, flow_response_runtime.ok_result(result)
    except Exception as exc:
        flow_response_runtime.log_flow_error(legacy_globals, "api.flow.page.prepare_normal_dialog.error", exc)
        return 500, {"error": str(exc)}


def require_normal_dialog_http(legacy_globals: dict[str, Any], payload: Any = None) -> tuple[int, dict[str, Any]]:
    try:
        result = flow_control_runtime.require_normal_dialog(legacy_globals, "debug-require-normal-dialog")
        return 200, flow_response_runtime.ok_result(result)
    except Exception as exc:
        flow_response_runtime.log_flow_error(legacy_globals, "api.flow.page.require_normal_dialog.error", exc)
        if flow_response_runtime.is_ingest_error(legacy_globals, exc):
            return 409, flow_response_runtime.ingest_error_payload(exc)
        return 500, {"error": str(exc)}


def set_prompt_http(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    data = flow_response_runtime.payload_dict(payload)
    try:
        config = data.get("config")
        result = flow_action_runtime.set_prompt(
            legacy_globals,
            str(data.get("prompt") or ""),
            config if isinstance(config, dict) else None,
        )
        if not isinstance(result, dict):
            result = {"result": result}
        return (200 if result.get("ok") else 409), result
    except Exception as exc:
        flow_response_runtime.log_flow_error(legacy_globals, "api.flow.page.set_prompt.error", exc)
        if flow_response_runtime.is_ingest_error(legacy_globals, exc):
            return 409, flow_response_runtime.ingest_error_payload(exc)
        return 500, {"error": str(exc)}


def click_submit_http(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    data = flow_response_runtime.payload_dict(payload)
    try:
        expected_count = int(data.get("expectedCount") or 0)
        result = flow_action_runtime.click_submit(legacy_globals, expected_count)
        if not isinstance(result, dict):
            result = {"result": result}
        return (200 if result.get("ok") else 409), result
    except Exception as exc:
        flow_response_runtime.log_flow_error(legacy_globals, "api.flow.page.click_submit.error", exc)
        if flow_response_runtime.is_ingest_error(legacy_globals, exc):
            return 409, flow_response_runtime.ingest_error_payload(exc)
        return 500, {"error": str(exc)}


def bind_reference_http(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    data = flow_response_runtime.payload_dict(payload)
    raw_files = data.get("files")
    files = [Path(str(item)) for item in raw_files] if isinstance(raw_files, list) else []
    try:
        result = flow_action_runtime.bind_reference(legacy_globals, files)
        if not isinstance(result, dict):
            result = {"result": result}
        return (200 if result.get("ok") else 409), result
    except Exception as exc:
        flow_response_runtime.log_flow_error(legacy_globals, "api.flow.page.bind_reference.error", exc)
        if flow_response_runtime.is_ingest_error(legacy_globals, exc):
            return 409, flow_response_runtime.ingest_error_payload(exc)
        return 500, {"error": str(exc)}


def prepare_reference_images_http(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    data = flow_response_runtime.payload_dict(payload)
    reference_images = data.get("referenceImages")
    job_id = str(data.get("jobId") or "") if isinstance(data, dict) else ""
    try:
        result = flow_action_runtime.prepare_reference_images(
            legacy_globals,
            reference_images if isinstance(reference_images, list) else [],
            job_id=job_id,
        )
        if not isinstance(result, dict):
            result = {"result": result}
        return 200, result
    except Exception as exc:
        flow_response_runtime.log_flow_error(legacy_globals, "api.flow.page.prepare_reference_images.error", exc)
        if flow_response_runtime.is_ingest_error(legacy_globals, exc):
            return 409, flow_response_runtime.ingest_error_payload(exc)
        return 500, {"error": str(exc)}


def submit_prompt_http(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    data = flow_response_runtime.payload_dict(payload)
    config = data.get("config")
    reference_images = data.get("referenceImages")
    try:
        result = flow_action_runtime.submit_prompt(
            legacy_globals,
            str(data.get("prompt") or ""),
            config if isinstance(config, dict) else None,
            reference_images if isinstance(reference_images, list) else None,
            str(data.get("jobId") or "").strip() or None,
            str(data.get("stage") or "").strip() or None,
            str(data.get("slotId") or "").strip() or None,
            flow_response_runtime.slot_index(data),
        )
        return 200, result if isinstance(result, dict) else {"result": result}
    except Exception as exc:
        if flow_response_runtime.is_ingest_error(legacy_globals, exc):
            flow_response_runtime.write_submit_error_progress(
                legacy_globals,
                data,
                config,
                str(exc),
                str(getattr(exc, "code", "") or type(exc).__name__),
            )
            append_debug_log = legacy_globals.get("append_debug_log")
            if callable(append_debug_log):
                append_debug_log(
                    "api.flow.page.submit_prompt.error",
                    {"error": str(exc), "code": getattr(exc, "code", "")},
                )
            return 409, {
                "error": str(exc),
                "code": getattr(exc, "code", ""),
                "action": getattr(exc, "action", ""),
            }
        flow_response_runtime.write_submit_error_progress(legacy_globals, data, config, str(exc), type(exc).__name__)
        append_debug_log = legacy_globals.get("append_debug_log")
        if callable(append_debug_log):
            append_debug_log(
                "api.flow.page.submit_prompt.error",
                {"errorType": type(exc).__name__, "error": str(exc)},
            )
        return 500, {"error": str(exc)}


def collect_results_http(legacy_globals: dict[str, Any], payload: Any) -> tuple[int, dict[str, Any]]:
    data = flow_response_runtime.payload_dict(payload)
    try:
        result = flow_action_runtime.collect_results(
            legacy_globals,
            str(data.get("jobId") or "").strip() or None,
            str(data.get("prompt") or "").strip() or None,
            str(data.get("assetType") or "image").strip(),
        )
        return 200, result if isinstance(result, dict) else {"result": result}
    except Exception as exc:
        if flow_response_runtime.is_ingest_error(legacy_globals, exc):
            append_debug_log = legacy_globals.get("append_debug_log")
            if callable(append_debug_log):
                append_debug_log(
                    "api.flow.page.collect_results.error",
                    {"error": str(exc), "code": getattr(exc, "code", "")},
                )
            return 409, {
                "error": str(exc),
                "code": getattr(exc, "code", ""),
                "action": getattr(exc, "action", ""),
            }
        append_debug_log = legacy_globals.get("append_debug_log")
        if callable(append_debug_log):
            append_debug_log(
                "api.flow.page.collect_results.error",
                {"errorType": type(exc).__name__, "error": str(exc)},
            )
        return 500, {"error": str(exc)}

