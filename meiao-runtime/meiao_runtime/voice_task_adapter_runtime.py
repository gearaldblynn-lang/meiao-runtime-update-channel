from __future__ import annotations

from typing import Any

from . import voice_runtime


def create_elevenlabs_voice_task(legacy_globals: dict[str, Any], payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    return voice_runtime.create(legacy_globals, payload)
