from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable


AtomicJsonWriter = Callable[[Path, dict[str, Any]], None]
ClientStateReader = Callable[[], dict[str, Any]]
ProjectUrlNormalizer = Callable[[str], str]


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _root_key(item: dict[str, Any]) -> str:
    return "::".join(str(item.get("key") or "").split("::")[:3])


def read_flow_progress_store(progress_file: Path) -> dict[str, Any]:
    if not progress_file.exists():
        return {"version": 1, "items": {}}
    try:
        data = json.loads(progress_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"version": 1, "items": {}}
        if not isinstance(data.get("items"), dict):
            data["items"] = {}
        return data
    except Exception:
        return {"version": 1, "items": {}}


def flow_progress_key(
    job_id: str | None,
    stage: str | None = None,
    slot_id: str | None = None,
    slot_index: int | None = None,
    phase: str | None = None,
) -> str:
    job_key = str(job_id or "FLOW").strip() or "FLOW"
    slot_key = str(slot_id or slot_index or "JOB").strip() or "JOB"
    stage_key = str(stage or "Flow").strip() or "Flow"
    phase_key = str(phase or "phase").strip() or "phase"
    return f"{job_key}::{stage_key}::{slot_key}::{phase_key}"


def write_flow_progress(
    progress_file: Path,
    atomic_write_json: AtomicJsonWriter,
    item: dict[str, Any],
    now_ms: int | None = None,
) -> None:
    job_id = str(item.get("jobId") or "").strip()
    if not job_id:
        return
    store = read_flow_progress_store(progress_file)
    items = store.get("items") if isinstance(store.get("items"), dict) else {}
    slot_index_raw = str(item.get("slotIndex") or "")
    key = flow_progress_key(
        job_id,
        str(item.get("stage") or "").strip() or None,
        str(item.get("slotId") or "").strip() or None,
        int(item.get("slotIndex")) if slot_index_raw.isdigit() else None,
        str(item.get("phase") or "").strip() or None,
    )
    items[key] = {
        **item,
        "key": key,
        "updatedAt": int(now_ms if now_ms is not None else time.time() * 1000),
    }
    ordered = sorted(items.values(), key=lambda entry: _to_int(entry.get("updatedAt")), reverse=True)[:80]
    atomic_write_json(
        progress_file,
        {"version": 1, "items": {str(entry.get("key")): entry for entry in ordered if entry.get("key")}},
    )


def clear_flow_progress_scope(
    progress_file: Path,
    atomic_write_json: AtomicJsonWriter,
    job_id: str | None,
    stage: str | None = None,
    slot_id: str | None = None,
    slot_index: int | None = None,
) -> None:
    if not job_id:
        return
    store = read_flow_progress_store(progress_file)
    items = store.get("items") if isinstance(store.get("items"), dict) else {}
    root = "::".join(flow_progress_key(job_id, stage, slot_id, slot_index, "scope").split("::")[:3])
    kept = {
        key: value
        for key, value in items.items()
        if "::".join(str(key).split("::")[:3]) != root
    }
    atomic_write_json(progress_file, {"version": 1, "items": kept})


def get_flow_progress(progress_file: Path, job_id: str | None = None) -> dict[str, Any]:
    store = read_flow_progress_store(progress_file)
    items = list((store.get("items") if isinstance(store.get("items"), dict) else {}).values())
    items = [item for item in items if isinstance(item, dict)]
    if job_id:
        items = [item for item in items if str(item.get("jobId") or "") == str(job_id)]

    phase_scoped_roots = {
        "::".join(str(item.get("key") or "").split("::")[:3])
        for item in items
        if len(str(item.get("key") or "").split("::")) >= 4
    }
    if phase_scoped_roots:
        items = [
            item
            for item in items
            if len(str(item.get("key") or "").split("::")) >= 4 or _root_key(item) not in phase_scoped_roots
        ]

    latest_starts: dict[str, int] = {}
    for item in items:
        root = _root_key(item)
        if not root or item.get("phase") != "start":
            continue
        latest_starts[root] = max(latest_starts.get(root, 0), _to_int(item.get("updatedAt")))
    if latest_starts:
        items = [
            item
            for item in items
            if _to_int(item.get("updatedAt")) >= latest_starts.get(_root_key(item), 0)
        ]

    latest_errors: dict[str, int] = {}
    for item in items:
        root = _root_key(item)
        if not root or item.get("phase") != "error" or item.get("status") != "failed":
            continue
        latest_errors[root] = max(latest_errors.get(root, 0), _to_int(item.get("updatedAt")))
    if latest_errors:
        items = [
            item
            for item in items
            if not (
                item.get("status") == "running"
                and _to_int(item.get("updatedAt")) <= latest_errors.get(_root_key(item), 0)
            )
        ]

    latest_done_sends: dict[str, int] = {}
    for item in items:
        root = _root_key(item)
        if not root or item.get("phase") != "send" or item.get("status") != "done":
            continue
        latest_done_sends[root] = max(latest_done_sends.get(root, 0), _to_int(item.get("updatedAt")))
    if latest_done_sends:
        items = [
            item
            for item in items
            if not (
                item.get("status") == "running"
                and item.get("phase") in {"start", "page"}
                and _to_int(item.get("updatedAt")) <= latest_done_sends.get(_root_key(item), 0)
            )
        ]

    items.sort(key=lambda entry: _to_int(entry.get("updatedAt")), reverse=True)
    return {"ok": True, "items": items[:80]}


def latest_flow_project_url_from_progress(
    progress_file: Path,
    job_id: str | None = None,
    project_url_normalizer: ProjectUrlNormalizer | None = None,
) -> str:
    progress = get_flow_progress(progress_file, job_id)
    for item in progress.get("items") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if re.search(r"/project/[^/?#]+", url):
            if project_url_normalizer:
                return project_url_normalizer(url) or url
            return url
    return ""


def flow_job_has_submission_evidence(
    progress_file: Path,
    job_id: str | None,
    read_client_state: ClientStateReader,
    now_ms: int | None = None,
    max_age_ms: int = 24 * 60 * 60 * 1000,
) -> bool:
    job_key = str(job_id or "").strip()
    if not job_key:
        return False
    current_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    try:
        progress = get_flow_progress(progress_file, job_key)
        recent_phases: set[str] = set()
        for item in progress.get("items") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("jobId") or "").strip() != job_key:
                continue
            updated_at = _to_int(item.get("updatedAt"))
            is_recent = updated_at <= 0 or current_ms - updated_at <= max_age_ms
            if str(item.get("phase") or "") == "send" and str(item.get("status") or "") == "done" and is_recent:
                return True
            if is_recent and str(item.get("status") or "") == "done":
                phase = str(item.get("phase") or "")
                if phase in {"input", "upload", "reference-files"}:
                    recent_phases.add(phase)
        if "input" in recent_phases and ("upload" in recent_phases or "reference-files" in recent_phases):
            return True
    except Exception:
        pass

    try:
        state = read_client_state()
        jobs = state.get("meiao-flow-fission-jobs") if isinstance(state, dict) else []
        if not isinstance(jobs, list):
            return False
        for job in jobs:
            if not isinstance(job, dict) or str(job.get("id") or "").strip() != job_key:
                continue
            submissions = job.get("flowSubmissions")
            if not isinstance(submissions, list):
                return False
            for submission in submissions:
                if not isinstance(submission, dict):
                    continue
                if str(submission.get("status") or "") != "submitted":
                    continue
                created_at = _to_int(submission.get("createdAt"))
                if created_at <= 0 or current_ms - created_at <= max_age_ms:
                    return True
    except Exception:
        pass
    return False
