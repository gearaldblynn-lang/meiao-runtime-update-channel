from src.pyJianYingDraft.video_segment import VideoSegment

import asyncio
from src.utils.logger import logger
from src.pyJianYingDraft import ScriptFile, trange, IntroType
import src.pyJianYingDraft as draft
from src.utils.draft_cache import DRAFT_CACHE
from exceptions import CustomException, CustomError
import os
from src.utils import helper
from src.utils.download import download
import config
import json
import time
from typing import List, Dict, Any, Tuple, Optional
from src.utils.draft_lock_manager import get_draft_lock_manager
from src.schemas.add_videos import SegmentInfo


def add_videos(
    draft_url: str, 
    video_infos: str,
    scene_timelines: Optional[List[Dict[str, int]]] = None,
    alpha: float = 1.0, 
    scale_x: float = 1.0, 
    scale_y: float = 1.0, 
    transform_x: int = 0, 
    transform_y: int = 0
) -> Tuple[str, str, List[str], List[str], int, int, List[SegmentInfo]]:
    """
    添加视频到剪映草稿的业务逻辑（同步版本，兼容旧代码）
    
    Args:
        draft_url: ""  // [必选] 草稿 URL
        video_infos: [ 
            {
                "video_url": "https://example.com/video1.mp4", // [必选] 视频文件的 URL 地址
                "width": 1920, // [可选] 视频宽度，不传则自动获取视频文件尺寸
                "height": 1080, // [可选] 视频高度，不传则自动获取视频文件尺寸
                "start": 0.0, // [必选] 视频在时间轴上的开始时间 (微秒)
                "end": 12000000.0, // [必选] 视频在时间轴上的结束时间 (微秒)
                "duration": 12000000.0, // [可选] 视频总时长 (微秒)，如果不传则默认为 end-start
                "mask": "", // 遮罩类型 [可选]，默认值为 None
                "transition": "", // 转场效果名称 [可选]，默认值为 None
                "transition_duration": 500000.0, // 转场持续时间 (微秒)[可选]，默认值为 500000
                "volume": 1.0, // 音量大小 [0, 10][可选]，默认值为 1.0，10 为最大音量
            } 
        ] // [必选]
        scene_timelines: [ // [可选] 场景时间线数组，用于视频变速，与 video_infos 一一对应
            {
                "start": 0, // [必选] 场景开始时间 (微秒)
                "end": 6000000 // [必选] 场景结束时间 (微秒)
            }
        ]
        // 变速原理：speed = (video.end - video.start) / (scene_timeline.end - scene_timeline.start)
        // 示例：视频时间轴 0-2000000(2 秒)，场景时间线 0-1000000(1 秒)，则视频以 2 倍速播放
        // 如果不提供 scene_timelines 或对应项为 None，视频以正常速度 (1.0 倍) 播放
        alpha: 全局透明度 [0, 1][可选]，默认值为 1.0
        scale_x: X 轴缩放比例 [可选]，默认值为 1.0
        scale_y: Y 轴缩放比例 [可选]，默认值为 1.0
        transform_x: X 轴位置偏移 (像素)[可选]，默认值为 0
        transform_y: Y 轴位置偏移 (像素)[可选]，默认值为 0
    
    Returns:
        "draft_url": "https://capcut-mate.jcaigc.cn/openapi/capcut-mate/v1/get_draft?draft_id=...",
        "track_id": "video-track-uuid",
        "video_ids": ["video1-uuid", "video2-uuid", "video3-uuid"],
        "segment_ids": ["segment1-uuid", "segment2-uuid", "segment3-uuid"],
        "videos_count": 3, [未用]
        "total_duration": 15000000 [未用]

    Raises:
        CustomException: 视频批量添加失败
    """
    # 调用内部处理函数（不获取锁，由外层控制）
    return _add_videos_internal(
        draft_url=draft_url,
        video_infos=video_infos,
        scene_timelines=scene_timelines,
        alpha=alpha,
        scale_x=scale_x,
        scale_y=scale_y,
        transform_x=transform_x,
        transform_y=transform_y,
        prepared_videos=None,
    )


