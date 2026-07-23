from __future__ import annotations

from typing import Callable
import mimetypes

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from open_storyline.mvp.activity import ActivityService
from open_storyline.mvp.jobs import JobManager, JobStore, JobStoreError
from open_storyline.mvp.outcomes import retry_ux_enabled
from open_storyline.mvp.prompt_versions import (
    PromptVersionService,
    validate_run_settings,
)
from open_storyline.mvp.retention import RetentionService
from open_storyline.mvp.session_media import SessionMediaStore


class SessionPayload(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class SessionSourceUploadPayload(BaseModel):
    original_filename: str = Field(min_length=1, max_length=255)
    expected_size: int = Field(gt=0)
    media_type: str | None = Field(default=None, max_length=255)


class PromptVersionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=12000)
    max_clips: int = Field(default=8, ge=1, le=50)
    asset_policy: str = Field(default="auto", max_length=32)
    max_generated_assets_per_clip: int = Field(default=2, ge=0, le=20)
    stock_policy: str = Field(default="off", max_length=32)
    max_stock_assets_per_clip: int = Field(default=0, ge=0, le=20)
    stock_asset_kind: str = Field(default="video", max_length=16)

    @model_validator(mode="before")
    @classmethod
    def reject_retired_edit_mode(cls, value):
        if isinstance(value, dict) and "edit_mode" in value:
            raise ValueError(
                "edit_mode is retired; Agentic editing is the only workflow"
            )
        return value


class FavoriteRunPayload(BaseModel):
    run_id: str = Field(min_length=32, max_length=32)


class PromptRunPayload(BaseModel):
    prior_attempt_id: str | None = Field(default=None, min_length=32, max_length=32)
    use_quality_feedback: bool = False


