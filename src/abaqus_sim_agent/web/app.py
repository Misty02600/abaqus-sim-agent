from __future__ import annotations

import asyncio
import json
import mimetypes
import secrets
from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import SecretStr
from starlette.background import BackgroundTask

from ..coding import model_catalog
from ..coding.composition import ModelFactory, RunnerFactory
from ..coding.credential_store import CredentialStoreError
from ..coding.domain_knowledge import DomainKnowledgeInventory
from ..coding.model import DeepSeekGateway
from ..coding.model_catalog import ModelCatalogUnavailable
from ..coding.model_connections import create_model as create_deepseek_chat_model
from ..coding.model_selection import AvailableModel
from ..coding.providers import (
    ProviderOption,
    ProviderUnavailable,
    provider_catalog,
    require_provider_dependencies,
)
from ..coding.run_lock import CodingRunAlreadyLocked
from ..execution import Abaqus2024Runner
from .artifacts import snapshot_file
from .contracts import (
    AnswerCommand,
    ApiError,
    ArtifactPage,
    CommandResponse,
    CreateRun,
    DeleteRunResponse,
    DirectoryBrowse,
    MessageCommand,
    ModelConnection,
    RenameCommand,
    RunCommand,
    RunList,
    RunSnapshot,
    RuntimePolicyCommand,
    SessionInfo,
    StopCommand,
    TimelinePage,
    ToolExecution,
    ToolExecutionCancelCommand,
    ToolExecutionList,
    WebError,
    WorkspaceDirectory,
    WorkspaceSearch,
)
from .controller import RunController, ServerConfig
from .directories import browse_directories
from .events import ViewEvents
from .model_login import LoginRequest, LoginStatus, ModelLogins
from .workspace import list_workspace, search_workspace, workspace_preview


