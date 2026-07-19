from __future__ import annotations

from pathlib import Path
from typing import Callable
import asyncio
import mimetypes
import os

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from open_storyline.mvp.jobs import JobManager, JobStore, JobStoreError
from open_storyline.mvp.retention import RetentionService
from open_storyline.mvp.session_media import SessionMediaStore


ALLOWED_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


class SessionPayload(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class SessionSourceUploadPayload(BaseModel):
    original_filename: str = Field(min_length=1, max_length=255)
    expected_size: int = Field(gt=0)
    media_type: str | None = Field(default=None, max_length=255)


def _http_error(exc: JobStoreError) -> HTTPException:
    if exc.code in {
        "JOB_NOT_FOUND",
        "ARTIFACT_NOT_FOUND",
        "SESSION_NOT_FOUND",
        "SESSION_SOURCE_NOT_FOUND",
        "SOURCE_UPLOAD_NOT_FOUND",
    }:
        status = 404
    elif exc.code in {
        "DATABASE_UNAVAILABLE",
        "JOB_STATE_UNAVAILABLE",
        "JOB_QUEUE_FULL",
        "RETENTION_BUSY",
        "SOURCE_VALIDATION_UNAVAILABLE",
    }:
        status = 503
    elif exc.code in {
        "SESSION_ACTIVE_JOBS",
        "SESSION_SOURCE_IMMUTABLE",
        "SOURCE_UPLOAD_BUSY",
        "UPLOAD_METADATA_CONFLICT",
        "UPLOAD_OFFSET_MISMATCH",
        "UPLOAD_STATE_INVALID",
        "SESSION_WORKFLOW_LEGACY",
        "SESSION_WORKFLOW_REUSABLE",
    }:
        status = 409
    elif exc.code == "UPLOAD_TOO_LARGE":
        status = 413
    elif exc.code == "VIDEO_TYPE_UNSUPPORTED":
        status = 415
    elif exc.code in {
        "SOURCE_VIDEO_INVALID",
        "SOURCE_VALIDATION_TIMEOUT",
        "UPLOAD_INCOMPLETE",
    }:
        status = 422
    elif exc.code in {"SESSION_SOURCE_EXPIRED", "SESSION_SOURCE_UNAVAILABLE"}:
        status = 410
    elif exc.code in {"UPLOAD_WRITE_FAILED", "SOURCE_VALIDATION_STORAGE_FAILED"}:
        status = 500
    else:
        status = 400
    detail: dict[str, object] = {"code": exc.code, "message": str(exc)}
    details = getattr(exc, "details", None)
    if isinstance(details, dict) and details:
        detail["details"] = details
    return HTTPException(
        status_code=status,
        detail=detail,
    )


def create_mvp_router(
    get_store: Callable[[], JobStore],
    get_manager: Callable[[], JobManager],
    get_retention: Callable[[], RetentionService | None] | None = None,
    get_session_media: Callable[[], SessionMediaStore | None] | None = None,
    get_workspace_mode: Callable[[], str] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/mvp", tags=["video-mvp"])

    @router.post("/sessions", status_code=201)
    async def create_session(payload: SessionPayload):
        try:
            mode = get_workspace_mode() if get_workspace_mode is not None else "legacy"
            return await get_store().create_session(
                payload.title,
                workflow_version=2 if mode == "enabled" else 1,
            )
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.get("/sessions")
    async def list_sessions(limit: int = 20, cursor: str | None = None):
        try:
            return await get_store().list_sessions(limit=limit, cursor=cursor)
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.get("/sessions/{session_id}")
    async def get_session(
        session_id: str,
        job_limit: int = 20,
        job_cursor: str | None = None,
    ):
        try:
            store = get_store()
            current = await store.get_session(session_id)
            jobs = await store.list_jobs(
                session_id,
                limit=job_limit,
                cursor=job_cursor,
            )
            return {**current, "jobs": jobs["items"], "next_job_cursor": jobs["next_cursor"]}
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.delete("/sessions/{session_id}")
    async def delete_session(session_id: str):
        service = get_retention() if get_retention is not None else None
        if service is None:
            raise HTTPException(
                status_code=503,
                detail={"code": "RETENTION_UNAVAILABLE"},
            )
        try:
            return await service.delete_session(session_id)
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.get("/sessions/{session_id}/jobs")
    async def list_session_jobs(
        session_id: str,
        limit: int = 20,
        cursor: str | None = None,
    ):
        try:
            return await get_store().list_jobs(session_id, limit=limit, cursor=cursor)
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.post("/sessions/{session_id}/jobs", status_code=202)
    async def create_session_job(
        session_id: str,
        file: UploadFile = File(...),
        prompt: str = Form(...),
        max_clips: int = Form(8),
        edit_mode: str = Form("legacy"),
        asset_policy: str = Form("auto"),
        max_generated_assets_per_clip: int = Form(2),
        stock_policy: str = Form("off"),
        max_stock_assets_per_clip: int = Form(0),
    ):
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in ALLOWED_VIDEO_SUFFIXES:
            raise HTTPException(
                status_code=415,
                detail={
                    "code": "VIDEO_TYPE_UNSUPPORTED",
                    "message": f"supported extensions: {', '.join(sorted(ALLOWED_VIDEO_SUFFIXES))}",
                },
            )
        store = get_store()
        try:
            editing_session = await store.get_session(session_id)
            if editing_session["workflow_version"] != 1:
                raise JobStoreError(
                    "SESSION_WORKFLOW_REUSABLE",
                    "create prompt versions and runs for reusable sessions",
                )
            state = await store.create(
                editing_session_id=session_id,
                prompt=prompt,
                filename=file.filename or "video.mp4",
                max_clips=max_clips,
                edit_mode=edit_mode,
                asset_policy=asset_policy,
                max_generated_assets_per_clip=max_generated_assets_per_clip,
                stock_policy=stock_policy,
                max_stock_assets_per_clip=max_stock_assets_per_clip,
            )
        except JobStoreError as exc:
            await file.close()
            raise _http_error(exc) from exc

        target = store.input_path(state["id"], file.filename or "video.mp4")
        limit = int(
            os.getenv("OPENSTORYLINE_MAX_UPLOAD_BYTES", str(8 * 1024 * 1024 * 1024))
        )
        size = 0
        try:
            with target.open("xb") as stream:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise JobStoreError(
                            "UPLOAD_TOO_LARGE",
                            f"upload exceeds {limit} bytes",
                        )
                    await asyncio.to_thread(stream.write, chunk)
                await asyncio.to_thread(stream.flush)
                await asyncio.to_thread(os.fsync, stream.fileno())
            state = await store.mark_uploaded(state["id"], target, size)
            await get_manager().enqueue(state["id"])
            return await store.load(state["id"])
        except JobStoreError as exc:
            await asyncio.to_thread(target.unlink, missing_ok=True)
            await store.fail(state["id"], code=exc.code, message=str(exc))
            raise _http_error(exc) from exc
        except asyncio.CancelledError:
            await asyncio.to_thread(target.unlink, missing_ok=True)
            try:
                await asyncio.shield(
                    store.fail(
                        state["id"],
                        code="UPLOAD_INTERRUPTED",
                        message="the upload was interrupted",
                    )
                )
            except (JobStoreError, OSError):
                pass
            raise
        except OSError as exc:
            await asyncio.to_thread(target.unlink, missing_ok=True)
            await store.fail(
                state["id"],
                code="UPLOAD_WRITE_FAILED",
                message=str(exc),
            )
            raise HTTPException(
                status_code=500,
                detail={"code": "UPLOAD_WRITE_FAILED"},
            ) from exc
        finally:
            await file.close()

    def session_media() -> SessionMediaStore:
        service = get_session_media() if get_session_media is not None else None
        if service is None:
            raise HTTPException(
                status_code=503,
                detail={"code": "SESSION_MEDIA_UNAVAILABLE"},
            )
        return service

    @router.post("/sessions/{session_id}/input-video/uploads", status_code=201)
    async def initialize_session_source(
        session_id: str,
        payload: SessionSourceUploadPayload,
    ):
        try:
            return await session_media().initialize(
                session_id,
                original_filename=payload.original_filename,
                expected_size=payload.expected_size,
                media_type=payload.media_type,
            )
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.get("/sessions/{session_id}/input-video")
    async def get_session_source(session_id: str):
        try:
            return await session_media().status(session_id)
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.patch("/sessions/{session_id}/input-video/uploads/{upload_id}")
    async def append_session_source(
        session_id: str,
        upload_id: str,
        request: Request,
    ):
        raw_offset = request.headers.get("upload-offset", "")
        try:
            offset = int(raw_offset)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"code": "UPLOAD_OFFSET_INVALID"},
            ) from None
        content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type not in {"application/octet-stream", "application/offset+octet-stream"}:
            raise HTTPException(
                status_code=415,
                detail={"code": "UPLOAD_CHUNK_TYPE_UNSUPPORTED"},
            )
        raw_length = request.headers.get("content-length")
        try:
            content_length = int(raw_length) if raw_length is not None else None
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"code": "UPLOAD_CHUNK_INVALID"},
            ) from None
        try:
            return await session_media().append_chunk(
                session_id,
                upload_id,
                offset=offset,
                chunks=request.stream(),
                content_length=content_length,
            )
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.post(
        "/sessions/{session_id}/input-video/uploads/{upload_id}/complete"
    )
    async def complete_session_source(session_id: str, upload_id: str):
        try:
            return await session_media().complete(session_id, upload_id)
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.delete("/sessions/{session_id}/input-video/uploads/{upload_id}")
    async def cancel_session_source(session_id: str, upload_id: str):
        try:
            return await session_media().cancel(session_id, upload_id)
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.get("/sessions/{session_id}/input-video/content")
    async def preview_session_source(session_id: str):
        try:
            path, source = await session_media().resolve_ready(session_id)
        except JobStoreError as exc:
            raise _http_error(exc) from exc
        return FileResponse(
            path,
            media_type=source["media_type"],
            filename=source["original_filename"],
            content_disposition_type="inline",
        )

    @router.post("/jobs")
    async def unscoped_job_creation_is_retired():
        return JSONResponse(
            {
                "detail": {
                    "code": "SESSION_REQUIRED",
                    "message": "create the job under /api/mvp/sessions/{session_id}/jobs",
                }
            },
            status_code=409,
        )

    @router.get("/jobs/{job_id}")
    async def get_job(job_id: str):
        try:
            return await get_store().load(job_id)
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.get("/jobs/{job_id}/artifacts")
    async def list_artifacts(job_id: str):
        try:
            state = await get_store().load(job_id)
            return {"job_id": job_id, "artifacts": state.get("artifacts", [])}
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.get("/jobs/{job_id}/artifacts/{artifact_name}")
    async def download_artifact(job_id: str, artifact_name: str):
        try:
            path = await get_store().resolve_artifact(job_id, artifact_name)
        except JobStoreError as exc:
            raise _http_error(exc) from exc
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(path, filename=path.name, media_type=media_type)

    @router.get("/jobs/{job_id}/bundle")
    async def download_bundle(job_id: str):
        try:
            path = await get_store().build_bundle(job_id)
        except JobStoreError as exc:
            raise _http_error(exc) from exc
        return FileResponse(
            path,
            filename=path.name,
            media_type="application/zip",
        )

    return router
