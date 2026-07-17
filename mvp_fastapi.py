from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import hmac
import sys

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from open_storyline.config import default_config_path, load_settings
from open_storyline.mvp.api import create_mvp_router
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
from open_storyline.mvp.pipeline import MVPJobProcessor


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = load_settings(default_config_path())
        database = Database.from_env()
        auth_service = AuthService(database, AuthSettings.from_env())
        store = JobStore(Path(config.project.outputs_dir) / "mvp_jobs", database)
        manager = JobManager(store, MVPJobProcessor(config))
        app.state.config = config
        app.state.database = database
        app.state.auth_service = auth_service
        app.state.mvp_jobs = store
        app.state.mvp_manager = manager
        try:
            await manager.start()
            yield
        finally:
            await manager.stop()
            await database.dispose()

    app = FastAPI(
        title="OpenStoryline Remote Video MVP",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.database = None
    app.state.auth_service = None

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

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(ROOT_DIR / "web" / "mvp.html")

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
    ))
    app.include_router(create_auth_router(lambda: app.state.auth_service))
    return app


app = create_app()
