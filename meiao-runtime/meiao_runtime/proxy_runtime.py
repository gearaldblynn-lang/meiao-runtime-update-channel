from __future__ import annotations

import inspect
from typing import Awaitable, Callable

from fastapi import Request
from starlette.responses import Response

from .route_helpers import json_response as _json


LegacyProxy = Callable[[Request, dict[str, str]], Awaitable[Response] | Response]


async def invoke(legacy_proxy: LegacyProxy | None, request: Request, headers: dict[str, str]) -> Response:
    if legacy_proxy is None:
        return _json(501, {"error": "Legacy proxy unavailable."}, headers)
    result = legacy_proxy(request, headers)
    if inspect.isawaitable(result):
        return await result
    return result
