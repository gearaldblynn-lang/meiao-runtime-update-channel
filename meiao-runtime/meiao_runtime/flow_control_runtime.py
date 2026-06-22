from __future__ import annotations

from typing import Any

from .route_helpers import callable_or_raise as _callable


def detect_platform_key(legacy_globals: dict[str, Any], source_url: str | None) -> Any:
    return _callable(legacy_globals, "detect_platform_key")(source_url)


def open_login(
    legacy_globals: dict[str, Any],
    platform_key: str | None,
    source_url: str | None,
    mode: str,
) -> Any:
    return _callable(legacy_globals, "open_login_window")(platform_key, source_url, mode)


def start_chrome(legacy_globals: dict[str, Any], force_restart: bool, profile_directory: str) -> Any:
    return _callable(legacy_globals, "start_flow_chrome")(
        force_restart=force_restart,
        profile_directory=profile_directory,
    )


def open_url(
    legacy_globals: dict[str, Any],
    force_restart: bool,
    profile_directory: str,
    url: str | None,
) -> Any:
    return _callable(legacy_globals, "open_flow_url")(
        force_restart=force_restart,
        profile_directory=profile_directory,
        url=url,
    )


def account_login(
    legacy_globals: dict[str, Any],
    email: str,
    password: str,
    profile_directory: str,
    force_restart: bool,
) -> Any:
    return _callable(legacy_globals, "flow_google_login")(
        email=email,
        password=password,
        profile_directory=profile_directory,
        force_restart=force_restart,
    )


def account_continue(legacy_globals: dict[str, Any], email: str, profile_directory: str) -> Any:
    return _callable(legacy_globals, "flow_continue_google_login")(
        email=email,
        profile_directory=profile_directory,
    )


def select_network(legacy_globals: dict[str, Any]) -> Any:
    return _callable(legacy_globals, "flow_network_status")(auto_select=True)


def start_project(legacy_globals: dict[str, Any], force_new: bool) -> Any:
    return _callable(legacy_globals, "flow_start_project")(force_new=force_new)


def require_normal_dialog(legacy_globals: dict[str, Any], label: str) -> Any:
    return _callable(legacy_globals, "flow_require_normal_composer_fast")(label)
