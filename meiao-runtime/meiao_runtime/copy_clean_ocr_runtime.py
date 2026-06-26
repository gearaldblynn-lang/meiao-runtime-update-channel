from __future__ import annotations

import math
import re
import statistics
from typing import Any, Callable


MIN_OCR_CONFIDENCE = 0.55


def ocr_available() -> bool:
    try:
        from rapidocr_onnxruntime import RapidOCR  # noqa: F401
        return True
    except Exception:
        return False


def _default_engine_factory() -> Any:
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def _full_frame_region(width: int, height: int) -> dict[str, int]:
    return {"x1": 0, "y1": 0, "x2": width, "y2": height, "sourceWidth": width, "sourceHeight": height}


def _unwrap_ocr_result(raw_result: Any) -> list[Any]:
    if isinstance(raw_result, tuple) and raw_result:
        raw_result = raw_result[0]
    if raw_result is None:
        return []
    if isinstance(raw_result, list):
        return raw_result
    return []


def _box_bounds(raw_box: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(raw_box, (list, tuple)) or len(raw_box) < 4:
        return None
    points: list[tuple[float, float]] = []
    for point in raw_box:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            points.append((float(point[0]), float(point[1])))
        except (TypeError, ValueError):
            continue
    if len(points) < 4:
        return None
    x_values = [point[0] for point in points]
    y_values = [point[1] for point in points]
    return min(x_values), min(y_values), max(x_values), max(y_values)


def _parse_ocr_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, (list, tuple)) or not item:
        return None
    bounds = _box_bounds(item[0])
    if bounds is None:
        return None
    confidence = 1.0
    text = str(item[1] or "").strip() if len(item) >= 2 else ""
    if len(item) >= 3:
        try:
            confidence = float(item[2])
        except (TypeError, ValueError):
            confidence = 0.0
    x1, y1, x2, y2 = bounds
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "confidence": confidence, "text": text}


def _meaningful_text_length(text: str) -> int:
    return sum(1 for char in text if char.isalnum() or "\u4e00" <= char <= "\u9fff")


def _looks_like_subtitle_box(box: dict[str, Any], width: int, height: int) -> bool:
    box_width = float(box["x2"] - box["x1"])
    box_height = float(box["y2"] - box["y1"])
    if float(box["confidence"]) < MIN_OCR_CONFIDENCE:
        return False
    if box_width < width * 0.08 or box_height < height * 0.012:
        return False
    if box_height > height * 0.20:
        return False

    text = str(box.get("text") or "").strip()
    compact_text = re.sub(r"\s+", "", text)
    if not compact_text:
        return False
    if re.fullmatch(r"[\d:：/\\.\-]+", compact_text):
        return False

    meaningful_length = _meaningful_text_length(compact_text)
    width_ratio = box_width / max(1, width)
    center_x_ratio = ((float(box["x1"]) + float(box["x2"])) / 2) / max(1, width)
    center_y_ratio = ((float(box["y1"]) + float(box["y2"])) / 2) / max(1, height)
    if center_x_ratio < 0.18 or center_x_ratio > 0.82:
        return False
    if center_y_ratio < 0.58:
        return width_ratio >= 0.40 and meaningful_length >= 5
    if width_ratio >= 0.26 and meaningful_length >= 4:
        return True
    if center_y_ratio >= 0.58 and width_ratio >= 0.18 and meaningful_length >= 3:
        return True
    return False


def _cluster_boxes_by_y(boxes: list[dict[str, float]], height: int) -> list[list[dict[str, float]]]:
    tolerance = max(22.0, height * 0.055)
    clusters: list[list[dict[str, float]]] = []
    for box in sorted(boxes, key=lambda item: (item["y1"] + item["y2"]) / 2):
        center = (box["y1"] + box["y2"]) / 2
        if clusters:
            last_center = statistics.median([(item["y1"] + item["y2"]) / 2 for item in clusters[-1]])
            if abs(center - last_center) <= tolerance:
                clusters[-1].append(box)
                continue
        clusters.append([box])
    return clusters


def _stable_clusters(boxes: list[dict[str, float]], frame_count: int, height: int) -> list[list[dict[str, float]]]:
    min_cluster_count = max(2, math.ceil(max(1, frame_count) * 0.35))
    return [cluster for cluster in _cluster_boxes_by_y(boxes, height) if len(cluster) >= min_cluster_count]


def detect_subtitle_region_from_frames(
    frames: list[Any],
    width: int,
    height: int,
    engine_factory: Callable[[], Any] | None = None,
) -> dict[str, Any] | None:
    if not frames or width <= 0 or height <= 0:
        return None

    try:
        engine = (engine_factory or _default_engine_factory)()
    except Exception:
        return None

    boxes: list[dict[str, float]] = []
    for frame in frames:
        try:
            raw_items = _unwrap_ocr_result(engine(frame))
        except Exception:
            continue
        for raw_item in raw_items:
            box = _parse_ocr_item(raw_item)
            if box is None:
                continue
            if not _looks_like_subtitle_box(box, width, height):
                continue
            boxes.append(box)

    clusters = _stable_clusters(boxes, len(frames), height)
    if not clusters:
        return None
    if len(clusters) >= 2:
        return {
            "hasSubtitle": True,
            "region": _full_frame_region(width, height),
            "confidence": 0.72,
            "method": "multi-position-ocr-full-frame",
        }

    cluster = clusters[0]
    y1 = max(0, min(height - 1, round(min(item["y1"] for item in cluster) - height * 0.02)))
    y2 = max(y1 + 1, min(height, round(max(item["y2"] for item in cluster) + height * 0.03)))
    avg_confidence = sum(float(item["confidence"]) for item in cluster) / max(1, len(cluster))
    return {
        "hasSubtitle": True,
        "region": {"x1": 0, "y1": y1, "x2": width, "y2": y2, "sourceWidth": width, "sourceHeight": height},
        "confidence": round(max(0.6, min(0.96, avg_confidence)), 2),
        "method": "ocr-text-line",
    }
