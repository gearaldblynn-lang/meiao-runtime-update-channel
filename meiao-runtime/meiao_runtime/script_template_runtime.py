from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .route_helpers import append_debug_log as _append_debug_log
from .route_helpers import callable_or_raise as _callable


def download_xlsx(legacy_globals: dict[str, Any]) -> tuple[int, dict[str, Any] | bytes, dict[str, str], str | None]:
    source = legacy_globals["SCRIPT_TEMPLATE_SOURCE_FILE"]
    if not source.exists():
        return 404, {"error": "Template file does not exist."}, {}, None
    payload = source.read_bytes()
    filename = quote(str(legacy_globals["SCRIPT_TEMPLATE_FILENAME"]))
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
        "Content-Length": str(len(payload)),
    }
    return 200, payload, headers, str(legacy_globals["SCRIPT_TEMPLATE_MIME"])


def import_xlsx(legacy_globals: dict[str, Any], filename: str, file_bytes: bytes) -> tuple[int, dict[str, Any]]:
    filename = str(filename or "").strip()
    suffix = Path(filename).suffix.lower()
    if not file_bytes:
        return 400, {"error": "Template file is empty."}
    if suffix not in {".xlsx", ".xlsm"}:
        return 400, {"error": "Please upload an .xlsx Excel template."}
    load_workbook = legacy_globals["load_workbook"]
    workbook = load_workbook(BytesIO(file_bytes), data_only=True, read_only=True)
    sheet_name, header_row, shot_requirements = _callable(legacy_globals, "find_script_table_rows")(workbook)
    _append_debug_log(
        legacy_globals,
        "api.script_template.import",
        {"filename": filename, "sheetName": sheet_name, "headerRow": header_row, "rowCount": len(shot_requirements)},
    )
    return 200, {
        "format": "xlsx",
        "fileName": filename,
        "sheetName": sheet_name,
        "headerRow": header_row,
        "rowCount": len(shot_requirements),
        "shotRequirements": shot_requirements,
    }
