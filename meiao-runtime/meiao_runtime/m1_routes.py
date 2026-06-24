from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import Body, FastAPI, File, Form, Query, Request, UploadFile
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response

from . import ab_dedup_runtime, capcut_audio_cache_runtime, capcut_draft_runtime, capcut_file_runtime, capcut_template_preview_runtime, copy_clean_runtime, diagnosis_runtime, flow_mutation_runtime, license_admin_runtime, local_system_runtime, media_ingest_runtime, runtime_log_runtime, script_template_runtime, vector_mutation_runtime, voice_runtime
from .route_helpers import (
    append_debug_log as _append_debug_log,
    callable_or_raise as _callable,
    ingest_error_response as _ingest_error_response,
    json_response as _json,
    payload_dict as _payload,
)


RouteErrorHandler = Callable[[str, BaseException], JSONResponse]
LogErrorHandler = Callable[[str, BaseException], None]
LegacyProxy = Callable[[Request, dict[str, str]], Any]

LICENSE_HEADERS = {"X-Meiao-FastAPI-Route": "license"}
VOICE_HEADERS = {"X-Meiao-FastAPI-Route": "voice-runtime"}
ANALYSIS_HEADERS = {"X-Meiao-FastAPI-Route": "analysis-models"}
COPY_CLEAN_HEADERS = {"X-Meiao-FastAPI-Route": "copy-clean"}
MEDIA_HEADERS = {"X-Meiao-FastAPI-Route": "media-ingest"}
SCRIPT_HEADERS = {"X-Meiao-FastAPI-Route": "script-template"}
FLOW_LOG_HEADERS = {"X-Meiao-FastAPI-Route": "runtime-log"}
FLOW_HEADERS = {"X-Meiao-FastAPI-Route": "flow-runtime"}
CAPCUT_HEADERS = {"X-Meiao-FastAPI-Route": "capcut-mate"}


async def json_payload(request: Request) -> Any:
    try:
        return await request.json()
    except Exception:
        return {}


