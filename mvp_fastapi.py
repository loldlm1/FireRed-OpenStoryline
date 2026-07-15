from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import hmac
import os
import sys

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from open_storyline.config import default_config_path, load_settings
from open_storyline.mvp.api import create_mvp_router
from open_storyline.mvp.jobs import JobManager, JobStore
from open_storyline.mvp.pipeline import MVPJobProcessor


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        token = os.getenv("OPENSTORYLINE_WEB_TOKEN", "").strip()
        if len(token) < 16:
            raise RuntimeError("OPENSTORYLINE_WEB_TOKEN must contain at least 16 characters")
        config = load_settings(default_config_path())
        store = JobStore(Path(config.project.outputs_dir) / "mvp_jobs")
        manager = JobManager(store, MVPJobProcessor(config))
        app.state.config = config
        app.state.mvp_jobs = store
        app.state.mvp_manager = manager
        await manager.start()
        try:
            yield
        finally:
            await manager.stop()

    app = FastAPI(
        title="OpenStoryline Remote Video MVP",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def require_access_token(request: Request, call_next):
        if not request.url.path.startswith("/api/mvp"):
            return await call_next(request)
        expected = os.getenv("OPENSTORYLINE_WEB_TOKEN", "").strip()
        authorization = request.headers.get("authorization", "")
        supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        if not expected:
            return JSONResponse(
                {"detail": {"code": "WEB_TOKEN_NOT_CONFIGURED"}},
                status_code=503,
            )
        if not supplied or not hmac.compare_digest(supplied, expected):
            return JSONResponse(
                {"detail": {"code": "UNAUTHORIZED", "message": "invalid access token"}},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(ROOT_DIR / "web" / "mvp.html")

    @app.get("/health")
    async def health():
        return {"status": "ok", "inference": "remote-only", "renderer": "ffmpeg-cpu"}

    app.include_router(create_mvp_router(
        lambda: app.state.mvp_jobs,
        lambda: app.state.mvp_manager,
    ))
    return app


app = create_app()
