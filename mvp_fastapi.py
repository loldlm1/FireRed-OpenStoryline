from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
import hmac
import os
import sys
import time

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from open_storyline.config import default_config_path, load_settings
from open_storyline.mvp.activity import ActivityService
from open_storyline.mvp.api import create_mvp_router
from open_storyline.mvp.audit import AuditService
from open_storyline.mvp.catalog import load_creative_catalog
from open_storyline.mvp.auth import (
    CSRF_COOKIE,
    SAFE_METHODS,
    SESSION_COOKIE,
    AuthService,
    AuthSettings,
    AuthUnavailable,
    create_auth_router,
)
from open_storyline.mvp.database import Database
from open_storyline.mvp.jobs import JobManager, JobStore
from open_storyline.mvp.observability import emit_event, finish_request, start_request
from open_storyline.mvp.pipeline import MVPJobProcessor
from open_storyline.mvp.prompt_versions import PromptVersionService
from open_storyline.mvp.retention import (
    RetentionScheduler,
    RetentionService,
    RetentionSettings,
)
from open_storyline.mvp.session_media import SessionMediaStore


SESSION_WORKSPACE_MODES = frozenset({"legacy", "enabled"})
WORKSPACE_CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "connect-src 'self'",
        "img-src 'self'",
        "font-src 'self'",
        "media-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "frame-src 'none'",
        "worker-src 'none'",
        "manifest-src 'self'",
    )
)


class SessionWorkspaceConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class SessionWorkspaceSettings:
    mode: str

    @classmethod
    def from_env(cls) -> "SessionWorkspaceSettings":
        mode = os.getenv("OPENSTORYLINE_SESSION_WORKSPACE_MODE", "legacy").strip()
        if len(mode) > 16 or mode not in SESSION_WORKSPACE_MODES:
            raise SessionWorkspaceConfigurationError(
                "OPENSTORYLINE_SESSION_WORKSPACE_MODE must be legacy or enabled"
            )
        return cls(mode=mode)