def register(app: FastAPI, legacy_globals: dict[str, Any], runtime_error_response: RouteErrorHandler, log_legacy_error: LogErrorHandler, legacy_proxy: LegacyProxy | None = None) -> None:
    ingest_error_type = legacy_globals.get("IngestError")

    def is_ingest_error(error: BaseException) -> bool:
        return isinstance(ingest_error_type, type) and isinstance(error, ingest_error_type)

    @app.post("/api/license/admin/login")
    async def license_admin_login(payload: Any = Body(default=None)) -> Response:
        try:
            status_code, result = await run_in_threadpool(license_admin_runtime.login, legacy_globals, payload)
            return _json(status_code, result, LICENSE_HEADERS)
        except Exception as error:
            if is_ingest_error(error):
                return _ingest_error_response(error, LICENSE_HEADERS)
            log_legacy_error("api.license.admin.login.error", error)
            return _json(500, {"error": f"主管理登录失败：{error}"}, LICENSE_HEADERS)

    @app.post("/api/license/admin/session")
    async def license_admin_session() -> Response:
        try:
            status_code, result = await run_in_threadpool(license_admin_runtime.session, legacy_globals)
            return _json(status_code, result, LICENSE_HEADERS)
        except Exception as error:
            if is_ingest_error(error):
                return _ingest_error_response(error, LICENSE_HEADERS)
            return _json(500, {"error": f"读取主管理会话失败：{error}"}, LICENSE_HEADERS)

    @app.post("/api/license/admin/logout")
    async def license_admin_logout() -> Response:
        status_code, result = await run_in_threadpool(license_admin_runtime.logout, legacy_globals)
        return _json(status_code, result, LICENSE_HEADERS)

    @app.post("/api/license/admin/list")
    async def license_admin_list(payload: Any = Body(default=None)) -> Response:
        try:
            status_code, result = await run_in_threadpool(license_admin_runtime.list_licenses, legacy_globals, payload)
            return _json(status_code, result, LICENSE_HEADERS)
        except Exception as error:
            if is_ingest_error(error):
                return _ingest_error_response(error, LICENSE_HEADERS)
            return _json(500, {"error": f"读取授权列表失败：{error}"}, LICENSE_HEADERS)

    @app.post("/api/license/admin/create")
    async def license_admin_create(payload: Any = Body(default=None)) -> Response:
        try:
            status_code, result = await run_in_threadpool(license_admin_runtime.create, legacy_globals, payload)
            return _json(status_code, result, LICENSE_HEADERS)
        except Exception as error:
            if is_ingest_error(error):
                return _ingest_error_response(error, LICENSE_HEADERS)
            return _json(500, {"error": f"创建授权失败：{error}"}, LICENSE_HEADERS)

    @app.post("/api/license/admin/status")
    async def license_admin_status(payload: Any = Body(default=None)) -> Response:
        try:
            status_code, result = await run_in_threadpool(license_admin_runtime.set_status, legacy_globals, payload)
            return _json(status_code, result, LICENSE_HEADERS)
        except Exception as error:
            if is_ingest_error(error):
                return _ingest_error_response(error, LICENSE_HEADERS)
            return _json(500, {"error": f"更新授权状态失败：{error}"}, LICENSE_HEADERS)

    @app.post("/api/license/admin/reset-devices")
    async def license_admin_reset_devices(payload: Any = Body(default=None)) -> Response:
        try:
            status_code, result = await run_in_threadpool(license_admin_runtime.reset_devices, legacy_globals, payload)
            return _json(status_code, result, LICENSE_HEADERS)
        except Exception as error:
            if is_ingest_error(error):
                return _ingest_error_response(error, LICENSE_HEADERS)
            return _json(500, {"error": f"重置绑定设备失败：{error}"}, LICENSE_HEADERS)
    @app.post("/api/voice/elevenlabs/create")
    async def elevenlabs_voice_create(request: Request) -> Response:
        try:
            payload = await json_payload(request)
            status_code, result = await run_in_threadpool(voice_runtime.create, legacy_globals, payload)
            return _json(status_code, result, VOICE_HEADERS)
        except Exception as error:
            if is_ingest_error(error):
                return _ingest_error_response(error, VOICE_HEADERS)
            log_legacy_error("api.voice.elevenlabs.create.error", error)
            return _json(500, {"error": str(error)}, VOICE_HEADERS)

    @app.post("/api/voice/elevenlabs/status")
    async def elevenlabs_voice_status(request: Request) -> Response:
        try:
            payload = await json_payload(request)
            status_code, result = await run_in_threadpool(voice_runtime.status, legacy_globals, payload)
            return _json(status_code, result, VOICE_HEADERS)
        except Exception as error:
            if is_ingest_error(error):
                return _ingest_error_response(error, VOICE_HEADERS)
            log_legacy_error("api.voice.elevenlabs.status.error", error)
            return _json(500, {"error": str(error)}, VOICE_HEADERS)

    @app.get("/api/voice/elevenlabs/preview")
    async def elevenlabs_voice_preview(request: Request, voiceId: str = Query("", alias="voiceId"), voice: str = "") -> Response:
        try:
            voice_id = str(voiceId or voice or "").strip()
            target = await run_in_threadpool(voice_runtime.preview_target, legacy_globals, voice_id)
            if target is None:
                return _json(404, {"error": "Voice preview file is unavailable"}, VOICE_HEADERS)
            return voice_runtime.preview_file_response(Path(target), request.headers.get("range"), VOICE_HEADERS)
        except Exception as error:
            log_legacy_error("api.voice.elevenlabs.preview.error", error)
            return _json(500, {"error": str(error)}, VOICE_HEADERS)

    @app.post("/api/voice/elevenlabs/preview/prefetch")
    async def elevenlabs_voice_preview_prefetch(payload: Any = None) -> Response:
        try:
            status_code, result = await run_in_threadpool(voice_runtime.preview_prefetch, legacy_globals, payload)
            return _json(status_code, result, VOICE_HEADERS)
        except Exception as error:
            log_legacy_error("api.voice.elevenlabs.preview.prefetch.error", error)
            return _json(500, {"error": str(error)}, VOICE_HEADERS)

    @app.post("/api/diagnosis/extract")
    async def diagnosis_extract(payload: Any = None) -> Response:
        try:
            status_code, result = await run_in_threadpool(diagnosis_runtime.extract, legacy_globals, payload)
            return _json(status_code, result, ANALYSIS_HEADERS)
        except Exception as error:
            if is_ingest_error(error):
                return _ingest_error_response(error, ANALYSIS_HEADERS)
            log_legacy_error("api.diagnosis.extract.error", error)
            return _json(500, {"error": f"Video diagnosis extraction failed: {error}"}, ANALYSIS_HEADERS)
    @app.post("/api/copy-clean/submit")
    async def copy_clean_submit(payload: Any = None) -> Response:
        try:
            data = _payload(payload)
            items = data.get("items")
            if not isinstance(items, list) or not items:
                return _json(400, {"error": "缺少 items"}, COPY_CLEAN_HEADERS)
            result = await run_in_threadpool(copy_clean_runtime.submit, legacy_globals, items)
            return _json(200, result, COPY_CLEAN_HEADERS)
        except Exception as error:
            log_legacy_error("api.copy_clean.submit.error", error)
            return _json(500, {"error": str(error)}, COPY_CLEAN_HEADERS)

    @app.post("/api/copy-clean/detect-region")
    async def copy_clean_detect_region(payload: Any = None) -> Response:
        try:
            data = _payload(payload)
            items = data.get("items")
            if not isinstance(items, list) or not items:
                return _json(400, {"error": "缺少 items"}, COPY_CLEAN_HEADERS)
            result = await run_in_threadpool(copy_clean_runtime.detect_region, legacy_globals, items)
            return _json(200, result, COPY_CLEAN_HEADERS)
        except Exception as error:
            log_legacy_error("api.copy_clean.detect_region.error", error)
            return _json(500, {"error": str(error)}, COPY_CLEAN_HEADERS)

    @app.post("/api/copy-clean/progress")
    async def copy_clean_progress(payload: Any = None) -> Response:
        try:
            data = _payload(payload)
            task_ids = data.get("taskIds")
            if not isinstance(task_ids, list) or not task_ids:
                return _json(400, {"error": "缺少 taskIds"}, COPY_CLEAN_HEADERS)
            status_code, result = await run_in_threadpool(copy_clean_runtime.progress, legacy_globals, data, task_ids)
            return _json(status_code, result, COPY_CLEAN_HEADERS)
        except Exception as error:
            log_legacy_error("api.copy_clean.progress.error", error)
            return _json(500, {"error": str(error)}, COPY_CLEAN_HEADERS)

    @app.post("/api/ingest-url")
    async def ingest_url(payload: Any = None) -> Response:
        try:
            status_code, result = await run_in_threadpool(media_ingest_runtime.ingest_url, legacy_globals, payload)
            return _json(status_code, result, MEDIA_HEADERS)
        except Exception as error:
            if is_ingest_error(error):
                return _ingest_error_response(error, MEDIA_HEADERS)
            log_legacy_error("api.ingest_url.error", error)
            return _json(500, {"error": str(error)}, MEDIA_HEADERS)

    @app.post("/api/ingest-local")
    async def ingest_local(
        file: UploadFile | None = File(None),
        projectId: str = Form(""),
        clientItemId: str = Form(""),
        uploadForAnalysis: str = Form(""),
    ) -> Response:
        try:
            if file is None:
                return _json(400, {"error": "Missing local video file"}, MEDIA_HEADERS)
            file_bytes = await file.read()
            filename = file.filename or "local-video.mp4"
            status_code, result = await run_in_threadpool(
                media_ingest_runtime.ingest_local,
                legacy_globals,
                filename=filename,
                file_bytes=file_bytes,
                project_id=str(projectId or "").strip(),
                client_item_id=str(clientItemId or "").strip(),
                upload_for_analysis=str(uploadForAnalysis or "").strip().lower() in {"1", "true", "yes"},
            )
            return _json(status_code, result, MEDIA_HEADERS)
        except Exception as error:
            if is_ingest_error(error):
                return _ingest_error_response(error, MEDIA_HEADERS)
            log_legacy_error("api.ingest_local.error", error)
            return _json(500, {"error": str(error)}, MEDIA_HEADERS)

    @app.delete("/api/media/{media_id:path}")
    async def media_delete(media_id: str) -> Response:
        try:
            status_code, result = await run_in_threadpool(media_ingest_runtime.delete_media, legacy_globals, media_id)
            return _json(status_code, result, MEDIA_HEADERS)
        except Exception as error:
            log_legacy_error("api.media.delete.error", error)
            return _json(500, {"error": str(error)}, MEDIA_HEADERS)

    @app.post("/api/media/{media_id:path}/scene-split")
    async def media_scene_split(media_id: str, payload: Any = None) -> Response:
        try:
            status_code, result = await run_in_threadpool(media_ingest_runtime.scene_split, legacy_globals, media_id, payload)
            return _json(status_code, result, MEDIA_HEADERS)
        except Exception as error:
            if is_ingest_error(error):
                return _ingest_error_response(error, MEDIA_HEADERS)
            log_legacy_error("api.scene_split.error", error)
            return _json(500, {"error": str(error)}, MEDIA_HEADERS)
    @app.get("/api/script-template/xlsx")
    async def script_template_download() -> Response:
        try:
            status_code, payload, extra_headers, media_type = await run_in_threadpool(script_template_runtime.download_xlsx, legacy_globals)
            if isinstance(payload, bytes):
                return Response(payload, status_code=status_code, media_type=media_type, headers={**SCRIPT_HEADERS, **extra_headers})
            return _json(status_code, payload, SCRIPT_HEADERS)
        except Exception as error:
            log_legacy_error("api.script_template.download.error", error)
            return _json(500, {"error": str(error)}, SCRIPT_HEADERS)

    @app.post("/api/script-template/import")
    async def script_template_import(file: UploadFile | None = File(None)) -> Response:
        try:
            if file is None:
                return _json(400, {"error": "Missing template file"}, SCRIPT_HEADERS)
            filename = str(file.filename or "").strip()
            file_bytes = await file.read()
            status_code, result = await run_in_threadpool(script_template_runtime.import_xlsx, legacy_globals, filename, file_bytes)
            return _json(status_code, result, SCRIPT_HEADERS)
        except Exception as error:
            log_legacy_error("api.script_template.import.error", error)
            return _json(500, {"error": str(error)}, SCRIPT_HEADERS)

    @app.post("/api/ab-dedup/run")
    async def ab_dedup_run(payload: Any = None) -> Response:
        try:
            status_code, result = await run_in_threadpool(ab_dedup_runtime.run, legacy_globals, payload)
            return _json(status_code, result, ANALYSIS_HEADERS)
        except Exception as error:
            if is_ingest_error(error):
                return _ingest_error_response(error, ANALYSIS_HEADERS)
            log_legacy_error("api.ab_dedup.run.error", error)
            return _json(500, {"error": str(error)}, ANALYSIS_HEADERS)

    @app.get("/api/logs/ingest")
    async def ingest_logs() -> Response:
        status_code, result = await run_in_threadpool(runtime_log_runtime.ingest_logs, legacy_globals)
        return _json(status_code, result, FLOW_LOG_HEADERS)

    @app.post("/api/log")
    async def client_log(payload: Any = None) -> Response:
        status_code, result = await run_in_threadpool(runtime_log_runtime.client_log, legacy_globals, payload)
        return _json(status_code, result, FLOW_LOG_HEADERS)
    @app.get("/api/system/select-video-folder")
    async def system_select_video_folder(request: Request) -> Response:
        return await local_system_runtime.select_video_folder(legacy_proxy, request, MEDIA_HEADERS)

    @app.get("/api/system/select-export-folder")
    async def system_select_export_folder(request: Request) -> Response:
        return await local_system_runtime.select_export_folder(legacy_proxy, request, MEDIA_HEADERS)

    @app.post("/api/system/open-local-path")
    async def system_open_local_path(request: Request) -> Response:
        return await local_system_runtime.open_local_path(legacy_proxy, request, MEDIA_HEADERS)
    @app.post("/api/auth/open-login")
    async def auth_open_login(payload: Any = Body(default=None)) -> Response:
        status_code, result = await run_in_threadpool(flow_mutation_runtime.auth_open_login_http, legacy_globals, payload)
        return _json(status_code, result, FLOW_HEADERS)

    @app.post("/api/flow/chrome/start")
    async def flow_chrome_start(payload: Any = Body(default=None)) -> Response:
        status_code, result = await run_in_threadpool(flow_mutation_runtime.chrome_start_http, legacy_globals, payload)
        return _json(status_code, result, FLOW_HEADERS)

    @app.post("/api/flow/chrome/open")
    async def flow_chrome_open(payload: Any = Body(default=None)) -> Response:
        status_code, result = await run_in_threadpool(flow_mutation_runtime.chrome_open_http, legacy_globals, payload)
        return _json(status_code, result, FLOW_HEADERS)

    @app.post("/api/flow/account/login")
    async def flow_account_login(payload: Any = Body(default=None)) -> Response:
        status_code, result = await run_in_threadpool(flow_mutation_runtime.account_login_http, legacy_globals, payload)
        return _json(status_code, result, FLOW_HEADERS)

    @app.post("/api/flow/account/continue")
    async def flow_account_continue(payload: Any = Body(default=None)) -> Response:
        status_code, result = await run_in_threadpool(flow_mutation_runtime.account_continue_http, legacy_globals, payload)
        return _json(status_code, result, FLOW_HEADERS)

    @app.post("/api/flow/network/select")
    async def flow_network_select(payload: Any = Body(default=None)) -> Response:
        status_code, result = await run_in_threadpool(flow_mutation_runtime.network_select_http, legacy_globals, payload)
        return _json(status_code, result, FLOW_HEADERS)

    @app.post("/api/flow/page/start-project")
    async def flow_start_project(payload: Any = Body(default=None)) -> Response:
        status_code, result = await run_in_threadpool(flow_mutation_runtime.start_project_http, legacy_globals, payload)
        return _json(status_code, result, FLOW_HEADERS)

    @app.post("/api/flow/page/prepare-normal-dialog")
    async def flow_prepare_normal_dialog(payload: Any = Body(default=None)) -> Response:
        status_code, result = await run_in_threadpool(flow_mutation_runtime.prepare_normal_dialog_http, legacy_globals, payload)
        return _json(status_code, result, FLOW_HEADERS)

    @app.post("/api/flow/page/require-normal-dialog")
    async def flow_require_normal_dialog(payload: Any = Body(default=None)) -> Response:
        status_code, result = await run_in_threadpool(flow_mutation_runtime.require_normal_dialog_http, legacy_globals, payload)
        return _json(status_code, result, FLOW_HEADERS)

    @app.post("/api/flow/page/bind-reference")
    async def flow_bind_reference(payload: Any = Body(default=None)) -> Response:
        status_code, result = await run_in_threadpool(flow_mutation_runtime.bind_reference_http, legacy_globals, payload)
        return _json(status_code, result, FLOW_HEADERS)

    @app.post("/api/flow/page/prepare-reference-images")
    async def flow_prepare_reference_images(payload: Any = Body(default=None)) -> Response:
        status_code, result = await run_in_threadpool(flow_mutation_runtime.prepare_reference_images_http, legacy_globals, payload)
        return _json(status_code, result, FLOW_HEADERS)

    @app.post("/api/flow/page/set-prompt")
    async def flow_set_prompt(payload: Any = Body(default=None)) -> Response:
        status_code, result = await run_in_threadpool(flow_mutation_runtime.set_prompt_http, legacy_globals, payload)
        return _json(status_code, result, FLOW_HEADERS)

    @app.post("/api/flow/page/click-submit")
    async def flow_click_submit(payload: Any = Body(default=None)) -> Response:
        status_code, result = await run_in_threadpool(flow_mutation_runtime.click_submit_http, legacy_globals, payload)
        return _json(status_code, result, FLOW_HEADERS)

    @app.post("/api/flow/page/submit-prompt")
    async def flow_submit_prompt(payload: Any = Body(default=None)) -> Response:
        status_code, result = await run_in_threadpool(flow_mutation_runtime.submit_prompt_http, legacy_globals, payload)
        return _json(status_code, result, FLOW_HEADERS)

    @app.post("/api/flow/page/collect-results")
    async def flow_collect_results(payload: Any = Body(default=None)) -> Response:
        status_code, result = await run_in_threadpool(flow_mutation_runtime.collect_results_http, legacy_globals, payload)
        return _json(status_code, result, FLOW_HEADERS)
    @app.post("/api/capcut-mate/generate-draft")
    async def capcut_generate_draft(payload: Any = Body(default=None)) -> Response:
        status_code, result = await run_in_threadpool(capcut_draft_runtime.generate_draft, legacy_globals, payload)
        return _json(status_code, result, CAPCUT_HEADERS)

    @app.post("/api/capcut-mate/template-preview")
    async def capcut_template_preview(request: Request, payload: Any = Body(default=None)) -> Response:
        request_base_url = str(request.base_url).rstrip("/")
        status_code, result = await run_in_threadpool(
            capcut_template_preview_runtime.template_preview,
            legacy_globals,
            payload,
            request_base_url,
        )
        return _json(status_code, result, CAPCUT_HEADERS)

    @app.post("/api/capcut-mate/audio-cache/auto-download")
    async def capcut_audio_cache_auto_download(payload: Any = Body(default=None)) -> Response:
        status_code, result = await run_in_threadpool(capcut_audio_cache_runtime.auto_download, legacy_globals, payload)
        return _json(status_code, result, CAPCUT_HEADERS)

    @app.get("/api/capcut-mate/audio-cache/open")
    async def capcut_audio_cache_open() -> Response:
        status_code, result = await run_in_threadpool(capcut_audio_cache_runtime.open_cache, legacy_globals)
        return _json(status_code, result, CAPCUT_HEADERS)

    @app.get("/api/capcut-mate/template-preview-media")
    async def capcut_template_preview_media() -> Response:
        try:
            status_code, result = await run_in_threadpool(capcut_file_runtime.template_preview_media, legacy_globals)
            return _json(status_code, result, CAPCUT_HEADERS)
        except Exception as error:
            log_legacy_error("api.capcut_mate.template_preview_media.error", error)
            return _json(500, {"error": f"读取模板预览素材失败：{error}"}, CAPCUT_HEADERS)

    @app.get("/api/capcut-mate/audio-file")
    async def capcut_audio_file(request: Request) -> Response:
        raw_path = str(request.query_params.get("path") or "")
        range_header = request.headers.get("range")
        try:
            return await run_in_threadpool(
                capcut_file_runtime.audio_file_response,
                legacy_globals,
                raw_path,
                range_header,
                CAPCUT_HEADERS,
            )
        except Exception as error:
            log_legacy_error("api.capcut_mate.audio_file.error", error)
            return _json(500, {"error": f"读取音频失败：{error}"}, CAPCUT_HEADERS)

    @app.post("/api/capcut-mate/open-draft")
    async def capcut_open_draft(payload: Any = Body(default=None)) -> Response:
        status_code, result = await run_in_threadpool(capcut_draft_runtime.open_draft, legacy_globals, payload)
        return _json(status_code, result, CAPCUT_HEADERS)

    @app.post("/api/capcut-mate/delete-draft")
    async def capcut_delete_draft(payload: Any = Body(default=None)) -> Response:
        status_code, result = await run_in_threadpool(capcut_draft_runtime.delete_draft, legacy_globals, payload)
        return _json(status_code, result, CAPCUT_HEADERS)

    @app.post("/api/capcut-mate/export-video")
    async def capcut_export_video(payload: Any = Body(default=None)) -> Response:
        status_code, result = await run_in_threadpool(capcut_draft_runtime.export_video, legacy_globals, payload)
        return _json(status_code, result, CAPCUT_HEADERS)

    @app.post("/api/capcut-mate/export-status")
    async def capcut_export_status(payload: Any = Body(default=None)) -> Response:
        status_code, result = await run_in_threadpool(capcut_draft_runtime.export_status, legacy_globals, payload)
        return _json(status_code, result, CAPCUT_HEADERS)

    @app.post("/api/capcut-mate/archive-exported-video")
    async def capcut_archive_exported_video(payload: Any = Body(default=None)) -> Response:
        status_code, result = await run_in_threadpool(capcut_draft_runtime.archive_exported_video, legacy_globals, payload)
        return _json(status_code, result, CAPCUT_HEADERS)
    @app.post("/api/vectors/embed-scenes")
    async def vectors_embed_scenes(request: Request) -> Response:
        return await vector_mutation_runtime.embed_scenes(legacy_proxy, request, ANALYSIS_HEADERS)

    @app.post("/api/vectors/tag-tasks/submit")
    async def vectors_tag_tasks_submit(request: Request) -> Response:
        return await vector_mutation_runtime.tag_tasks_submit(legacy_proxy, request, ANALYSIS_HEADERS)

    @app.post("/api/vectors/tag-tasks/cancel")
    async def vectors_tag_tasks_cancel(request: Request) -> Response:
        return await vector_mutation_runtime.tag_tasks_cancel(legacy_proxy, request, ANALYSIS_HEADERS)

    @app.post("/api/vectors/tag-tasks/clear")
    async def vectors_tag_tasks_clear(request: Request) -> Response:
        return await vector_mutation_runtime.tag_tasks_clear(legacy_proxy, request, ANALYSIS_HEADERS)

    @app.post("/api/vectors/prune")
    async def vectors_prune(request: Request) -> Response:
        return await vector_mutation_runtime.prune(legacy_proxy, request, ANALYSIS_HEADERS)
