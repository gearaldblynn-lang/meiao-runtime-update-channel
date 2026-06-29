from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


STATUS_TIMEOUT_SECONDS = 5


def snapshot_task(
    legacy_globals: dict[str, Any],
    payload: dict[str, Any] | None = None,
    control: Any | None = None,
) -> tuple[int, dict[str, Any]]:
    if control is not None and hasattr(control, "raise_if_cancelled"):
        control.raise_if_cancelled()

    runtime_root = Path(legacy_globals.get("BASE_DIR") or Path.cwd())
    binary = runtime_root / "meiao-runtime.exe"
    if not binary.exists():
        return 500, {
            "ok": False,
            "error": f"meiao-runtime.exe not found under runtime root: {runtime_root}",
            "runtimeRoot": str(runtime_root),
        }

    try:
        completed = subprocess.run(
            [str(binary), "status", "--root", str(runtime_root)],
            cwd=str(runtime_root),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=STATUS_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 500, {
            "ok": False,
            "error": f"meiao-runtime status timed out after {STATUS_TIMEOUT_SECONDS}s",
            "runtimeRoot": str(runtime_root),
        }
    except OSError as error:
        return 500, {
            "ok": False,
            "error": f"meiao-runtime status failed to start: {error}",
            "runtimeRoot": str(runtime_root),
        }

    if control is not None and hasattr(control, "raise_if_cancelled"):
        control.raise_if_cancelled()

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if completed.returncode != 0:
        return 500, {
            "ok": False,
            "error": f"meiao-runtime status exited with code {completed.returncode}",
            "runtimeRoot": str(runtime_root),
            "stdout": _tail(stdout),
            "stderr": _tail(stderr),
        }

    try:
        status = json.loads(stdout)
    except json.JSONDecodeError as error:
        return 500, {
            "ok": False,
            "error": f"meiao-runtime status returned invalid JSON: {error}",
            "runtimeRoot": str(runtime_root),
            "stdout": _tail(stdout),
            "stderr": _tail(stderr),
        }

    if not isinstance(status, dict):
        return 500, {
            "ok": False,
            "error": "meiao-runtime status returned a non-object JSON payload",
            "runtimeRoot": str(runtime_root),
            "stdout": _tail(stdout),
            "stderr": _tail(stderr),
        }

    return 200, {
        "ok": True,
        "source": "meiao-runtime status",
        "runtimeRoot": str(runtime_root),
        "status": status,
    }


def _tail(value: str, limit: int = 2000) -> str:
    if len(value) <= limit:
        return value
    return value[-limit:]
