from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
import asyncio
import json
import os
import re
import threading
import uuid
import zipfile

from open_storyline.mvp.security import sanitize_for_persistence, sanitize_text


JOB_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
TERMINAL_STATES = {"completed", "failed", "cancelled"}
Processor = Callable[[str, "JobStore"], Awaitable[dict[str, Any] | None]]


class JobStoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_filename(value: str) -> str:
    name = Path(str(value or "video.mp4").replace("\\", "/")).name
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", name).strip(".-")
    return (stem or "video.mp4")[:180]


class JobStore:
    """Single-process durable job store with atomic JSON state updates."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _job_dir(self, job_id: str) -> Path:
        if not JOB_ID_PATTERN.fullmatch(str(job_id or "")):
            raise JobStoreError("JOB_ID_INVALID", "invalid job id")
        return self.root / job_id

    def _state_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "job.json"

    def _write_atomic(self, path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def create(self, *, prompt: str, filename: str, max_clips: int = 8) -> dict[str, Any]:
        clean_prompt = str(prompt or "").strip()
        if not clean_prompt:
            raise JobStoreError("PROMPT_REQUIRED", "an editing prompt is required")
        if not 1 <= int(max_clips) <= 50:
            raise JobStoreError("MAX_CLIPS_INVALID", "max_clips must be between 1 and 50")
        job_id = uuid.uuid4().hex
        job_dir = self._job_dir(job_id)
        (job_dir / "input").mkdir(parents=True)
        (job_dir / "output").mkdir()
        (job_dir / "work").mkdir()
        now = _now()
        state = {
            "id": job_id,
            "state": "uploading",
            "progress": 0.0,
            "prompt": clean_prompt[:12000],
            "request": {"max_clips": int(max_clips)},
            "input": {
                "original_filename": _safe_filename(filename),
                "stored_filename": "",
                "size": 0,
            },
            "artifacts": [],
            "error": None,
            "created_at": now,
            "updated_at": now,
            "recovery_count": 0,
        }
        with self._lock:
            self._write_atomic(job_dir / "job.json", state)
        return state

    def load(self, job_id: str) -> dict[str, Any]:
        path = self._state_path(job_id)
        try:
            with self._lock:
                value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise JobStoreError("JOB_NOT_FOUND", "job not found") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise JobStoreError("JOB_STATE_UNAVAILABLE", "job state is unavailable") from exc
        if not isinstance(value, dict) or value.get("id") != job_id:
            raise JobStoreError("JOB_STATE_INVALID", "job state is invalid")
        return value

    def update(self, job_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            state = self.load(job_id)
            state.update(changes)
            state["updated_at"] = _now()
            self._write_atomic(self._state_path(job_id), state)
        return state

    def input_path(self, job_id: str, original_filename: str) -> Path:
        suffix = Path(_safe_filename(original_filename)).suffix.lower() or ".mp4"
        return self._job_dir(job_id) / "input" / f"source{suffix}"

    def mark_uploaded(self, job_id: str, path: Path, size: int) -> dict[str, Any]:
        expected = self._job_dir(job_id) / "input"
        resolved = path.resolve()
        if expected.resolve() not in resolved.parents or not resolved.is_file():
            raise JobStoreError("UPLOAD_PATH_INVALID", "uploaded file is outside the job input directory")
        state = self.load(job_id)
        input_info = dict(state.get("input") or {})
        input_info.update({"stored_filename": path.name, "size": int(size)})
        return self.update(job_id, input=input_info, state="queued", progress=0.05, error=None)

    def fail(self, job_id: str, *, code: str, message: str, details: Any = None) -> dict[str, Any]:
        error = {
            "code": sanitize_text(code, limit=200),
            "message": sanitize_text(message, limit=1200),
        }
        if details is not None:
            error["details"] = sanitize_for_persistence(details)
        state = self.update(job_id, state="failed", error=error)
        failure_path = self.output_dir(job_id) / "failure.json"
        failure = sanitize_for_persistence({
            "job_id": job_id,
            "state": "failed",
            "stage": state.get("stage"),
            "error": error,
            "created_at": state.get("created_at"),
            "failed_at": state.get("updated_at"),
        })
        with self._lock:
            self._write_atomic(failure_path, failure)
        return self.register_artifact(job_id, failure_path, kind="failure")

    def output_dir(self, job_id: str) -> Path:
        path = self._job_dir(job_id) / "output"
        path.mkdir(exist_ok=True)
        return path

    def work_dir(self, job_id: str) -> Path:
        path = self._job_dir(job_id) / "work"
        path.mkdir(exist_ok=True)
        return path

    def source_path(self, job_id: str) -> Path:
        state = self.load(job_id)
        filename = str((state.get("input") or {}).get("stored_filename") or "")
        if not filename or Path(filename).name != filename:
            raise JobStoreError("JOB_INPUT_MISSING", "job input is missing")
        path = (self._job_dir(job_id) / "input" / filename).resolve()
        input_dir = (self._job_dir(job_id) / "input").resolve()
        if input_dir not in path.parents or not path.is_file():
            raise JobStoreError("JOB_INPUT_MISSING", "job input is missing")
        return path

    def register_artifact(self, job_id: str, path: str | Path, *, kind: str) -> dict[str, Any]:
        output_dir = self.output_dir(job_id).resolve()
        artifact_path = Path(path).resolve()
        if output_dir not in artifact_path.parents or not artifact_path.is_file():
            raise JobStoreError("ARTIFACT_PATH_INVALID", "artifact is outside the job output directory")
        state = self.load(job_id)
        artifacts = [item for item in state.get("artifacts", []) if item.get("name") != artifact_path.name]
        artifacts.append({
            "name": artifact_path.name,
            "kind": str(kind),
            "size": artifact_path.stat().st_size,
        })
        return self.update(job_id, artifacts=artifacts)

    def resolve_artifact(self, job_id: str, name: str) -> Path:
        state = self.load(job_id)
        clean_name = Path(str(name or "").replace("\\", "/")).name
        known = {str(item.get("name")) for item in state.get("artifacts", [])}
        if not clean_name or clean_name != name or clean_name not in known:
            raise JobStoreError("ARTIFACT_NOT_FOUND", "artifact not found")
        path = (self.output_dir(job_id) / clean_name).resolve()
        if self.output_dir(job_id).resolve() not in path.parents or not path.is_file():
            raise JobStoreError("ARTIFACT_NOT_FOUND", "artifact not found")
        return path

    def build_bundle(self, job_id: str) -> Path:
        state = self.load(job_id)
        destination = self.work_dir(job_id) / f"{job_id}-artifacts.zip"
        temporary = destination.with_suffix(".tmp")
        temporary.unlink(missing_ok=True)
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                for artifact in state.get("artifacts", []):
                    name = str(artifact.get("name") or "")
                    try:
                        path = self.resolve_artifact(job_id, name)
                    except JobStoreError:
                        continue
                    bundle.write(path, arcname=name)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def recover_pending(self) -> list[str]:
        recovered: list[str] = []
        for path in sorted(self.root.glob("*/job.json")):
            job_id = path.parent.name
            if not JOB_ID_PATTERN.fullmatch(job_id):
                continue
            try:
                state = self.load(job_id)
            except JobStoreError:
                continue
            if state.get("state") not in {"queued", "running"}:
                continue
            count = int(state.get("recovery_count") or 0) + 1
            self.update(job_id, state="queued", recovery_count=count)
            recovered.append(job_id)
        return recovered


class JobManager:
    def __init__(self, store: JobStore, processor: Optional[Processor] = None) -> None:
        self.store = store
        self.processor = processor
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> None:
        recovered = self.store.recover_pending()
        if self.processor is None:
            return
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="openstoryline-mvp-worker")
        for job_id in recovered:
            await self.queue.put(job_id)

    async def enqueue(self, job_id: str) -> None:
        state = self.store.load(job_id)
        if state.get("state") in TERMINAL_STATES:
            raise JobStoreError("JOB_TERMINAL", "terminal jobs cannot be queued")
        self.store.update(job_id, state="queued")
        if self.processor is not None:
            await self.queue.put(job_id)

    async def stop(self) -> None:
        if self._worker is None:
            return
        await self.queue.put(None)
        await self._worker
        self._worker = None

    async def _run(self) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                if job_id is None:
                    return
                self.store.update(job_id, state="running", progress=0.1, error=None)
                result = await self.processor(job_id, self.store) if self.processor else None
                current = self.store.load(job_id)
                if current.get("state") not in TERMINAL_STATES:
                    self.store.update(job_id, state="completed", progress=1.0, **(result or {}))
            except Exception as exc:
                if job_id is not None:
                    code = str(getattr(exc, "code", "JOB_PROCESSING_FAILED"))
                    details = getattr(exc, "to_dict", lambda: None)()
                    self.store.fail(job_id, code=code, message=str(exc), details=details)
            finally:
                self.queue.task_done()
