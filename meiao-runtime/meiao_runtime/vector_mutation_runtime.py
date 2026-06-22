from __future__ import annotations

from fastapi import Request
from starlette.responses import Response

from .proxy_runtime import LegacyProxy, invoke


async def embed_scenes(legacy_proxy: LegacyProxy | None, request: Request, headers: dict[str, str]) -> Response:
    return await invoke(legacy_proxy, request, headers)


async def tag_tasks_submit(legacy_proxy: LegacyProxy | None, request: Request, headers: dict[str, str]) -> Response:
    return await invoke(legacy_proxy, request, headers)


async def tag_tasks_cancel(legacy_proxy: LegacyProxy | None, request: Request, headers: dict[str, str]) -> Response:
    return await invoke(legacy_proxy, request, headers)


async def prune(legacy_proxy: LegacyProxy | None, request: Request, headers: dict[str, str]) -> Response:
    return await invoke(legacy_proxy, request, headers)
