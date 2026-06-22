from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable


class StateStore:
    def __init__(self, legacy_globals: dict[str, Any]) -> None:
        self.legacy_globals = legacy_globals
        self._lock = threading.RLock()

    def run_exclusive(self, callback: Callable[[], Any]) -> Any:
        with self._lock:
            return callback()

    def recover_client_state(self) -> dict[str, Any]:
        recover = self.legacy_globals.get("build_recovered_client_state")
        if not callable(recover):
            raise RuntimeError("Legacy client state recovery is unavailable.")
        value = recover()
        return value if isinstance(value, dict) else {}

    def check_client_state_health(self) -> dict[str, Any]:
        check_health = self.legacy_globals.get("build_client_state_health")
        if not callable(check_health):
            raise RuntimeError("Legacy client state health is unavailable.")
        value = check_health()
        return value if isinstance(value, dict) else {}

    def read_client_state(self) -> dict[str, Any]:
        with self._lock:
            path = self._path("CLIENT_STATE_FILE")
            if not path.exists():
                return self._read_latest_client_state_backup()
            payload = self._read_json(path, {})
            return payload if isinstance(payload, dict) and payload else self._read_latest_client_state_backup()

    def write_client_state(self, state: dict[str, Any], *, snapshot_reason: str = "before-write") -> None:
        if not isinstance(state, dict):
            raise TypeError("client state must be a dict")
        with self._lock:
            current = self.read_client_state()
            should_snapshot = self.legacy_globals.get("should_snapshot_client_state_change")
            snapshot = self.legacy_globals.get("snapshot_client_state")
            if callable(should_snapshot) and callable(snapshot) and should_snapshot(current, state):
                snapshot(current, snapshot_reason)
            self._atomic_write_json(self._path("CLIENT_STATE_FILE"), state)

    def read_sidecar(self, name: str) -> list[dict[str, Any]]:
        with self._lock:
            payload = self._read_json(self._sidecar_path(name), [])
            items = payload if isinstance(payload, list) else []
            if name == "batch-draft-projects":
                normalize = self.legacy_globals.get("normalize_batch_draft_projects")
                if callable(normalize):
                    return normalize(items)
            return [item for item in items if isinstance(item, dict)]

    def write_sidecar(self, name: str, items: list[dict[str, Any]]) -> None:
        if not isinstance(items, list):
            raise TypeError(f"{name} sidecar must be a list")
        with self._lock:
            payload: list[dict[str, Any]] = items
            if name == "batch-draft-projects":
                normalize = self.legacy_globals.get("normalize_batch_draft_projects")
                if callable(normalize):
                    payload = normalize(items)
            self._atomic_write_json(self._sidecar_path(name), payload)

    def sync_client_state_sidecars(self, state: dict[str, Any]) -> None:
        with self._lock:
            media_items = state.get("meiao-ingest-items")
            if isinstance(media_items, list):
                merge_media = self.legacy_globals.get("merge_media_library_items")
                items = merge_media(media_items, []) if callable(merge_media) else media_items
                self.write_sidecar("media-library", items)

            draft_templates = state.get("meiao-draft-templates")
            if isinstance(draft_templates, list):
                merge_templates = self.legacy_globals.get("merge_draft_templates")
                templates = merge_templates(draft_templates, []) if callable(merge_templates) else draft_templates
                self.write_sidecar("draft-templates", templates)

            batch_projects = state.get("meiao-batch-draft-projects")
            if isinstance(batch_projects, list):
                self.write_sidecar("batch-draft-projects", batch_projects)

    def sync_client_state_value(self, key: str, value: object, *, replace: bool = False) -> None:
        if not key.startswith("meiao-"):
            return
        with self._lock:
            current = self.read_client_state()
            should_skip = self.legacy_globals.get("should_skip_empty_client_state_update")
            if callable(should_skip) and should_skip(key, value, current.get(key), allow_empty=replace):
                return
            merge_value = self.legacy_globals.get("merge_client_state_value")
            current[key] = merge_value(key, value, current.get(key), replace) if callable(merge_value) else value
            self.write_client_state(current)
            self.sync_client_state_sidecars(current)

    def _path(self, legacy_name: str) -> Path:
        value = self.legacy_globals.get(legacy_name)
        if value is None:
            raise RuntimeError(f"Legacy path {legacy_name} is unavailable.")
        return Path(value)

    def _sidecar_path(self, name: str) -> Path:
        mapping = {
            "media-library": "MEDIA_LIBRARY_FILE",
            "draft-templates": "DRAFT_TEMPLATES_FILE",
            "batch-draft-projects": "BATCH_DRAFT_PROJECTS_FILE",
        }
        if name not in mapping:
            raise KeyError(f"Unknown sidecar {name}")
        return self._path(mapping[name])

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _read_latest_client_state_backup(self) -> dict[str, Any]:
        fallback = self.legacy_globals.get("read_latest_client_state_backup")
        if callable(fallback):
            value = fallback()
            return value if isinstance(value, dict) else {}
        return {}

    def _atomic_write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(payload, ensure_ascii=False, indent=2)
        last_error: BaseException | None = None
        for attempt in range(10):
            tmp_file = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
            try:
                tmp_file.write_text(data, encoding="utf-8")
                tmp_file.replace(path)
                return
            except PermissionError as error:
                last_error = error
                time.sleep(min(0.05 * (attempt + 1), 0.5))
            finally:
                try:
                    if tmp_file.exists():
                        tmp_file.unlink()
                except Exception:
                    pass
        append_debug_log = self.legacy_globals.get("append_debug_log")
        if callable(append_debug_log):
            append_debug_log("state_store.write.error", {"path": str(path), "error": str(last_error)})
        if last_error:
            raise last_error
        raise RuntimeError(f"Failed to write {path}")
