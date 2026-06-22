from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote


def _legacy_callable(legacy_globals: dict[str, Any], name: str) -> Any:
    value = legacy_globals.get(name)
    if not callable(value):
        raise RuntimeError(f"Legacy callable {name} is unavailable.")
    return value


def locate_installation(legacy_globals: dict[str, Any]) -> dict[str, Any]:
    return _legacy_callable(legacy_globals, "locate_capcut_installation")()


def build_assets_result(legacy_globals: dict[str, Any]) -> tuple[int, dict[str, Any]]:
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
        from src.pyJianYingDraft.metadata.audio_scene_effect import AudioSceneEffectType
        from src.pyJianYingDraft.metadata.filter_meta import FilterType
        from src.pyJianYingDraft.metadata.font_meta import FontType
        from src.pyJianYingDraft.metadata.text_intro import TextIntro
        from src.pyJianYingDraft.metadata.text_loop import TextLoopAnim
        from src.pyJianYingDraft.metadata.text_outro import TextOutro
        from src.pyJianYingDraft.metadata.video_character_effect import VideoCharacterEffectType
        from src.pyJianYingDraft.metadata.video_group_animation import GroupAnimationType
        from src.pyJianYingDraft.metadata.video_intro import IntroType
        from src.pyJianYingDraft.metadata.video_outro import OutroType
        from src.pyJianYingDraft.metadata.video_scene_effect import VideoSceneEffectType
        from src.pyJianYingDraft.metadata.tone_effect import ToneEffectType

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

        def cached_font_url(item: Any) -> str:
            effect_id = str(item.value.effect_id or "").strip()
            if not effect_id:
                return ""
            source_root = font_cache_root / effect_id
            if not source_root.exists():
                return ""
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
        for scan_root in roots:
            if not scan_root.exists():
                continue
            try:
                for item in scan_root.rglob("*"):
                    item_path = str(item)
                    if item.is_file() and item.suffix.lower() in audio_file_suffixes and not skip_pattern.search(item_path):
                        candidates.append(item)
                        if len(candidates) >= 300:
                            break
            except Exception:
                continue
            if len(candidates) >= 300:
                break
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
