from __future__ import annotations

import json
import mimetypes
import shutil
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import HTTP
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote, unquote

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from . import analysis_models, capcut_mate, capcut_task_adapter_runtime, copy_clean_runtime, diagnosis_runtime, flow_mutation_runtime, flow_runtime, flow_task_adapter_runtime, global_settings, license_runtime, m1_routes, task_runtime, voice_task_adapter_runtime
from .state_store import StateStore


LOCAL_ORIGIN_RE = r"^https?://(127\.0\.0\.1|localhost|\[::1\]|::1)(:\d+)?$"
LEGACY_DISPATCH_LOCK = threading.RLock()
CLIENT_STATE_LOCK = threading.RLock()
CLIENT_STATE_RECOVER_LOCK = threading.RLock()
CLIENT_STATE_RECOVER_CACHE: dict[str, Any] = {"expires_at": 0.0, "payload": None}
CLIENT_STATE_ROUTE_HEADERS = {"X-Meiao-FastAPI-Route": "client-state"}
MEDIA_STORAGE_LOCK = threading.RLock()
MEDIA_STORAGE_ROUTE_HEADERS = {"X-Meiao-FastAPI-Route": "media-storage"}
MEDIA_LIBRARY_CACHE: dict[str, Any] = {"expires_at": 0.0, "payload": None}
LICENSE_ROUTE_HEADERS = {"X-Meiao-FastAPI-Route": "license"}
BGM_LIBRARY_LOCK = threading.RLock()
BGM_LIBRARY_ROUTE_HEADERS = {"X-Meiao-FastAPI-Route": "bgm-library"}
CAPCUT_MATE_ROUTE_HEADERS = {"X-Meiao-FastAPI-Route": "capcut-mate"}
FLOW_RUNTIME_ROUTE_HEADERS = {"X-Meiao-FastAPI-Route": "flow-runtime"}
ANALYSIS_MODELS_ROUTE_HEADERS = {"X-Meiao-FastAPI-Route": "analysis-models"}
GLOBAL_SETTINGS_ROUTE_HEADERS = {"X-Meiao-FastAPI-Route": "global-settings"}
MEDIA_OPERATION_ROUTE_HEADERS = {"X-Meiao-FastAPI-Route": "media-operation"}
PRODUCT_PROJECT_ROUTE_HEADERS = {"X-Meiao-FastAPI-Route": "product-projects"}
TASK_RUNTIME_ROUTE_HEADERS = {"X-Meiao-FastAPI-Route": "task-runtime"}


@dataclass
class LegacyResult:
    status_code: int
    headers: dict[str, str]
    body: bytes


class RequestHeaders:
    def __init__(self, headers: dict[str, str]) -> None:
        self._headers = {key.lower(): value for key, value in headers.items()}

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._headers.get(key.lower(), default)


def parse_legacy_response(raw: bytes) -> LegacyResult:
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    status_code = 500
    headers: dict[str, str] = {}
    if lines:
        parts = lines[0].decode("iso-8859-1", errors="replace").split(" ", 2)
        if len(parts) >= 2 and parts[1].isdigit():
            status_code = int(parts[1])
    if len(lines) > 1:
        message = BytesParser(policy=HTTP).parsebytes(b"\r\n".join(lines[1:]) + b"\r\n\r\n")
        for key, value in message.items():
            if key.lower() not in {"connection", "server", "date", "content-length"}:
                headers[key] = value
    return LegacyResult(status_code=status_code, headers=headers, body=body)


def normalize_json_response(result: LegacyResult) -> LegacyResult:
    content_type = next((value for key, value in result.headers.items() if key.lower() == "content-type"), "")
    if "application/json" not in content_type.lower() or not result.body:
        return result
    try:
        payload = json.loads(result.body.decode("utf-8"))
    except Exception:
        return result
    headers = {key: value for key, value in result.headers.items() if key.lower() != "content-type"}
    headers["Content-Type"] = "application/json; charset=utf-8"
    return LegacyResult(
        status_code=result.status_code,
        headers=headers,
        body=json.dumps(payload).encode("utf-8"),
    )


def json_response(payload: Any, status_code: int = 200, headers: dict[str, str] | None = None) -> Response:
    return Response(
        json.dumps(payload).encode("utf-8"),
        status_code=status_code,
        headers=headers,
        media_type="application/json; charset=utf-8",
    )


def clear_client_state_recover_cache() -> None:
    CLIENT_STATE_RECOVER_CACHE["payload"] = None
    CLIENT_STATE_RECOVER_CACHE["expires_at"] = 0.0


def clear_media_storage_cache() -> None:
    MEDIA_LIBRARY_CACHE["payload"] = None
    MEDIA_LIBRARY_CACHE["expires_at"] = 0.0


def dispatch_legacy(handler_cls: type[Any], method: str, path: str, headers: dict[str, str], body: bytes, client_host: str, client_port: int) -> LegacyResult:
    handler = object.__new__(handler_cls)
    requestline_path = quote(path, safe="/:?&=%#[]@!$&'()*+,;")
    handler.requestline = f"{method} {requestline_path} HTTP/1.1"
    handler.command = method
    handler.path = path
    handler.request_version = "HTTP/1.1"
    handler.headers = RequestHeaders({**headers, "content-length": str(len(body))})
    handler.rfile = BytesIO(body)
    handler.wfile = BytesIO()
    handler.client_address = (client_host, client_port)
    handler.server = SimpleNamespace(server_name="127.0.0.1", server_port=8787)
    handler.close_connection = True
    handler.raw_requestline = handler.requestline.encode("iso-8859-1")

    method_name = f"do_{method.upper()}"
    if not hasattr(handler, method_name):
        return LegacyResult(405, {"Content-Type": "application/json; charset=utf-8"}, b'{"error":"Method not allowed"}')
    with LEGACY_DISPATCH_LOCK:
        getattr(handler, method_name)()
    return parse_legacy_response(handler.wfile.getvalue())


