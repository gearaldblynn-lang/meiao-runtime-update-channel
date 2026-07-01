from __future__ import annotations

import base64
import hashlib
import math
import time
import uuid
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import random
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse


MICROSECONDS_PER_MS = 1000
_STICKER_FREE_TITLE_BLOCKLIST = re.compile("(vip|svip|\u4f1a\u5458|\u4ed8\u8d39|\u4e13\u4eab|\u5145\u503c|\u8d35\u5bbe|\u4f1a\u5458\u5361|premium|paid|pay)", re.IGNORECASE)
BASIC_DEDUPE_FILTER_POOL_LIMIT = 100
BASIC_DEDUPE_STICKER_POOL_LIMIT = 500
MAX_DRAFT_PLAYBACK_SPEED = 1.3
_JIANYING_IMPORT_LOCK = threading.Lock()


def runtime_base_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_runtime_data_root() -> Path:
    base_dir = runtime_base_dir()
    raw_value = os.environ.get("MEIAO_DATA_DIR")
    config_file = Path(os.environ.get("MEIAO_CONFIG_FILE") or base_dir / "config.local.json").expanduser()
    if not raw_value and config_file.exists():
        try:
            config = json.loads(config_file.read_text(encoding="utf-8"))
            storage = config.get("storage") if isinstance(config.get("storage"), dict) else {}
            raw_value = (
                config.get("dataDir")
                or config.get("data_dir")
                or storage.get("dataDir")
                or storage.get("data_dir")
                or storage.get("root")
            )
        except Exception:
            raw_value = None
    data_root = Path(str(raw_value).strip()) if raw_value else base_dir / "storage"
    data_root = data_root.expanduser()
    if not data_root.is_absolute():
        data_root = base_dir / data_root
    return data_root.resolve()


def runtime_public_base_url() -> str:
    configured = str(os.environ.get("MEIAO_PUBLIC_BASE_URL") or os.environ.get("MEIAO_RUNTIME_URL") or "").strip().rstrip("/")
    if configured:
        return configured
    port = str(os.environ.get("MEIAO_PORT") or os.environ.get("MEIAO_RUNTIME_PORT") or "8787").strip() or "8787"
    return f"http://127.0.0.1:{port}"


@dataclass(frozen=True)
class DraftClip:
    source_url: str
    timeline_start_ms: int
    timeline_end_ms: int
    source_start_ms: int = 0
    source_end_ms: int = 0
    source_duration_ms: int = 0
    match_adjusted_duration_ms: int = 0
    playback_speed: float = 1.0
    volume: float = 1.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    transform_x: int = 0
    transform_y: int = 0
    transition: str = ""
    transition_duration_ms: int = 500
    mask: str = ""
    fit_mode: str = "cover"

    @property
    def duration_ms(self) -> int:
        return max(1, self.timeline_end_ms - self.timeline_start_ms)

    def to_capcut_video_info(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "video_url": self.source_url,
            "start": self.timeline_start_ms * MICROSECONDS_PER_MS,
            "end": self.timeline_end_ms * MICROSECONDS_PER_MS,
            "duration": self.duration_ms * MICROSECONDS_PER_MS,
            "source_start": max(0, self.source_start_ms) * MICROSECONDS_PER_MS,
            "source_end": max(0, self.source_end_ms) * MICROSECONDS_PER_MS,
            "source_duration": max(1, self.source_duration_ms or self.duration_ms) * MICROSECONDS_PER_MS,
            "speed": max(0.05, self.playback_speed or 1.0),
            "volume": self.volume,
            "scale_x": self.scale_x,
            "scale_y": self.scale_y,
            "transform_x": self.transform_x,
            "transform_y": self.transform_y,
            "fit_mode": self.fit_mode,
        }
        if self.transition:
            info["transition"] = self.transition
            info["transition_duration"] = max(100_000, self.transition_duration_ms * MICROSECONDS_PER_MS)
        if self.mask:
            info["mask"] = self.mask
        return info


class CapCutMateError(RuntimeError):
    pass


