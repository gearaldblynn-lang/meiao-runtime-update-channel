from __future__ import annotations

from pathlib import Path
from typing import Any

from .route_helpers import callable_or_raise as _callable


def set_prompt(legacy_globals: dict[str, Any], prompt: str, config: dict[str, Any] | None = None) -> Any:
    return _callable(legacy_globals, "flow_set_current_prompt")(prompt, config)


def click_submit(legacy_globals: dict[str, Any], expected_count: int = 0) -> Any:
    return _callable(legacy_globals, "flow_click_current_prompt_submit")(expected_count)


def bind_reference(legacy_globals: dict[str, Any], files: list[Path]) -> Any:
    return _callable(legacy_globals, "flow_bind_reference_files_to_prompt")(files)


def prepare_reference_images(
    legacy_globals: dict[str, Any],
    reference_images: list[Any],
    job_id: str = "",
) -> Any:
    return _callable(legacy_globals, "flow_prepare_reference_images_for_project")(
        reference_images,
        job_id=job_id,
    )


def submit_prompt(
    legacy_globals: dict[str, Any],
    prompt: str,
    config: dict[str, Any] | None,
    reference_images: list[Any] | None,
    job_id: str | None,
    stage: str | None,
    slot_id: str | None,
    slot_index: int | None,
) -> Any:
    return _callable(legacy_globals, "flow_submit_prompt")(
        prompt,
        config,
        reference_images,
        job_id,
        stage,
        slot_id,
        slot_index,
    )


def collect_results(
    legacy_globals: dict[str, Any],
    job_id: str | None,
    prompt: str | None,
    asset_type: str,
) -> Any:
    return _callable(legacy_globals, "flow_collect_results")(
        job_id=job_id,
        prompt=prompt,
        asset_type=asset_type,
    )
