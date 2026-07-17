from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import hashlib
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
from open_storyline.mvp.database import Database
from open_storyline.mvp.jobs import JobManager, JobStore
from open_storyline.mvp.pipeline import MVPJobProcessor
from open_storyline.mvp.rate_limit import PersistentRateLimiter, RateDecision, RatePolicy


def _enabled(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _client_scope(request: Request) -> str:
    address = request.client.host if request.client else "unknown"
    if _enabled("OPENSTORYLINE_TRUST_PROXY_HEADERS"):
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        if forwarded:
            address = forwarded[:128]
    digest = hashlib.sha256(address.encode("utf-8", errors="replace")).hexdigest()[:24]
    return f"unauthorized:client:{digest}"


def _supplied_token(request: Request) -> str:
    api_key = request.headers.get("x-api-key", "").strip()
    if api_key:
        return api_key
    authorization = request.headers.get("authorization", "")
    return authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""


def _limited(scope: str, decision: RateDecision) -> JSONResponse:
    return JSONResponse(
        {
            "detail": {
                "code": "RATE_LIMIT_EXCEEDED",
                "scope": scope,
                "message": "request quota exceeded; retry after the indicated interval",
            }
        },
        status_code=429,
        headers=decision.headers(),
    )


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        token = os.getenv("OPENSTORYLINE_WEB_TOKEN", "").strip()
        if len(token) < 16:
            raise RuntimeError("OPENSTORYLINE_WEB_TOKEN must contain at least 16 characters")
        config = load_settings(default_config_path())
        database = Database.from_env()
        store = JobStore(Path(config.project.outputs_dir) / "mvp_jobs")
        manager = JobManager(store, MVPJobProcessor(config))
        limiter_path = os.getenv("OPENSTORYLINE_RATE_LIMIT_DB", "").strip()
        if not limiter_path:
            limiter_path = str(Path(config.project.outputs_dir) / "mvp_rate_limits.sqlite3")
        app.state.config = config
        app.state.database = database
        app.state.mvp_jobs = store
        app.state.mvp_manager = manager
        app.state.rate_limiter = PersistentRateLimiter(limiter_path)
        app.state.rate_policy = RatePolicy.from_env()
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
    app.state.rate_limiter = None
    app.state.rate_policy = None
    app.state.database = None

    @app.middleware("http")
    async def require_access_token(request: Request, call_next):
        if not request.url.path.startswith("/api/mvp"):
            return await call_next(request)
        expected = os.getenv("OPENSTORYLINE_WEB_TOKEN", "").strip()
        if not expected:
            return JSONResponse(
                {"detail": {"code": "WEB_TOKEN_NOT_CONFIGURED"}},
                status_code=503,
            )
        limiter = app.state.rate_limiter
        policy = app.state.rate_policy
        if limiter is None or policy is None:
            return JSONResponse(
                {"detail": {"code": "RATE_LIMITER_UNAVAILABLE"}},
                status_code=503,
            )

        supplied = _supplied_token(request)
        if not supplied or not hmac.compare_digest(supplied, expected):
            try:
                global_decision = await asyncio.to_thread(
                    limiter.check,
                    "unauthorized:global",
                    policy.unauthorized_global,
                )
                if not global_decision.allowed:
                    return _limited("unauthorized_global", global_decision)
                client_decision = await asyncio.to_thread(
                    limiter.check,
                    _client_scope(request),
                    policy.unauthorized_client,
                )
            except Exception:
                return JSONResponse(
                    {"detail": {"code": "RATE_LIMITER_UNAVAILABLE"}},
                    status_code=503,
                )
            if not client_decision.allowed:
                return _limited("unauthorized_client", client_decision)
            return JSONResponse(
                {"detail": {"code": "UNAUTHORIZED", "message": "invalid access token"}},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer", **client_decision.headers()},
            )

        key_digest = hashlib.sha256(expected.encode("utf-8")).hexdigest()[:24]
        try:
            api_decision = await asyncio.to_thread(
                limiter.check,
                f"api:{key_digest}",
                policy.api,
            )
            if not api_decision.allowed:
                return _limited("api", api_decision)
            active_decision = api_decision
            if request.method == "POST" and request.url.path.rstrip("/") == "/api/mvp/jobs":
                jobs_decision = await asyncio.to_thread(
                    limiter.check,
                    f"jobs:{key_digest}",
                    policy.jobs,
                )
                if not jobs_decision.allowed:
                    return _limited("jobs", jobs_decision)
                active_decision = jobs_decision
        except Exception:
            return JSONResponse(
                {"detail": {"code": "RATE_LIMITER_UNAVAILABLE"}},
                status_code=503,
            )

        response = await call_next(request)
        response.headers.update(active_decision.headers())
        return response

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
    return app


app = create_app()
