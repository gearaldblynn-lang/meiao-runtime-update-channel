from __future__ import annotations

import json
import inspect
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SUPPORTED_FIXTURE_TASK_TYPES = {"fixture-success", "fixture-fail", "fixture-sleep"}
TERMINAL_STATUSES = {"success", "failed", "cancelled", "canceled"}
COMPACT_VALUE_MAX_BYTES = 800
COMPACT_MAX_LOGS = 20


class TaskControl:
    def __init__(self, cancel_event: threading.Event) -> None:
        self._cancel_event = cancel_event

    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancel_requested():
            raise InterruptedError()


class TaskRuntime:
    def __init__(self, data_root: Path, handlers: dict[str, Callable[[dict[str, Any]], Any]] | None = None) -> None:
        self.data_root = Path(data_root)
        self.root = self.data_root / "runtime-tasks"
        self.store_file = self.root / "tasks.json"
        self.handlers = handlers or {}
        self._lock = threading.RLock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._tasks: dict[str, dict[str, Any]] = {}
        self._load()

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            tasks = [self._public_task(task) for task in self._tasks.values()]
        return sorted(tasks, key=lambda task: str(task.get("createdAt", "")), reverse=True)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return self._public_task(task) if task else None

    def create_task(self, task_type: str, payload: Any | None = None) -> dict[str, Any]:
        if task_type not in SUPPORTED_FIXTURE_TASK_TYPES and task_type not in self.handlers:
            raise ValueError(f"Unsupported task type: {task_type}")
        task_id = f"task-{uuid.uuid4().hex}"
        now = self._now()
        task = {
            "id": task_id,
            "type": task_type,
            "status": "queued",
            "progress": 0,
            "payload": payload if isinstance(payload, dict) else {},
            "result": None,
            "error": None,
            "logs": [{"at": now, "message": "Task queued"}],
            "cancelRequested": False,
            "createdAt": now,
            "updatedAt": now,
        }
        cancel_event = threading.Event()
        with self._lock:
            self._tasks[task_id] = task
            self._cancel_events[task_id] = cancel_event
            self._save_locked()
        worker = threading.Thread(target=self._run_task, args=(task_id, cancel_event), daemon=True)
        worker.start()
        return self._public_task(task)

    def cancel_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            if task.get("status") in TERMINAL_STATUSES:
                return self._public_task(task)
            task["cancelRequested"] = True
            self._append_log_locked(task, "Cancel requested")
            self._save_locked()
            cancel_event = self._cancel_events.get(task_id)
            if cancel_event:
                cancel_event.set()
            if task.get("status") == "queued":
                self._finish_locked(task, "cancelled", progress=task.get("progress", 0), result=None, error=None)
            return self._public_task(task)

    def _run_task(self, task_id: str, cancel_event: threading.Event) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.get("status") in TERMINAL_STATUSES:
                return
            self._update_locked(task, status="running", progress=10)
            self._append_log_locked(task, "Task started")
            self._save_locked()

        try:
            task_type = str(task.get("type"))
            if task_type == "fixture-success":
                time.sleep(0.05)
                self._raise_if_cancelled(cancel_event)
                with self._lock:
                    current = self._tasks[task_id]
                    payload = current.get("payload") if isinstance(current.get("payload"), dict) else {}
                    self._finish_locked(
                        current,
                        "success",
                        progress=100,
                        result={"ok": True, "echo": payload},
                        error=None,
                    )
            elif task_type == "fixture-fail":
                time.sleep(0.05)
                raise RuntimeError("Fixture task failed")
            elif task_type == "fixture-sleep":
                payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
                seconds = payload.get("seconds", 1)
                try:
                    total = min(max(float(seconds), 0.1), 10.0)
                except (TypeError, ValueError):
                    total = 1.0
                steps = max(int(total / 0.05), 1)
                for index in range(steps):
                    self._raise_if_cancelled(cancel_event)
                    time.sleep(total / steps)
                    with self._lock:
                        current = self._tasks.get(task_id)
                        if current is None:
                            return
                        progress = min(95, 10 + int((index + 1) / steps * 80))
                        self._update_locked(current, status="running", progress=progress, save=False)
                        self._save_locked()
                self._raise_if_cancelled(cancel_event)
                with self._lock:
                    current = self._tasks[task_id]
                    self._finish_locked(current, "success", progress=100, result={"ok": True}, error=None)
            elif task_type in self.handlers:
                self._raise_if_cancelled(cancel_event)
                with self._lock:
                    current = self._tasks[task_id]
                    self._update_locked(current, status="running", progress=35, save=False)
                    self._append_log_locked(current, f"Running {task_type}")
                    self._save_locked()
                    payload = current.get("payload") if isinstance(current.get("payload"), dict) else {}
                result = self._call_handler(self.handlers[task_type], payload, TaskControl(cancel_event))
                self._raise_if_cancelled(cancel_event)
                status_code, result_payload = self._normalize_handler_result(result)
                if status_code >= 400:
                    error_message = self._error_from_payload(result_payload) or f"{task_type} failed with status {status_code}"
                    with self._lock:
                        current = self._tasks[task_id]
                        self._finish_locked(current, "failed", progress=100, result=result_payload, error=error_message)
                else:
                    with self._lock:
                        current = self._tasks[task_id]
                        self._finish_locked(current, "success", progress=100, result=result_payload, error=None)
        except InterruptedError:
            with self._lock:
                current = self._tasks.get(task_id)
                if current:
                    self._finish_locked(current, "cancelled", progress=current.get("progress", 0), result=None, error=None)
        except Exception as error:
            with self._lock:
                current = self._tasks.get(task_id)
                if current:
                    self._finish_locked(current, "failed", progress=current.get("progress", 0), result=None, error=str(error))
        finally:
            with self._lock:
                self._cancel_events.pop(task_id, None)

    def _raise_if_cancelled(self, cancel_event: threading.Event) -> None:
        if cancel_event.is_set():
            raise InterruptedError()

    def _call_handler(self, handler: Callable[[dict[str, Any]], Any], payload: dict[str, Any], control: TaskControl) -> Any:
        try:
            signature = inspect.signature(handler)
        except (TypeError, ValueError):
            return handler(payload)
        parameters = list(signature.parameters.values())
        if any(parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters):
            return handler(payload, control)
        positional = [
            parameter
            for parameter in parameters
            if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        if len(positional) >= 2:
            return handler(payload, control)
        control_parameter = signature.parameters.get("control")
        if control_parameter and control_parameter.kind == inspect.Parameter.KEYWORD_ONLY:
            return handler(payload, control=control)
        return handler(payload)

    def _update_locked(self, task: dict[str, Any], *, status: str, progress: int, save: bool = True) -> None:
        task["status"] = status
        task["progress"] = progress
        task["updatedAt"] = self._now()
        if save:
            self._save_locked()

    def _finish_locked(
        self,
        task: dict[str, Any],
        status: str,
        *,
        progress: Any,
        result: Any,
        error: str | None,
    ) -> None:
        task["status"] = status
        task["progress"] = progress
        task["result"] = result
        task["error"] = error
        task["updatedAt"] = self._now()
        self._append_log_locked(task, f"Task {status}")
        self._save_locked()

    def _append_log_locked(self, task: dict[str, Any], message: str) -> None:
        logs = task.get("logs")
        if not isinstance(logs, list):
            logs = []
            task["logs"] = logs
        logs.append({"at": self._now(), "message": message})

    def _public_task(self, task: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(task, ensure_ascii=False))

    def _normalize_handler_result(self, result: Any) -> tuple[int, Any]:
        if isinstance(result, tuple) and len(result) == 2:
            status, payload = result
            try:
                return int(status), payload
            except (TypeError, ValueError):
                return 500, {"error": f"Invalid task handler status: {status}"}
        return 200, result

    def _error_from_payload(self, payload: Any) -> str:
        if isinstance(payload, dict):
            value = payload.get("error") or payload.get("message") or payload.get("msg")
            if value is not None:
                return str(value)
        return ""

    def _load(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.store_file.exists():
            self._tasks = {}
            return
        try:
            payload = json.loads(self.store_file.read_text(encoding="utf-8"))
        except Exception:
            self._tasks = {}
            return
        tasks = payload.get("tasks") if isinstance(payload, dict) else []
        loaded: dict[str, dict[str, Any]] = {}
        if isinstance(tasks, list):
            for item in tasks:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    task = dict(item)
                    if task.get("status") in {"queued", "running"}:
                        task["status"] = "failed"
                        task["error"] = "Runtime restarted before task completed"
                        task["updatedAt"] = self._now()
                    loaded[task["id"]] = task
        self._tasks = loaded
        if loaded:
            self._save_locked()

    def _save_locked(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {"tasks": [self._compact_task_for_storage(task) for task in self._tasks.values()]}
        data = json.dumps(payload, ensure_ascii=False, indent=2)
        tmp_file = self.store_file.with_name(f"{self.store_file.name}.{uuid.uuid4().hex}.tmp")
        tmp_file.write_text(data, encoding="utf-8")
        tmp_file.replace(self.store_file)

    def _compact_task_for_storage(self, task: dict[str, Any]) -> dict[str, Any]:
        if str(task.get("status") or "") not in TERMINAL_STATUSES:
            return task
        compacted = dict(task)
        compacted["payload"] = self._compact_value_for_storage(compacted.get("payload"))
        compacted["result"] = self._compact_value_for_storage(compacted.get("result"))
        compacted["logs"] = self._compact_logs_for_storage(compacted.get("logs"))
        return compacted

    def _compact_value_for_storage(self, value: Any) -> Any:
        if value is None:
            return None
        data = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if len(data.encode("utf-8")) <= COMPACT_VALUE_MAX_BYTES:
            return value
        keys = sorted(value.keys()) if isinstance(value, dict) else []
        return {
            "_compacted": True,
            "type": type(value).__name__,
            "keys": keys[:30],
            "bytes": len(data.encode("utf-8")),
        }

    def _compact_logs_for_storage(self, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        compacted_items = [self._compact_value_for_storage(item) for item in value]
        if len(compacted_items) <= COMPACT_MAX_LOGS:
            return compacted_items
        keep_head = COMPACT_MAX_LOGS // 2
        keep_tail = COMPACT_MAX_LOGS - keep_head
        return [
            *compacted_items[:keep_head],
            {"message": f"Compacted {len(compacted_items) - COMPACT_MAX_LOGS} middle log entries"},
            *compacted_items[-keep_tail:],
        ]

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