def _prepare_videos_local_files(draft_url: str, video_infos: str) -> List[Dict[str, Any]]:
    """
    校验草稿、解析 video_infos、规范化时间字段并下载素材到草稿目录。
    不含对 ScriptFile 的修改，可在草稿写锁外调用。
    """
    draft_id = helper.get_url_param(draft_url, "draft_id")
    if (not draft_id) or (draft_id not in DRAFT_CACHE):
        raise CustomException(CustomError.INVALID_DRAFT_URL)

    draft_dir = os.path.join(config.DRAFT_DIR, draft_id)
    draft_video_dir = os.path.join(draft_dir, "assets", "videos")
    os.makedirs(name=draft_video_dir, exist_ok=True)

    videos = parse_video_data(json_str=video_infos)
    if len(videos) == 0:
        logger.info(f"No video info, draft_id: {draft_id}")
        raise CustomException(CustomError.INVALID_VIDEO_INFO)

    for video in videos:
        video["original_start"] = video["start"]
        video["original_end"] = video["end"]
        local_video_path = str(video.get("local_video_path") or "").strip()
        if local_video_path and os.path.isfile(local_video_path):
            video["local_video_path"] = local_video_path
        else:
            video["local_video_path"] = download(url=video["video_url"], save_dir=draft_video_dir)

    _inspect_and_apply_average_duration_strategy(videos)
    return videos


def _inspect_and_apply_average_duration_strategy(videos: List[Dict[str, Any]]) -> None:
    source_duration_total = 0
    for video in videos:
        try:
            material = draft.VideoMaterial(video["local_video_path"])
            material_duration = max(1, int(material.duration))
        except Exception as exc:
            logger.warning("Failed to inspect video duration, fallback to declared duration: %s", exc)
            material_duration = int(video.get("source_duration") or video.get("duration") or max(1, video["end"] - video["start"]))
        source_start = max(0, int(video.get("source_start") or 0))
        source_end = int(video.get("source_end") or 0)
        declared_source_duration = int(video.get("source_duration") or 0)
        if source_end > source_start:
            source_duration = source_end - source_start
        elif declared_source_duration > 0:
            source_duration = declared_source_duration
        else:
            source_duration = material_duration
        if material_duration > 0:
            source_duration = min(max(1, source_duration), max(1, material_duration - min(source_start, material_duration - 1)))
        video["source_duration"] = source_duration
        source_duration_total += source_duration

    voice_duration = max([int(video.get("voice_duration") or 0) for video in videos] or [0])
    if voice_duration <= 0 or source_duration_total <= 0:
        return

    target_total = max(len(videos), round((source_duration_total + voice_duration) / 2))
    durations = _distribute_durations([int(video["source_duration"]) for video in videos], target_total)
    cursor = 0
    for video, duration in zip(videos, durations):
        video["start"] = cursor
        video["end"] = cursor + duration
        video["duration"] = duration
        video["speed"] = int(video["source_duration"]) / max(1, duration)
        video["original_start"] = video["start"]
        video["original_end"] = video["end"]
        cursor += duration
    logger.info(
        "Applied average duration strategy: video_source_total=%s, voice_duration=%s, target_total=%s",
        source_duration_total,
        voice_duration,
        target_total,
    )


