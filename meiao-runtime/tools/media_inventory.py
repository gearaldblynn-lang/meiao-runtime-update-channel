from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_ROOT = ROOT / "release" / "meiao-runtime"
AUXILIARY_NAMES = {
    "__capcut_assets__",
    "__dedupe_aux__",
    "_capcut_assets",
    "bgm-library",
    "capcut-fonts",
    "capcut-test-audio",
}


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def stable_script_id(ingest_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "", str(ingest_id or "").strip().lower())
    return f"S-{slug[:24]}" if slug else ""


def media_id_from_item(item: dict[str, Any]) -> str:
    for key in ("backendMediaId", "mediaId", "fileId"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    for key in ("remoteVideoUrl", "sourceVideoUrl", "sourceUrl"):
        value = str(item.get(key) or "").strip()
        if "/media/" in value:
            return value.split("/media/", 1)[1].split("?", 1)[0].split("#", 1)[0].split("/", 1)[0]
    return ""


def category_for_dir(name: str, ingest_media_ids: set[str]) -> str:
    lower = name.lower()
    if name in ingest_media_ids:
        return "ingest-media"
    if name in AUXILIARY_NAMES:
        return "auxiliary"
    if lower.startswith("flow-fission") or lower.startswith("_orphan-flow-fission"):
        return "flow-generated"
    return "unknown"


def inventory(runtime_root: Path) -> dict[str, Any]:
    state_path = runtime_root / "storage" / "client-state" / "state.json"
    media_root = runtime_root / "storage" / "media"
    state = load_json(state_path)
    ingest_items = state.get("meiao-ingest-items") if isinstance(state, dict) else []
    ingest_items = ingest_items if isinstance(ingest_items, list) else []
    ingest_media_ids = {
        media_id_from_item(item)
        for item in ingest_items
        if isinstance(item, dict) and media_id_from_item(item)
    }
    directories: list[dict[str, Any]] = []
    media_dirs = [path for path in media_root.iterdir() if path.is_dir()] if media_root.exists() else []
    for path in sorted(media_dirs, key=lambda item: item.name.lower()):
        files = [item for item in path.rglob("*") if item.is_file()]
        directories.append({
            "name": path.name,
            "category": category_for_dir(path.name, ingest_media_ids),
            "fileCount": len(files),
            "bytes": sum(item.stat().st_size for item in files),
        })

    dir_names = {item["name"] for item in directories}
    missing_ingest_media = sorted(ingest_media_ids - dir_names)
    scene_records = state.get("meiao-scene-split-records") if isinstance(state, dict) else {}
    scene_records = scene_records if isinstance(scene_records, dict) else {}
    ingest_script_ids = {
        stable_script_id(str(item.get("id") or ""))
        for item in ingest_items
        if isinstance(item, dict) and item.get("id")
    }
    scene_records_without_ingest: list[str] = []
    missing_scene_segments: list[dict[str, str]] = []
    for key, record in scene_records.items():
        if not isinstance(record, dict):
            continue
        ingest_id = str(record.get("ingestId") or "")
        if ingest_id and stable_script_id(ingest_id) not in ingest_script_ids:
            scene_records_without_ingest.append(str(key))
        media_id = str(record.get("mediaId") or "")
        for segment in record.get("sceneSegments") or []:
            if not isinstance(segment, dict):
                continue
            segment_media_id = str(segment.get("mediaId") or media_id)
            filename = str(segment.get("filename") or "")
            if segment_media_id and filename and not (media_root / segment_media_id / "scenes" / filename).exists():
                missing_scene_segments.append({"record": str(key), "mediaId": segment_media_id, "filename": filename})

    by_category: dict[str, int] = {}
    for item in directories:
        by_category[item["category"]] = by_category.get(item["category"], 0) + 1
    return {
        "runtimeRoot": str(runtime_root),
        "summary": {
            "directories": len(directories),
            "byCategory": by_category,
            "ingestItems": len(ingest_items),
            "ingestMediaIds": len(ingest_media_ids),
            "missingIngestMedia": len(missing_ingest_media),
            "sceneRecords": len(scene_records),
            "sceneRecordsWithoutIngest": len(scene_records_without_ingest),
            "missingSceneSegments": len(missing_scene_segments),
        },
        "missingIngestMedia": missing_ingest_media,
        "sceneRecordsWithoutIngest": scene_records_without_ingest,
        "missingSceneSegments": missing_scene_segments[:50],
        "directories": directories,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument("--output")
    args = parser.parse_args()
    result = inventory(Path(args.runtime_root).resolve())
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
