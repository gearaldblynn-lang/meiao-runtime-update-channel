from __future__ import annotations

from fastapi import Request
from starlette.responses import Response

from .proxy_runtime import LegacyProxy, invoke


async def select_video_folder(legacy_proxy: LegacyProxy | None, request: Request, headers: dict[str, str]) -> Response:
    return await invoke(legacy_proxy, request, headers)


async def select_export_folder(legacy_proxy: LegacyProxy | None, request: Request, headers: dict[str, str]) -> Response:
    return await invoke(legacy_proxy, request, headers)


async def select_capcut_executable(legacy_proxy: LegacyProxy | None, request: Request, headers: dict[str, str]) -> Response:
    return await invoke(legacy_proxy, request, headers)


async def open_local_path(legacy_proxy: LegacyProxy | None, request: Request, headers: dict[str, str]) -> Response:
    return await invoke(legacy_proxy, request, headers)