def _distribute_durations(source_durations: List[int], target_total: int) -> List[int]:
    if not source_durations:
        return []
    safe_target = max(len(source_durations), round(target_total))
    source_total = sum(max(1, value) for value in source_durations)
    if source_total <= 0:
        even = max(1, safe_target // len(source_durations))
        return [max(1, safe_target - even * index) if index == len(source_durations) - 1 else even for index, _ in enumerate(source_durations)]
    used = 0
    durations: List[int] = []
    for index, source_duration in enumerate(source_durations):
        if index == len(source_durations) - 1:
            durations.append(max(1, safe_target - used))
        else:
            duration = max(1, round((max(1, source_duration) / source_total) * safe_target))
            used += duration
            durations.append(duration)
    return durations


async def add_videos_async(
    draft_url: str, 
    video_infos: str,
    scene_timelines: Optional[List[Dict[str, int]]] = None,
    alpha: float = 1.0, 
    scale_x: float = 1.0, 
    scale_y: float = 1.0, 
    transform_x: int = 0, 
    transform_y: int = 0,
    lock_timeout: float = 30.0
) -> Tuple[str, str, List[str], List[str], int, int, List[SegmentInfo]]:
    """
    添加视频到剪映草稿的异步版本（带并发锁保护）
    
    功能：
    1. 使用 DraftLockManager 防止同一草稿的并发写操作
    2. 支持超时控制，避免无限等待
    3. 自动释放锁，即使发生异常
    4. 视频下载在获取锁之前完成，持锁阶段仅修改草稿与写盘
    
    Args:
        draft_url: 草稿 URL，格式：".../get_draft?draft_id=xxx"
        video_infos: JSON 字符串，包含视频信息列表，详见 add_videos 函数
        scene_timelines: 场景时间线列表，用于视频变速，与 video_infos 一一对应
        alpha: 全局透明度 [0, 1]，默认 1.0
        scale_x: X 轴缩放比例，默认 1.0
        scale_y: Y 轴缩放比例，默认 1.0
        transform_x: X 轴位置偏移（像素），默认 0
        transform_y: Y 轴位置偏移（像素），默认 0
        lock_timeout: 获取锁的超时时间（秒），默认 30 秒
    
    Returns:
        tuple: (draft_url, track_id, video_ids, segment_ids)
    
    Raises:
        CustomException: 视频添加失败，或 `DRAFT_LOCK_TIMEOUT`（获取写锁超时）
    
    Example:
        >>> result = await add_videos_async(
        ...     draft_url="http://.../draft_id=123",
        ...     video_infos='[{"video_url":"...", "start":0, "end":5000000}]'
        ... )
    """
    # 提取草稿 ID
    draft_id = helper.get_url_param(draft_url, "draft_id")
    if not draft_id:
        raise CustomException(CustomError.INVALID_DRAFT_URL)

    logger.info(f"[flow:add_videos] prep_start, draft_id: {draft_id}")
    # 解析、规范化与下载在锁外完成，缩短持锁时间
    # 下载与预处理放到线程池，避免阻塞事件循环导致锁超时漂移
    prep_started_at = time.monotonic()
    prepared_videos = await asyncio.to_thread(
        _prepare_videos_local_files,
        draft_url,
        video_infos,
    )
    logger.info(
        f"[flow:add_videos] prep_done, draft_id: {draft_id}, "
        f"count: {len(prepared_videos)}, elapsed: {time.monotonic() - prep_started_at:.3f}s"
    )

    lock_manager = get_draft_lock_manager()

    logger.info(f"[flow:add_videos] lock_wait_start, draft_id: {draft_id}, timeout: {lock_timeout}s")
    try:
        await lock_manager.acquire_lock(draft_id, timeout=lock_timeout)
        logger.info(f"[flow:add_videos] lock_acquired, draft_id: {draft_id}")
    except asyncio.TimeoutError:
        logger.error(f"Timeout waiting for lock on draft_id: {draft_id}")
        raise CustomException(
            CustomError.DRAFT_LOCK_TIMEOUT,
            f"Failed to acquire lock for draft {draft_id} after {lock_timeout}s",
        )

    try:
        return _add_videos_internal(
            draft_url=draft_url,
            video_infos=video_infos,
            scene_timelines=scene_timelines,
            alpha=alpha,
            scale_x=scale_x,
            scale_y=scale_y,
            transform_x=transform_x,
            transform_y=transform_y,
            prepared_videos=prepared_videos,
        )
    finally:
        await lock_manager.release_lock(draft_id)
        logger.info(f"[flow:add_videos] lock_released, draft_id: {draft_id}")


def _add_videos_internal(
    draft_url: str,
    video_infos: str,
    scene_timelines: Optional[List[Dict[str, int]]] = None,
    alpha: float = 1.0,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
    transform_x: int = 0,
    transform_y: int = 0,
    prepared_videos: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, str, List[str], List[str], int, int, List[SegmentInfo]]:
    """
    添加视频的内部处理函数（无锁，需外层控制并发）
    
    此函数不包含锁机制，必须在已获取锁的情况下调用
    
    Args:
        draft_url: 草稿 URL
        video_infos: 视频信息 JSON 字符串（当 prepared_videos 为 None 时参与解析）
        scene_timelines: 场景时间线列表
        alpha: 全局透明度
        scale_x: X 轴缩放比例
        scale_y: Y 轴缩放比例
        transform_x: X 轴位置偏移
        transform_y: Y 轴位置偏移
        prepared_videos: 若已在外部完成解析与下载（含 local_video_path），则直接使用，跳过下载
    
    Returns:
        tuple: (draft_url, track_id, video_ids, segment_ids)
    """
    logger.info(f"_add_videos_internal, draft_url: {draft_url}")

    # 1. 提取草稿 ID
    draft_id = helper.get_url_param(draft_url, "draft_id")
    if (not draft_id) or (draft_id not in DRAFT_CACHE):
        raise CustomException(CustomError.INVALID_DRAFT_URL)

    # 2. 创建保存视频资源的目录
    draft_dir = os.path.join(config.DRAFT_DIR, draft_id)
    draft_video_dir = os.path.join(draft_dir, "assets", "videos")
    os.makedirs(name=draft_video_dir, exist_ok=True)

    if prepared_videos is not None:
        videos = prepared_videos
    else:
        videos = parse_video_data(json_str=video_infos)
        if len(videos) == 0:
            logger.info(f"No video info, draft_id: {draft_id}")
            raise CustomException(CustomError.INVALID_VIDEO_INFO)
        for video in videos:
            video["original_start"] = video["start"]
            video["original_end"] = video["end"]

    if prepared_videos is None:
        _inspect_and_apply_average_duration_strategy(videos)

    logger.info(f"Parsed {len(videos)} videos, scene_timelines: {scene_timelines}")

    # 4. 从缓存中获取草稿
    script: ScriptFile = DRAFT_CACHE[draft_id]

    # 5. 添加视频轨道（明确说明不使用主轨道，并设置合适的渲染层级）
    track_name = f"video_track_{helper.gen_unique_id()}"
    # 设置 relative_index=10 确保视频轨道在主视频轨道之上，避免与主轨道冲突
    script.add_track(track_type=draft.TrackType.video, track_name=track_name, relative_index=10)

    # 6. 遍历视频信息，添加视频到草稿中的指定轨道，收集片段 ID
    segment_ids = []
    segment_infos = []
    current_track_end = 0  # 跟踪当前轨道上的实际结束位置（用于处理变速后的连续性）
    should_keep_continuity = _has_valid_scene_timelines(scene_timelines, len(videos))
    logger.info(
        f"Timeline placement mode, should_keep_continuity={should_keep_continuity}, "
        f"scene_timelines_count={len(scene_timelines) if scene_timelines else 0}"
    )
    for i, video in enumerate(videos):
        # 获取对应的场景时间线（如果有）
        scene_timeline = scene_timelines[i] if scene_timelines and i < len(scene_timelines) else None
        
        # 仅在 scene_timelines 有效时，沿用历史行为：自动连续拼接（处理变速后的间隙问题）
        # 否则严格使用 video_infos 里传入的 start/end。
        if should_keep_continuity and i > 0 and current_track_end > 0:
            # 使用原始时长计算新的 end
            original_duration = video['original_end'] - video['original_start']
            video['start'] = current_track_end
            video['end'] = video['start'] + original_duration
            logger.info(f"Adjusted video {i} start time to {video['start']} for continuity, original_duration: {original_duration}")
        
        clip_scale_x = video.get("scale_x", scale_x)
        clip_scale_y = video.get("scale_y", scale_y)
        flip_horizontal = False
        if isinstance(clip_scale_x, (int, float)) and clip_scale_x < 0:
            flip_horizontal = True
            clip_scale_x = abs(clip_scale_x)
        segment_id, segment_info, actual_duration = add_video_to_draft(script, track_name, draft_video_dir=draft_video_dir, video=video,
                                      scene_timeline=scene_timeline,
                                      alpha=alpha,
                                      scale_x=clip_scale_x,
                                      scale_y=clip_scale_y,
                                      transform_x=video.get("transform_x", transform_x),
                                      transform_y=video.get("transform_y", transform_y),
                                      flip_horizontal=flip_horizontal)
        segment_ids.append(segment_id)
        segment_infos.append(segment_info)
        # 更新当前轨道结束位置（使用实际播放时长，而非原始时间轴时长）
        current_track_end = video['start'] + actual_duration
        logger.info(f"Video {i} added, track end position: {current_track_end}, actual_duration: {actual_duration}")
    logger.info(f"segment_ids: {segment_ids}")

    # 7. 保存草稿
    script.save()

    # 8. 获取当前视频轨道 id
    track_id = ""
    for key in script.tracks.keys():
        if script.tracks[key].name == track_name:
            track_id = script.tracks[key].track_id
            break
    logger.info(f"draft_id: {draft_id}, track_id: {track_id}")

    # 9. 获取当前所有视频资源 ID（全局唯一 ID）
    video_ids = [video.material_id for video in script.materials.videos]
    logger.info(f"draft_id: {draft_id}, video_ids: {video_ids}")

    # TODO: 这里还是有点小问题，为什么得到的 video_ids 与 segment_ids 的结果一样
    source_duration_total = sum(int(video.get("source_duration") or 0) for video in videos)
    return draft_url, track_id, video_ids, segment_ids, current_track_end, source_duration_total, segment_infos


def _is_valid_scene_timeline(scene_timeline: Any) -> bool:
    """
    判断单个 scene_timeline 是否为有效值。
    有效条件：必须是字典，且包含 start/end，且 end > start。
    """
    if not isinstance(scene_timeline, dict):
        return False
    if "start" not in scene_timeline or "end" not in scene_timeline:
        return False
    return scene_timeline["end"] > scene_timeline["start"]


def _has_valid_scene_timelines(
    scene_timelines: Optional[List[Dict[str, int]]],
    video_count: int,
) -> bool:
    """
    判断 scene_timelines 是否“指定了有效值”。
    当且仅当每个视频都存在对应且有效的 scene_timeline 时，返回 True。
    """
    if not scene_timelines:
        return False
    if len(scene_timelines) < video_count:
        return False
    return all(_is_valid_scene_timeline(scene_timelines[i]) for i in range(video_count))

def add_video_to_draft(
    script: ScriptFile,
    track_name: str,
    draft_video_dir: str,
    video: dict, 
    scene_timeline: Optional[Dict[str, int]] = None,
    alpha: float = 1.0, 
    scale_x: float = 1.0, 
    scale_y: float = 1.0, 
    transform_x: int = 0, 
    transform_y: int = 0,
    flip_horizontal: bool = False
    ) -> Tuple[str, SegmentInfo, int]:
    """
    向剪映草稿中添加视频
    
    Args:
        script: 草稿文件对象
        track_name: 视频轨道名称
        draft_video_dir: 视频资源目录
        video: 视频信息字典，包含以下字段：
            video_url: 视频URL
            width: 视频宽度(像素)
            height: 视频高度(像素)
            start: 视频在时间轴上的开始时间(微秒)
            end: 视频在时间轴上的结束时间(微秒)
            duration: 视频总时长(微秒)，可选，默认为end-start
            mask: 遮罩类型(可选)
            transition: 转场效果(可选)
            transition_duration: 转场持续时间(可选)
            volume: 音量大小(可选)
        scene_timeline: 场景时间线字典，包含以下字段：
            start: 场景开始时间(微秒)
            end: 场景结束时间(微秒)
            用于计算视频变速：speed = (video.end - video.start) / (scene_timeline.end - scene_timeline.start)
        alpha: 视频透明度
        scale_x: 横向缩放
        scale_y: 纵向缩放
        transform_x: X轴位置偏移(像素)
        transform_y: Y轴位置偏移(像素)       
    
    Returns:
        segment_id: 片段ID
        actual_duration: 视频在轨道上的实际播放时长(微秒)，考虑变速后的时长
    """
    try:
        video_path = video.get("local_video_path")
        if video_path:
            if not os.path.isfile(video_path):
                raise CustomException(CustomError.VIDEO_ADD_FAILED, f"Missing local file: {video_path}")
        else:
            video_path = download(url=video["video_url"], save_dir=draft_video_dir)

        # 1. 创建视频素材
        video_material = draft.VideoMaterial(video_path)
        
        # 2. 获取视频播放时长（target duration）
        target_duration = video.get('duration', video['end'] - video['start'])
        
        # 获取草稿的宽高用于transform坐标转换
        draft_width = script.width
        draft_height = script.height
        fit_mode = str(video.get("fit_mode") or video.get("fitMode") or "").strip().lower()
        if fit_mode in {"cover", "fill"} and video_material.width > 0 and video_material.height > 0 and draft_width > 0 and draft_height > 0:
            width_ratio = draft_width / video_material.width
            height_ratio = draft_height / video_material.height
            contain_ratio = min(width_ratio, height_ratio)
            cover_ratio = max(width_ratio, height_ratio)
            cover_scale = cover_ratio / contain_ratio if contain_ratio > 0 else 1.0
            incoming_scale_x = float(scale_x or 1.0)
            incoming_scale_y = float(scale_y or 1.0)
            scale_x_sign = -1.0 if incoming_scale_x < 0 else 1.0
            scale_x = scale_x_sign * max(abs(incoming_scale_x), cover_scale)
            scale_y = max(abs(incoming_scale_y), cover_scale)
        logger.info(
            f"draft size: {draft_width}x{draft_height}, material: {video_material.width}x{video_material.height}, "
            f"fit_mode: {fit_mode or 'default'}, final_scale: {scale_x}x{scale_y}, "
            f"transform_x: {transform_x}, transform_y: {transform_y}"
        )

        # 4. 创建图像调节设置
        clip_settings = draft.ClipSettings(
            alpha=alpha,
            flip_horizontal=flip_horizontal,
            scale_x=scale_x,
            scale_y=scale_y,
            transform_x=transform_x / draft_width,  #半画布宽单位
            transform_y=transform_y / draft_height  #为半画布高单位
        )
        
        # 5. 计算在时间轴上的显示时长（target duration）
        display_duration = video['end'] - video['start']
        
        # 5.5 计算变速。优先使用显式 source_duration/speed，用于将素材均匀变速到目标时间轴。
        speed = 1.0
        actual_duration = display_duration  # 默认实际时长等于显示时长
        raw_source_start = int(video.get("source_start") or 0)
        source_start = max(0, min(video_material.duration - 1, raw_source_start)) if video_material.duration > 0 else 0
        raw_source_end = int(video.get("source_end") or 0)
        if raw_source_end > source_start:
            declared_source_duration = raw_source_end - source_start
        else:
            declared_source_duration = int(video.get("source_duration") or display_duration)
        declared_source_duration = max(1, min(max(1, video_material.duration - source_start), declared_source_duration))
        source_duration = min(video_material.duration, display_duration)
        if source_duration <= 0:
            source_duration = min(video_material.duration, display_duration)
        if video.get("speed"):
            speed = float(video.get("speed") or 1.0)
            actual_duration = display_duration
            source_duration = min(video_material.duration, max(1, round(display_duration * speed)))
            logger.info(
                f"Video explicit speed: {speed}x "
                f"(display_duration={display_duration}, source_duration={source_duration}, declared_source_duration={declared_source_duration})"
            )
        elif declared_source_duration != display_duration:
            speed = declared_source_duration / display_duration if display_duration > 0 else 1.0
            actual_duration = display_duration
            source_duration = min(video_material.duration, max(1, round(display_duration * speed)))
            logger.info(
                f"Video proportional speed calculated: {speed}x "
                f"(display_duration={display_duration}, source_duration={source_duration}, declared_source_duration={declared_source_duration})"
            )
        elif scene_timeline:
            scene_duration = scene_timeline['end'] - scene_timeline['start']
            if scene_duration > 0:
                # speed = 时间轴时长 / 场景时长
                # 例如：时间轴2秒，场景1秒，则speed=2（2倍速）
                speed = display_duration / scene_duration
                actual_duration = scene_duration  # 实际播放时长为场景时长
                logger.info(f"Video speed calculated: {speed}x (display_duration={display_duration}, scene_duration={scene_duration})")
        
        # 6. 创建视频片段
        # 用户传入 volume 范围为 [0, 10]，剪映内部范围为 [0, 10]
        raw_volume = video.get('volume', 1.0)
        video_segment = draft.VideoSegment(
            material=video_material, 
            target_timerange=trange(start=video['start'], duration=display_duration),
            source_timerange=trange(start=source_start, duration=min(source_duration, max(1, video_material.duration - source_start))),
            speed=speed,  # 使用计算出的速度
            volume=raw_volume,
            clip_settings=clip_settings
        )
        if flip_horizontal or abs(float(scale_x or 1.0) - 1.0) > 0.001 or abs(float(scale_y or 1.0) - 1.0) > 0.001:
            video_segment.uniform_scale = False
        logger.info(f"video_path: {video_path}, start: {video['start']}, target_duration: {target_duration}, display_duration: {display_duration}, source_start: {source_start}, source_duration: {source_duration}, speed: {speed}, raw_volume: {raw_volume}")

        # 6. 添加转场效果（如果指定了）
        transition_name = video.get('transition')
        if transition_name:
            transition_type = find_transition_type_by_name(transition_name)
            if transition_type:
                transition_duration = video.get('transition_duration', 500000)  # 默认500ms
                try:
                    video_segment.add_transition(transition_type, duration=transition_duration)
                    logger.info(f"Added transition '{transition_name}' with duration {transition_duration}us")
                except Exception as e:
                    logger.warning(f"Failed to add transition '{transition_name}': {str(e)}")
            else:
                logger.warning(f"Transition type not found for name: {transition_name}")

        # 7. 向指定轨道添加片段
        script.add_segment(video_segment, track_name)

        # 8. 返回片段ID和实际播放时长（注意：是segment_id而不是material_id）
        segment_info = SegmentInfo(
            id=video_segment.segment_id,
            start=video['start'],
            end=video['end'],
        )

        return video_segment.segment_id, segment_info, actual_duration
    except CustomException:
        logger.info(f"Add video to draft failed, draft_video_dir: {draft_video_dir}, video: {video}")
        raise
    except Exception as e:
        logger.error(f"Add video to draft failed, error: {str(e)}")
        raise CustomException(err=CustomError.VIDEO_ADD_FAILED)


def find_transition_type_by_name(transition_name: str) -> Optional[draft.TransitionType]:
    """
    根据转场名称查找对应的转场类型
    
    Args:
        transition_name: 转场名称
    
    Returns:
        对应的转场类型枚举，如果未找到则返回None
    """
    if not transition_name:
        return None
        
    try:
        return draft.TransitionType.from_name(transition_name)
    except ValueError:
        logger.warning(f"Transition type not found for name: {transition_name}")
        return None


def parse_video_data(json_str: str) -> List[Dict[str, Any]]:
    """
    解析视频数据的JSON字符串，处理可选字段的默认值
    
    Args:
        json_str: 包含视频数据的JSON字符串，格式如下：
        [ 
            {
                "video_url": "https://example.com/video1.mp4", // [必选] 视频文件的URL地址
                "width": 1920, // [可选] 视频宽度，不传则自动获取视频文件尺寸
                "height": 1080, // [可选] 视频高度，不传则自动获取视频文件尺寸
                "start": 0.0, // [必选] 视频在时间轴上的开始时间 
                "end": 12000000.0, // [必选] 视频在时间轴上的结束时间 
                "duration": 12000000.0, // [可选] 视频总时长(微秒)，如果不传则默认为end-start
                "mask": "", // 遮罩类型[可选]，默认值为None
                "transition": "", // 转场效果名称[可选]，默认值为None
                "transition_duration": 500000.0, // 转场持续时间(微秒)[可选]，默认值为500000
                "volume": 1.0, // 音量大小[0, 10][可选]，默认值为1.0，10为最大音量
            } 
        ]
        
    Returns:
        包含视频对象的数组，每个对象都处理了默认值
        
    Raises:
        json.JSONDecodeError: 当JSON格式错误时抛出
        KeyError: 当缺少必选字段时抛出
    """
    try:
        # 解析JSON字符串
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise CustomException(CustomError.INVALID_VIDEO_INFO, f"JSON parse error: {e.msg}")
    
    # 确保输入是列表
    if not isinstance(data, list):
        raise CustomException(CustomError.INVALID_VIDEO_INFO, "video_infos should be a list")
    
    result = []
    
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise CustomException(CustomError.INVALID_VIDEO_INFO, f"the {i}th item should be a dict")
        
        # 检查必选字段（移除width和height，因为它们现在是可选的）
        required_fields = ["video_url", "start", "end"]
        missing_fields = [field for field in required_fields if field not in item]
        
        if missing_fields:
            raise CustomException(CustomError.INVALID_VIDEO_INFO, f"the {i}th item is missing required fields: {', '.join(missing_fields)}")
        
        # 如果没有提供duration，则计算为end-start
        duration = item.get("duration", item["end"] - item["start"])
        
        # 创建处理后的对象，设置默认值
        processed_item = {
            "video_url": item["video_url"],
            "width": item.get("width"),  # 可选参数
            "height": item.get("height"),  # 可选参数
            "start": item["start"],
            "end": item["end"],
            "duration": duration,
            "mask": item.get("mask", None),  # 默认值 None
            "transition": item.get("transition", None),  # 默认值 None
            "transition_duration": item.get("transition_duration", 500000),  # 默认值 500000
            "volume": item.get("volume", 1.0),  # 默认值 1.0
            "local_video_path": item.get("local_video_path"),
            "source_start": item.get("source_start", 0),
            "source_end": item.get("source_end"),
            "source_duration": item.get("source_duration"),
            "speed": item.get("speed"),
            "scale_x": item.get("scale_x"),
            "scale_y": item.get("scale_y"),
            "transform_x": item.get("transform_x"),
            "transform_y": item.get("transform_y"),
            "fit_mode": item.get("fit_mode") or item.get("fitMode"),
        }
        
        # 验证数值范围：用户传入范围 [0, 10]，超范围时给默认值
        if processed_item["volume"] < 0 or processed_item["volume"] > 10:
            logger.warning(f"Volume {processed_item['volume']} out of range [0, 10], using default 1.0")
            processed_item["volume"] = 1.0
        
        if processed_item["transition_duration"] < 0:
            # 转场持续时间必须为非负数，给默认值
            processed_item["transition_duration"] = 500000
        
        result.append(processed_item)
    
    return result
