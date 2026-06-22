from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote


AtomicJsonWriter = Callable[[Path, dict[str, Any]], None]
Sanitizer = Callable[[str], str]
DebugLogger = Callable[[str, dict[str, Any]], None]


def read_reference_file_store(store_file: Path) -> dict[str, Any]:
    if not store_file.exists():
        return {"version": 1, "jobs": {}}
    try:
        data = json.loads(store_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"version": 1, "jobs": {}}
        if not isinstance(data.get("jobs"), dict):
            data["jobs"] = {}
        data.setdefault("version", 1)
        return data
    except Exception:
        return {"version": 1, "jobs": {}}


def write_reference_file_store(
    store_file: Path,
    atomic_write_json: AtomicJsonWriter,
    store: dict[str, Any],
) -> None:
    atomic_write_json(store_file, store)


def remember_reference_files(
    store_file: Path,
    atomic_write_json: AtomicJsonWriter,
    sanitize_filename: Sanitizer,
    append_debug_log: DebugLogger,
    job_id: str | None,
    reference_files: list[Path],
    now_ms: int | None = None,
) -> list[str]:
    job_key = sanitize_filename(job_id or "")
    if not job_key or not reference_files:
        return []
    files = [path.name for path in reference_files if path.exists()]
    if not files:
        return []
    store = read_reference_file_store(store_file)
    jobs = store.setdefault("jobs", {})
    current = jobs.get(job_key) if isinstance(jobs.get(job_key), dict) else {}
    jobs[job_key] = {
        **current,
        "files": sorted(set([*list(current.get("files") or []), *files])),
        "updatedAt": int(now_ms if now_ms is not None else time.time() * 1000),
    }
    write_reference_file_store(store_file, atomic_write_json, store)
    append_debug_log("api.flow.page.reference_files", {"jobId": job_key, "files": files})
    return files


def extract_media_name_token(url: str | None) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    match = re.search(r"[?&]name=([^&#]+)", value)
    return unquote(match.group(1)).strip() if match else ""


def remember_reference_media_urls(
    store_file: Path,
    atomic_write_json: AtomicJsonWriter,
    sanitize_filename: Sanitizer,
    append_debug_log: DebugLogger,
    job_id: str | None,
    prompt_media_status: dict[str, Any] | None,
    now_ms: int | None = None,
) -> list[str]:
    job_key = sanitize_filename(job_id or "")
    if not job_key or not isinstance(prompt_media_status, dict):
        return []
    urls: list[str] = []
    tokens: list[str] = []
    for item in prompt_media_status.get("promptMedia") or []:
        if not isinstance(item, dict):
            continue
        src = str(item.get("src") or "").strip()
        if not src:
            continue
        urls.append(src)
        token = extract_media_name_token(src)
        if token:
            tokens.append(token)
    if not urls and not tokens:
        return []
    store = read_reference_file_store(store_file)
    jobs = store.setdefault("jobs", {})
    current = jobs.get(job_key) if isinstance(jobs.get(job_key), dict) else {}
    jobs[job_key] = {
        **current,
        "files": list(current.get("files") or []),
        "urls": sorted(set([*list(current.get("urls") or []), *urls])),
        "mediaTokens": sorted(set([*list(current.get("mediaTokens") or []), *tokens])),
        "updatedAt": int(now_ms if now_ms is not None else time.time() * 1000),
    }
    write_reference_file_store(store_file, atomic_write_json, store)
    append_debug_log("api.flow.page.reference_media_urls", {"jobId": job_key, "urls": urls, "mediaTokens": tokens})
    return urls


def get_reference_file_names(
    store_file: Path,
    sanitize_filename: Sanitizer,
    job_id: str | None,
) -> set[str]:
    job_key = sanitize_filename(job_id or "")
    if not job_key:
        return set()
    store = read_reference_file_store(store_file)
    jobs = store.get("jobs") if isinstance(store.get("jobs"), dict) else {}
    job_entry = jobs.get(job_key) if isinstance(jobs, dict) else None
    if not isinstance(job_entry, dict):
        return set()
    return {str(item).strip() for item in (job_entry.get("files") or []) if str(item or "").strip()}


def get_reference_media_evidence(
    store_file: Path,
    sanitize_filename: Sanitizer,
    job_id: str | None,
) -> tuple[set[str], set[str]]:
    job_key = sanitize_filename(job_id or "")
    if not job_key:
        return set(), set()
    store = read_reference_file_store(store_file)
    jobs = store.get("jobs") if isinstance(store.get("jobs"), dict) else {}
    job_entry = jobs.get(job_key) if isinstance(jobs, dict) else None
    if not isinstance(job_entry, dict):
        return set(), set()
    urls = {str(item).strip() for item in (job_entry.get("urls") or []) if str(item or "").strip()}
    tokens = {str(item).strip() for item in (job_entry.get("mediaTokens") or []) if str(item or "").strip()}
    return urls, tokens