def _http_error(exc: JobStoreError) -> HTTPException:
    if exc.code in {
        "JOB_NOT_FOUND",
        "ARTIFACT_NOT_FOUND",
        "SESSION_NOT_FOUND",
        "SESSION_SOURCE_NOT_FOUND",
        "SOURCE_UPLOAD_NOT_FOUND",
        "PROMPT_VERSION_NOT_FOUND",
        "PRIOR_ATTEMPT_NOT_FOUND",
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
        "EDIT_MODE_RETIRED",
        "PROMPT_VERSION_CONFLICT",
        "PROMPT_RUN_CONFLICT",
        "FAVORITE_RUN_INVALID",
        "SESSION_SOURCE_CHANGED",
        "PRIOR_ATTEMPT_NOT_READY",
    }:
        status = 409
    elif exc.code == "UPLOAD_TOO_LARGE":
        status = 413
    elif exc.code == "UPLOAD_CHUNK_TIMEOUT":
        status = 408
    elif exc.code == "VIDEO_TYPE_UNSUPPORTED":
        status = 415
    elif exc.code in {
        "SOURCE_VIDEO_INVALID",
        "SOURCE_VALIDATION_TIMEOUT",
        "UPLOAD_INCOMPLETE",
        "PROMPT_INVALID",
        "PRIOR_ATTEMPT_REQUIRED",
        "PRIOR_QUALITY_FEEDBACK_FLAG_REQUIRED",
        "PRIOR_QUALITY_EVIDENCE_UNAVAILABLE",
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
    get_prompt_versions: Callable[[], PromptVersionService | None] | None = None,
    get_activity: Callable[[], ActivityService | None] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/mvp", tags=["video-mvp"])

    @router.post("/sessions", status_code=201)
    async def create_session(payload: SessionPayload):
        try:
            return await get_store().create_session(
                payload.title,
                workflow_version=2,
            )
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.get("/sessions")
    async def list_sessions(limit: int = 20, cursor: str | None = None):
        try:
            return await get_store().list_sessions(
                limit=limit,
                cursor=cursor,
                workflow_version=2,
            )
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
            if current["workflow_version"] != 2:
                raise JobStoreError(
                    "SESSION_WORKFLOW_LEGACY",
                    "historical sessions are read-only audit records",
                )
            jobs = await store.list_jobs(
                session_id,
                limit=job_limit,
                cursor=job_cursor,
            )
            return {
                **current,
                "jobs": jobs["items"],
                "next_job_cursor": jobs["next_cursor"],
                "capabilities": {
                    "retry_ux_enabled": retry_ux_enabled(),
                },
            }
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
            return await service.delete_session(
                session_id,
                require_workflow_version=2,
            )
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.get("/sessions/{session_id}/jobs")
    async def list_session_jobs(
        session_id: str,
        limit: int = 20,
        cursor: str | None = None,
    ):
        try:
            current = await get_store().get_session(session_id)
            if current["workflow_version"] != 2:
                raise JobStoreError(
                    "SESSION_WORKFLOW_LEGACY",
                    "historical sessions are read-only audit records",
                )
            return await get_store().list_jobs(session_id, limit=limit, cursor=cursor)
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    def session_media() -> SessionMediaStore:
        service = get_session_media() if get_session_media is not None else None
        if service is None:
            raise HTTPException(
                status_code=503,
                detail={"code": "SESSION_MEDIA_UNAVAILABLE"},
            )
        return service

    def prompt_versions() -> PromptVersionService:
        service = get_prompt_versions() if get_prompt_versions is not None else None
        if service is None:
            raise HTTPException(
                status_code=503,
                detail={"code": "PROMPT_VERSION_SERVICE_UNAVAILABLE"},
            )
        return service

    def activity() -> ActivityService:
        service = get_activity() if get_activity is not None else None
        return service or ActivityService(get_store())

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

    @router.get("/sessions/{session_id}/prompt-versions")
    async def list_prompt_versions(
        session_id: str,
        limit: int = 20,
        cursor: str | None = None,
    ):
        try:
            return await prompt_versions().list_versions(
                session_id,
                limit=limit,
                cursor=cursor,
            )
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.post("/sessions/{session_id}/prompt-versions", status_code=202)
    async def create_prompt_version(
        session_id: str,
        payload: PromptVersionPayload,
    ):
        try:
            result = await prompt_versions().create_version(
                session_id,
                prompt=payload.prompt,
                settings=validate_run_settings(
                    max_clips=payload.max_clips,
                    asset_policy=payload.asset_policy,
                    max_generated_assets_per_clip=(
                        payload.max_generated_assets_per_clip
                    ),
                    stock_policy=payload.stock_policy,
                    max_stock_assets_per_clip=payload.max_stock_assets_per_clip,
                    stock_asset_kind=payload.stock_asset_kind,
                ),
            )
            await get_manager().enqueue(result["run"]["id"])
            return result
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.get("/prompt-versions/{prompt_version_id}")
    async def get_prompt_version(prompt_version_id: str):
        try:
            return await prompt_versions().get_version(prompt_version_id)
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.post("/prompt-versions/{prompt_version_id}/runs", status_code=202)
    async def rerun_prompt_version(
        prompt_version_id: str,
        payload: PromptRunPayload | None = None,
    ):
        try:
            run = await prompt_versions().rerun(
                prompt_version_id,
                prior_attempt_id=payload.prior_attempt_id if payload else None,
                use_quality_feedback=payload.use_quality_feedback if payload else False,
            )
            await get_manager().enqueue(run["id"])
            return run
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.put("/sessions/{session_id}/favorite-run")
    async def select_favorite_run(session_id: str, payload: FavoriteRunPayload):
        try:
            return await prompt_versions().select_favorite(session_id, payload.run_id)
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.delete("/sessions/{session_id}/favorite-run")
    async def clear_favorite_run(session_id: str):
        try:
            return await prompt_versions().clear_favorite(session_id)
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.post("/jobs")
    async def unscoped_job_creation_is_retired():
        return JSONResponse(
            {
                "detail": {
                    "code": "SESSION_REQUIRED",
                    "message": "create an Agentic prompt version under /api/mvp/sessions/{session_id}/prompt-versions",
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

    @router.get("/jobs/{job_id}/events")
    async def replay_job_activity(
        job_id: str,
        after: int = 0,
        limit: int = 100,
    ):
        try:
            return await activity().list(
                job_id,
                after_sequence=after,
                limit=limit,
            )
        except JobStoreError as exc:
            raise _http_error(exc) from exc

    @router.get("/jobs/{job_id}/events/stream")
    async def stream_job_activity(
        job_id: str,
        request: Request,
        after: int = 0,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ):
        cursor = after
        if last_event_id is not None:
            try:
                cursor = int(last_event_id)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "EVENT_CURSOR_INVALID"},
                ) from None
        service = activity()
        try:
            await service.list(job_id, after_sequence=cursor, limit=1)
        except JobStoreError as exc:
            raise _http_error(exc) from exc

        async def event_stream():
            try:
                async for chunk in service.stream(
                    job_id,
                    after_sequence=cursor,
                    disconnected=request.is_disconnected,
                ):
                    yield chunk
            except JobStoreError:
                yield (
                    'event: stream_error\ndata: {"code":"ACTIVITY_STREAM_UNAVAILABLE",'
                    '"retryable":true}\n\n'
                ).encode("utf-8")

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

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

    @router.get("/jobs/{job_id}/artifacts/{artifact_name}/preview")
    async def preview_artifact(job_id: str, artifact_name: str):
        try:
            path = await get_store().resolve_artifact(job_id, artifact_name)
        except JobStoreError as exc:
            raise _http_error(exc) from exc
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if not media_type.startswith(("video/", "image/", "audio/")):
            raise HTTPException(
                status_code=415,
                detail={"code": "ARTIFACT_PREVIEW_UNSUPPORTED"},
            )
        return FileResponse(
            path,
            filename=path.name,
            media_type=media_type,
            content_disposition_type="inline",
        )

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
