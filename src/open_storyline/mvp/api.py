from __future__ import annotations

from pathlib import Path
from typing import Callable
import mimetypes
import os

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from open_storyline.mvp.jobs import JobManager, JobStore, JobStoreError


ALLOWED_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def _http_error(exc: JobStoreError) -> HTTPException:
    status = 404 if exc.code in {"JOB_NOT_FOUND", "ARTIFACT_NOT_FOUND"} else 400
    if exc.code in {"JOB_STATE_UNAVAILABLE", "JOB_STATE_INVALID"}:
        status = 503
    return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})


def create_mvp_router(
    get_store: Callable[[], JobStore],
    get_manager: Callable[[], JobManager],
) -> APIRouter:
    router = APIRouter(prefix="/api/mvp", tags=["video-mvp"])

    @router.post("/jobs", status_code=202)
    async def create_job(
        file: UploadFile = File(...),
        prompt: str = Form(...),
        max_clips: int = Form(8),
    ):
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in ALLOWED_VIDEO_SUFFIXES:
            raise HTTPException(status_code=415, detail={
                "code": "VIDEO_TYPE_UNSUPPORTED",
                "message": f"supported extensions: {', '.join(sorted(ALLOWED_VIDEO_SUFFIXES))}",
            })
        store = get_store()
        try:
            state = store.create(prompt=prompt, filename=file.filename or "video.mp4", max_clips=max_clips)
        except JobStoreError as exc:
            raise _http_error(exc) from exc

        target = store.input_path(state["id"], file.filename or "video.mp4")
        limit = int(os.getenv("OPENSTORYLINE_MAX_UPLOAD_BYTES", str(8 * 1024 * 1024 * 1024)))
        size = 0
        try:
            with target.open("xb") as stream:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise JobStoreError("UPLOAD_TOO_LARGE", f"upload exceeds {limit} bytes")
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            state = store.mark_uploaded(state["id"], target, size)
            await get_manager().enqueue(state["id"])
            return store.load(state["id"])
        except JobStoreError as exc:
            target.unlink(missing_ok=True)
            store.fail(state["id"], code=exc.code, message=str(exc))
            raise _http_error(exc) from exc
        except OSError as exc:
            target.unlink(missing_ok=True)
            store.fail(state["id"], code="UPLOAD_WRITE_FAILED", message=str(exc))
            raise HTTPException(status_code=500, detail={"code": "UPLOAD_WRITE_FAILED"}) from exc
        finally:
            await file.close()

    @router.get("/jobs/{job_id}")
    async def get_job(job_id: str):
        try:
            return get_store().load(job_id)
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.get("/jobs/{job_id}/artifacts")
    async def list_artifacts(job_id: str):
        try:
            state = get_store().load(job_id)
            return {"job_id": job_id, "artifacts": state.get("artifacts", [])}
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.get("/jobs/{job_id}/artifacts/{artifact_name}")
    async def download_artifact(job_id: str, artifact_name: str):
        try:
            path = get_store().resolve_artifact(job_id, artifact_name)
        except JobStoreError as exc:
            raise _http_error(exc) from exc
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(path, filename=path.name, media_type=media_type)

    @router.get("/jobs/{job_id}/bundle")
    async def download_bundle(job_id: str):
        try:
            path = get_store().build_bundle(job_id)
        except JobStoreError as exc:
            raise _http_error(exc) from exc
        return FileResponse(path, filename=path.name, media_type="application/zip")

    return router
