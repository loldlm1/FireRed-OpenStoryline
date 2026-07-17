from __future__ import annotations

from pathlib import Path
from typing import Callable
import asyncio
import mimetypes
import os

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from open_storyline.mvp.jobs import JobManager, JobStore, JobStoreError


ALLOWED_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


class SessionPayload(BaseModel):
    title: str = Field(min_length=1, max_length=160)


def _http_error(exc: JobStoreError) -> HTTPException:
    if exc.code in {"JOB_NOT_FOUND", "ARTIFACT_NOT_FOUND", "SESSION_NOT_FOUND"}:
        status = 404
    elif exc.code in {
        "DATABASE_UNAVAILABLE",
        "JOB_STATE_UNAVAILABLE",
        "JOB_QUEUE_FULL",
    }:
        status = 503
    else:
        status = 400
    return HTTPException(
        status_code=status,
        detail={"code": exc.code, "message": str(exc)},
    )


def create_mvp_router(
    get_store: Callable[[], JobStore],
    get_manager: Callable[[], JobManager],
) -> APIRouter:
    router = APIRouter(prefix="/api/mvp", tags=["video-mvp"])

    @router.post("/sessions", status_code=201)
    async def create_session(payload: SessionPayload):
        try:
            return await get_store().create_session(payload.title)
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
            state = await store.create(
                editing_session_id=session_id,
                prompt=prompt,
                filename=file.filename or "video.mp4",
                max_clips=max_clips,
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