def create_app() -> FastAPI:
    workspace_settings = SessionWorkspaceSettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = load_settings(default_config_path())
        creative_catalog = load_creative_catalog()
        database = Database.from_env()
        auth_service = AuthService(database, AuthSettings.from_env())
        retention_settings = RetentionSettings.from_env()
        store = JobStore(
            Path(config.project.outputs_dir) / "mvp_jobs",
            database,
            media_retention_days=retention_settings.media_days,
            audit_retention_days=retention_settings.audit_days,
            session_media_root=Path(config.project.outputs_dir) / "mvp_sessions",
        )
        session_media = SessionMediaStore(
            Path(config.project.outputs_dir) / "mvp_sessions",
            database,
            media_retention_days=retention_settings.media_days,
            incomplete_upload_hours=retention_settings.incomplete_upload_hours,
        )
        prompt_versions = PromptVersionService(store, session_media)
        activity = ActivityService(store)
        audit_service = AuditService(store)
        store.attach_audit(audit_service)
        retention_service = RetentionService(
            store,
            retention_settings,
            session_media=session_media,
        )
        store.attach_retention(retention_service)
        retention_scheduler = RetentionScheduler(retention_service)
        manager = JobManager(
            store,
            MVPJobProcessor(config, creative_catalog=creative_catalog),
        )
        app.state.config = config
        app.state.database = database
        app.state.auth_service = auth_service
        app.state.mvp_jobs = store
        app.state.mvp_manager = manager
        app.state.audit_service = audit_service
        app.state.session_media = session_media
        app.state.prompt_versions = prompt_versions
        app.state.activity = activity
        app.state.retention_service = retention_service
        app.state.retention_scheduler = retention_scheduler
        app.state.creative_catalog = creative_catalog
        emit_event(
            "creative_catalog_loaded",
            catalog_version=creative_catalog.version,
            manifest_sha256=creative_catalog.manifest_sha256,
            entry_count=len(creative_catalog.entries),
            quarantined_count=len(creative_catalog.quarantined),
        )
        try:
            await manager.start()
            await retention_scheduler.start()
            yield
        finally:
            await retention_scheduler.stop()
            await manager.stop()
            await database.dispose()

    app = FastAPI(
        title="OpenStoryline Remote Video MVP",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.database = None
    app.state.auth_service = None
    app.state.retention_service = None
    app.state.session_media = None
    app.state.prompt_versions = None
    app.state.activity = None
    app.state.creative_catalog = None
    app.state.session_workspace_mode = workspace_settings.mode

    @app.middleware("http")
    async def request_observability(request: Request, call_next):
        request_id, token = start_request(request.headers.get("x-request-id"))
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            emit_event(
                "http_request",
                duration_ms=int((time.monotonic() - started) * 1000),
                outcome="error",
                error_code="UNHANDLED_REQUEST_ERROR",
                method=request.method,
            )
            raise
        else:
            response.headers["X-Request-ID"] = request_id
            emit_event(
                "http_request",
                duration_ms=int((time.monotonic() - started) * 1000),
                outcome=str(response.status_code),
                method=request.method,
            )
            return response
        finally:
            finish_request(token)

    @app.middleware("http")
    async def require_browser_session(request: Request, call_next):
        if not request.url.path.startswith("/api/mvp"):
            return await call_next(request)
        if request.url.path in {
            "/api/mvp/auth/login",
            "/api/mvp/auth/session",
        }:
            return await call_next(request)
        service = app.state.auth_service
        if service is None:
            return JSONResponse(
                {"detail": {"code": "AUTH_UNAVAILABLE"}},
                status_code=503,
            )
        try:
            context = await service.resolve_session(request.cookies.get(SESSION_COOKIE))
        except AuthUnavailable:
            return JSONResponse(
                {"detail": {"code": "AUTH_UNAVAILABLE"}},
                status_code=503,
            )
        if context is None:
            return JSONResponse(
                {"detail": {"code": "UNAUTHENTICATED", "message": "login required"}},
                status_code=401,
            )
        request.state.auth_session = context
        if request.method not in SAFE_METHODS:
            header_token = request.headers.get("x-csrf-token", "")
            cookie_token = request.cookies.get(CSRF_COOKIE, "")
            if (
                not service.same_origin(request)
                or not header_token
                or not hmac.compare_digest(header_token, cookie_token)
                or not service.valid_csrf(context, header_token)
            ):
                return JSONResponse(
                    {"detail": {"code": "CSRF_VALIDATION_FAILED"}},
                    status_code=403,
                )
        return await call_next(request)

    @app.middleware("http")
    async def harden_browser_responses(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        if request.url.path == "/" or request.url.path.startswith("/static/mvp/"):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Content-Security-Policy"] = (
                WORKSPACE_CONTENT_SECURITY_POLICY
            )
            response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
            response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        return response

    app.mount(
        "/static/mvp",
        StaticFiles(directory=ROOT_DIR / "web" / "static" / "mvp"),
        name="mvp-static",
    )

    @app.get("/", include_in_schema=False)
    async def index():
        page = (
            "mvp.html"
            if app.state.session_workspace_mode == "enabled"
            else "mvp-legacy.html"
        )
        return FileResponse(ROOT_DIR / "web" / page)

    @app.get("/health")
    async def health():
        return {"status": "ok", "inference": "remote-only", "renderer": "ffmpeg-cpu"}

    @app.get("/up", include_in_schema=False)
    async def kamal_healthcheck():
        database = app.state.database
        if database is None:
            return JSONResponse(
                {"status": "unavailable", "code": "DATABASE_NOT_INITIALIZED"},
                status_code=503,
            )
        readiness = await database.readiness()
        if not readiness.ready:
            return JSONResponse(
                {"status": "unavailable", "code": readiness.code},
                status_code=503,
            )
        return {"status": "ok"}

    app.include_router(create_mvp_router(
        lambda: app.state.mvp_jobs,
        lambda: app.state.mvp_manager,
        lambda: app.state.retention_service,
        lambda: app.state.session_media,
        lambda: app.state.session_workspace_mode,
        lambda: app.state.prompt_versions,
        lambda: app.state.activity,
    ))
    app.include_router(create_auth_router(lambda: app.state.auth_service))
    return app


app = create_app()