def create_app(
    config: ServerConfig,
    *,
    api_keys: Mapping[DeepSeekGateway, SecretStr] | None = None,
    model_factory: ModelFactory = create_deepseek_chat_model,
    runner_factory: RunnerFactory = Abaqus2024Runner,
) -> FastAPI:
    controller = RunController(
        config,
        api_keys or {},
        model_factory=model_factory,
        runner_factory=runner_factory,
    )
    events = ViewEvents()
    logins = ModelLogins(controller)
    session = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        controller.start()
        events.loop = asyncio.get_running_loop()
        controller.changed = events.notify
        try:
            yield
        finally:
            await logins.close()
            await asyncio.to_thread(controller.close)

    app = FastAPI(
        title="Abaqus Sim Agent",
        version="1",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.controller = controller
    app.state.events = events

    def error(code: str, message: str, status: int) -> JSONResponse:
        return JSONResponse(
            ApiError(code=code, message=message).model_dump(), status_code=status
        )

    def cookie_name(request: Request) -> str:
        return f"abaqus_session_{request.url.port or 80}"

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        try:
            host = urlsplit("//" + request.headers.get("host", "")).hostname
        except ValueError:
            host = None
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return error("host_denied", "只接受本机访问", 421)
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.headers.get('host')}"
        if origin is not None and origin.rstrip("/") != expected_origin:
            return error("origin_denied", "不允许跨来源请求", 403)
        path = request.url.path
        if path.startswith("/api/"):
            is_bootstrap = path == "/api/v1/session" and request.method == "GET"
            if not is_bootstrap and not secrets.compare_digest(
                request.cookies.get(cookie_name(request), "").encode(), session.encode()
            ):
                return error(
                    "unauthorized", "本地浏览器会话尚未初始化，请刷新页面", 401
                )
            if request.method not in {"GET", "HEAD"}:
                if (
                    request.headers.get("content-type", "").split(";")[0].lower()
                    != "application/json"
                ):
                    return error("json_required", "请求必须为JSON", 415)
                if not secrets.compare_digest(
                    request.headers.get("x-csrf-token", "").encode(), csrf.encode()
                ):
                    return error("csrf_denied", "请求校验失败，请刷新页面", 403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'"
        )
        return response

    @app.exception_handler(WebError)
    async def web_error(request: Request, exc: WebError):
        return error(exc.code, exc.message, exc.status)

    @app.exception_handler(ProviderUnavailable)
    async def unavailable_provider(request: Request, exc: ProviderUnavailable):
        return error("provider_unavailable", str(exc), 422)

    @app.exception_handler(CredentialStoreError)
    async def credential_store_error(request: Request, exc: CredentialStoreError):
        return error("credential_store_unavailable", str(exc), 503)

    @app.exception_handler(ModelCatalogUnavailable)
    async def model_catalog_error(request: Request, exc: ModelCatalogUnavailable):
        return error("model_catalog_unavailable", str(exc), 503)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, exc: RequestValidationError):
        return error("invalid_input", "请求字段、格式或取值不正确", 422)

    @app.exception_handler(ValueError)
    @app.exception_handler(OSError)
    @app.exception_handler(CodingRunAlreadyLocked)
    async def corrupt_state(request: Request, exc: Exception):
        return error("state_unavailable", "本地记录不可用，请检查宿主日志", 409)

    @app.get("/api/v1/session", response_model=SessionInfo)
    def get_session(request: Request):
        response = JSONResponse(SessionInfo(csrf_token=csrf).model_dump())
        response.set_cookie(
            cookie_name(request), session, httponly=True, samesite="strict", path="/api"
        )
        return response

    @app.post("/api/v1/runs", response_model=RunSnapshot, status_code=201)
    def create_run(body: CreateRun):
        return controller.create(body)

    @app.get("/api/v1/runs", response_model=RunList)
    def list_runs():
        return controller.list_runs()

    @app.get("/api/v1/directories", response_model=DirectoryBrowse)
    def directories(
        path: str | None = Query(default=None, min_length=1, max_length=4096),
    ):
        return browse_directories(controller.registry.root, path)

    @app.get("/api/v1/knowledge-packs", response_model=DomainKnowledgeInventory)
    def list_knowledge_packs():
        return controller.list_knowledge_packs()

    @app.get("/api/v1/model-providers", response_model=tuple[ProviderOption, ...])
    def model_providers():
        return provider_catalog(include_models=False)

    @app.get("/api/v1/model-provider-dependencies")
    def model_provider_dependencies(provider: DeepSeekGateway):
        require_provider_dependencies(provider)
        return {"ready": True}

    @app.get("/api/v1/models", response_model=tuple[AvailableModel, ...])
    def models(refresh: bool = False):
        if refresh:
            model_catalog.catalog_store().get(refresh=True)
        return controller.available_models()

    @app.post("/api/v1/model-connections")
    def connect_model(body: ModelConnection):
        require_provider_dependencies(body.provider)
        try:
            controller.configure_model_key(body.provider, body.api_key)
        except CredentialStoreError:
            raise
        except ValueError as exc:
            raise WebError("invalid_connection", str(exc), 422) from exc
        return {"configured": True}

    @app.get("/api/v1/model-connections", response_model=tuple[DeepSeekGateway, ...])
    def model_connections():
        return tuple(
            sorted(
                controller.configured_providers(), key=lambda provider: provider.value
            )
        )

    @app.delete("/api/v1/model-connections/{provider:path}")
    def disconnect_model(provider: DeepSeekGateway):
        controller.credential_store.remove(provider)
        return {"removed": True}

    @app.post("/api/v1/model-logins", response_model=LoginStatus)
    async def begin_model_login(body: LoginRequest):
        try:
            return await logins.begin(body)
        except ValueError as exc:
            raise WebError("login_unavailable", str(exc), 422) from exc
        except Exception as exc:
            raise WebError("login_failed", "无法发起登录，请稍后重试。", 502) from exc

    @app.get("/api/v1/model-logins/{login_id}", response_model=LoginStatus)
    def model_login_status(login_id: str):
        status = logins.statuses.get(login_id)
        if status is None:
            raise WebError("login_not_found", "登录记录不存在。", 404)
        return status

    @app.delete("/api/v1/model-logins/{login_id}")
    async def cancel_model_login(login_id: str):
        await logins.cancel(login_id)
        return {"cancelled": True}

    @app.get("/api/v1/runs/{run_id}", response_model=RunSnapshot)
    def get_run(run_id: str):
        return controller.snapshot(run_id)

    @app.delete("/api/v1/runs/{run_id}", response_model=DeleteRunResponse)
    def delete_run(run_id: str, body: RunCommand):
        return controller.delete(run_id, body)

    @app.post("/api/v1/runs/{run_id}/runtime-policy", response_model=RunSnapshot)
    def update_runtime_policy(run_id: str, body: RuntimePolicyCommand):
        return controller.update_runtime_policy(run_id, body)

    @app.get(
        "/api/v1/runs/{run_id}/tool-executions",
        response_model=ToolExecutionList,
    )
    def tool_executions(run_id: str):
        return controller.list_tool_executions(run_id)

    @app.get(
        "/api/v1/runs/{run_id}/tool-executions/{execution_id}",
        response_model=ToolExecution,
    )
    def tool_execution(run_id: str, execution_id: str):
        return controller.get_tool_execution(run_id, execution_id)

    @app.post(
        "/api/v1/runs/{run_id}/tool-executions/{execution_id}/cancel",
        response_model=ToolExecution,
        status_code=202,
    )
    def cancel_tool_execution(
        run_id: str,
        execution_id: str,
        body: ToolExecutionCancelCommand,
    ):
        return controller.cancel_tool_execution(run_id, execution_id, body)

    def page_snapshot(run_id: str, version: str | None) -> RunSnapshot:
        snapshot = controller.snapshot(run_id)
        if version is not None and snapshot.version != version:
            raise WebError("view_changed", "任务视图已改变，请从第一页重新读取")
        return snapshot

    @app.get("/api/v1/runs/{run_id}/timeline", response_model=TimelinePage)
    def timeline(
        run_id: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=200),
        version: str | None = None,
    ):
        snapshot = page_snapshot(run_id, version)
        items = snapshot.view.messages
        end = min(offset + limit, len(items))
        return TimelinePage(
            run_id=run_id,
            version=snapshot.version,
            task_revision=snapshot.view.task_revision,
            offset=offset,
            total=len(items),
            next_offset=end if end < len(items) else None,
            items=items[offset:end],
        )

    @app.get("/api/v1/runs/{run_id}/artifacts", response_model=ArtifactPage)
    def artifact_list(
        run_id: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=200),
        version: str | None = None,
    ):
        snapshot = page_snapshot(run_id, version)
        items = snapshot.view.files
        end = min(offset + limit, len(items))
        return ArtifactPage(
            run_id=run_id,
            version=snapshot.version,
            task_revision=snapshot.view.task_revision,
            offset=offset,
            total=len(items),
            next_offset=end if end < len(items) else None,
            items=items[offset:end],
        )

    @app.get("/api/v1/runs/{run_id}/workspace", response_model=WorkspaceDirectory)
    def workspace_directory(run_id: str, path: str = ""):
        return list_workspace(controller, run_id, path)

    @app.get("/api/v1/runs/{run_id}/workspace/search", response_model=WorkspaceSearch)
    def workspace_search(
        run_id: str,
        q: str = Query(min_length=1, max_length=200),
        limit: int = Query(50, ge=1, le=100),
    ):
        return search_workspace(controller, run_id, q, limit)

    @app.get("/api/v1/runs/{run_id}/workspace/file")
    def workspace_file(run_id: str, path: str):
        content, media_type = workspace_preview(controller, run_id, path)
        if isinstance(content, Path):
            return FileResponse(content, media_type=media_type)
        return Response(content=content, media_type=media_type)

    @app.post(
        "/api/v1/runs/{run_id}/start", response_model=CommandResponse, status_code=202
    )
    def start_run(run_id: str, body: RunCommand):
        return controller.command(run_id, "start", body)

    @app.post(
        "/api/v1/runs/{run_id}/answers", response_model=CommandResponse, status_code=202
    )
    def answer_run(run_id: str, body: AnswerCommand):
        return controller.command(run_id, "answer", body)

    @app.post(
        "/api/v1/runs/{run_id}/continue",
        response_model=CommandResponse,
        status_code=202,
    )
    def continue_run(run_id: str, body: RunCommand):
        return controller.command(run_id, "continue", body)

    @app.post("/api/v1/runs/{run_id}/title", response_model=RunSnapshot)
    def rename_run(run_id: str, body: RenameCommand):
        return controller.rename(run_id, body)

    @app.post(
        "/api/v1/runs/{run_id}/stop", response_model=CommandResponse, status_code=202
    )
    def stop_response(run_id: str, body: StopCommand):
        return controller.stop(run_id, body)

    @app.get("/api/v1/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request):
        await asyncio.to_thread(controller.snapshot, run_id)

        async def stream():
            async with events.subscribe(run_id) as queue:
                kind = "sync"
                while not await request.is_disconnected():
                    yield f"event: {kind}\ndata: {json.dumps({'run_id': run_id})}\n\n"
                    try:
                        await asyncio.wait_for(queue.get(), timeout=15)
                        kind = "run.changed"
                    except TimeoutError:
                        kind = "sync"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    @app.post(
        "/api/v1/runs/{run_id}/messages",
        response_model=CommandResponse,
        status_code=202,
    )
    def message_run(run_id: str, body: MessageCommand):
        return controller.command(run_id, "message", body)

    @app.get("/api/v1/runs/{run_id}/artifacts/{file_id}")
    def artifact(run_id: str, file_id: str, preview: bool = False):
        content = snapshot_file(controller, run_id, file_id, preview=preview)
        return FileResponse(
            content.path,
            media_type=content.media_type,
            filename=None if preview else content.filename,
            background=BackgroundTask(content.path.unlink, missing_ok=True),
        )

    static = Path(__file__).parent.parent / "web_static"
    mimetypes.init()
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    app.mount(
        "/assets",
        StaticFiles(directory=static / "assets", check_dir=False),
        name="assets",
    )
    app.mount(
        "/icons",
        StaticFiles(directory=static / "icons", check_dir=False),
        name="icons",
    )

    @app.get("/")
    @app.get("/runs/{run_id}")
    def index(run_id: str | None = None):
        if not (static / "index.html").is_file():
            raise WebError(
                "frontend_missing", "前端尚未构建：请运行 scripts/build_web.py", 503
            )
        return FileResponse(static / "index.html")

    @app.get("/third-party-notices")
    def third_party_notices():
        return FileResponse(static / "THIRD_PARTY_NOTICES.txt", media_type="text/plain")

    return app
