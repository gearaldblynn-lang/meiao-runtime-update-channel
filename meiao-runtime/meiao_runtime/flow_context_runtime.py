from __future__ import annotations

import re
from typing import Any


def flow_url_is_project_context(url: str | None) -> bool:
    return bool(re.search(r"/project/[^/?#]+", str(url or "")))


def flow_page_project_ready(status: dict[str, Any] | None) -> bool:
    if not isinstance(status, dict):
        return False
    current_url = str(status.get("url") or "")
    if not re.search(r"/project/[^/?#]+", current_url) or re.search(r"/edit/[^/?#]+", current_url):
        return False
    if re.search(r"/project/[^/?#]+/[^?#]+", current_url):
        return False
    body_sample = str(status.get("bodySample") or "")
    if re.search(
        r"Your AI creative studio built|Unlock your best creative work|Our Models|Features may vary by Google AI subscription|Create with Google Flow\s+探索|Try in Google Flow\s+Learn More",
        body_sample,
        re.I,
    ):
        return False
    if re.search(r"出了点问题，请重试|出了点问题|Something went wrong|please try again", body_sample, re.I):
        return False
    return int(status.get("promptInputCount") or 0) > 0


def flow_page_edit_prompt_ready(status: dict[str, Any] | None) -> bool:
    if not isinstance(status, dict):
        return False
    current_url = str(status.get("url") or "")
    return bool(
        re.search(r"/project/[^/?#]+/edit/[^/?#]+", current_url)
        and int(status.get("promptInputCount") or 0) > 0
        and not status.get("loginRequired")
        and not status.get("verificationRequired")
        and not status.get("appErrorDetected")
    )


def flow_page_submission_ready(status: dict[str, Any] | None) -> bool:
    return flow_page_project_ready(status) or flow_page_edit_prompt_ready(status)


def flow_page_not_ready_message(status: dict[str, Any] | None, fallback: str = "Flow 新项目创作页未就绪。") -> str:
    if isinstance(status, dict) and str(status.get("message") or "").strip():
        return str(status.get("message"))
    return fallback


def flow_project_builder_url_from_url(current_url: str) -> str:
    match = re.match(r"^(https?://[^?#]+?/project/[^/?#]+)", str(current_url or ""))
    return match.group(1) if match else ""
