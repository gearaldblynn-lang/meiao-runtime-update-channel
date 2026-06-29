from __future__ import annotations

import json
import importlib.util
import os
import re
import shutil
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any
from urllib.parse import quote


ASSETS_CACHE_TTL_SECONDS = 60.0
ASSETS_CACHE_LOCK = threading.RLock()
ASSETS_CACHE: dict[str, Any] = {"expires_at": 0.0, "key": None, "payload": None}
MUSIC_SCAN_INDEX_VERSION = 1
BROAD_MUSIC_SCAN_ROOT_NAMES = {"ematerial", "resources"}
METADATA_CLASS_MODULES = {
    "AudioSceneEffectType": "audio_scene_effect",
    "FilterType": "filter_meta",
    "FontType": "font_meta",
    "TextIntro": "text_intro",
    "TextLoopAnim": "text_loop",
    "TextOutro": "text_outro",
    "VideoCharacterEffectType": "video_character_effect",
    "GroupAnimationType": "video_group_animation",
    "IntroType": "video_intro",
    "OutroType": "video_outro",
    "VideoSceneEffectType": "video_scene_effect",
    "ToneEffectType": "tone_effect",
}


def _legacy_callable(legacy_globals: dict[str, Any], name: str) -> Any:
    value = legacy_globals.get(name)
    if not callable(value):
        raise RuntimeError(f"Legacy callable {name} is unavailable.")
    return value


def locate_installation(legacy_globals: dict[str, Any]) -> dict[str, Any]:
    return _legacy_callable(legacy_globals, "locate_capcut_installation")()


def clear_assets_cache() -> None:
    with ASSETS_CACHE_LOCK:
        ASSETS_CACHE.update({"expires_at": 0.0, "key": None, "payload": None})


def _path_signature(path: Path) -> tuple[str, int, int] | tuple[str, None, None]:
    try:
        stat = path.stat()
        return (str(path), int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))), int(stat.st_size))
    except OSError:
        return (str(path), None, None)


def assets_cache_key(legacy_globals: dict[str, Any]) -> tuple[Any, ...]:
    base_dir = legacy_globals["BASE_DIR"]
    media_root = legacy_globals["MEDIA_ROOT"]
    root = base_dir / "integrations" / "capcut_mate" / "upstream" / "capcut-mate-main"
    return (
        str(root),
        str(media_root),
        _path_signature(root / "config" / "huazi.json"),
        _path_signature(root / "config" / "sticker.json"),
    )


def assets_music_scan_index_file(legacy_globals: dict[str, Any]) -> Path:
    data_root = legacy_globals.get("DATA_ROOT") or legacy_globals["MEDIA_ROOT"].parent
    return Path(data_root) / "capcut-mate" / "assets-music-scan-index.json"