class CapCutMateAdapter:
    def __init__(self, base_url: str = "http://127.0.0.1:30000", timeout_seconds: float = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _post(self, path: str, payload: dict[str, Any], *, timeout_seconds: float | None = None) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds or self.timeout_seconds) as response:
                data = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="ignore")
            raise CapCutMateError(body_text or f"capcut-mate HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise CapCutMateError(f"capcut-mate service unavailable: {exc}") from exc
        except TimeoutError as exc:
            raise CapCutMateError("capcut-mate request timed out") from exc

        try:
            parsed = json.loads(data) if data else {}
        except json.JSONDecodeError as exc:
            raise CapCutMateError(f"capcut-mate returned invalid JSON: {data[:200]}") from exc
        if not isinstance(parsed, dict):
            raise CapCutMateError("capcut-mate returned a non-object response")
        code = parsed.get("code")
        if code not in (None, 0):
            message = str(parsed.get("message") or parsed.get("msg") or "capcut-mate business error")
            raise CapCutMateError(f"{message} (code {code})")
        if isinstance(parsed.get("data"), dict):
            return parsed["data"]
        return parsed

    def create_draft(self, width: int = 1080, height: int = 1920) -> dict[str, Any]:
        return self._post("/openapi/capcut-mate/v1/create_draft", {"width": width, "height": height})

    def add_videos(self, draft_url: str, clips: list[DraftClip], *, voice_duration_ms: int = 0) -> dict[str, Any]:
        if not clips:
            raise CapCutMateError("draft plan has no clips")
        video_infos = [clip.to_capcut_video_info() for clip in clips]
        first = clips[0]
        return self._post(
            "/openapi/capcut-mate/v1/add_videos",
            {
                "draft_url": draft_url,
                "video_infos": json.dumps(video_infos, ensure_ascii=False),
                "scale_x": first.scale_x,
                "scale_y": first.scale_y,
                "transform_x": first.transform_x,
                "transform_y": first.transform_y,
            },
        )

    def add_dedupe_video(self, draft_url: str, video_info: dict[str, Any]) -> dict[str, Any]:
        source_path = str(video_info.get("video_url") or "").strip()
        if not source_path:
            return {"draft_url": draft_url, "track_id": "", "video_ids": [], "segment_ids": []}
        return self._post(
            "/openapi/capcut-mate/v1/add_videos",
            {
                "draft_url": draft_url,
                "video_infos": json.dumps([video_info], ensure_ascii=False),
                "alpha": float(video_info.get("alpha") or 0.02),
                "scale_x": float(video_info.get("scale_x") or 1.0),
                "scale_y": float(video_info.get("scale_y") or 1.0),
                "transform_x": int(video_info.get("transform_x") or 0),
                "transform_y": int(video_info.get("transform_y") or 0),
            },
        )

    def add_images(self, draft_url: str, image_infos: list[dict[str, Any]]) -> dict[str, Any]:
        if not image_infos:
            return {"draft_url": draft_url, "track_id": "", "image_ids": [], "segment_ids": [], "segment_infos": []}
        return self._post(
            "/openapi/capcut-mate/v1/add_images",
            {
                "draft_url": draft_url,
                "image_infos": json.dumps(image_infos, ensure_ascii=False),
            },
        )

    def add_dedupe_video_safe(self, draft_url: str, video_info: dict[str, Any]) -> dict[str, Any]:
        try:
            return self.add_dedupe_video(draft_url, video_info)
        except Exception as exc:
            return {"draft_url": draft_url, "track_id": "", "video_ids": [], "segment_ids": [], "error": str(exc)}

    def add_dedupe_videos_safe(self, draft_url: str, video_infos: list[dict[str, Any]]) -> dict[str, Any]:
        if not video_infos:
            return {"draft_url": draft_url, "track_ids": [], "video_ids": [], "segment_ids": [], "items": [], "errors": []}
        results: list[dict[str, Any]] = []
        errors: list[str] = []
        current_draft_url = draft_url
        for item in video_infos:
            try:
                result = self.add_dedupe_video(current_draft_url, item)
                current_draft_url = str(result.get("draft_url") or current_draft_url)
                results.append(result)
            except Exception as exc:
                errors.append(str(exc))
        return {
            "draft_url": current_draft_url,
            "track_ids": [item.get("track_id") for item in results if item.get("track_id")],
            "video_ids": [video_id for item in results for video_id in (item.get("video_ids") or [])],
            "segment_ids": [segment_id for item in results for segment_id in (item.get("segment_ids") or [])],
            "items": results,
            "errors": errors,
        }

    def add_audios(self, draft_url: str, audio_infos: list[dict[str, Any]]) -> dict[str, Any]:
        if not audio_infos:
            return {"draft_url": draft_url, "track_id": "", "audio_ids": []}
        return self._post(
            "/openapi/capcut-mate/v1/add_audios",
            {
                "draft_url": draft_url,
                "audio_infos": json.dumps(audio_infos, ensure_ascii=False),
            },
        )

    def get_audio_duration_ms(self, audio_url: str) -> int:
        audio_url = str(audio_url or "").strip()
        if not audio_url:
            return 0
        result = self._post(
            "/openapi/capcut-mate/v1/get_audio_duration",
            {"mp3_url": audio_url},
            timeout_seconds=120,
        )
        duration_us = int(result.get("duration") or 0)
        return round(duration_us / MICROSECONDS_PER_MS) if duration_us > 0 else 0

    def resolve_voice_duration_ms(self, plan: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        audio_url = voice_audio_url_from_plan(plan)
        planned_duration_ms = voice_duration_from_plan(plan)
        if planned_duration_ms > 0:
            return planned_duration_ms, {
                "source": "plan_audio_track",
                "planned_duration_ms": planned_duration_ms,
                "actual_duration_ms": 0,
                "audio_url": audio_url,
                "reason": "using precomputed voice duration",
            }
        if not audio_url:
            return planned_duration_ms, {
                "source": "plan",
                "planned_duration_ms": planned_duration_ms,
                "actual_duration_ms": 0,
                "reason": "missing voice url",
            }
        try:
            actual_duration_ms = self.get_audio_duration_ms(audio_url)
        except CapCutMateError as exc:
            if planned_duration_ms > 0:
                return planned_duration_ms, {
                    "source": "plan_fallback",
                    "planned_duration_ms": planned_duration_ms,
                    "actual_duration_ms": 0,
                    "audio_url": audio_url,
                    "warning": str(exc),
                }
            raise
        if actual_duration_ms <= 0:
            if planned_duration_ms > 0:
                return planned_duration_ms, {
                    "source": "plan_fallback",
                    "planned_duration_ms": planned_duration_ms,
                    "actual_duration_ms": 0,
                    "audio_url": audio_url,
                    "warning": "actual duration is empty",
                }
            raise CapCutMateError(f"failed to read actual voice duration: {audio_url}")
        return actual_duration_ms, {
            "source": "actual_audio",
            "planned_duration_ms": planned_duration_ms,
            "actual_duration_ms": actual_duration_ms,
            "audio_url": audio_url,
        }

    def add_captions(self, draft_url: str, captions: list[dict[str, Any]], *, style: dict[str, Any] | None = None) -> dict[str, Any]:
        if not captions:
            return {"draft_url": draft_url, "track_id": "", "text_ids": [], "segment_ids": [], "segment_infos": []}
        payload: dict[str, Any] = {
            "draft_url": draft_url,
            "captions": json.dumps(captions, ensure_ascii=False),
            "text_color": (style or {}).get("text_color") or "#ffffff",
            "border_color": (style or {}).get("border_color"),
            "alignment": (style or {}).get("alignment", 1),
            "alpha": (style or {}).get("alpha", 1.0),
            "font": (style or {}).get("font"),
            "font_size": (style or {}).get("font_size", 16),
            "letter_spacing": (style or {}).get("letter_spacing"),
            "line_spacing": (style or {}).get("line_spacing"),
            "scale_x": (style or {}).get("scale_x", 1.0),
            "scale_y": (style or {}).get("scale_y", 1.0),
            "transform_x": (style or {}).get("transform_x", 0.0),
            "transform_y": (style or {}).get("transform_y", 0.0),
            "style_text": (style or {}).get("style_text", False),
            "underline": (style or {}).get("underline", False),
            "italic": (style or {}).get("italic", False),
            "bold": (style or {}).get("bold", False),
            "has_shadow": (style or {}).get("has_shadow", False),
            "shadow_info": (style or {}).get("shadow_info"),
            "text_effect": (style or {}).get("text_effect"),
        }
        return self._post("/openapi/capcut-mate/v1/add_captions", payload)

    def add_effects(self, draft_url: str, effect_infos: list[dict[str, Any]]) -> dict[str, Any]:
        if not effect_infos:
            return {"draft_url": draft_url, "track_id": "", "effect_ids": [], "segment_ids": []}
        return self._post(
            "/openapi/capcut-mate/v1/add_effects",
            {
                "draft_url": draft_url,
                "effect_infos": json.dumps(effect_infos, ensure_ascii=False),
            },
        )

    def add_filters(self, draft_url: str, filter_infos: list[dict[str, Any]]) -> dict[str, Any]:
        if not filter_infos:
            return {"draft_url": draft_url, "track_id": "", "filter_ids": [], "segment_ids": []}
        return self._post(
            "/openapi/capcut-mate/v1/add_filters",
            {
                "draft_url": draft_url,
                "filter_infos": json.dumps(filter_infos, ensure_ascii=False),
            },
        )

    def add_filters_safe(self, draft_url: str, filter_infos: list[dict[str, Any]]) -> dict[str, Any]:
        if not filter_infos:
            return {"draft_url": draft_url, "track_ids": [], "filter_ids": [], "segment_ids": []}
        results: list[dict[str, Any]] = []
        errors: list[str] = []
        current_draft_url = draft_url
        for item in filter_infos:
            try:
                result = self.add_filters(current_draft_url, [item])
                current_draft_url = str(result.get("draft_url") or current_draft_url)
                results.append(result)
            except Exception as exc:
                errors.append(str(exc))
        return {
            "draft_url": current_draft_url,
            "track_ids": [item.get("track_id") for item in results if item.get("track_id")],
            "filter_ids": [filter_id for item in results for filter_id in (item.get("filter_ids") or [])],
            "segment_ids": [segment_id for item in results for segment_id in (item.get("segment_ids") or [])],
            "items": results,
            "errors": errors,
        }

    def add_sticker(self, draft_url: str, sticker_info: dict[str, Any]) -> dict[str, Any]:
        sticker_id = str(sticker_info.get("sticker_id") or "").strip()
        if not sticker_id:
            return {"draft_url": draft_url, "sticker_id": "", "track_id": "", "segment_id": "", "duration": 0}
        return self._post(
            "/openapi/capcut-mate/v1/add_sticker",
            {
                "draft_url": draft_url,
                "sticker_id": sticker_id,
                "start": int(sticker_info.get("start") or 0),
                "end": int(sticker_info.get("end") or 0),
                "scale": float(sticker_info.get("scale") or 1.0),
                "alpha": float(sticker_info.get("alpha") or 1.0),
                "transform_x": int(sticker_info.get("transform_x") or 0),
                "transform_y": int(sticker_info.get("transform_y") or 0),
            },
        )

    def add_stickers(self, draft_url: str, sticker_infos: list[dict[str, Any]]) -> dict[str, Any]:
        if not sticker_infos:
            return {"draft_url": draft_url, "track_ids": [], "sticker_ids": [], "segment_ids": []}
        results = [self.add_sticker(draft_url, item) for item in sticker_infos]
        return {
            "draft_url": results[-1].get("draft_url") or draft_url,
            "track_ids": [item.get("track_id") for item in results if item.get("track_id")],
            "sticker_ids": [item.get("sticker_id") for item in results if item.get("sticker_id")],
            "segment_ids": [item.get("segment_id") for item in results if item.get("segment_id")],
            "items": results,
        }

    def add_stickers_safe(self, draft_url: str, sticker_infos: list[dict[str, Any]]) -> dict[str, Any]:
        if not sticker_infos:
            return {"draft_url": draft_url, "track_ids": [], "sticker_ids": [], "segment_ids": []}
        results: list[dict[str, Any]] = []
        errors: list[str] = []
        current_draft_url = draft_url
        for item in sticker_infos:
            try:
                result = self.add_sticker(current_draft_url, item)
                current_draft_url = str(result.get("draft_url") or current_draft_url)
                results.append(result)
            except Exception as exc:
                errors.append(str(exc))
        return {
            "draft_url": current_draft_url,
            "track_ids": [item.get("track_id") for item in results if item.get("track_id")],
            "sticker_ids": [item.get("sticker_id") for item in results if item.get("sticker_id")],
            "segment_ids": [item.get("segment_id") for item in results if item.get("segment_id")],
            "items": results,
            "errors": errors,
        }

    def add_keyframes(self, draft_url: str, keyframes: list[dict[str, Any]]) -> dict[str, Any]:
        if not keyframes:
            return {"draft_url": draft_url, "keyframes_added": 0, "affected_segments": []}
        return self._post(
            "/openapi/capcut-mate/v1/add_keyframes",
            {
                "draft_url": draft_url,
                "keyframes": json.dumps(keyframes, ensure_ascii=False),
            },
        )

    def add_masks(self, draft_url: str, segment_ids: list[str], mask_name: str, *, feather: float = 0) -> dict[str, Any]:
        mask_name = normalize_mask_name(mask_name)
        if not segment_ids or not mask_name:
            return {"draft_url": draft_url, "masks_added": 0, "affected_segments": [], "mask_ids": []}
        return self._post(
            "/openapi/capcut-mate/v1/add_masks",
            {
                "draft_url": draft_url,
                "segment_ids": segment_ids,
                "name": mask_name,
                "X": 0,
                "Y": 0,
                "width": 1080,
                "height": 1920,
                "feather": max(0, min(100, float(feather or 0))),
                "rotation": 0,
                "invert": False,
                "roundCorner": 0,
            },
        )

    def add_masks_safe(self, draft_url: str, segment_ids: list[str], mask_name: str, *, feather: float = 0) -> dict[str, Any]:
        try:
            return self.add_masks(draft_url, segment_ids, mask_name, feather=feather)
        except Exception as exc:
            return {"draft_url": draft_url, "masks_added": 0, "affected_segments": [], "mask_ids": [], "error": str(exc)}

    def save_draft(self, draft_url: str) -> dict[str, Any]:
        return self._post("/openapi/capcut-mate/v1/save_draft", {"draft_url": draft_url})

    def generate_video(self, draft_url: str, api_key: str | None = None) -> dict[str, Any]:
        return self._post("/openapi/capcut-mate/v1/gen_video", {"draft_url": draft_url, "apiKey": api_key or ""})

    def query_video_status(self, draft_url: str) -> dict[str, Any]:
        return self._post("/openapi/capcut-mate/v1/gen_video_status", {"draft_url": draft_url})

    def recognize_subtitles(self, draft_url: str, *, timeout_seconds: float = 180) -> dict[str, Any]:
        return self._post(
            "/openapi/capcut-mate/v1/recognize_subtitles",
            {"draft_url": draft_url, "timeout": timeout_seconds},
            timeout_seconds=timeout_seconds + 30,
        )

    def import_draft_to_jianying(self, draft_url: str, *, target_draft_id: str | None = None) -> dict[str, Any]:
        source_draft_id = draft_id_from_url(draft_url)
        if not source_draft_id:
            raise CapCutMateError("draft_url missing draft_id")
        source_dir = capcut_mate_output_draft_dir(source_draft_id)
        if not source_dir.exists() or not source_dir.is_dir():
            raise CapCutMateError(f"capcut-mate draft folder not found: {source_dir}")
        draft_id = sanitize_jianying_draft_id(target_draft_id or source_draft_id)
        if not draft_id:
            raise CapCutMateError("target draft id is empty")

        with _JIANYING_IMPORT_LOCK:
            metadata_root = jianying_metadata_root()
            target_root = jianying_draft_root()
            target_root.mkdir(parents=True, exist_ok=True)
            target_dir = target_root / draft_id
            target_root_resolved = target_root.resolve()
            target_dir_resolved = target_dir.resolve()
            source_dir_resolved = source_dir.resolve()
            try:
                target_dir_resolved.relative_to(target_root_resolved)
            except ValueError as exc:
                raise CapCutMateError(f"target draft folder escapes Jianying draft root: {target_dir}") from exc
            if target_dir_resolved == target_root_resolved or target_dir_resolved == source_dir_resolved:
                raise CapCutMateError(f"unsafe target draft folder: {target_dir}")

            normalize_draft_metadata(source_dir, source_draft_id, source_dir)
            if target_dir.exists():
                if not target_dir.is_dir():
                    raise CapCutMateError(f"target draft path is not a folder: {target_dir}")
                shutil.rmtree(target_dir)
            shutil.copytree(source_dir, target_dir)
            rewrite_draft_json_paths(target_dir, source_dir, target_dir)
            normalize_draft_metadata(target_dir, draft_id, target_dir)
            upsert_root_metadata_index(metadata_root, draft_id, target_dir, target_root)
            target_root_meta = target_root / "root_meta_info.json"
            metadata_root_meta = metadata_root / "root_meta_info.json"
            if target_root_meta.exists() and target_root_meta.resolve() != metadata_root_meta.resolve():
                upsert_root_metadata_index(target_root, draft_id, target_dir, target_root)
            return {
                "draft_id": draft_id,
                "source_draft_id": source_draft_id,
                "source_dir": str(source_dir),
                "target_dir": str(target_dir),
                "reused_target": draft_id != source_draft_id,
            }

    def generate_draft_from_plan(self, plan: dict[str, Any], width: int = 1080, height: int = 1920) -> dict[str, Any]:
        stage_timings_ms: dict[str, float] = {}

        def timed(stage: str, callback: Any) -> Any:
            started = time.perf_counter()
            try:
                return callback()
            finally:
                stage_timings_ms[stage] = round((time.perf_counter() - started) * 1000, 1)

        clips = clips_from_frontend_plan(plan)
        raw_captions = plan.get("captions")
        if isinstance(raw_captions, list) and raw_captions and len(clips) < len(raw_captions):
            raise CapCutMateError(f"draft plan is incomplete: {len(raw_captions)} captions require matching video clips, got {len(clips)}")
        voice_duration_ms, voice_duration_result = timed("resolve_voice_duration", lambda: self.resolve_voice_duration_ms(plan))
        clips, timing_result = timed("normalize_timeline", lambda: normalize_timeline_for_voice(plan, clips, voice_duration_ms=voice_duration_ms))
        created = timed("create_draft", lambda: self.create_draft(width=width, height=height))
        draft_url = str(created.get("draft_url") or "").strip()
        if not draft_url:
            raise CapCutMateError("create_draft did not return draft_url")

        added_videos = timed("add_videos", lambda: self.add_videos(draft_url, clips, voice_duration_ms=voice_duration_ms))
        segment_ids = list(added_videos.get("segment_ids") or [])
        if len(segment_ids) != len(clips):
            raise CapCutMateError(f"video write incomplete: expected {len(clips)} clips, wrote {len(segment_ids)}")
        actual_total_duration_ms = int(added_videos.get("total_duration") or 0) // MICROSECONDS_PER_MS
        if actual_total_duration_ms <= 0:
            actual_total_duration_ms = duration_ms_from_segment_infos(added_videos.get("segment_infos") or [])
        if actual_total_duration_ms <= 0:
            actual_total_duration_ms = sum(clip.duration_ms for clip in clips)
        audio_result = timed(
            "add_audios",
            lambda: self.add_audios(draft_url, audio_infos_from_plan(plan, clips, target_duration_ms=actual_total_duration_ms, voice_duration_ms=voice_duration_ms)),
        )
        caption_result = timed(
            "add_captions",
            lambda: self.add_captions(
                draft_url,
                captions_from_plan(plan, clips, target_duration_ms=actual_total_duration_ms),
                style=caption_style_from_plan(plan, width=width, height=height),
            ),
        )
        filter_result = timed("add_filters", lambda: self.add_filters_safe(draft_url, filter_infos_from_plan(plan, clips, target_duration_ms=actual_total_duration_ms)))
        effect_result = timed("add_effects", lambda: self.add_effects(draft_url, effect_infos_from_plan(plan, clips)))
        dedupe_video_result = timed("add_dedupe_videos", lambda: self.add_dedupe_videos_safe(draft_url, dedupe_video_infos_from_plan(plan, target_duration_ms=actual_total_duration_ms)))
        sticker_result = timed("add_stickers", lambda: self.add_stickers_safe(draft_url, sticker_infos_from_plan(plan, target_duration_ms=actual_total_duration_ms, width=width, height=height)))
        video_mask = normalize_mask_name(str(plan.get("videoMask") or ""))
        if video_mask:
            mask_result = {
                "draft_url": draft_url,
                "masks_added": len(segment_ids),
                "affected_segments": segment_ids,
                "mask_ids": [],
                "applied_via_video_infos": True,
            }
        else:
            mask_result = timed(
                "add_masks",
                lambda: self.add_masks_safe(
                    draft_url,
                    segment_ids,
                    video_mask,
                    feather=float(plan.get("maskFeather") or 0),
                ),
            )
        keyframe_result = timed(
            "add_keyframes",
            lambda: self.add_keyframes(
                draft_url,
                keyframes_from_plan(
                    plan,
                    clips,
                    segment_ids=segment_ids,
                    width=width,
                    height=height,
                ),
            ),
        )
        saved = timed("save_draft", lambda: self.save_draft(draft_url))
        final_draft_url = saved.get("draft_url") or keyframe_result.get("draft_url") or mask_result.get("draft_url") or sticker_result.get("draft_url") or dedupe_video_result.get("draft_url") or effect_result.get("draft_url") or filter_result.get("draft_url") or audio_result.get("draft_url") or added_videos.get("draft_url") or draft_url
        import_result = timed(
            "import_draft_to_jianying",
            lambda: self.import_draft_to_jianying(
                str(final_draft_url),
                target_draft_id=str(plan.get("targetDraftId") or "").strip() or None,
            ),
        )
        recognize_subtitles_result = timed("recognize_subtitles", lambda: self.maybe_recognize_subtitles(plan, str(final_draft_url), audio_result))

        return {
            "draft_url": final_draft_url,
            "clip_count": len(clips),
            "timing_result": timing_result,
            "voice_duration_result": voice_duration_result,
            "stage_timings_ms": stage_timings_ms,
            "create_result": created,
            "add_videos_result": added_videos,
            "add_audios_result": audio_result,
            "add_captions_result": caption_result,
            "add_filters_result": filter_result,
            "add_effects_result": effect_result,
            "add_stickers_result": sticker_result,
            "add_dedupe_video_result": dedupe_video_result,
            "add_masks_result": mask_result,
            "add_keyframes_result": keyframe_result,
            "save_result": saved,
            "import_result": import_result,
            "recognize_subtitles_result": recognize_subtitles_result,
        }

    def maybe_recognize_subtitles(self, plan: dict[str, Any], draft_url: str, audio_result: dict[str, Any]) -> dict[str, Any]:
        if is_jianying_subtitle_recognition_plan(plan):
            return {"recognized": False, "skipped": True, "reason": "deferred to export"}
        if list(plan.get("captions") or []):
            return {"recognized": False, "skipped": True, "reason": "content captions added; intelligent recognition is manual until clear-existing-subtitles is supported"}
        subtitle_preset = str(plan.get("subtitlePreset") or "none").strip()
        if subtitle_preset == "none":
            return {"recognized": False, "skipped": True, "reason": "subtitle disabled"}
        return {"recognized": False, "skipped": True, "reason": "script captions mode"}


def draft_id_from_url(draft_url: str) -> str:
    parsed = urlparse(draft_url)
    return (parse_qs(parsed.query).get("draft_id") or [""])[0].strip()


def sanitize_jianying_draft_id(raw_id: str) -> str:
    text = re.sub(r"[^0-9A-Za-z_.-]+", "-", str(raw_id or "").strip()).strip(".-")
    return text[:120].rstrip(".-")


def normalize_mask_name(mask_name: str) -> str:
    name = str(mask_name or "").strip()
    aliases = {
        "圆形": "圆形",
        "圓形": "圆形",
        "爱心": "爱心",
        "愛心": "爱心",
        "星形": "星形",
        "矩形": "矩形",
        "线性": "线性",
        "線性": "线性",
        "镜面": "镜面",
        "鏡面": "镜面",
    }
    return aliases.get(name, name)


def capcut_mate_output_draft_dir(draft_id: str) -> Path:
    return Path(__file__).resolve().parent / "upstream" / "capcut-mate-main" / "output" / "draft" / draft_id


def jianying_draft_root() -> Path:
    configured = os.environ.get("DRAFT_SAVE_PATH", "").strip()
    if configured:
        return Path(configured)
    metadata_root = jianying_metadata_root()
    root_meta = read_json_dict(metadata_root / "root_meta_info.json")
    for candidate in infer_jianying_draft_roots(root_meta):
        if candidate.exists() and candidate.resolve() != metadata_root.resolve():
            return candidate
    root_path = str(root_meta.get("root_path") or "").strip()
    if root_path:
        return Path(root_path)
    return metadata_root


def jianying_metadata_root() -> Path:
    return Path.home() / "AppData" / "Local" / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"


def infer_jianying_draft_roots(root_meta: dict[str, Any]) -> list[Path]:
    roots: list[Path] = []
    store = root_meta.get("all_draft_store")
    if not isinstance(store, list):
        return roots
    for item in store:
        if not isinstance(item, dict):
            continue
        root_path = str(item.get("draft_root_path") or "").strip()
        if root_path:
            roots.append(Path(root_path))
            continue
        fold_path = str(item.get("draft_fold_path") or "").strip()
        if fold_path:
            roots.append(Path(fold_path).parent)
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).lower().replace("\\", "/")
        if key and key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def rewrite_draft_json_paths(target_dir: Path, source_dir: Path, imported_dir: Path) -> None:
    for name in ("draft_content.json", "draft_info.json", "draft_meta_info.json"):
        path = target_dir / name
        if not path.exists() or not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        data = replace_path_prefixes(data, source_dir, imported_dir)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")


def normalize_draft_metadata(draft_dir: Path, draft_id: str, folder_path: Path) -> None:
    meta_path = draft_dir / "draft_meta_info.json"
    if not meta_path.exists() or not meta_path.is_file():
        return
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return
    data["draft_name"] = draft_id
    data["draft_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, draft_id)).upper()
    data["draft_fold_path"] = str(folder_path)
    data["draft_root_path"] = str(folder_path.parent)
    data["tm_draft_create"] = int(time.time() * 1_000_000)
    data["tm_draft_modified"] = int(time.time() * 1_000_000)
    duration = read_draft_duration_us(draft_dir)
    if duration > 0:
        data["tm_duration"] = duration
    meta_path.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")


def upsert_root_metadata_index(metadata_root: Path, draft_id: str, draft_dir: Path, draft_root: Path) -> None:
    metadata_root.mkdir(parents=True, exist_ok=True)
    root_meta_path = metadata_root / "root_meta_info.json"
    root_data: dict[str, Any]
    if root_meta_path.exists():
        try:
            parsed = json.loads(root_meta_path.read_text(encoding="utf-8"))
            root_data = parsed if isinstance(parsed, dict) else {}
        except Exception:
            root_data = {}
    else:
        root_data = {}

    store = root_data.get("all_draft_store")
    if not isinstance(store, list):
        store = []

    meta = read_json_dict(draft_dir / "draft_meta_info.json")
    duration = read_draft_duration_us(draft_dir)
    now_us = int(time.time() * 1_000_000)
    draft_uuid = str(meta.get("draft_id") or uuid.uuid5(uuid.NAMESPACE_URL, draft_id)).upper()
    draft_path = str(draft_dir).replace("\\", "/")
    root_path = str(draft_root).replace("\\", "/")
    entry = {
        "cloud_draft_cover": False,
        "cloud_draft_sync": False,
        "draft_cloud_last_action_download": False,
        "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "",
        "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": f"{draft_path}\\draft_cover.jpg",
        "draft_fold_path": draft_path,
        "draft_id": draft_uuid,
        "draft_is_ai_shorts": False,
        "draft_is_cloud_temp_draft": False,
        "draft_is_invisible": False,
        "draft_is_web_article_video": False,
        "draft_json_file": f"{draft_path}\\draft_content.json",
        "draft_name": draft_id,
        "draft_new_version": str(meta.get("draft_new_version") or ""),
        "draft_root_path": root_path,
        "draft_timeline_materials_size": estimate_draft_materials_size(draft_dir),
        "draft_type": "",
        "draft_web_article_video_enter_from": "",
        "streaming_edit_draft_ready": True,
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_entry_id": -1,
        "tm_draft_cloud_modified": 0,
        "tm_draft_cloud_parent_entry_id": -1,
        "tm_draft_cloud_space_id": -1,
        "tm_draft_cloud_user_id": -1,
        "tm_draft_create": int(meta.get("tm_draft_create") or now_us),
        "tm_draft_modified": int(meta.get("tm_draft_modified") or now_us),
        "tm_draft_removed": 0,
        "tm_duration": duration,
    }

    def is_same(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        return (
            str(item.get("draft_name") or "") == draft_id
            or str(item.get("draft_fold_path") or "").replace("\\", "/") == draft_path
            or str(item.get("draft_id") or "").upper() == draft_uuid
        )

    root_data["all_draft_store"] = [entry, *[item for item in store if not is_same(item)]]
    root_data["draft_ids"] = len(root_data["all_draft_store"])
    root_data["root_path"] = root_path
    root_meta_path.write_text(json.dumps(root_data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def read_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def estimate_draft_materials_size(draft_dir: Path) -> int:
    assets_dir = draft_dir / "assets"
    if not assets_dir.exists():
        return 0
    total = 0
    for item in assets_dir.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return total


def read_draft_duration_us(draft_dir: Path) -> int:
    content_path = draft_dir / "draft_content.json"
    if not content_path.exists():
        return 0
    try:
        data = json.loads(content_path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    duration = data.get("duration") if isinstance(data, dict) else 0
    return int(duration) if isinstance(duration, (int, float)) and duration > 0 else 0


def duration_ms_from_segment_infos(segment_infos: list[dict[str, Any]]) -> int:
    ends: list[int] = []
    for item in segment_infos:
        if not isinstance(item, dict):
            continue
        try:
            ends.append(int(item.get("end") or 0))
        except (TypeError, ValueError):
            continue
    end_us = max(ends or [0])
    return end_us // MICROSECONDS_PER_MS if end_us > 0 else 0


def detect_unstable_audio_speed(draft_url: str, *, min_speed: float = 0.5, max_speed: float = 2.0) -> str:
    """字幕识别前检查配音变速，过快/过慢会导致剪映识别不到人声。"""
    draft_id = draft_id_from_url(draft_url)
    if not draft_id:
        return ""
    draft_dir = jianying_draft_root() / draft_id
    data = read_json_dict(draft_dir / "draft_info.json") or read_json_dict(draft_dir / "draft_content.json")
    tracks = data.get("tracks") if isinstance(data, dict) else []
    if not isinstance(tracks, list):
        return ""
    speeds: list[float] = []
    for track in tracks:
        if not isinstance(track, dict) or track.get("type") != "audio":
            continue
        segments = track.get("segments")
        if not isinstance(segments, list):
            continue
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            speed = segment.get("speed")
            if isinstance(speed, (int, float)) and speed > 0:
                speeds.append(float(speed))
    if not speeds:
        return ""
    unstable = [speed for speed in speeds if speed < min_speed or speed > max_speed]
    if not unstable:
        return ""
    formatted = ", ".join(f"{speed:.2f}x" for speed in unstable[:3])
    return f"配音速度 {formatted} 超出智能字幕稳定识别范围，已跳过自动识别"


def replace_path_prefixes(value: Any, source_dir: Path, imported_dir: Path) -> Any:
    if isinstance(value, dict):
        return {key: replace_path_prefixes(item, source_dir, imported_dir) for key, item in value.items()}
    if isinstance(value, list):
        return [replace_path_prefixes(item, source_dir, imported_dir) for item in value]
    if isinstance(value, str):
        source_text = str(source_dir)
        imported_text = str(imported_dir)
        source_posix = source_dir.as_posix()
        imported_posix = imported_dir.as_posix()
        return value.replace(source_text, imported_text).replace(source_posix, imported_posix)
    return value


def clips_from_frontend_plan(plan: dict[str, Any]) -> list[DraftClip]:
    raw_clips = plan.get("clips")
    if not isinstance(raw_clips, list):
        return []

    plan_id = str(plan.get("id") or plan.get("taskId") or "draft")
    random_zoom_enabled = bool(plan.get("randomZoomEnabled", True))
    random_flip_enabled = bool(plan.get("randomFlipEnabled", True))
    zoom_min = clamp_float(min(float(plan.get("randomZoomMin", 1.02) or 1.02), float(plan.get("randomZoomMax", 1.12) or 1.12)), 1.0, 3.0)
    zoom_max = clamp_float(max(float(plan.get("randomZoomMin", 1.02) or 1.02), float(plan.get("randomZoomMax", 1.12) or 1.12)), zoom_min, 3.0)
    remove_original_audio = bool(plan.get("removeOriginalAudio", True))
    default_volume = 0.0 if remove_original_audio else 1.0
    default_scale = float(plan.get("clipScale", 1.0) or 1.0)
    default_transform_x = int(plan.get("clipTransformX", 0) or 0)
    default_transform_y = int(plan.get("clipTransformY", 0) or 0)
    default_transition = str(plan.get("transitionStyle") or "")
    default_transition_duration = int(plan.get("transitionDurationMs", 500) or 500)
    default_mask = str(plan.get("videoMask") or "")
    requirements_by_order: dict[int, int] = {}
    raw_requirements = plan.get("shotRequirements")
    if isinstance(raw_requirements, list):
        for raw_requirement in raw_requirements:
            if not isinstance(raw_requirement, dict):
                continue
            order = int(raw_requirement.get("order") or 0)
            duration_sec = float(raw_requirement.get("durationSec") or 0)
            if order > 0 and duration_sec > 0:
                requirements_by_order[order] = max(1, round(duration_sec * 1000))

    clips: list[DraftClip] = []
    for index, raw in enumerate(raw_clips):
        if not isinstance(raw, dict):
            continue
        source_url = str(raw.get("sourceUrl") or "").strip()
        if not source_url:
            continue
        seed = f"{plan_id}:{raw.get('id') or source_url}:{index}"
        raw_scale_x = float(raw.get("scaleX", default_scale) or default_scale)
        raw_scale_y = float(raw.get("scaleY", default_scale) or default_scale)
        # The frontend already composes "cover canvas" scale with random zoom.
        # Keep that value so mixed-ratio sources fill the selected draft ratio.
        generated_scale = stable_between(f"{seed}:zoom", zoom_min, zoom_max) if random_zoom_enabled else default_scale
        allow_random_flip = raw.get("allowRandomFlip")
        if allow_random_flip is None:
            allow_random_flip = not clip_looks_packaging_primary(raw)
        generated_flip = -1.0 if random_flip_enabled and bool(allow_random_flip) and stable_fraction(f"{seed}:flip") >= 0.5 else 1.0
        if raw.get("scaleX") is not None or raw.get("scaleY") is not None:
            scale_x = abs(raw_scale_x) * generated_flip
            scale_y = abs(raw_scale_y)
        else:
            scale_x = generated_scale * generated_flip if random_zoom_enabled or random_flip_enabled else raw_scale_x
            scale_y = generated_scale if random_zoom_enabled else raw_scale_y
        clip_order = int(raw.get("order") or index + 1)
        required_duration_ms = int(raw.get("requiredDurationMs") or requirements_by_order.get(clip_order) or 0)
        match_adjusted_duration_ms = int(raw.get("matchAdjustedDurationMs") or raw.get("durationMs") or required_duration_ms or 1)
        full_source_duration_ms = resolve_local_video_duration_ms(source_url)
        source_start_ms = max(0, int(raw.get("sourceStartMs") or 0))
        raw_source_end_ms = int(raw.get("sourceEndMs") or 0)
        if raw_source_end_ms <= source_start_ms:
            raw_source_end_ms = source_start_ms + int(raw.get("sourceDurationMs") or raw.get("durationMs") or 1)
        if full_source_duration_ms > 0:
            source_start_ms = min(source_start_ms, max(0, full_source_duration_ms - 1))
            source_end_ms = min(max(source_start_ms + 1, raw_source_end_ms), full_source_duration_ms)
        else:
            source_end_ms = max(source_start_ms + 1, raw_source_end_ms)
        source_duration_ms = max(1, int(raw.get("sourceDurationMs") or (source_end_ms - source_start_ms) or raw.get("durationMs") or 1))
        source_duration_ms = min(source_duration_ms, max(1, source_end_ms - source_start_ms))
        clips.append(
            DraftClip(
                source_url=source_url,
                timeline_start_ms=int(raw.get("timelineStartMs") or 0),
                timeline_end_ms=int(raw.get("timelineEndMs") or raw.get("durationMs") or 1),
                source_start_ms=source_start_ms,
                source_end_ms=source_end_ms,
                source_duration_ms=source_duration_ms,
                match_adjusted_duration_ms=match_adjusted_duration_ms,
                playback_speed=float(raw.get("playbackSpeed") or 1.0),
                volume=float(raw.get("volume", default_volume) or default_volume),
                scale_x=scale_x,
                scale_y=scale_y,
                transform_x=int(raw.get("transformX", default_transform_x) or default_transform_x),
                transform_y=int(raw.get("transformY", default_transform_y) or default_transform_y),
                transition=str(raw.get("transition") or default_transition),
                transition_duration_ms=int(raw.get("transitionDurationMs", default_transition_duration) or default_transition_duration),
                mask=str(raw.get("mask") or default_mask),
                fit_mode=str(raw.get("fitMode") or raw.get("fit_mode") or "cover"),
            )
        )
    return clips


def resolve_local_video_duration_ms(source_url: str) -> int:
    path = local_media_file_from_url(source_url)
    if not path:
        return 0
    return probe_video_duration_ms(path)


def local_media_file_from_url(source_url: str) -> Path | None:
    parsed = urlparse(source_url)
    if parsed.scheme and parsed.hostname not in {"127.0.0.1", "localhost"}:
        return None
    path = parsed.path if parsed.scheme else source_url
    if not path.startswith("/media/"):
        return None
    media_root = (resolve_runtime_data_root() / "media").resolve()
    relative = unquote(path.removeprefix("/media/")).replace("/", os.sep)
    candidate = (media_root / relative).resolve()
    try:
        if not candidate.is_relative_to(media_root):
            return None
    except AttributeError:
        if os.path.commonpath([str(candidate), str(media_root)]) != str(media_root):
            return None
    return candidate if candidate.is_file() else None


def probe_video_duration_ms(path: Path) -> int:
    material_duration_ms = probe_video_material_duration_ms(path)
    if material_duration_ms > 0:
        return material_duration_ms

    runtime_root = Path(__file__).resolve().parents[2]
    ffprobe = runtime_root / "runtime" / "ffprobe.exe"
    if not ffprobe.exists():
        return 0
    try:
        result = subprocess.run(
            [
                str(ffprobe),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        duration_sec = float((result.stdout or "").strip() or 0)
    except Exception:
        return 0
    return max(1, round(duration_sec * 1000)) if duration_sec > 0 else 0


def probe_video_material_duration_ms(path: Path) -> int:
    upstream_root = Path(__file__).resolve().parent / "upstream" / "capcut-mate-main"
    if not upstream_root.exists():
        return 0
    try:
        upstream_root_text = str(upstream_root)
        if upstream_root_text not in sys.path:
            sys.path.insert(0, upstream_root_text)
        import src.pyJianYingDraft as draft  # type: ignore

        material = draft.VideoMaterial(str(path))
        duration_us = int(getattr(material, "duration", 0) or 0)
    except Exception:
        return 0
    return max(1, round(duration_us / MICROSECONDS_PER_MS)) if duration_us > 0 else 0


def normalize_timeline_for_voice(plan: dict[str, Any], clips: list[DraftClip], *, voice_duration_ms: int | None = None) -> tuple[list[DraftClip], dict[str, Any]]:
    if not clips:
        return clips, {"normalized": False, "reason": "no clips"}
    voice_duration_ms = int(voice_duration_ms or voice_duration_from_plan(plan) or 0)
    video_source_total_ms = sum(max(1, clip.source_duration_ms or clip.duration_ms) for clip in clips)
    match_adjusted_durations = [max(1, clip.match_adjusted_duration_ms or clip.duration_ms) for clip in clips]
    matched_video_total_ms = sum(match_adjusted_durations)
    planned_video_total_ms = sum(max(1, clip.duration_ms) for clip in clips)
    if video_source_total_ms <= 0 or matched_video_total_ms <= 0:
        return clips, {
            "normalized": False,
            "reason": "missing voice or video duration",
            "video_source_total_ms": video_source_total_ms,
            "matched_video_total_ms": matched_video_total_ms,
            "planned_video_total_ms": planned_video_total_ms,
            "voice_duration_ms": voice_duration_ms,
        }

    duration_mode = str(plan.get("durationMode") or plan.get("duration_mode") or "").strip()
    if duration_mode == "script_timing":
        normalized: list[DraftClip] = []
        cursor = 0
        for clip in clips:
            source_duration_ms = max(1, clip.source_duration_ms or clip.duration_ms)
            duration_ms = max(1, clip.duration_ms)
            if source_duration_ms / duration_ms > MAX_DRAFT_PLAYBACK_SPEED:
                duration_ms = max(duration_ms, math.ceil(source_duration_ms / MAX_DRAFT_PLAYBACK_SPEED))
            end_ms = cursor + duration_ms
            normalized.append(
                replace(
                    clip,
                    timeline_start_ms=cursor,
                    timeline_end_ms=end_ms,
                    playback_speed=source_duration_ms / duration_ms,
                )
            )
            cursor = end_ms
        return normalized, {
            "normalized": True,
            "duration_mode": "script_timing",
            "video_source_total_ms": video_source_total_ms,
            "matched_video_total_ms": matched_video_total_ms,
            "planned_video_total_ms": planned_video_total_ms,
            "voice_duration_ms": voice_duration_ms,
            "target_total_ms": cursor,
            "video_speed_min": min(round(clip.playback_speed, 4) for clip in normalized),
            "video_speed_max": max(round(clip.playback_speed, 4) for clip in normalized),
            "voice_speed": round(voice_duration_ms / max(1, cursor), 4),
        }

    target_total_ms = round((matched_video_total_ms + voice_duration_ms) / 2) if voice_duration_ms > 0 else matched_video_total_ms
    target_durations = distribute_durations(match_adjusted_durations, target_total_ms)
    normalized: list[DraftClip] = []
    cursor = 0
    for clip, target_duration_ms in zip(clips, target_durations):
        source_duration_ms = max(1, clip.source_duration_ms or clip.duration_ms)
        duration_ms = max(1, int(target_duration_ms or clip.duration_ms or source_duration_ms))
        if source_duration_ms / duration_ms > MAX_DRAFT_PLAYBACK_SPEED:
            duration_ms = max(duration_ms, math.ceil(source_duration_ms / MAX_DRAFT_PLAYBACK_SPEED))
        playback_speed = source_duration_ms / duration_ms
        end_ms = cursor + duration_ms
        normalized.append(
            replace(
                clip,
                timeline_start_ms=cursor,
                timeline_end_ms=end_ms,
                playback_speed=playback_speed,
            )
        )
        cursor = end_ms
    target_total_ms = cursor

    return normalized, {
        "normalized": True,
        "video_source_total_ms": video_source_total_ms,
        "matched_video_total_ms": matched_video_total_ms,
        "planned_video_total_ms": planned_video_total_ms,
        "voice_duration_ms": voice_duration_ms,
        "target_total_ms": target_total_ms,
        "video_speed_min": min(round(clip.playback_speed, 4) for clip in normalized),
        "video_speed_max": max(round(clip.playback_speed, 4) for clip in normalized),
        "voice_speed": round(voice_duration_ms / max(1, target_total_ms), 4),
    }


def voice_duration_from_plan(plan: dict[str, Any]) -> int:
    raw_tracks = plan.get("audioTracks")
    if not isinstance(raw_tracks, list):
        return 0
    for raw in raw_tracks:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("sourceType") or "").strip() == "bgm_library":
            continue
        duration_ms = int(raw.get("durationMs") or 0)
        if duration_ms > 0:
            return duration_ms
        start_ms = int(raw.get("startMs") or 0)
        end_ms = int(raw.get("endMs") or 0)
        if end_ms > start_ms:
            return end_ms - start_ms
    return 0


def voice_audio_url_from_plan(plan: dict[str, Any]) -> str:
    raw_tracks = plan.get("audioTracks")
    if not isinstance(raw_tracks, list):
        return ""
    for raw in raw_tracks:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("sourceType") or "").strip() == "bgm_library":
            continue
        audio_url = str(raw.get("sourceUrl") or raw.get("dataUrl") or "").strip()
        if audio_url:
            return audio_url
    return ""


def distribute_durations(source_durations: list[int], target_total_ms: int) -> list[int]:
    if not source_durations:
        return []
    safe_target_total = max(len(source_durations), round(target_total_ms))
    source_total = sum(max(1, value) for value in source_durations)
    if source_total <= 0:
        even = max(1, safe_target_total // len(source_durations))
        return [
            max(1, safe_target_total - even * index) if index == len(source_durations) - 1 else even
            for index, _ in enumerate(source_durations)
        ]
    used = 0
    durations: list[int] = []
    for index, source_duration in enumerate(source_durations):
        if index == len(source_durations) - 1:
            durations.append(max(1, safe_target_total - used))
        else:
            duration = max(1, round((max(1, source_duration) / source_total) * safe_target_total))
            used += duration
            durations.append(duration)
    return durations


def stable_fraction(seed: str) -> float:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64 - 1)


def stable_between(seed: str, low: float, high: float) -> float:
    return low + stable_fraction(seed) * (high - low)


def clamp_float(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def clip_looks_packaging_primary(raw: dict[str, Any]) -> bool:
    text_parts = [
        str(raw.get("visualDescription") or ""),
        " ".join(str(item) for item in raw.get("retrievalKeywords") or []),
        str(raw.get("id") or ""),
    ]
    text = " ".join(text_parts)
    return bool(
        re.search(
            r"产品包装|包装罐|包装袋|包装盒|外包装|瓶身|罐身|盒身|标签|Logo|logo|商标|正面展示|包装正面|印有",
            text,
        )
    )


def audio_infos_from_plan(plan: dict[str, Any], clips: list[DraftClip], *, target_duration_ms: int = 0, voice_duration_ms: int = 0) -> list[dict[str, Any]]:
    raw_tracks = plan.get("audioTracks")

    total_duration_ms = target_duration_ms or sum(clip.duration_ms for clip in clips)
    infos: list[dict[str, Any]] = []
    if not isinstance(raw_tracks, list):
        raw_tracks = []
    for raw in raw_tracks:
        if not isinstance(raw, dict):
            continue
        audio_url = str(raw.get("sourceUrl") or raw.get("dataUrl") or "").strip()
        if not audio_url:
            continue
        source_type = str(raw.get("sourceType") or "").strip()
        start_ms = int(raw.get("startMs") or 0)
        end_ms = int(raw.get("endMs") or 0)
        duration_ms = int(raw.get("durationMs") or max(1, end_ms - start_ms))
        if source_type != "bgm_library" and voice_duration_ms > 0:
            duration_ms = voice_duration_ms
        if total_duration_ms > 0:
            start_ms = 0
            end_ms = total_duration_ms
            if source_type == "bgm_library":
                duration_ms = total_duration_ms
                speed = 1.0
            else:
                speed = duration_ms / total_duration_ms
        else:
            speed = float(raw.get("playbackSpeed") or 1.0)
        infos.append(
            {
                "audio_url": audio_url,
                "start": start_ms * MICROSECONDS_PER_MS,
                "end": max(start_ms + 1, end_ms) * MICROSECONDS_PER_MS,
                "duration": max(1, duration_ms) * MICROSECONDS_PER_MS,
                "speed": max(0.05, speed),
                "volume": float(raw.get("volume", 1.0) or 1.0),
                **({"audio_effect": str(raw.get("audioEffect") or "").strip()} if str(raw.get("audioEffect") or "").strip() else {}),
            }
        )
    infos.extend(keyword_sound_infos_from_plan(plan, clips, target_duration_ms=total_duration_ms))
    return infos


def keyword_terms_from_plan(plan: dict[str, Any]) -> list[str]:
    if not bool(plan.get("captionKeywordEnabled", False)):
        return []
    raw = str(plan.get("captionKeywords") or "").strip()
    if not raw:
        return []
    terms = [item.strip() for item in re.split(r"[\s,，、|]+", raw) if item.strip()]
    return sorted(set(terms), key=len, reverse=True)


def looks_like_audio_source(value: str) -> bool:
    source = str(value or "").strip()
    if not source:
        return False
    if source.startswith(("http://", "https://", "file://", "/", "\\")):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", source):
        return True
    return bool(re.search(r"\.(mp3|m4a|aac|wav|flac|ogg)(\?|#|$)", source, re.IGNORECASE))


def keyword_sound_infos_from_plan(plan: dict[str, Any], clips: list[DraftClip], *, target_duration_ms: int = 0) -> list[dict[str, Any]]:
    if not bool(plan.get("keywordSoundEnabled", False)):
        return []
    sound_url = str(plan.get("keywordSoundUrl") or "").strip()
    if not looks_like_audio_source(sound_url):
        return []
    terms = keyword_terms_from_plan(plan)
    if not terms:
        return []
    captions = captions_from_plan(plan, clips, target_duration_ms=target_duration_ms)
    if not captions:
        return []
    total_us = max(0, int(target_duration_ms or 0) * MICROSECONDS_PER_MS)
    duration_us = 450 * MICROSECONDS_PER_MS
    result: list[dict[str, Any]] = []
    last_start = -10 * MICROSECONDS_PER_MS
    for caption in captions:
        text = str(caption.get("text") or "")
        if not any(term and term in text for term in terms):
            continue
        start_us = int(caption.get("start") or 0)
        if start_us - last_start < 300 * MICROSECONDS_PER_MS:
            continue
        end_us = start_us + duration_us
        if total_us > 0:
            end_us = min(end_us, total_us)
        if end_us <= start_us:
            continue
        result.append({
            "audio_url": sound_url,
            "start": start_us,
            "end": end_us,
            "duration": max(1, end_us - start_us),
            "speed": 1.0,
            "volume": 0.8,
        })
        last_start = start_us
    return result


def captions_from_plan(plan: dict[str, Any], clips: list[DraftClip], *, target_duration_ms: int = 0) -> list[dict[str, Any]]:
    if is_jianying_subtitle_recognition_plan(plan):
        return []
    raw_captions = plan.get("captions")
    if not isinstance(raw_captions, list) or not raw_captions:
        return []

    captions: list[dict[str, Any]] = []
    raw_total_ms = 0
    for raw in raw_captions:
        if not isinstance(raw, dict):
            continue
        raw_total_ms = max(raw_total_ms, int(raw.get("endMs") or 0))

    for index, raw in enumerate(raw_captions):
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        if index < len(clips):
            start_ms = clips[index].timeline_start_ms
            end_ms = clips[index].timeline_end_ms
        else:
            start_ms = int(raw.get("startMs") or 0)
            end_ms = max(int(raw.get("endMs") or 0), start_ms + 1)
            if target_duration_ms > 0 and raw_total_ms > 0:
                old_total_ms = max(1, raw_total_ms)
                start_ms = round((start_ms / old_total_ms) * target_duration_ms)
                end_ms = round((end_ms / old_total_ms) * target_duration_ms)
            elif target_duration_ms > 0:
                end_ms = min(end_ms, target_duration_ms)
        if target_duration_ms > 0:
            start_ms = max(0, min(start_ms, target_duration_ms - 1))
            end_ms = max(start_ms + 1, min(end_ms, target_duration_ms))
        caption: dict[str, Any] = {
            "start": start_ms * MICROSECONDS_PER_MS,
            "end": max(end_ms, start_ms + 1) * MICROSECONDS_PER_MS,
            "text": text,
            "keyword_color": str(plan.get("captionKeywordColor") or "#ff7100"),
            "font_size": caption_font_size_from_plan(plan),
        }
        if bool(plan.get("captionKeywordEnabled", False)):
            keywords = str(plan.get("captionKeywords") or "").strip()
            if keywords:
                caption["keyword"] = re.sub(r"[\s,，、]+", "|", keywords)
                caption["keyword_font_size"] = caption_keyword_font_size_from_plan(plan)
        if str(plan.get("captionTextAnimationIn") or "").strip():
            caption["in_animation"] = str(plan.get("captionTextAnimationIn") or "").strip()
        if str(plan.get("captionTextAnimationLoop") or "").strip():
            caption["loop_animation"] = str(plan.get("captionTextAnimationLoop") or "").strip()
        if str(plan.get("captionTextAnimationOut") or "").strip():
            caption["out_animation"] = str(plan.get("captionTextAnimationOut") or "").strip()
        captions.append(caption)
    return captions


def is_jianying_subtitle_recognition_plan(plan: dict[str, Any]) -> bool:
    return str(plan.get("subtitlePreset") or "").strip() == "jianying_recognize"


def caption_style_from_plan(plan: dict[str, Any], *, width: int, height: int) -> dict[str, Any]:
    transform_y = default_caption_transform_y(height)
    raw_transform_y = plan.get("captionTransformY")
    if raw_transform_y is not None:
        raw_y = int(raw_transform_y or 0)
        if raw_y == 690:
            transform_y = default_caption_transform_y(height)
        else:
            transform_y = -max(-height, min(height, raw_y + round(height * 0.14)))
    transform_x = max(-width, min(width, int(plan.get("captionTransformX") or 0)))
    alignment = max(0, min(2, int(plan.get("captionAlignment") if plan.get("captionAlignment") is not None else 1)))
    alpha = max(0.0, min(1.0, float(plan.get("captionAlpha") if plan.get("captionAlpha") is not None else 100) / 100))
    scale_x = max(0.2, min(3.0, float(plan.get("captionScaleX") if plan.get("captionScaleX") is not None else 100) / 100))
    scale_y = max(0.2, min(3.0, float(plan.get("captionScaleY") if plan.get("captionScaleY") is not None else 100) / 100))
    style: dict[str, Any] = {
        "text_color": str(plan.get("captionColor") or "#ffffff"),
        "border_color": str(plan.get("captionBorderColor") or "#111113"),
        "font": str(plan.get("captionFont") or "") or None,
        "alignment": alignment,
        "alpha": alpha,
        "font_size": caption_font_size_from_plan(plan),
        "scale_x": scale_x,
        "scale_y": scale_y,
        "transform_x": transform_x,
        "transform_y": transform_y,
        "letter_spacing": float(plan.get("captionLetterSpacing") if plan.get("captionLetterSpacing") is not None else 0),
        "line_spacing": float(plan.get("captionLineSpacing") if plan.get("captionLineSpacing") is not None else 0),
        "style_text": False,
        "underline": bool(plan.get("captionUnderline", False)),
        "italic": bool(plan.get("captionItalic", False)),
        "bold": bool(plan.get("captionBold", False)),
        "has_shadow": bool(plan.get("captionShadowEnabled", True)),
        "shadow_info": {
            "shadow_alpha": 0.82,
            "shadow_color": "#000000",
            "shadow_diffuse": 16.0,
            "shadow_distance": 6.0,
            "shadow_angle": -45.0,
        },
    }
    return style


def caption_font_size_from_plan(plan: dict[str, Any]) -> int:
    raw_size = round(float(plan.get("captionFontSize") or 10))
    if raw_size == 46:
        raw_size = 10
    return max(6, min(40, raw_size))


def caption_keyword_font_size_from_plan(plan: dict[str, Any]) -> int:
    raw_size = round(float(plan.get("captionKeywordFontSize") or 12))
    if raw_size == 58:
        raw_size = 12
    return max(6, min(46, raw_size))


def default_caption_transform_y(height: int) -> int:
    return round(height * -0.72)


def filter_infos_from_plan(plan: dict[str, Any], clips: list[DraftClip], *, target_duration_ms: int = 0) -> list[dict[str, Any]]:
    raw_layers = plan.get("filterLayers")
    if isinstance(raw_layers, list) and raw_layers:
        duration_ms = max(1, target_duration_ms or sum(clip.duration_ms for clip in clips))
        result: list[dict[str, Any]] = []
        for raw in raw_layers[:12]:
            if not isinstance(raw, dict):
                continue
            choice = str(raw.get("filterChoice") or "").strip()
            if choice == "recommended":
                choice = "1980"
            elif choice == "random":
                choice = "Lofi II"
            if not choice:
                continue
            start_ms = max(0, int(float(raw.get("startSec") or 0) * 1000))
            end_ms = int(float(raw.get("endSec") or 0) * 1000)
            if end_ms <= start_ms:
                end_ms = duration_ms
            end_ms = max(start_ms + 1, min(end_ms, duration_ms))
            intensity = float(raw.get("intensity") if raw.get("intensity") is not None else 100)
            opacity = float(raw.get("opacity") if raw.get("opacity") is not None else 100)
            result.append({
                "filter_title": choice,
                "start": start_ms * MICROSECONDS_PER_MS,
                "end": end_ms * MICROSECONDS_PER_MS,
                "intensity": int(clamp_float(intensity * opacity / 100, 0, 100)),
            })
        if result:
            return result

    choice = str(plan.get("filterChoice") or "").strip()
    if choice == "recommended":
        choice = "1980"
    elif choice == "random":
        choice = "Lofi II"
    if is_basic_dedupe_enabled(plan):
        duration_us = max(1, (target_duration_ms or sum(clip.duration_ms for clip in clips)) * MICROSECONDS_PER_MS)
        filters = [
            {"filter_title": name, "start": 0, "end": duration_us, "intensity": 2}
            for name in pick_basic_dedupe_filters(str(plan.get("id") or "draft"))
        ]
        if choice:
            filters.append(
                {
                    "filter_title": choice,
                    "start": 0,
                    "end": duration_us,
                    "intensity": int(clamp_float(float(plan.get("filterIntensity") or 100), 0, 100)),
                }
            )
        return filters
    if not choice:
        return []
    if not clips:
        return []
    duration_ms = max(1, sum(clip.duration_ms for clip in clips))
    start_ms = max(0, int(float(plan.get("filterStartSec") or 0) * 1000))
    end_ms = int(float(plan.get("filterEndSec") or 0) * 1000)
    if end_ms <= start_ms:
        end_ms = duration_ms
    end_ms = max(start_ms + 1, min(end_ms, duration_ms))
    intensity = int(clamp_float(float(plan.get("filterIntensity") or 100), 0, 100))
    track_count = int(clamp_float(float(plan.get("filterTrackCount") or 1), 1, 8))
    return [
        {"filter_title": choice, "start": start_ms * MICROSECONDS_PER_MS, "end": end_ms * MICROSECONDS_PER_MS, "intensity": intensity}
        for _ in range(track_count)
    ]


def effect_infos_from_plan(plan: dict[str, Any], clips: list[DraftClip]) -> list[dict[str, Any]]:
    raw_layers = plan.get("effectLayers")
    if isinstance(raw_layers, list) and raw_layers:
        duration_ms = max(1, sum(clip.duration_ms for clip in clips))
        result: list[dict[str, Any]] = []
        for raw in raw_layers[:12]:
            if not isinstance(raw, dict):
                continue
            choice = str(raw.get("effectChoice") or "").strip()
            if not choice:
                continue
            start_ms = max(0, int(float(raw.get("startSec") or 0) * 1000))
            end_ms = int(float(raw.get("endSec") or 0) * 1000)
            if end_ms <= start_ms:
                end_ms = duration_ms
            end_ms = max(start_ms + 1, min(end_ms, duration_ms))
            result.append({"effect_title": choice, "start": start_ms * MICROSECONDS_PER_MS, "end": end_ms * MICROSECONDS_PER_MS})
        if result:
            return result

    choice = str(plan.get("effectChoice") or "").strip()
    if not choice:
        return []
    if choice == "recommended":
        choice = "VCR"
    elif choice == "random":
        choice = "betamax"
    if not clips:
        return []
    duration_ms = max(1, sum(clip.duration_ms for clip in clips))
    start_ms = max(0, int(float(plan.get("effectStartSec") or 0) * 1000))
    end_ms = int(float(plan.get("effectEndSec") or 0) * 1000)
    if end_ms <= start_ms:
        end_ms = duration_ms
    end_ms = max(start_ms + 1, min(end_ms, duration_ms))
    return [{"effect_title": choice, "start": start_ms * MICROSECONDS_PER_MS, "end": end_ms * MICROSECONDS_PER_MS}]


def sticker_infos_from_plan(plan: dict[str, Any], *, target_duration_ms: int, width: int, height: int) -> list[dict[str, Any]]:
    raw_layers = plan.get("stickerLayers")
    if bool(plan.get("stickersEnabled", False)) and isinstance(raw_layers, list) and raw_layers:
        total_duration_us = max(1, target_duration_ms * MICROSECONDS_PER_MS)
        result: list[dict[str, Any]] = []
        for raw in raw_layers[:12]:
            if not isinstance(raw, dict):
                continue
            sticker_id = str(raw.get("stickerId") or "").strip()
            if not sticker_id:
                continue
            start_ms = max(0, int(float(raw.get("startSec") or 0) * 1000))
            end_ms = int(float(raw.get("endSec") or 0) * 1000)
            if end_ms <= start_ms:
                end_ms = target_duration_ms
            start_us = min(start_ms * MICROSECONDS_PER_MS, total_duration_us - 1)
            end_us = max(start_us + 1, min(end_ms * MICROSECONDS_PER_MS, total_duration_us))
            result.append(
                {
                    "sticker_id": sticker_id,
                    "start": start_us,
                    "end": end_us,
                    "scale": clamp_float(float(raw.get("scale") if raw.get("scale") is not None else 0.35), 0.05, 3.0),
                    "alpha": clamp_float(float(raw.get("alpha") if raw.get("alpha") is not None else 100) / 100, 0.0, 1.0),
                    "transform_x": round(width * clamp_float(float(raw.get("transformX") or 0), -1.0, 1.0)),
                    "transform_y": round(height * clamp_float(float(raw.get("transformY") or 0), -1.0, 1.0)),
                }
            )
        if result:
            return result

    if bool(plan.get("stickersEnabled", False)) and str(plan.get("stickerId") or "").strip():
        return [
            {
                "sticker_id": str(plan.get("stickerId") or "").strip(),
                "start": 0,
                "end": max(1, target_duration_ms * MICROSECONDS_PER_MS),
                "scale": clamp_float(float(plan.get("stickerScale") if plan.get("stickerScale") is not None else 0.35), 0.05, 3.0),
                "alpha": clamp_float(float(plan.get("stickerAlpha") if plan.get("stickerAlpha") is not None else 100) / 100, 0.0, 1.0),
                "transform_x": round(width * clamp_float(float(plan.get("stickerTransformX") if plan.get("stickerTransformX") is not None else 0.28), -1.0, 1.0)),
                "transform_y": round(height * clamp_float(float(plan.get("stickerTransformY") if plan.get("stickerTransformY") is not None else -0.3), -1.0, 1.0)),
            }
        ]

    if is_basic_dedupe_enabled(plan):
        return []

    if not bool(plan.get("dedupeStickerEnabled", False)):
        return []
    raw_ids = plan.get("dedupeStickerIds")
    if not isinstance(raw_ids, list):
        return []
    sticker_ids = [str(item).strip() for item in raw_ids if str(item).strip()]
    if not sticker_ids:
        return []
    total_duration_us = max(1, target_duration_ms * MICROSECONDS_PER_MS)
    result: list[dict[str, Any]] = []
    for index, sticker_id in enumerate(sticker_ids[:6]):
        result.append(
            {
                "sticker_id": sticker_id,
                "start": 0,
                "end": total_duration_us,
                "scale": 0.18,
                "alpha": 0.02,
                "transform_x": round(width * (0.32 if index % 2 == 0 else -0.32)),
                "transform_y": round(height * (-0.36 if index % 2 == 0 else 0.3)),
            }
        )
    return result


def is_basic_dedupe_enabled(plan: dict[str, Any]) -> bool:
    if plan.get("dedupeDisabled") is True:
        return False
    return True


def local_aux_video_media_url(path: Path) -> str:
    media_root = resolve_runtime_data_root() / "media"
    media_id = "__dedupe_aux__"
    media_dir = media_root / media_id
    media_dir.mkdir(parents=True, exist_ok=True)
    stat = path.stat()
    digest_source = f"{path.resolve()}:{stat.st_mtime_ns}:{stat.st_size}".encode("utf-8", errors="ignore")
    digest = hashlib.sha256(digest_source).hexdigest()[:16]
    suffix = path.suffix.lower() or ".mp4"
    target = media_dir / f"aux-{digest}{suffix}"
    if not target.exists() or target.stat().st_size != stat.st_size:
        shutil.copy2(path, target)
    return f"{runtime_public_base_url()}/media/{quote(media_id, safe='')}/{quote(target.name, safe='')}"


def pick_basic_dedupe_filters(seed: str) -> list[str]:
    candidates = load_basic_dedupe_filter_pool()
    return deterministic_sample(candidates, 5, f"{seed}:filters")


def load_basic_dedupe_filter_pool() -> list[str]:
    fallback = ["1980", "ABG", "Ditto", "KE1", "KV5D", "Lofi II", "VHS III", "涓夋磱VPC", "涔︽剰", "浜偆", "浠插缁垮厜", "浼奸敠"]
    root = Path(__file__).resolve().parent / "upstream" / "capcut-mate-main"
    if not root.exists():
        return fallback
    inserted = False
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
        inserted = True
    try:
        from src.pyJianYingDraft.metadata.filter_meta import FilterType

        names = [
            str(item.value.name).strip()
            for item in FilterType
            if str(item.value.name).strip() and not bool(item.value.is_vip)
        ]
        return names[:BASIC_DEDUPE_FILTER_POOL_LIMIT] or fallback
    except Exception:
        return fallback
    finally:
        if inserted:
            try:
                sys.path.remove(str(root))
            except ValueError:
                pass


def pick_basic_dedupe_sticker_ids(seed: str, *, count: int) -> list[str]:
    sticker_path = Path(__file__).resolve().parent / "upstream" / "capcut-mate-main" / "config" / "sticker.json"
    try:
        raw = json.loads(sticker_path.read_text(encoding="utf-8"))
    except Exception:
        raw = []
    ids: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            sticker_id = str(item.get("sticker_id") or "").strip()
            sticker = item.get("sticker") if isinstance(item.get("sticker"), dict) else {}
            sticker_type = int(sticker.get("sticker_type") or 0)
            title = str(item.get("title") or "").strip()
            if not sticker_id or sticker_type != 1:
                continue
            if title and _STICKER_FREE_TITLE_BLOCKLIST.search(title):
                continue
            ids.append(sticker_id)
            if len(ids) >= BASIC_DEDUPE_STICKER_POOL_LIMIT:
                break
    fallback = ["6927249213973679363", "7084388179691818247", "6895927477177240845", "7050482851074034976", "7050494369782123814", "7498203639404563736"]
    return deterministic_sample(ids or fallback, count, f"{seed}:stickers")


def dedupe_video_infos_from_plan(plan: dict[str, Any], *, target_duration_ms: int) -> list[dict[str, Any]]:
    if not is_basic_dedupe_enabled(plan):
        return []
    folder = Path(str(plan.get("dedupeAuxVideoFolderPath") or "").strip())
    if not folder.exists() or not folder.is_dir():
        return []
    video_files = sorted(
        [
            item
            for item in folder.rglob("*")
            if item.is_file() and item.suffix.lower() in {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
        ],
        key=lambda item: str(item).lower(),
    )
    if not video_files or target_duration_ms <= 0:
        return []
    requested_count = int(clamp_float(float(plan.get("dedupeAuxVideoCount") if plan.get("dedupeAuxVideoCount") is not None else 2), 0, 4))
    if requested_count <= 0:
        return []
    chosen_files = deterministic_sample(video_files, min(requested_count, len(video_files)), f"{plan.get('id') or 'draft'}:aux-video:{time.time_ns()}")
    while chosen_files and len(chosen_files) < requested_count:
        chosen_files.append(chosen_files[0])
    duration_us = max(1, target_duration_ms * MICROSECONDS_PER_MS)
    alpha = clamp_float(float(plan.get("dedupeAuxAlpha") if plan.get("dedupeAuxAlpha") is not None else 2) / 100, 0.0, 0.2)
    infos: list[dict[str, Any]] = []
    for index, chosen in enumerate(chosen_files[:requested_count]):
        try:
            video_url = local_aux_video_media_url(chosen)
        except Exception:
            video_url = str(chosen)
        source_duration_ms = probe_video_material_duration_ms(chosen) or probe_video_duration_ms(chosen) or target_duration_ms
        source_duration_us = max(1, source_duration_ms * MICROSECONDS_PER_MS)
        infos.append(
            {
                "video_url": video_url,
                "local_video_path": str(chosen),
                "start": 0,
                "end": duration_us,
                "duration": duration_us,
                "source_duration": source_duration_us,
                "speed": source_duration_us / duration_us,
                "volume": 0.0,
                "fit_mode": "cover",
                "alpha": alpha,
                "scale_x": 1.0,
                "scale_y": 1.0,
                "transform_x": 0,
                "transform_y": 0,
                "dedupe_aux_index": index + 1,
            }
        )
    return infos


def deterministic_sample(items: list[Any], count: int, seed: str) -> list[Any]:
    if not items or count <= 0:
        return []
    rng = random.Random(hashlib.sha256(seed.encode("utf-8")).hexdigest())
    pool = list(items)
    rng.shuffle(pool)
    return pool[: min(count, len(pool))]


def keyframes_from_plan(
    plan: dict[str, Any],
    clips: list[DraftClip],
    *,
    segment_ids: list[str],
    width: int,
    height: int,
) -> list[dict[str, Any]]:
  motion_preset = str(plan.get("motionPreset") or "").strip()
  if not motion_preset or not clips or not segment_ids:
    return []
  if motion_preset == "recommended":
    motion_preset = "light_push_in"
  elif motion_preset == "random":
    motion_preset = "light_pan_left"

  keyframes: list[dict[str, Any]] = []
  for segment_id, clip in zip(segment_ids, clips):
        if motion_preset == "light_push_in":
            end_offset = max(1, clip.duration_ms * MICROSECONDS_PER_MS)
            keyframes.extend(
                [
                    {"segment_id": segment_id, "property": "UNIFORM_SCALE", "offset": 0, "value": 1.0},
                    {"segment_id": segment_id, "property": "UNIFORM_SCALE", "offset": end_offset, "value": 1.08},
                ]
            )
        elif motion_preset == "light_pan_left":
            end_offset = max(1, clip.duration_ms * MICROSECONDS_PER_MS)
            keyframes.extend(
                [
                    {"segment_id": segment_id, "property": "KFTypePositionX", "offset": 0, "value": 0.04},
                    {"segment_id": segment_id, "property": "KFTypePositionX", "offset": end_offset, "value": -0.04},
                ]
            )
        elif motion_preset == "light_pan_right":
            end_offset = max(1, clip.duration_ms * MICROSECONDS_PER_MS)
            keyframes.extend(
                [
                    {"segment_id": segment_id, "property": "KFTypePositionX", "offset": 0, "value": -0.04},
                    {"segment_id": segment_id, "property": "KFTypePositionX", "offset": end_offset, "value": 0.04},
                ]
            )
        elif motion_preset == "light_rise":
            end_offset = max(1, clip.duration_ms * MICROSECONDS_PER_MS)
            keyframes.extend(
                [
                    {"segment_id": segment_id, "property": "KFTypePositionY", "offset": 0, "value": 0.04},
                    {"segment_id": segment_id, "property": "KFTypePositionY", "offset": end_offset, "value": -0.04},
                ]
            )
  return keyframes
