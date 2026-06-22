from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_ROOT = ROOT / "release" / "meiao-runtime"
DEFAULT_OUTPUT_ROOT = ROOT / ".tmp" / "runtime-data-backups"
QUICK_ITEMS = ["config.local.json", "storage", "logs", "integrations/upstream"]
LARGE_ITEMS = ["drafts", "media"]


def copy_path(source: Path, target: Path) -> tuple[int, int]:
    if source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return 1, source.stat().st_size
    if source.is_dir():
        files = 0
        bytes_total = 0
        for item in source.rglob("*"):
            if not item.is_file():
                continue
            relative = item.relative_to(source)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)
            files += 1
            bytes_total += item.stat().st_size
        return files, bytes_total
    return 0, 0


def backup(runtime_root: Path, output_root: Path, mode: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = output_root / f"meiao-runtime-data-{timestamp}-{mode}"
    backup_root.mkdir(parents=True, exist_ok=False)

    items = QUICK_ITEMS + (LARGE_ITEMS if mode == "full" else [])
    skipped = [] if mode == "full" else LARGE_ITEMS[:]
    copied: list[str] = []
    missing: list[str] = []
    file_count = 0
    byte_count = 0

    for item in items:
        source = runtime_root / item
        if not source.exists():
            missing.append(item)
            continue
        files, bytes_total = copy_path(source, backup_root / item)
        copied.append(item)
        file_count += files
        byte_count += bytes_total

    manifest = {
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "runtimeRoot": str(runtime_root),
        "mode": mode,
        "copied": copied,
        "skipped": skipped,
        "missing": missing,
        "fileCount": file_count,
        "byteCount": byte_count,
    }
    (backup_root / "backup-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return backup_root


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--mode", choices=["quick", "full"], default="full")
    args = parser.parse_args()

    path = backup(Path(args.runtime_root).resolve(), Path(args.output_root).resolve(), args.mode)
    print(f"Runtime data backup created: {path}")


if __name__ == "__main__":
    main()