def directory_signature(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
        return {
            "path": str(path),
            "exists": True,
            "mtimeNs": int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
            "size": int(stat.st_size),
        }
    except OSError:
        return {"path": str(path), "exists": False, "mtimeNs": None, "size": None}


def read_music_scan_index(index_file: Path) -> dict[str, Any]:
    try:
        payload = json.loads(index_file.read_text(encoding="utf-8"))
    except Exception:
        return {"version": MUSIC_SCAN_INDEX_VERSION, "roots": {}}
    if not isinstance(payload, dict) or payload.get("version") != MUSIC_SCAN_INDEX_VERSION:
        return {"version": MUSIC_SCAN_INDEX_VERSION, "roots": {}}
    if not isinstance(payload.get("roots"), dict):
        payload["roots"] = {}
    return payload


def write_music_scan_index(index_file: Path, payload: dict[str, Any]) -> None:
    try:
        index_file.parent.mkdir(parents=True, exist_ok=True)
        temp = index_file.with_suffix(index_file.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(index_file)
    except Exception:
        return


def is_broad_music_scan_root(path: Path) -> bool:
    return path.name.lower() in BROAD_MUSIC_SCAN_ROOT_NAMES


def cached_music_candidates(entry: dict[str, Any], audio_file_suffixes: set[str], skip_pattern: re.Pattern[str]) -> list[Path]:
    paths = entry.get("paths") if isinstance(entry.get("paths"), list) else []
    candidates: list[Path] = []
    for raw_path in paths:
        path = Path(str(raw_path or ""))
        path_text = str(path)
        if path.suffix.lower() not in audio_file_suffixes or skip_pattern.search(path_text):
            continue
        try:
            if path.is_file():
                candidates.append(path)
        except OSError:
            continue
    return candidates


def scan_music_candidates(
    roots: list[Path],
    audio_file_suffixes: set[str],
    skip_pattern: re.Pattern[str],
    index_file: Path,
    *,
    force: bool = False,
    limit: int = 300,
) -> list[Path]:
    index = read_music_scan_index(index_file)
    indexed_roots = index.get("roots") if isinstance(index.get("roots"), dict) else {}
    next_index_roots = dict(indexed_roots)
    candidates: list[Path] = []
    index_changed = False

    for scan_root in roots:
        if len(candidates) >= limit:
            break
        if not scan_root.exists():
            continue
        root_key = str(scan_root)
        signature = directory_signature(scan_root)
        if is_broad_music_scan_root(scan_root) and not force:
            entry = indexed_roots.get(root_key)
            if isinstance(entry, dict) and entry.get("source") == signature:
                candidates.extend(cached_music_candidates(entry, audio_file_suffixes, skip_pattern))
                candidates = candidates[:limit]
                continue

        root_candidates: list[Path] = []
        try:
            for dirpath, _dirnames, filenames in os.walk(scan_root):
                for filename in filenames:
                    item = Path(dirpath) / filename
                    item_path = str(item)
                    if item.suffix.lower() in audio_file_suffixes and not skip_pattern.search(item_path):
                        root_candidates.append(item)
                        candidates.append(item)
                        if len(candidates) >= limit:
                            break
                if len(candidates) >= limit:
                    break
        except Exception:
            continue

        if is_broad_music_scan_root(scan_root):
            next_index_roots[root_key] = {
                "source": signature,
                "paths": [str(item) for item in root_candidates],
                "updatedAt": int(time.time() * 1000),
            }
            index_changed = True

    if index_changed:
        write_music_scan_index(index_file, {"version": MUSIC_SCAN_INDEX_VERSION, "roots": next_index_roots})
    return candidates


def _metadata_package_name(metadata_root: Path) -> str:
    return f"_meiao_capcut_metadata_{abs(hash(str(metadata_root.resolve())))}"


def _load_metadata_module(metadata_root: Path, module_name: str) -> Any:
    package_name = _metadata_package_name(metadata_root)
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = [str(metadata_root)]  # type: ignore[attr-defined]
        package.__package__ = package_name
        sys.modules[package_name] = package

    qualified_name = f"{package_name}.{module_name}"
    cached = sys.modules.get(qualified_name)
    if cached is not None:
        return cached

    module_path = metadata_root / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(qualified_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load CapCut Mate metadata module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = package_name
    sys.modules[qualified_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(qualified_name, None)
        raise
    return module


def load_metadata_classes(root: Path) -> dict[str, Any]:
    metadata_root = root / "src" / "pyJianYingDraft" / "metadata"
    _load_metadata_module(metadata_root, "effect_meta")
    return {
        class_name: getattr(_load_metadata_module(metadata_root, module_name), class_name)
        for class_name, module_name in METADATA_CLASS_MODULES.items()
    }


def build_assets_result(legacy_globals: dict[str, Any], *, force: bool = False) -> tuple[int, dict[str, Any]]:
    key = assets_cache_key(legacy_globals)
    now = time.monotonic()
    with ASSETS_CACHE_LOCK:
        cached = ASSETS_CACHE.get("payload")
        if not force and cached is not None and ASSETS_CACHE.get("key") == key and float(ASSETS_CACHE.get("expires_at") or 0.0) > now:
            return 200, cached
        status_code, payload = _build_assets_result_uncached(legacy_globals, force=force)
        if status_code == 200:
            ASSETS_CACHE.update({"expires_at": time.monotonic() + ASSETS_CACHE_TTL_SECONDS, "key": key, "payload": payload})
        else:
            ASSETS_CACHE.update({"expires_at": 0.0, "key": None, "payload": None})
        return status_code, payload


def _build_assets_result_uncached(legacy_globals: dict[str, Any], *, force: bool = False) -> tuple[int, dict[str, Any]]:
    base_dir = legacy_globals["BASE_DIR"]
    media_root = legacy_globals["MEDIA_ROOT"]
    audio_file_suffixes = legacy_globals["AUDIO_FILE_SUFFIXES"]
    root = base_dir / "integrations" / "capcut_mate" / "upstream" / "capcut-mate-main"
    if not root.exists():
        return 404, {"error": "剪映小助手资源目录不存在", "root": str(root)}

    inserted = False
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
        inserted = True
    try:
        metadata_classes = load_metadata_classes(root)
        AudioSceneEffectType = metadata_classes["AudioSceneEffectType"]
        FilterType = metadata_classes["FilterType"]
        FontType = metadata_classes["FontType"]
        TextIntro = metadata_classes["TextIntro"]
        TextLoopAnim = metadata_classes["TextLoopAnim"]
        TextOutro = metadata_classes["TextOutro"]
        VideoCharacterEffectType = metadata_classes["VideoCharacterEffectType"]
        GroupAnimationType = metadata_classes["GroupAnimationType"]
        IntroType = metadata_classes["IntroType"]
        OutroType = metadata_classes["OutroType"]
        VideoSceneEffectType = metadata_classes["VideoSceneEffectType"]
        ToneEffectType = metadata_classes["ToneEffectType"]

        def effect_meta_item(item: Any) -> dict[str, Any]:
            return {
                "name": item.value.name,
                "isVip": bool(item.value.is_vip),
                "resourceId": item.value.resource_id,
                "effectId": item.value.effect_id,
            }

        def media_relative_url(path: Path) -> str:
            return "/media/" + "/".join(quote(part, safe="") for part in path.relative_to(media_root).parts)

        font_cache_root = Path.home() / "AppData" / "Local" / "JianyingPro" / "User Data" / "Cache" / "effect"
        font_media_root = media_root / "capcut-fonts"
        existing_font_effect_ids: set[str] = set()
        try:
            if font_cache_root.exists():
                with os.scandir(font_cache_root) as entries:
                    existing_font_effect_ids = {entry.name for entry in entries if entry.is_dir(follow_symlinks=False)}
        except OSError:
            existing_font_effect_ids = set()

        def cached_font_url(item: Any) -> str:
            effect_id = str(item.value.effect_id or "").strip()
            if not effect_id:
                return ""
            if effect_id not in existing_font_effect_ids:
                return ""
            source_root = font_cache_root / effect_id
            candidates = [
                path
                for path in source_root.rglob("*")
                if path.is_file() and path.suffix.lower() in {".ttf", ".otf", ".woff", ".woff2"}
            ]
            if not candidates:
                return ""
            source = candidates[0]
            target_dir = font_media_root / effect_id
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / source.name
            try:
                if not target.exists() or target.stat().st_size != source.stat().st_size:
                    shutil.copy2(source, target)
                return media_relative_url(target)
            except OSError:
                return ""

        def font_meta_item(item: Any) -> dict[str, Any]:
            data = effect_meta_item(item)
            url = cached_font_url(item)
            if url:
                data["fontUrl"] = url
            return data

        filters = [effect_meta_item(item) for item in FilterType]
        fonts = [font_meta_item(item) for item in FontType]
        effects = [effect_meta_item(item) for item in [*list(VideoSceneEffectType), *list(VideoCharacterEffectType)]]
        audio_scene_effects = [effect_meta_item(item) for item in AudioSceneEffectType]
        tone_effects = [effect_meta_item(item) for item in ToneEffectType]
        huazi_path = root / "config" / "huazi.json"
        raw_text_effects = json.loads(huazi_path.read_text(encoding="utf-8")) if huazi_path.exists() else []
        text_effects = [
            {
                "id": str(item.get("id") or ""),
                "title": str(item.get("title") or ""),
                "isVip": bool(item.get("is_vip")),
            }
            for item in raw_text_effects
            if isinstance(item, dict)
        ]
        text_animations = {
            "in": [{"name": item.value.title, "isVip": bool(item.value.is_vip), "resourceId": item.value.resource_id, "effectId": item.value.effect_id} for item in TextIntro],
            "out": [{"name": item.value.title, "isVip": bool(item.value.is_vip), "resourceId": item.value.resource_id, "effectId": item.value.effect_id} for item in TextOutro],
            "loop": [{"name": item.value.title, "isVip": bool(item.value.is_vip), "resourceId": item.value.resource_id, "effectId": item.value.effect_id} for item in TextLoopAnim],
        }
        image_animations = {
            "in": [{"name": item.value.title, "isVip": bool(item.value.is_vip), "resourceId": item.value.resource_id, "effectId": item.value.effect_id} for item in IntroType],
            "out": [{"name": item.value.title, "isVip": bool(item.value.is_vip), "resourceId": item.value.resource_id, "effectId": item.value.effect_id} for item in OutroType],
            "loop": [{"name": item.value.title, "isVip": bool(item.value.is_vip), "resourceId": item.value.resource_id, "effectId": item.value.effect_id} for item in GroupAnimationType],
        }
    finally:
        if inserted:
            try:
                sys.path.remove(str(root))
            except ValueError:
                pass

    sticker_path = root / "config" / "sticker.json"
    stickers: list[dict[str, Any]] = []
    sticker_title_status = "missing"
    raw_stickers: list[dict[str, Any]] = []
    free_sticker_title_blocklist = re.compile("(vip|svip|会员|付费|专享|充值|贵宾|会员卡|premium|paid|pay)", re.IGNORECASE)
    if sticker_path.exists():
        raw_stickers = json.loads(sticker_path.read_text(encoding="utf-8"))
        if isinstance(raw_stickers, list):
            for item in raw_stickers:
                if not isinstance(item, dict):
                    continue
                sticker = item.get("sticker") if isinstance(item.get("sticker"), dict) else {}
                image = sticker.get("large_image") if isinstance(sticker.get("large_image"), dict) else {}
                sticker_id = str(item.get("sticker_id") or "").strip()
                title = str(item.get("title") or "").strip()
                sticker_type = int(sticker.get("sticker_type") or 0)
                stickers.append(
                    {
                        "id": sticker_id,
                        "title": title,
                        "imageUrl": str(image.get("image_url") or ""),
                        "stickerType": sticker_type,
                        "isCommercialCandidate": bool(sticker_id and sticker_type == 1 and not free_sticker_title_blocklist.search(title)),
                    }
                )
            titles = [item.get("title", "") for item in stickers[:20]]
            sticker_title_status = "ok" if any(title and "�" not in title for title in titles) else "garbled"
        else:
            raw_stickers = []
    free_sticker_candidate_count = 0
    for item in raw_stickers:
        if not isinstance(item, dict):
            continue
        sticker = item.get("sticker") if isinstance(item.get("sticker"), dict) else {}
        sticker_id = str(item.get("sticker_id") or "").strip()
        title = str(item.get("title") or "").strip()
        sticker_type = int(sticker.get("sticker_type") or 0)
        if sticker_id and sticker_type == 1 and not free_sticker_title_blocklist.search(title):
            free_sticker_candidate_count += 1

    def collect_music_tracks() -> list[dict[str, Any]]:
        candidates: list[Path] = []
        roots = [
            Path.home() / "AppData" / "Local" / "JianyingPro" / "User Data" / "Cache" / "music",
            Path.home() / "AppData" / "Local" / "JianyingPro" / "User Data" / "EMaterial",
            Path.home() / "AppData" / "Local" / "JianyingPro" / "User Data" / "Resources",
            Path.home() / "AppData" / "Local" / "CapCut" / "User Data" / "Cache" / "music",
            Path.home() / "AppData" / "Local" / "CapCut" / "User Data" / "EMaterial",
            Path.home() / "AppData" / "Local" / "CapCut" / "User Data" / "Resources",
        ]
        skip_pattern = re.compile(r"(voice|tts|eleven|dub|配音|口播|旁白|draft|com\.lveditor|storage[\\/]+media|output[\\/]+draft)", re.IGNORECASE)
        candidates = scan_music_candidates(
            roots,
            audio_file_suffixes,
            skip_pattern,
            assets_music_scan_index_file(legacy_globals),
            force=force,
        )
        tracks: list[dict[str, Any]] = []
        seen: set[str] = set()
        category_counts: dict[str, int] = {}
        file_metadata_index = _legacy_callable(legacy_globals, "build_jianying_audio_file_metadata_index")()
        for item in candidates:
            key = str(item.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                stat = item.stat()
            except OSError:
                continue
            source = "jianying_music_cache" if "jianyingpro" in str(item).lower() else "capcut_music_cache"
            index_item = _legacy_callable(legacy_globals, "read_audio_cache_index_item")(item)
            cfg_item = file_metadata_index.get(item.name.lower(), {})
            indexed_category = str(index_item.get("category") or "").strip()
            cfg_category = str(cfg_item.get("category") or "").strip()
            category = indexed_category if indexed_category and indexed_category != "未归类缓存" else cfg_category
            category = category or ("节拍缓存" if item.suffix.lower() == ".beat" else "其他")
            indexed_title = str(index_item.get("title") or cfg_item.get("title") or "").strip()
            indexed_author = str(index_item.get("author") or cfg_item.get("author") or "").strip()
            music_id = str(index_item.get("musicId") or cfg_item.get("musicId") or cfg_item.get("id") or cfg_item.get("web_id") or "").strip()
            category_counts[category] = category_counts.get(category, 0) + 1
            title = indexed_title or (item.stem if not re.fullmatch(r"[0-9a-f]{16,64}", item.stem, re.IGNORECASE) else f"剪映音乐 {len(tracks) + 1}")
            tracks.append(
                {
                    "id": f"capcut-audio-{len(tracks) + 1}",
                    "name": title,
                    "title": title,
                    "author": indexed_author,
                    "musicId": music_id,
                    "fileName": item.name,
                    "source": source,
                    "category": category,
                    "mediaKind": "beat" if item.suffix.lower() == ".beat" else "music",
                    "size": stat.st_size,
                    "url": f"/api/capcut-mate/audio-file?path={quote(str(item), safe='')}",
                    "isCommercialCandidate": bool(index_item.get("isCommercialCandidate", cfg_item.get("isCommercialCandidate", True))),
                }
            )
        return tracks

    missing_music_index_entries = _legacy_callable(legacy_globals, "prune_missing_audio_cache_index")()
    music_tracks = collect_music_tracks()
    music_cache_status = _legacy_callable(legacy_globals, "inspect_jianying_music_cache_state")()
    cached_music_categories = _legacy_callable(legacy_globals, "load_jianying_music_collections")()
    music_categories = cached_music_categories or [
        {"id": "recommended", "name": "推荐音乐", "cacheable": True},
        {"id": "latest", "name": "最新", "cacheable": True},
        {"id": "pure", "name": "纯音乐", "cacheable": True},
        {"id": "light", "name": "轻快", "cacheable": True},
        {"id": "vlog", "name": "Vlog", "cacheable": True},
        {"id": "travel", "name": "旅行", "cacheable": True},
        {"id": "birthday", "name": "生日", "cacheable": True},
        {"id": "marketing", "name": "营销", "cacheable": True},
        {"id": "beat", "name": "卡点", "cacheable": True},
        {"id": "warm", "name": "温暖", "cacheable": True},
        {"id": "food", "name": "美食", "cacheable": True},
        {"id": "fashion", "name": "时尚", "cacheable": True},
        {"id": "unbox", "name": "开箱", "cacheable": True},
    ]
    sound_effect_categories = [
        {"id": "scene", "name": "场景音效", "cacheable": False},
        {"id": "tone", "name": "变声音色", "cacheable": False},
        {"id": "hot", "name": "热门音效", "cacheable": True},
        {"id": "life", "name": "生活", "cacheable": True},
        {"id": "transition", "name": "转场", "cacheable": True},
        {"id": "nature", "name": "自然", "cacheable": True},
        {"id": "ui", "name": "提示音", "cacheable": True},
    ]

    return 200, {
        "root": str(root),
        "filters": {"readable": True, "count": len(filters), "items": filters, "samples": filters[:20]},
        "effects": {"readable": True, "count": len(effects), "items": effects, "samples": effects[:20]},
        "audioSceneEffects": {"readable": True, "count": len(audio_scene_effects), "items": [{**item, "category": "场景音效", "mediaKind": "sound_effect", "isCommercialCandidate": True} for item in audio_scene_effects], "samples": audio_scene_effects[:20]},
        "toneEffects": {"readable": True, "count": len(tone_effects), "items": tone_effects, "samples": tone_effects[:20]},
        "musicTracks": {"readable": True, "count": len(music_tracks), "items": music_tracks, "samples": music_tracks[:20], "categories": music_categories, "cacheFolder": str(Path.home() / "AppData" / "Local" / "JianyingPro" / "User Data" / "Cache" / "music"), "missingIndexEntries": missing_music_index_entries, "cacheStatus": music_cache_status},
        "audioCategories": {"music": music_categories, "soundEffects": sound_effect_categories},
        "fonts": {"readable": True, "count": len(fonts), "items": fonts, "samples": fonts[:20]},
        "textEffects": {"readable": True, "count": len(text_effects), "items": text_effects, "samples": text_effects[:20]},
        "textAnimations": {
            "readable": True,
            "in": {"count": len(text_animations["in"]), "items": text_animations["in"]},
            "out": {"count": len(text_animations["out"]), "items": text_animations["out"]},
            "loop": {"count": len(text_animations["loop"]), "items": text_animations["loop"]},
        },
        "imageAnimations": {
            "readable": True,
            "in": {"count": len(image_animations["in"]), "items": image_animations["in"]},
            "out": {"count": len(image_animations["out"]), "items": image_animations["out"]},
            "loop": {"count": len(image_animations["loop"]), "items": image_animations["loop"]},
        },
        "stickers": {
            "readable": bool(stickers),
            "count": len(raw_stickers),
            "freeCandidateCount": free_sticker_candidate_count,
            "items": stickers,
            "samples": stickers[:20],
            "titleStatus": sticker_title_status,
            "source": str(sticker_path),
        },
    }


def static_assets_index_file(legacy_globals: dict[str, Any]) -> Path:
    data_root = legacy_globals.get("DATA_ROOT") or legacy_globals["MEDIA_ROOT"].parent
    return Path(data_root) / "capcut-mate" / "assets-static-metadata-index.json"


def static_assets_source_signature(root: Path) -> dict[str, Any]:
    metadata_root = root / "src" / "pyJianYingDraft" / "metadata"
    files: dict[str, list[Any]] = {"effect_meta": list(_path_signature(metadata_root / "effect_meta.py"))}
    for module_name in sorted(METADATA_CLASS_MODULES.values()):
        files[module_name] = list(_path_signature(metadata_root / f"{module_name}.py"))
    files["huazi"] = list(_path_signature(root / "config" / "huazi.json"))
    files["sticker"] = list(_path_signature(root / "config" / "sticker.json"))
    return {"root": str(root), "files": files}


def read_static_assets_index(index_file: Path, source: dict[str, Any]) -> dict[str, Any] | None:
    try:
        payload = json.loads(index_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("source") != source:
        return None
    static_assets = payload.get("static")
    if not isinstance(static_assets, dict):
        return None
    for key in ("filters", "effects", "audioSceneEffects", "toneEffects", "fonts", "textEffects", "textAnimations", "imageAnimations", "stickers"):
        if not isinstance(static_assets.get(key), dict):
            return None
    return static_assets


def write_static_assets_index(index_file: Path, source: dict[str, Any], static_assets: dict[str, Any]) -> None:
    try:
        index_file.parent.mkdir(parents=True, exist_ok=True)
        temp = index_file.with_suffix(index_file.suffix + ".tmp")
        temp.write_text(json.dumps({"source": source, "static": static_assets}, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(index_file)
    except Exception:
        return


def extract_static_assets(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload.get(key)
        for key in ("filters", "effects", "audioSceneEffects", "toneEffects", "fonts", "textEffects", "textAnimations", "imageAnimations", "stickers", "audioCategories")
        if isinstance(payload.get(key), dict)
    }


def build_music_assets_sections(
    legacy_globals: dict[str, Any],
    *,
    force: bool,
    fallback_music_categories: list[dict[str, Any]],
    fallback_sound_effect_categories: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    audio_file_suffixes = legacy_globals["AUDIO_FILE_SUFFIXES"]
    roots = [
        Path.home() / "AppData" / "Local" / "JianyingPro" / "User Data" / "Cache" / "music",
        Path.home() / "AppData" / "Local" / "JianyingPro" / "User Data" / "EMaterial",
        Path.home() / "AppData" / "Local" / "JianyingPro" / "User Data" / "Resources",
        Path.home() / "AppData" / "Local" / "CapCut" / "User Data" / "Cache" / "music",
        Path.home() / "AppData" / "Local" / "CapCut" / "User Data" / "EMaterial",
        Path.home() / "AppData" / "Local" / "CapCut" / "User Data" / "Resources",
    ]
    skip_pattern = re.compile(r"(voice|tts|eleven|dub|閰嶉煶|鍙ｆ挱|鏃佺櫧|draft|com\.lveditor|storage[\\/]+media|output[\\/]+draft)", re.IGNORECASE)
    candidates = scan_music_candidates(
        roots,
        audio_file_suffixes,
        skip_pattern,
        assets_music_scan_index_file(legacy_globals),
        force=force,
    )
    tracks: list[dict[str, Any]] = []
    seen: set[str] = set()
    file_metadata_index = _legacy_callable(legacy_globals, "build_jianying_audio_file_metadata_index")()
    for item in candidates:
        key = str(item.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            stat = item.stat()
        except OSError:
            continue
        source = "jianying_music_cache" if "jianyingpro" in str(item).lower() else "capcut_music_cache"
        index_item = _legacy_callable(legacy_globals, "read_audio_cache_index_item")(item)
        cfg_item = file_metadata_index.get(item.name.lower(), {})
        indexed_category = str(index_item.get("category") or "").strip()
        cfg_category = str(cfg_item.get("category") or "").strip()
        category = indexed_category if indexed_category and indexed_category != "鏈綊绫荤紦瀛?" else cfg_category
        category = category or ("鑺傛媿缂撳瓨" if item.suffix.lower() == ".beat" else "鍏朵粬")
        indexed_title = str(index_item.get("title") or cfg_item.get("title") or "").strip()
        indexed_author = str(index_item.get("author") or cfg_item.get("author") or "").strip()
        music_id = str(index_item.get("musicId") or cfg_item.get("musicId") or cfg_item.get("id") or cfg_item.get("web_id") or "").strip()
        title = indexed_title or (item.stem if not re.fullmatch(r"[0-9a-f]{16,64}", item.stem, re.IGNORECASE) else f"鍓槧闊充箰 {len(tracks) + 1}")
        tracks.append(
            {
                "id": f"capcut-audio-{len(tracks) + 1}",
                "name": title,
                "title": title,
                "author": indexed_author,
                "musicId": music_id,
                "fileName": item.name,
                "source": source,
                "category": category,
                "mediaKind": "beat" if item.suffix.lower() == ".beat" else "music",
                "size": stat.st_size,
                "url": f"/api/capcut-mate/audio-file?path={quote(str(item), safe='')}",
                "isCommercialCandidate": bool(index_item.get("isCommercialCandidate", cfg_item.get("isCommercialCandidate", True))),
            }
        )
    missing_music_index_entries = _legacy_callable(legacy_globals, "prune_missing_audio_cache_index")()
    music_cache_status = _legacy_callable(legacy_globals, "inspect_jianying_music_cache_state")()
    cached_music_categories = _legacy_callable(legacy_globals, "load_jianying_music_collections")()
    music_categories = cached_music_categories or fallback_music_categories
    return (
        {
            "readable": True,
            "count": len(tracks),
            "items": tracks,
            "samples": tracks[:20],
            "categories": music_categories,
            "cacheFolder": str(Path.home() / "AppData" / "Local" / "JianyingPro" / "User Data" / "Cache" / "music"),
            "missingIndexEntries": missing_music_index_entries,
            "cacheStatus": music_cache_status,
        },
        {"music": music_categories, "soundEffects": fallback_sound_effect_categories},
    )


_BUILD_ASSETS_RESULT_UNCACHED_LEGACY = _build_assets_result_uncached


def _build_assets_result_uncached(legacy_globals: dict[str, Any], *, force: bool = False) -> tuple[int, dict[str, Any]]:
    base_dir = legacy_globals["BASE_DIR"]
    root = base_dir / "integrations" / "capcut_mate" / "upstream" / "capcut-mate-main"
    if not root.exists():
        return 404, {"error": "鍓槧灏忓姪鎵嬭祫婧愮洰褰曚笉瀛樺湪", "root": str(root)}

    source = static_assets_source_signature(root)
    index_file = static_assets_index_file(legacy_globals)
    if not force:
        static_assets = read_static_assets_index(index_file, source)
        if static_assets is not None:
            audio_categories = static_assets.get("audioCategories") if isinstance(static_assets.get("audioCategories"), dict) else {}
            fallback_music_categories = audio_categories.get("music") if isinstance(audio_categories.get("music"), list) else []
            fallback_sound_effect_categories = audio_categories.get("soundEffects") if isinstance(audio_categories.get("soundEffects"), list) else []
            music_tracks, next_audio_categories = build_music_assets_sections(
                legacy_globals,
                force=False,
                fallback_music_categories=fallback_music_categories,
                fallback_sound_effect_categories=fallback_sound_effect_categories,
            )
            return 200, {
                "root": str(root),
                "filters": static_assets["filters"],
                "effects": static_assets["effects"],
                "audioSceneEffects": static_assets["audioSceneEffects"],
                "toneEffects": static_assets["toneEffects"],
                "musicTracks": music_tracks,
                "audioCategories": next_audio_categories,
                "fonts": static_assets["fonts"],
                "textEffects": static_assets["textEffects"],
                "textAnimations": static_assets["textAnimations"],
                "imageAnimations": static_assets["imageAnimations"],
                "stickers": static_assets["stickers"],
            }

    status_code, payload = _BUILD_ASSETS_RESULT_UNCACHED_LEGACY(legacy_globals, force=force)
    if status_code == 200:
        write_static_assets_index(index_file, source, extract_static_assets(payload))
    return status_code, payload