def create_app(legacy_handler_cls: type[Any] | None = None) -> FastAPI:
    app = FastAPI(title="MEIAO Local Runtime", docs_url=None, redoc_url=None, openapi_url=None)
    legacy_globals = getattr(getattr(legacy_handler_cls, "do_GET", None), "__globals__", {}) if legacy_handler_cls else {}
    state_store = StateStore(legacy_globals)
    data_root = Path(legacy_globals.get("DATA_ROOT") or Path.cwd() / "storage")

    def copy_clean_submit_task(payload: dict[str, Any]) -> dict[str, Any]:
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("items must be an array")
        return copy_clean_runtime.submit(legacy_globals, items)

    def diagnosis_extract_task(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return diagnosis_runtime.extract(legacy_globals, payload)

    def voice_elevenlabs_create_task(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return voice_task_adapter_runtime.create_elevenlabs_voice_task(legacy_globals, payload)

    def flow_submit_prompt_task(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return flow_task_adapter_runtime.submit_prompt_task(legacy_globals, payload)

    def flow_collect_results_task(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return flow_task_adapter_runtime.collect_results_task(legacy_globals, payload)

    def capcut_generate_draft_task(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return capcut_task_adapter_runtime.generate_draft_task(legacy_globals, payload)

    def capcut_export_video_task(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return capcut_task_adapter_runtime.export_video_task(legacy_globals, payload)

    runtime_tasks = task_runtime.TaskRuntime(
        data_root,
        {
            "copy-clean-submit": copy_clean_submit_task,
            "diagnosis-extract": diagnosis_extract_task,
            "voice-elevenlabs-create": voice_elevenlabs_create_task,
            "flow-submit-prompt": flow_submit_prompt_task,
            "flow-collect-results": flow_collect_results_task,
            "capcut-generate-draft": capcut_generate_draft_task,
            "capcut-export-video": capcut_export_video_task,
        },
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=LOCAL_ORIGIN_RE,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS", "HEAD"],
        allow_headers=["Content-Type"],
    )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.head("/api/health")
    async def health_head() -> Response:
        return Response(status_code=200)

    @app.get("/api/system/environment")
    async def system_environment() -> Response:
        build_environment_health = legacy_globals.get("build_environment_health")
        if not callable(build_environment_health):
            return json_response({"error": "Environment health is unavailable."}, status_code=500)
        payload = await run_in_threadpool(build_environment_health)
        return json_response(payload)

    def legacy_callable(name: str) -> Any:
        value = legacy_globals.get(name)
        if not callable(value):
            raise RuntimeError(f"Legacy callable {name} is unavailable.")
        return value

    def log_legacy_error(event: str, error: BaseException) -> None:
        append_debug_log = legacy_globals.get("append_debug_log")
        if callable(append_debug_log):
            append_debug_log(event, {"error": str(error), "traceback": traceback.format_exc()})

    def runtime_error_response(event: str, error: BaseException, headers: dict[str, str]) -> Response:
        log_legacy_error(event, error)
        ingest_error = legacy_globals.get("IngestError")
        if isinstance(ingest_error, type) and isinstance(error, ingest_error):
            return json_response(
                {"error": str(error), "code": getattr(error, "code", ""), "action": getattr(error, "action", "")},
                status_code=409,
                headers=headers,
            )
        return json_response({"error": str(error)}, status_code=500, headers=headers)

    async def json_payload(request: Request) -> Any:
        try:
            return await request.json()
        except Exception:
            return {}

    @app.get("/api/system/capcut/locate")
    async def system_capcut_locate() -> Response:
        try:
            payload = await run_in_threadpool(capcut_mate.locate_installation, legacy_globals)
            return json_response(payload, headers=CAPCUT_MATE_ROUTE_HEADERS)
        except Exception as error:
            log_legacy_error("api.system.capcut.locate.error", error)
            return json_response({"error": f"定位剪映安装失败：{error}"}, status_code=500, headers=CAPCUT_MATE_ROUTE_HEADERS)

    @app.get("/api/capcut-mate/assets")
    async def capcut_mate_assets() -> Response:
        try:
            status_code, payload = await run_in_threadpool(capcut_mate.build_assets_result, legacy_globals)
            return json_response(payload, status_code=status_code, headers=CAPCUT_MATE_ROUTE_HEADERS)
        except Exception as error:
            log_legacy_error("api.capcut_mate.assets.error", error)
            return json_response({"error": f"读取剪映资源失败：{error}"}, status_code=500, headers=CAPCUT_MATE_ROUTE_HEADERS)

    @app.get("/api/auth/status")
    async def auth_status() -> Response:
        try:
            payload = await run_in_threadpool(flow_runtime.auth_status, legacy_globals)
            return json_response(payload, headers=FLOW_RUNTIME_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.auth.status.error", error, FLOW_RUNTIME_ROUTE_HEADERS)

    @app.get("/api/flow/chrome/status")
    async def flow_chrome_status() -> Response:
        try:
            payload = await run_in_threadpool(flow_runtime.chrome_status, legacy_globals)
            return json_response(payload, headers=FLOW_RUNTIME_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.flow.chrome.status.error", error, FLOW_RUNTIME_ROUTE_HEADERS)

    @app.get("/api/flow/network/status")
    async def flow_network_status() -> Response:
        try:
            payload = await run_in_threadpool(flow_runtime.network_status, legacy_globals)
            return json_response(payload, headers=FLOW_RUNTIME_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.flow.network.status.error", error, FLOW_RUNTIME_ROUTE_HEADERS)

    @app.get("/api/flow/page/status")
    async def flow_page_status() -> Response:
        try:
            payload = await run_in_threadpool(flow_runtime.page_status, legacy_globals)
            return json_response(payload, headers=FLOW_RUNTIME_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.flow.page.status.error", error, FLOW_RUNTIME_ROUTE_HEADERS)

    @app.get("/api/flow/page/prompt-media-status")
    async def flow_prompt_media_status(request: Request) -> Response:
        try:
            try:
                expected_count = int(str(request.query_params.get("expectedCount") or "0"))
            except Exception:
                expected_count = 0
            payload = await run_in_threadpool(flow_runtime.prompt_media_status, legacy_globals, expected_count)
            return json_response(payload, headers=FLOW_RUNTIME_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.flow.page.prompt_media_status.error", error, FLOW_RUNTIME_ROUTE_HEADERS)

    @app.get("/api/flow/page/prompt-input-snapshot")
    async def flow_prompt_input_snapshot(request: Request) -> Response:
        try:
            label = str(request.query_params.get("label") or "").strip()
            payload = await run_in_threadpool(flow_runtime.prompt_input_snapshot, legacy_globals, label)
            return json_response(payload, headers=FLOW_RUNTIME_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.flow.page.prompt_input_snapshot.error", error, FLOW_RUNTIME_ROUTE_HEADERS)

    @app.get("/api/flow/page/progress")
    async def flow_page_progress(request: Request) -> Response:
        try:
            job_id = str(request.query_params.get("jobId") or "").strip() or None
            payload = await run_in_threadpool(flow_runtime.progress, legacy_globals, job_id)
            return json_response(payload, headers=FLOW_RUNTIME_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.flow.page.progress.error", error, FLOW_RUNTIME_ROUTE_HEADERS)

    @app.get("/api/flow/page/click-targets")
    async def flow_page_click_targets() -> Response:
        try:
            payload = await run_in_threadpool(flow_runtime.click_targets, legacy_globals)
            return json_response(payload, headers=FLOW_RUNTIME_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.flow.page.click_targets.error", error, FLOW_RUNTIME_ROUTE_HEADERS)

    @app.get("/api/flow/live-readiness")
    async def flow_live_readiness(request: Request) -> Response:
        try:
            runtime_url = str(request.base_url).rstrip("/")
            payload = await run_in_threadpool(flow_runtime.live_readiness, legacy_globals, runtime_url)
            return json_response(payload, headers=FLOW_RUNTIME_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.flow.live_readiness.error", error, FLOW_RUNTIME_ROUTE_HEADERS)

    @app.get("/api/model-registry")
    async def model_registry_get() -> Response:
        try:
            payload = await run_in_threadpool(analysis_models.get_model_registry, legacy_globals)
            return json_response(payload, headers=ANALYSIS_MODELS_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.model_registry.get.error", error, ANALYSIS_MODELS_ROUTE_HEADERS)

    @app.post("/api/model-registry")
    async def model_registry_save(request: Request) -> Response:
        try:
            payload = await json_payload(request)
            status_code, result = await run_in_threadpool(analysis_models.save_model_registry, legacy_globals, payload)
            return json_response(result, status_code=status_code, headers=ANALYSIS_MODELS_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.model_registry.save.error", error, ANALYSIS_MODELS_ROUTE_HEADERS)

    @app.get("/api/analysis/models")
    async def analysis_models_get() -> Response:
        try:
            payload = await run_in_threadpool(analysis_models.get_analysis_models, legacy_globals)
            return json_response(payload, headers=ANALYSIS_MODELS_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.analysis.models.get.error", error, ANALYSIS_MODELS_ROUTE_HEADERS)

    @app.post("/api/analysis/models")
    async def analysis_models_save(request: Request) -> Response:
        try:
            payload = await json_payload(request)
            status_code, result = await run_in_threadpool(analysis_models.save_analysis_models, legacy_globals, payload)
            return json_response(result, status_code=status_code, headers=ANALYSIS_MODELS_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.analysis.models.save.error", error, ANALYSIS_MODELS_ROUTE_HEADERS)

    @app.post("/api/analysis/chat")
    async def analysis_chat(request: Request) -> Response:
        try:
            payload = await json_payload(request)
            status_code, result = await run_in_threadpool(analysis_models.chat, legacy_globals, payload)
            return json_response(result, status_code=status_code, headers=ANALYSIS_MODELS_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.analysis.chat.error", error, ANALYSIS_MODELS_ROUTE_HEADERS)

    @app.get("/api/vectors/status")
    async def vectors_status() -> Response:
        try:
            payload = await run_in_threadpool(analysis_models.vector_status, legacy_globals)
            return json_response(payload, headers=ANALYSIS_MODELS_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.vectors.status.error", error, ANALYSIS_MODELS_ROUTE_HEADERS)

    @app.get("/api/vectors/tag-tasks")
    async def vectors_tag_tasks(request: Request) -> Response:
        try:
            project_id = str(request.query_params.get("projectId") or "").strip()
            payload = await run_in_threadpool(analysis_models.vector_tag_tasks, legacy_globals, project_id)
            return json_response(payload, headers=ANALYSIS_MODELS_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.vectors.tag_tasks.error", error, ANALYSIS_MODELS_ROUTE_HEADERS)

    @app.post("/api/vectors/search")
    async def vectors_search(request: Request) -> Response:
        try:
            payload = await json_payload(request)
            status_code, result = await run_in_threadpool(analysis_models.vector_search, legacy_globals, payload)
            return json_response(result, status_code=status_code, headers=ANALYSIS_MODELS_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.vectors.search.error", error, ANALYSIS_MODELS_ROUTE_HEADERS)

    @app.get("/api/client-state")
    async def client_state_get() -> Response:
        try:
            def load_state() -> dict[str, Any]:
                return {"state": state_store.read_client_state()}

            payload = await run_in_threadpool(load_state)
            return json_response(payload, headers=CLIENT_STATE_ROUTE_HEADERS)
        except Exception as error:
            log_legacy_error("api.client_state.get.error", error)
            return json_response({"error": "读取工作台数据失败。"}, status_code=500, headers=CLIENT_STATE_ROUTE_HEADERS)

    @app.get("/api/client-state/recover")
    async def client_state_recover() -> Response:
        try:
            def recover_state() -> dict[str, Any]:
                with CLIENT_STATE_RECOVER_LOCK:
                    cached_payload = CLIENT_STATE_RECOVER_CACHE.get("payload")
                    if isinstance(cached_payload, dict) and time.time() < float(CLIENT_STATE_RECOVER_CACHE.get("expires_at") or 0):
                        return cached_payload
                    state = state_store.recover_client_state()
                    payload = {"state": state, "cleanedChanges": []}
                    CLIENT_STATE_RECOVER_CACHE["payload"] = payload
                    CLIENT_STATE_RECOVER_CACHE["expires_at"] = time.time() + 5
                    return payload

            payload = await run_in_threadpool(recover_state)
            return json_response(payload, headers=CLIENT_STATE_ROUTE_HEADERS)
        except Exception as error:
            log_legacy_error("api.client_state.recover.error", error)
            return json_response({"error": "恢复工作台数据失败。"}, status_code=500, headers=CLIENT_STATE_ROUTE_HEADERS)

    @app.get("/api/client-state/summary")
    async def client_state_summary() -> Response:
        try:
            def summarize_state() -> Any:
                return legacy_callable("client_state_source_signature")()

            payload = await run_in_threadpool(summarize_state)
            return json_response(payload, headers=CLIENT_STATE_ROUTE_HEADERS)
        except Exception as error:
            log_legacy_error("api.client_state.summary.error", error)
            return json_response({"error": "读取工作台数据摘要失败。"}, status_code=500, headers=CLIENT_STATE_ROUTE_HEADERS)

    @app.get("/api/client-state/health")
    async def client_state_health() -> Response:
        try:
            def health_state() -> Any:
                return state_store.check_client_state_health()

            payload = await run_in_threadpool(health_state)
            return json_response(payload, headers=CLIENT_STATE_ROUTE_HEADERS)
        except Exception as error:
            log_legacy_error("api.client_state.health.error", error)
            return json_response({"error": "检查本地数据健康失败。"}, status_code=500, headers=CLIENT_STATE_ROUTE_HEADERS)

    @app.post("/api/client-state")
    async def client_state_save(request: Request) -> Response:
        try:
            payload = await request.json()
        except Exception:
            return json_response({"error": "请求 JSON 无效。"}, status_code=400, headers=CLIENT_STATE_ROUTE_HEADERS)
        try:
            def save_state() -> dict[str, Any]:
                incoming = payload.get("state") if isinstance(payload, dict) else None
                if not isinstance(incoming, dict):
                    return {"__status": 400, "payload": {"error": "state 必须是对象。"}}

                with CLIENT_STATE_LOCK:
                    current = state_store.read_client_state()
                    skipped: list[str] = []
                    allow_empty = payload.get("allowEmptyProtected") is True
                    should_skip = legacy_callable("should_skip_empty_client_state_update")
                    merge_value = legacy_callable("merge_client_state_value")
                    replace_requested = legacy_callable("client_state_replace_requested")
                    for key, value in incoming.items():
                        if not isinstance(key, str) or not key.startswith("meiao-"):
                            continue
                        if should_skip(key, value, current.get(key), allow_empty=allow_empty):
                            skipped.append(key)
                            continue
                        current[key] = merge_value(key, value, current.get(key), replace_requested(payload, key))

                    state_store.write_client_state(current)
                    state_store.sync_client_state_sidecars(current)
                    if isinstance(current.get("meiao-ingest-items"), list):
                        clear_media_storage_cache()
                    clear_client_state_recover_cache()
                    return {"__status": 200, "payload": {"ok": True, "keys": sorted(current.keys()), "skippedEmpty": skipped}}

            result = await run_in_threadpool(save_state)
            return json_response(result["payload"], status_code=result["__status"], headers=CLIENT_STATE_ROUTE_HEADERS)
        except Exception as error:
            log_legacy_error("api.client_state.save.error", error)
            return json_response({"error": "保存工作台数据失败。"}, status_code=500, headers=CLIENT_STATE_ROUTE_HEADERS)

    @app.post("/api/client-state/clean")
    async def client_state_clean(request: Request) -> Response:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        try:
            def clean_state() -> Any:
                with CLIENT_STATE_LOCK:
                    result = state_store.run_exclusive(
                        lambda: legacy_callable("clean_client_state")(apply=bool(payload.get("apply")) if isinstance(payload, dict) else False)
                    )
                    append_debug_log = legacy_globals.get("append_debug_log")
                    if callable(append_debug_log):
                        append_debug_log(
                            "api.client_state.clean",
                            {"applied": result.get("applied"), "changes": result.get("changes")} if isinstance(result, dict) else {},
                        )
                    if isinstance(payload, dict) and payload.get("apply"):
                        clear_client_state_recover_cache()
                    return result

            result = await run_in_threadpool(clean_state)
            return json_response(result, headers=CLIENT_STATE_ROUTE_HEADERS)
        except Exception as error:
            log_legacy_error("api.client_state.clean.error", error)
            return json_response({"error": "清理本地工作台数据失败。"}, status_code=500, headers=CLIENT_STATE_ROUTE_HEADERS)

    @app.get("/api/media-library/items")
    async def media_library_list() -> Response:
        try:
            def load_items() -> dict[str, Any]:
                with MEDIA_STORAGE_LOCK:
                    cached_payload = MEDIA_LIBRARY_CACHE.get("payload")
                    if isinstance(cached_payload, dict) and time.time() < float(MEDIA_LIBRARY_CACHE.get("expires_at") or 0):
                        return cached_payload
                    saved_items = state_store.read_sidecar("media-library")
                    disk_items = legacy_callable("recover_media_library_items_from_disk")()
                    items = legacy_callable("merge_media_library_items")(saved_items, disk_items)
                    if items != saved_items:
                        state_store.write_sidecar("media-library", items)
                        state_store.sync_client_state_value("meiao-ingest-items", items)
                        clear_client_state_recover_cache()
                    payload = {"items": items, "saved": len(saved_items), "recovered": len(items) - len(saved_items)}
                    MEDIA_LIBRARY_CACHE["payload"] = payload
                    MEDIA_LIBRARY_CACHE["expires_at"] = time.time() + 5
                    return payload

            payload = await run_in_threadpool(load_items)
            return json_response(payload, headers=MEDIA_STORAGE_ROUTE_HEADERS)
        except Exception as error:
            log_legacy_error("api.media_library.list.error", error)
            return json_response({"error": "read media library failed"}, status_code=500, headers=MEDIA_STORAGE_ROUTE_HEADERS)

    @app.post("/api/media-library/items")
    async def media_library_save(request: Request) -> Response:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        try:
            def save_items() -> dict[str, Any]:
                raw_items = payload.get("items") if isinstance(payload, dict) else None
                if not isinstance(raw_items, list):
                    return {"__status": 400, "payload": {"error": "items must be an array"}}
                with MEDIA_STORAGE_LOCK:
                    items = [item for item in raw_items if isinstance(item, dict) and str(item.get("id") or "").strip()]
                    current = state_store.read_sidecar("media-library")
                    if not items and current and payload.get("allowEmptyProtected") is not True:
                        return {"__status": 200, "payload": {"ok": True, "count": len(current), "skipped": True}}
                    if payload.get("replace") is not True:
                        items = legacy_callable("merge_media_library_items")(items, current)
                    state_store.write_sidecar("media-library", items)
                    state_store.sync_client_state_value("meiao-ingest-items", items, replace=payload.get("replace") is True)
                    clear_client_state_recover_cache()
                    clear_media_storage_cache()
                    return {"__status": 200, "payload": {"ok": True, "count": len(items)}}

            result = await run_in_threadpool(save_items)
            return json_response(result["payload"], status_code=result["__status"], headers=MEDIA_STORAGE_ROUTE_HEADERS)
        except Exception as error:
            log_legacy_error("api.media_library.save.error", error)
            return json_response({"error": "save media library failed"}, status_code=500, headers=MEDIA_STORAGE_ROUTE_HEADERS)

    @app.get("/api/draft-templates")
    async def draft_templates_list() -> Response:
        try:
            payload = await run_in_threadpool(lambda: {"templates": state_store.read_sidecar("draft-templates")})
            return json_response(payload, headers=MEDIA_STORAGE_ROUTE_HEADERS)
        except Exception as error:
            log_legacy_error("api.draft_templates.list.error", error)
            return json_response({"error": "read draft templates failed"}, status_code=500, headers=MEDIA_STORAGE_ROUTE_HEADERS)

    @app.post("/api/draft-templates")
    async def draft_templates_save(request: Request) -> Response:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        try:
            def save_templates() -> dict[str, Any]:
                raw_templates = payload.get("templates") if isinstance(payload, dict) else None
                if not isinstance(raw_templates, list):
                    return {"__status": 400, "payload": {"error": "templates must be an array"}}
                with MEDIA_STORAGE_LOCK:
                    current = state_store.read_sidecar("draft-templates")
                    incoming = [item for item in raw_templates if isinstance(item, dict) and str(item.get("id") or "").strip()]
                    if not incoming and current and payload.get("allowEmptyProtected") is not True:
                        return {"__status": 200, "payload": {"ok": True, "count": len(current), "templates": current, "skipped": True}}
                    exact_replace = payload.get("replace") is True and payload.get("destructive") is True
                    templates = legacy_callable("merge_draft_templates")(incoming, current if not exact_replace else [])
                    state_store.write_sidecar("draft-templates", templates)
                    state_store.sync_client_state_value("meiao-draft-templates", templates, replace=exact_replace)
                    clear_client_state_recover_cache()
                    return {"__status": 200, "payload": {"ok": True, "count": len(templates), "templates": templates}}

            result = await run_in_threadpool(save_templates)
            return json_response(result["payload"], status_code=result["__status"], headers=MEDIA_STORAGE_ROUTE_HEADERS)
        except Exception as error:
            log_legacy_error("api.draft_templates.save.error", error)
            return json_response({"error": "save draft templates failed"}, status_code=500, headers=MEDIA_STORAGE_ROUTE_HEADERS)

    @app.get("/api/batch-draft-projects")
    async def batch_draft_projects_list() -> Response:
        try:
            def load_projects() -> dict[str, Any]:
                with MEDIA_STORAGE_LOCK:
                    state = state_store.read_client_state()
                    state_projects = state.get("meiao-batch-draft-projects")
                    projects = legacy_callable("normalize_batch_draft_projects")([
                        *state_store.read_sidecar("batch-draft-projects"),
                        *(state_projects if isinstance(state_projects, list) else []),
                    ])
                    if projects:
                        state_store.write_sidecar("batch-draft-projects", projects)
                        state_store.sync_client_state_value("meiao-batch-draft-projects", projects)
                        clear_client_state_recover_cache()
                    return {"projects": projects}

            payload = await run_in_threadpool(load_projects)
            return json_response(payload, headers=MEDIA_STORAGE_ROUTE_HEADERS)
        except Exception as error:
            log_legacy_error("api.batch_draft_projects.list.error", error)
            return json_response({"error": "read batch draft projects failed"}, status_code=500, headers=MEDIA_STORAGE_ROUTE_HEADERS)

    @app.post("/api/batch-draft-projects")
    async def batch_draft_projects_save(request: Request) -> Response:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        try:
            def save_projects() -> dict[str, Any]:
                raw_projects = payload.get("projects") if isinstance(payload, dict) else None
                if not isinstance(raw_projects, list):
                    return {"__status": 400, "payload": {"error": "projects must be an array"}}
                with MEDIA_STORAGE_LOCK:
                    current = state_store.read_sidecar("batch-draft-projects")
                    incoming = [item for item in raw_projects if isinstance(item, dict) and str(item.get("id") or "").strip()]
                    if not incoming and current and payload.get("allowEmptyProtected") is not True:
                        return {"__status": 200, "payload": {"ok": True, "count": len(current), "projects": current, "skipped": True}}
                    exact_replace = payload.get("replace") is True and payload.get("destructive") is True
                    merge_projects = legacy_callable("merge_batch_draft_projects_for_persistence")
                    projects = merge_projects(incoming, current, replace=exact_replace)
                    state_store.write_sidecar("batch-draft-projects", projects)
                    state_store.sync_client_state_value("meiao-batch-draft-projects", projects, replace=exact_replace)
                    clear_client_state_recover_cache()
                    return {"__status": 200, "payload": {"ok": True, "count": len(projects), "projects": projects}}

            result = await run_in_threadpool(save_projects)
            return json_response(result["payload"], status_code=result["__status"], headers=MEDIA_STORAGE_ROUTE_HEADERS)
        except Exception as error:
            log_legacy_error("api.batch_draft_projects.save.error", error)
            return json_response({"error": "save batch draft projects failed"}, status_code=500, headers=MEDIA_STORAGE_ROUTE_HEADERS)

    @app.get("/api/license/status")
    @app.post("/api/license/status")
    async def license_status(request: Request) -> Response:
        try:
            force_online = request.query_params.get("force") == "1"
            payload = await run_in_threadpool(legacy_callable("get_license_status"), force_online)
            return json_response(payload, headers=LICENSE_ROUTE_HEADERS)
        except Exception as error:
            log_legacy_error("api.license.status.error", error)
            return json_response({"error": f"read license status failed: {error}"}, status_code=500, headers=LICENSE_ROUTE_HEADERS)

    @app.post("/api/license/activate")
    async def license_activate(request: Request) -> Response:
        try:
            payload = await json_payload(request)
            status_code, result = await run_in_threadpool(license_runtime.activate, legacy_globals, payload)
            return json_response(result, status_code=status_code, headers=LICENSE_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.license.activate.error", error, LICENSE_ROUTE_HEADERS)

    @app.post("/api/license/rebind")
    async def license_rebind(request: Request) -> Response:
        try:
            payload = await json_payload(request)
            status_code, result = await run_in_threadpool(license_runtime.rebind, legacy_globals, payload)
            return json_response(result, status_code=status_code, headers=LICENSE_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.license.rebind.error", error, LICENSE_ROUTE_HEADERS)

    @app.post("/api/license/verify")
    async def license_verify() -> Response:
        try:
            payload = await run_in_threadpool(license_runtime.verify, legacy_globals)
            return json_response(payload, headers=LICENSE_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.license.verify.error", error, LICENSE_ROUTE_HEADERS)

    @app.post("/api/license/logout")
    async def license_logout() -> Response:
        try:
            payload = await run_in_threadpool(license_runtime.logout, legacy_globals)
            return json_response(payload, headers=LICENSE_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.license.logout.error", error, LICENSE_ROUTE_HEADERS)

    @app.get("/api/global-settings")
    async def global_settings_get() -> Response:
        try:
            payload = await run_in_threadpool(global_settings.get_bundle, legacy_globals)
            return json_response(payload, headers=GLOBAL_SETTINGS_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.global_settings.get.error", error, GLOBAL_SETTINGS_ROUTE_HEADERS)

    @app.post("/api/global-settings")
    async def global_settings_save(request: Request) -> Response:
        try:
            payload = await json_payload(request)
            status_code, result = await run_in_threadpool(global_settings.save_bundle, legacy_globals, payload)
            return json_response(result, status_code=status_code, headers=GLOBAL_SETTINGS_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.global_settings.save.error", error, GLOBAL_SETTINGS_ROUTE_HEADERS)

    @app.post("/api/global-settings/backup")
    async def global_settings_backup() -> Response:
        try:
            payload = await run_in_threadpool(global_settings.backup, legacy_globals)
            return json_response(payload, headers=GLOBAL_SETTINGS_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.global_settings.backup.error", error, GLOBAL_SETTINGS_ROUTE_HEADERS)

    @app.post("/api/global-settings/restore")
    async def global_settings_restore(request: Request) -> Response:
        try:
            payload = await json_payload(request)
            status_code, result = await run_in_threadpool(global_settings.restore, legacy_globals, payload)
            return json_response(result, status_code=status_code, headers=GLOBAL_SETTINGS_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.global_settings.restore.error", error, GLOBAL_SETTINGS_ROUTE_HEADERS)

    @app.post("/api/system/check-export-folder")
    async def system_check_export_folder(request: Request) -> Response:
        try:
            payload = await json_payload(request)
            status_code, result = await run_in_threadpool(global_settings.check_export_folder, legacy_globals, state_store, payload)
            return json_response(result, status_code=status_code, headers=GLOBAL_SETTINGS_ROUTE_HEADERS)
        except Exception as error:
            return runtime_error_response("api.system.check_export_folder.error", error, GLOBAL_SETTINGS_ROUTE_HEADERS)

    @app.get("/api/bgm-library")
    async def bgm_library_list() -> Response:
        try:
            def load_tracks() -> dict[str, Any]:
                with BGM_LIBRARY_LOCK:
                    normalize_bgm_item = legacy_callable("normalize_bgm_item")
                    items = [item for item in (normalize_bgm_item(raw) for raw in legacy_callable("read_bgm_library")()) if item]
                    legacy_callable("write_bgm_library")(items)
                    return {"items": sorted(items, key=lambda item: int(item.get("updatedAt") or 0), reverse=True)}

            payload = await run_in_threadpool(load_tracks)
            return json_response(payload, headers=BGM_LIBRARY_ROUTE_HEADERS)
        except Exception as error:
            log_legacy_error("api.bgm_library.list.error", error)
            return json_response({"error": f"read BGM library failed: {error}"}, status_code=500, headers=BGM_LIBRARY_ROUTE_HEADERS)

    @app.post("/api/bgm-library/upload")
    async def bgm_library_upload(
        file: UploadFile | None = File(default=None),
        name: str = Form(default=""),
        category: str = Form(default=""),
    ) -> Response:
        try:
            def save_track() -> dict[str, Any]:
                if file is None or not str(file.filename or "").strip():
                    return {"__status": 400, "payload": {"error": "请选择 BGM 音频文件。"}}
                original_name = Path(str(file.filename)).name
                suffix = Path(original_name).suffix.lower()
                allowed_suffixes = legacy_globals.get("AUDIO_FILE_SUFFIXES") or {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"}
                if suffix not in allowed_suffixes:
                    return {"__status": 400, "payload": {"error": "BGM 仅支持 mp3/m4a/aac/wav/flac/ogg。"}}
                bgm_root = legacy_globals.get("BGM_ROOT")
                if bgm_root is None:
                    raise RuntimeError("BGM root is unavailable.")

                with BGM_LIBRARY_LOCK:
                    track_id = f"BGM-{uuid.uuid4().hex[:12]}"
                    file_name = f"{track_id}{suffix}"
                    target = bgm_root / file_name
                    bgm_root.mkdir(parents=True, exist_ok=True)
                    file.file.seek(0)
                    with target.open("wb") as output:
                        shutil.copyfileobj(file.file, output)
                    now = int(time.time() * 1000)
                    item = {
                        "id": track_id,
                        "name": str(name or Path(original_name).stem).strip(),
                        "category": str(category or "").strip(),
                        "fileName": file_name,
                        "originalName": original_name,
                        "mimeType": file.content_type or mimetypes.guess_type(original_name)[0] or "audio/mpeg",
                        "size": target.stat().st_size,
                        "createdAt": now,
                        "updatedAt": now,
                    }
                    items = [item, *[raw for raw in legacy_callable("read_bgm_library")() if str(raw.get("id") or "") != track_id]]
                    legacy_callable("write_bgm_library")(items)
                    return {"__status": 200, "payload": {"item": legacy_callable("normalize_bgm_item")(item)}}

            result = await run_in_threadpool(save_track)
            return json_response(result["payload"], status_code=result["__status"], headers=BGM_LIBRARY_ROUTE_HEADERS)
        except Exception as error:
            log_legacy_error("api.bgm_library.upload.error", error)
            return json_response({"error": f"BGM 上传失败：{error}"}, status_code=500, headers=BGM_LIBRARY_ROUTE_HEADERS)

    @app.post("/api/bgm-library/update")
    async def bgm_library_update(request: Request) -> Response:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        try:
            def update_track() -> dict[str, Any]:
                track_id = str(payload.get("id") or "").strip() if isinstance(payload, dict) else ""
                with BGM_LIBRARY_LOCK:
                    items = legacy_callable("read_bgm_library")()
                    now = int(time.time() * 1000)
                    updated = None
                    next_items = []
                    for item in items:
                        if str(item.get("id") or "") == track_id:
                            item = {
                                **item,
                                "name": str(payload.get("name") or item.get("name") or item.get("fileName") or "").strip(),
                                "category": str(payload.get("category") if payload.get("category") is not None else item.get("category") or "").strip(),
                                "updatedAt": now,
                            }
                            updated = item
                        next_items.append(item)
                    if not updated:
                        return {"__status": 404, "payload": {"error": "BGM does not exist."}}
                    legacy_callable("write_bgm_library")(next_items)
                    return {"__status": 200, "payload": {"item": legacy_callable("normalize_bgm_item")(updated)}}

            result = await run_in_threadpool(update_track)
            return json_response(result["payload"], status_code=result["__status"], headers=BGM_LIBRARY_ROUTE_HEADERS)
        except Exception as error:
            log_legacy_error("api.bgm_library.update.error", error)
            return json_response({"error": f"update BGM failed: {error}"}, status_code=500, headers=BGM_LIBRARY_ROUTE_HEADERS)

    @app.delete("/api/bgm-library/{raw_track_id:path}")
    async def bgm_library_delete(raw_track_id: str) -> Response:
        try:
            def delete_track() -> dict[str, Any]:
                track_id = unquote(raw_track_id).strip()
                removed = None
                next_items = []
                with BGM_LIBRARY_LOCK:
                    for item in legacy_callable("read_bgm_library")():
                        if str(item.get("id") or "") == track_id:
                            removed = item
                        else:
                            next_items.append(item)
                    if not removed:
                        return {"__status": 404, "payload": {"error": "BGM does not exist."}}
                    bgm_root = legacy_globals.get("BGM_ROOT")
                    if bgm_root is not None:
                        target = bgm_root / str(removed.get("fileName") or "")
                        if target.exists() and target.parent == bgm_root:
                            target.unlink()
                    legacy_callable("write_bgm_library")(next_items)
                    return {"__status": 200, "payload": {"deleted": True}}

            result = await run_in_threadpool(delete_track)
            return json_response(result["payload"], status_code=result["__status"], headers=BGM_LIBRARY_ROUTE_HEADERS)
        except Exception as error:
            log_legacy_error("api.bgm_library.delete.error", error)
            return json_response({"error": f"delete BGM failed: {error}"}, status_code=500, headers=BGM_LIBRARY_ROUTE_HEADERS)

    @app.post("/api/media/extract-frame")
    async def media_extract_frame(request: Request) -> Response:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        try:
            def extract_frame() -> Any:
                result = legacy_callable("extract_frame_from_media_url")(
                    str(payload.get("url") or payload.get("mediaUrl") or ""),
                    float(payload.get("offsetSeconds") or 0.05),
                )
                append_debug_log = legacy_globals.get("append_debug_log")
                if callable(append_debug_log) and isinstance(result, dict):
                    append_debug_log("api.media.extract_frame", {"sourceUrl": result.get("sourceUrl"), "mediaId": result.get("mediaId")})
                return result

            result = await run_in_threadpool(extract_frame)
            return json_response(result, headers=MEDIA_OPERATION_ROUTE_HEADERS)
        except Exception as error:
            ingest_error = legacy_globals.get("IngestError")
            if isinstance(ingest_error, type) and isinstance(error, ingest_error):
                log_legacy_error("api.media.extract_frame.error", error)
                return json_response(
                    {"error": str(error), "code": getattr(error, "code", ""), "action": getattr(error, "action", "")},
                    status_code=409,
                    headers=MEDIA_OPERATION_ROUTE_HEADERS,
                )
            log_legacy_error("api.media.extract_frame.error", error)
            return json_response({"error": str(error)}, status_code=500, headers=MEDIA_OPERATION_ROUTE_HEADERS)

    @app.post("/api/media/scene-segments/delete")
    async def media_scene_segments_delete(request: Request) -> Response:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        try:
            def delete_segments() -> dict[str, Any]:
                media_id = str(payload.get("mediaId") or "").strip() if isinstance(payload, dict) else ""
                segments = payload.get("segments") if isinstance(payload, dict) else None
                if not media_id:
                    return {"__status": 400, "payload": {"error": "mediaId is required."}}
                if not isinstance(segments, list):
                    return {"__status": 400, "payload": {"error": "segments must be an array."}}
                result = legacy_callable("delete_scene_segment_files")(media_id, segments)
                append_debug_log = legacy_globals.get("append_debug_log")
                if callable(append_debug_log):
                    append_debug_log("api.media.scene_segments.delete", {"mediaId": media_id, **result})
                return {"__status": 200, "payload": {"ok": True, **result}}

            result = await run_in_threadpool(delete_segments)
            return json_response(result["payload"], status_code=result["__status"], headers=MEDIA_OPERATION_ROUTE_HEADERS)
        except Exception as error:
            log_legacy_error("api.media.scene_segments.delete.error", error)
            return json_response({"error": f"delete scene segment files failed: {error}"}, status_code=500, headers=MEDIA_OPERATION_ROUTE_HEADERS)

    @app.post("/api/product-projects/delete")
    async def product_project_delete(request: Request) -> Response:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        try:
            def delete_project() -> dict[str, Any]:
                project_id = str(payload.get("projectId") or "").strip() if isinstance(payload, dict) else ""
                confirm_text = str(payload.get("confirmText") or "").strip() if isinstance(payload, dict) else ""
                if not project_id:
                    return {"__status": 400, "payload": {"error": "projectId is required."}}
                with LEGACY_DISPATCH_LOCK, CLIENT_STATE_LOCK, MEDIA_STORAGE_LOCK:
                    state = state_store.run_exclusive(legacy_callable("build_recovered_client_state"))
                    projects = state.get("meiao-product-projects") if isinstance(state.get("meiao-product-projects"), list) else []
                    project = next(
                        (
                            item
                            for item in projects
                            if isinstance(item, dict) and str(item.get("id") or "").strip() == project_id
                        ),
                        None,
                    )
                    project_name = str(project.get("name") or project_id).strip() if isinstance(project, dict) else project_id
                    expected_texts = {project_id, project_name, f"删除{project_name}"}
                    if confirm_text not in expected_texts:
                        return {
                            "__status": 409,
                            "payload": {
                                "error": f"二次确认失败：请输入项目名称“{project_name}”或项目 ID“{project_id}”。",
                                "projectName": project_name,
                            },
                        }
                    result = state_store.run_exclusive(lambda: legacy_callable("delete_product_project_cascade")(project_id))
                    clear_client_state_recover_cache()
                    clear_media_storage_cache()
                    return {"__status": 200, "payload": {**result, "projectName": project_name}}

            result = await run_in_threadpool(delete_project)
            return json_response(result["payload"], status_code=result["__status"], headers=PRODUCT_PROJECT_ROUTE_HEADERS)
        except Exception as error:
            log_legacy_error("api.product_projects.delete.error", error)
            return json_response({"error": f"delete project failed: {error}"}, status_code=500, headers=PRODUCT_PROJECT_ROUTE_HEADERS)

    @app.get("/api/runtime/tasks")
    async def runtime_tasks_list() -> Response:
        payload = await run_in_threadpool(runtime_tasks.list_tasks)
        return json_response({"tasks": payload}, headers=TASK_RUNTIME_ROUTE_HEADERS)

    @app.post("/api/runtime/tasks")
    async def runtime_tasks_create(request: Request) -> Response:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        task_type = payload.get("type") if isinstance(payload, dict) else None
        task_payload = payload.get("payload") if isinstance(payload, dict) else None
        if not isinstance(task_type, str) or not task_type.strip():
            return json_response({"error": "Missing task type"}, status_code=400, headers=TASK_RUNTIME_ROUTE_HEADERS)
        try:
            task = await run_in_threadpool(runtime_tasks.create_task, task_type.strip(), task_payload)
        except ValueError as error:
            return json_response({"error": str(error)}, status_code=400, headers=TASK_RUNTIME_ROUTE_HEADERS)
        return json_response({"task": task}, status_code=202, headers=TASK_RUNTIME_ROUTE_HEADERS)

    @app.get("/api/runtime/tasks/{task_id}")
    async def runtime_tasks_get(task_id: str) -> Response:
        task = await run_in_threadpool(runtime_tasks.get_task, task_id)
        if task is None:
            return json_response({"error": "Task not found"}, status_code=404, headers=TASK_RUNTIME_ROUTE_HEADERS)
        return json_response({"task": task}, headers=TASK_RUNTIME_ROUTE_HEADERS)

    @app.post("/api/runtime/tasks/{task_id}/cancel")
    async def runtime_tasks_cancel(task_id: str) -> Response:
        task = await run_in_threadpool(runtime_tasks.cancel_task, task_id)
        if task is None:
            return json_response({"error": "Task not found"}, status_code=404, headers=TASK_RUNTIME_ROUTE_HEADERS)
        return json_response({"task": task}, headers=TASK_RUNTIME_ROUTE_HEADERS)

    async def native_legacy_proxy(request: Request, route_headers: dict[str, str]) -> Response:
        body = await request.body()
        client = request.client
        legacy_path = request.url.path
        if request.url.query:
            legacy_path = f"{legacy_path}?{request.url.query}"
        result = await run_in_threadpool(
            dispatch_legacy,
            legacy_handler_cls,
            request.method,
            legacy_path,
            dict(request.headers),
            body,
            client.host if client else "127.0.0.1",
            client.port if client else 0,
        )
        response_headers = {
            key: value
            for key, value in result.headers.items()
            if key.lower() not in {"content-length", "connection", "server", "date"}
        }
        response_headers.update(route_headers)
        return Response(
            result.body,
            status_code=result.status_code,
            headers=response_headers,
            media_type=result.headers.get("content-type") or None,
        )

    m1_routes.register(app, legacy_globals, runtime_error_response, log_legacy_error, native_legacy_proxy)

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "DELETE", "OPTIONS", "HEAD"])
    async def legacy_compat(full_path: str, request: Request) -> Response:
        if legacy_handler_cls is None:
            return Response(b'{"error":"Runtime handler is not configured"}', status_code=500, media_type="application/json")
        body = await request.body()
        path = "/" + full_path
        if request.url.query:
            path = f"{path}?{request.url.query}"
        client = request.client
        result = await run_in_threadpool(
            dispatch_legacy,
            legacy_handler_cls,
            request.method,
            path,
            dict(request.headers),
            body,
            client.host if client else "127.0.0.1",
            client.port if client else 0,
        )
        result = normalize_json_response(result)
        response_body = b"" if request.method.upper() == "HEAD" else result.body
        return Response(content=response_body, status_code=result.status_code, headers=result.headers)

    return app
