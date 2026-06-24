from urllib.parse import parse_qs, urlparse

import src.pyJianYingDraft as draft
from src.utils.logger import logger
from src.utils.video_task_manager import UIAutomationInitializerInThread, task_manager


def recognize_subtitles(draft_url: str, draft_name: str = "", timeout: float = 180) -> dict:
    """打开剪映草稿并触发智能字幕识别。"""
    name = (draft_name or extract_draft_id_from_url(draft_url)).strip()
    if not name:
        raise ValueError("无法从草稿URL中提取draft_id")
    if draft.JianyingController is None:
        raise RuntimeError("JianyingController unavailable: requires Windows and capcut-mate[windows]")

    logger.info("recognize_subtitles called, draft_name=%s, timeout=%s", name, timeout)
    with task_manager.export_video_lock:
        with UIAutomationInitializerInThread():
            ctrl = draft.JianyingController()
            ctrl.recognize_subtitles(name, timeout=max(30, float(timeout or 180)))

    return {"draft_url": draft_url, "draft_name": name, "recognized": True}


def extract_draft_id_from_url(draft_url: str) -> str:
    parsed = urlparse(draft_url or "")
    return (parse_qs(parsed.query).get("draft_id") or [""])[0].strip()
