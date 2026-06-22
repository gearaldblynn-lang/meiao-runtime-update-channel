from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_ROOT = ROOT / "release" / "meiao-runtime"
TERMINAL_STATUSES = {"success", "failed", "cancelled", "canceled"}
ACTIVE_STATUSES = {"queued", "running"}


def load_json(path: Path) -> Any:
    if not path.exists():
        return {"tasks": []}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def task_time(task: dict[str, Any]) -> str:
    return str(task.get("updatedAt") or task.get("createdAt") or "")


def compact_value(value: Any) -> Any:
    if value is None:
        return None
    data = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(data) <= 800:
        return value
    keys = sorted(value.keys()) if isinstance(value, dict) else []
    return {
        "_compacted": True,
        "type": type(value).__name__,
        "keys": keys[:30],
        "bytes": len(data.encode("utf-8")),
    }


def compact_logs(value: Any, max_logs: int) -> Any:
    if not isinstance(value, list):
        return value
    compacted_items = [compact_value(item) for item in value]
    if len(value) <= max_logs:
        return compacted_items
    keep_head = max_logs // 2
    keep_tail = max_logs - keep_head
    return [
        *compacted_items[:keep_head],
        {"message": f"Compacted {len(value) - max_logs} middle log entries"},
        *compacted_items[-keep_tail:],
    ]


def compact_task(task: dict[str, Any], max_logs: int) -> dict[str, Any]:
    status = str(task.get("status") or "")
    if status in ACTIVE_STATUSES:
        return task
    compacted = dict(task)
    compacted["payload"] = compact_value(compacted.get("payload"))
    compacted["result"] = compact_value(compacted.get("result"))
    compacted["logs"] = compact_logs(compacted.get("logs"), max_logs)
    compacted["compactedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return compacted


def compact_payload(payload: Any, keep_full_terminal: int, max_logs: int) -> dict[str, Any]:
    tasks = payload.get("tasks") if isinstance(payload, dict) else []
    if not isinstance(tasks, list):
        tasks = []
    terminal = [
        task for task in tasks
        if isinstance(task, dict) and str(task.get("status") or "") in TERMINAL_STATUSES
    ]
    keep_terminal_ids = {
        str(task.get("id"))
        for task in sorted(terminal, key=task_time, reverse=True)[:max(0, keep_full_terminal)]
    }
    next_tasks = []
    for task in tasks:
        if not isinstance(task, dict):
            next_tasks.append(task)
            continue
        if str(task.get("id")) in keep_terminal_ids:
            next_tasks.append(task)
        else:
            next_tasks.append(compact_task(task, max_logs))
    return {"tasks": next_tasks}


def write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{int(time.time() * 1000)}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def backup_file(path: Path) -> Path:
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{path.stem}-{time.strftime('%Y%m%d-%H%M%S')}{path.suffix}"
    backup.write_bytes(path.read_bytes())
    return backup


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--keep-full-terminal", type=int, default=0)
    parser.add_argument("--max-logs", type=int, default=20)
    args = parser.parse_args()

    runtime_root = Path(args.runtime_root).resolve()
    tasks_path = runtime_root / "storage" / "runtime-tasks" / "tasks.json"
    before_bytes = tasks_path.stat().st_size if tasks_path.exists() else 0
    payload = load_json(tasks_path)
    next_payload = compact_payload(payload, args.keep_full_terminal, max(2, args.max_logs))
    next_data = json.dumps(next_payload, ensure_ascii=False, indent=2)
    after_bytes = len(next_data.encode("utf-8"))
    task_count = len(next_payload.get("tasks", [])) if isinstance(next_payload.get("tasks"), list) else 0
    compacted_count = sum(
        1 for task in next_payload.get("tasks", [])
        if isinstance(task, dict) and task.get("compactedAt")
    )
    backup_path = ""
    if args.apply and tasks_path.exists():
        backup_path = str(backup_file(tasks_path))
        write_atomic(tasks_path, next_payload)
    print(json.dumps({
        "path": str(tasks_path),
        "beforeBytes": before_bytes,
        "afterBytes": after_bytes,
        "savedBytes": max(0, before_bytes - after_bytes),
        "taskCount": task_count,
        "compactedCount": compacted_count,
        "wouldWrite": bool(args.apply),
        "backupPath": backup_path,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
