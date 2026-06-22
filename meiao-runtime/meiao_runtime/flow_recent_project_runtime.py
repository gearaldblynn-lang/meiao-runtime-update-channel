from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable


AtomicJsonWriter = Callable[[Path, Any], None]

PROJECT_URL_PATTERN = re.compile(r"https://labs\.google/fx/zh/tools/flow/project/[0-9a-f-]+", re.I)


def normalize_project_url(url: object) -> str:
    value = str(url or "").strip()
    match = PROJECT_URL_PATTERN.search(value)
    return match.group(0) if match else ""


def read_recent_project_urls(recent_projects_file: Path) -> list[str]:
    try:
        raw = json.loads(recent_projects_file.read_text(encoding="utf-8")) if recent_projects_file.exists() else []
        if isinstance(raw, list):
            return [url for url in (normalize_project_url(item) for item in raw) if url]
        if isinstance(raw, dict) and isinstance(raw.get("urls"), list):
            return [url for url in (normalize_project_url(item) for item in raw.get("urls")) if url]
    except Exception:
        return []
    return []


def remember_project_url(
    recent_projects_file: Path,
    atomic_write_json: AtomicJsonWriter,
    url: object,
) -> None:
    project_url = normalize_project_url(url)
    if not project_url:
        return
    urls = [item for item in read_recent_project_urls(recent_projects_file) if item != project_url]
    urls.insert(0, project_url)
    atomic_write_json(recent_projects_file, urls[:8])


def _append_log_project_urls(urls: list[str], log_root: Path) -> None:
    try:
        log_path = log_root / "ingest-debug.jsonl"
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-300:]
            for line in reversed(lines):
                url = normalize_project_url(line)
                if url:
                    urls.append(url)
    except Exception:
        pass


def _append_client_state_project_urls(urls: list[str], client_state_file: Path, limit: int) -> None:
    try:
        candidate_files = [client_state_file]
        if client_state_file.parent.exists():
            candidate_files.extend(
                sorted(client_state_file.parent.glob("state*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:8]
            )
        for path in candidate_files:
            if not path.exists() or path.stat().st_size > 16_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for match in reversed(PROJECT_URL_PATTERN.findall(text)):
                urls.append(match)
                if len(urls) >= limit * 4:
                    break
            if len(urls) >= limit * 4:
                break
    except Exception:
        pass


def recent_project_urls(
    recent_projects_file: Path,
    log_root: Path,
    client_state_file: Path,
    limit: int = 5,
) -> list[str]:
    urls = read_recent_project_urls(recent_projects_file)
    _append_log_project_urls(urls, log_root)
    _append_client_state_project_urls(urls, client_state_file, limit)

    result: list[str] = []
    for url in urls:
        if url and url not in result:
            result.append(url)
        if len(result) >= max(1, limit):
            break
    return result
