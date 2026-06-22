from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from starlette.responses import Response


def _json_response(status_code: int, payload: dict[str, Any], headers: dict[str, str] | None = None) -> Response:
    return Response(
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        status_code=status_code,
        headers=headers or {},
        media_type="application/json; charset=utf-8",
    )


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve()
        resolved_root = root.resolve()
    except OSError:
        return False
    return resolved == resolved_root or resolved_root in resolved.parents


def template_preview_media(legacy_globals: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    media_root = Path(legacy_globals["MEDIA_ROOT"])
    video_file_suffixes = set(legacy_globals["VIDEO_FILE_SUFFIXES"])

    def media_url(path: Path) -> str:
        relative_parts = [quote(part, safe="") for part in path.relative_to(media_root).parts]
        return "/media/" + "/".join(relative_parts)

    videos = sorted(
        [
            item
            for item in media_root.rglob("*")
            if item.is_file() and item.suffix.lower() in video_file_suffixes
        ],
        key=lambda item: (0 if "scene" in str(item).lower() else 1, item.stat().st_size if item.exists() else 0),
    )
    return 200, {
        "videos": [
            {
                "url": media_url(item),
                "name": item.stem,
                "path": str(item),
            }
            for item in videos[:12]
        ]
    }


def allowed_audio_roots(legacy_globals: dict[str, Any]) -> list[Path]:
    base_dir = Path(legacy_globals["BASE_DIR"])
    return [
        base_dir / "integrations" / "capcut_mate" / "upstream" / "capcut-mate-main" / "output" / "draft",
        Path.home() / "AppData" / "Local" / "JianyingPro" / "User Data" / "Cache" / "music",
        Path.home() / "AppData" / "Local" / "JianyingPro" / "User Data" / "EMaterial",
        Path.home() / "AppData" / "Local" / "JianyingPro" / "User Data" / "Resources",
        Path.home() / "AppData" / "Local" / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft",
        Path.home() / "AppData" / "Local" / "CapCut" / "User Data" / "Cache" / "music",
        Path.home() / "AppData" / "Local" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft",
    ]


def audio_file_response(
    legacy_globals: dict[str, Any],
    raw_path: str,
    range_header: str | None,
    headers: dict[str, str] | None = None,
) -> Response:
    audio_file_suffixes = set(legacy_globals["AUDIO_FILE_SUFFIXES"])
    target = Path(raw_path)
    response_headers = dict(headers or {})

    if not raw_path or not target.exists() or not target.is_file() or target.suffix.lower() not in audio_file_suffixes:
        return _json_response(404, {"error": "音频文件不存在。"}, response_headers)

    resolved = target.resolve()
    if not any(root.exists() and _path_is_under(resolved, root) for root in allowed_audio_roots(legacy_globals)):
        return _json_response(403, {"error": "音频路径不在允许范围。"}, response_headers)

    mime = mimetypes.guess_type(target.name)[0] or "audio/mpeg"
    file_size = target.stat().st_size
    base_headers = {
        **response_headers,
        "Accept-Ranges": "bytes",
        "Content-Type": mime,
    }

    if range_header:
        match = re.match(r"bytes=(\d*)-(\d*)", str(range_header))
        if match and file_size > 0:
            start_text, end_text = match.groups()
            start = int(start_text) if start_text else 0
            end = int(end_text) if end_text else file_size - 1
            start = max(0, min(start, file_size - 1))
            end = max(start, min(end, file_size - 1))
            length = end - start + 1
            with target.open("rb") as file:
                file.seek(start)
                body = file.read(length)
            return Response(
                body,
                status_code=206,
                headers={
                    **base_headers,
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Content-Length": str(length),
                },
                media_type=mime,
            )

    body = target.read_bytes()
    return Response(
        body,
        status_code=200,
        headers={**base_headers, "Content-Length": str(file_size)},
        media_type=mime,
    )
