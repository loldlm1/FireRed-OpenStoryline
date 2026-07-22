from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Full, Queue
from typing import Any
import json
import os
import subprocess
import threading
import time
import uuid

from open_storyline.mvp.ffmpega_contracts import (
    DETERMINISTIC_SKILLS,
    EFFECT_PARAMETER_INVENTORY,
    FFMPEGA_SOURCE_COMMIT,
    validate_typed_effects,
)


MAX_REQUEST_BYTES = 256 * 1024
MAX_HISTORY_ITEMS = 100
EXPECTED_INPUT_KEYS = frozenset({
    "prompt",
    "video_path",
    "llm_model",
    "no_llm_mode",
    "quality_preset",
    "seed",
    "pipeline_json",
    "advanced_options",
    "save_output",
    "output_path",
    "use_vision",
    "verify_output",
    "allow_model_downloads",
})


class ServiceError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class RenderJob:
    prompt_id: str
    source: Path
    destination: Path
    effects: tuple[dict[str, Any], ...]
    quality_preset: str


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ServiceError("FFMPEGA_CONFIG_INVALID") from exc
    if not minimum <= value <= maximum:
        raise ServiceError("FFMPEGA_CONFIG_INVALID")
    return value


def _dry_run_timeout(execution_timeout: int) -> int:
    configured = _bounded_int(
        "FFMPEGA_DRY_RUN_TIMEOUT_SECONDS",
        180,
        30,
        600,
    )
    return min(execution_timeout, configured)


def _inside(root: Path, path: Path) -> bool:
    return path == root or root in path.parents


def _resolve_source(root: Path, raw: Any) -> Path:
    if not isinstance(raw, str) or not raw.startswith("/"):
        raise ServiceError("FFMPEGA_INPUT_MISSING")
    try:
        path = Path(raw).resolve(strict=True)
    except OSError as exc:
        raise ServiceError("FFMPEGA_INPUT_MISSING") from exc
    if not path.is_file() or not _inside(root, path):
        raise ServiceError("FFMPEGA_PATH_NOT_SHARED")
    return path


def _resolve_destination(root: Path, raw: Any) -> Path:
    if not isinstance(raw, str) or not raw.startswith("/"):
        raise ServiceError("FFMPEGA_OUTPUT_MISSING")
    path = Path(raw)
    if path.suffix.lower() != ".mp4":
        raise ServiceError("FFMPEGA_OUTPUT_MISSING")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise ServiceError("FFMPEGA_OUTPUT_MISSING") from exc
    destination = parent / path.name
    if not _inside(root, parent) or destination.is_symlink():
        raise ServiceError("FFMPEGA_PATH_NOT_SHARED")
    return destination


def _normalize_effects(raw_pipeline: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(raw_pipeline, list) or not 1 <= len(raw_pipeline) <= 5:
        raise ServiceError("FFMPEGA_PLAN_INVALID")
    normalized: list[dict[str, Any]] = []
    for raw_effect in raw_pipeline:
        if not isinstance(raw_effect, dict) or set(raw_effect) != {"skill", "params"}:
            raise ServiceError("FFMPEGA_PLAN_INVALID")
        skill = raw_effect.get("skill")
        params = raw_effect.get("params")
        if skill not in DETERMINISTIC_SKILLS or not isinstance(params, dict):
            raise ServiceError("FFMPEGA_SKILL_BLOCKED")
        inventory = EFFECT_PARAMETER_INVENTORY[skill]
        if not set(params).issubset(inventory):
            raise ServiceError("FFMPEGA_PARAMETER_BLOCKED")
        complete_params = dict(params)
        for name, parameter in inventory.items():
            if name in complete_params:
                continue
            if parameter.default is None:
                raise ServiceError("FFMPEGA_PLAN_INVALID")
            complete_params[name] = parameter.default
        normalized.append({"skill": skill, "params": complete_params})
    try:
        effects = validate_typed_effects({"effects": normalized})
    except (TypeError, ValueError) as exc:
        raise ServiceError("FFMPEGA_PLAN_INVALID") from exc
    return tuple(effects)


def parse_prompt_request(payload: Any, *, shared_root: Path) -> RenderJob:
    if not isinstance(payload, dict) or not set(payload).issubset({"prompt", "client_id"}):
        raise ServiceError("FFMPEGA_RESPONSE_INVALID")
    workflow = payload.get("prompt")
    if not isinstance(workflow, dict) or len(workflow) != 1:
        raise ServiceError("FFMPEGA_RESPONSE_INVALID")
    node = next(iter(workflow.values()))
    if not isinstance(node, dict) or set(node) != {"class_type", "inputs"}:
        raise ServiceError("FFMPEGA_RESPONSE_INVALID")
    if node.get("class_type") != "FFMPEGAgent":
        raise ServiceError("FFMPEGA_SKILL_BLOCKED")
    inputs = node.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != EXPECTED_INPUT_KEYS:
        raise ServiceError("FFMPEGA_RESPONSE_INVALID")
    required_values = {
        "prompt": "",
        "llm_model": "none",
        "no_llm_mode": "manual",
        "seed": 0,
        "advanced_options": True,
        "save_output": True,
        "use_vision": False,
        "verify_output": False,
        "allow_model_downloads": False,
    }
    if any(inputs.get(name) != value for name, value in required_values.items()):
        raise ServiceError("FFMPEGA_PARAMETER_BLOCKED")
    quality_preset = inputs.get("quality_preset")
    if quality_preset not in {"draft", "standard", "high", "lossless"}:
        raise ServiceError("FFMPEGA_PARAMETER_BLOCKED")
    source = _resolve_source(shared_root, inputs.get("video_path"))
    destination = _resolve_destination(shared_root, inputs.get("output_path"))
    if source == destination:
        raise ServiceError("FFMPEGA_PATH_NOT_SHARED")
    pipeline_json = inputs.get("pipeline_json")
    if not isinstance(pipeline_json, str) or len(pipeline_json.encode("utf-8")) > 64 * 1024:
        raise ServiceError("FFMPEGA_PLAN_INVALID")
    try:
        pipeline = json.loads(pipeline_json)
    except json.JSONDecodeError as exc:
        raise ServiceError("FFMPEGA_PLAN_INVALID") from exc
    if not isinstance(pipeline, dict) or set(pipeline) != {
        "effects_mode", "pipeline", "raw_ffmpeg", "sam3"
    }:
        raise ServiceError("FFMPEGA_PLAN_INVALID")
    if (
        pipeline.get("effects_mode") != "skills"
        or pipeline.get("raw_ffmpeg") != ""
        or pipeline.get("sam3") is not None
    ):
        raise ServiceError("FFMPEGA_PARAMETER_BLOCKED")
    return RenderJob(
        prompt_id=uuid.uuid4().hex,
        source=source,
        destination=destination,
        effects=_normalize_effects(pipeline.get("pipeline")),
        quality_preset=quality_preset,
    )


def _probe_output(path: Path) -> None:
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height:format=duration",
            "-of", "json",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise ServiceError("FFMPEGA_EXECUTION_FAILED")
    try:
        probe = json.loads(result.stdout)
        duration = float((probe.get("format") or {}).get("duration") or 0)
        streams = probe.get("streams") or []
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ServiceError("FFMPEGA_EXECUTION_FAILED") from exc
    if duration <= 0 or not streams:
        raise ServiceError("FFMPEGA_EXECUTION_FAILED")


def render_with_upstream(job: RenderJob) -> None:
    from ffmpega.core.executor.process_manager import ProcessManager
    from ffmpega.skills.composer import Pipeline, SkillComposer
    from ffmpega.skills.registry import get_registry

    timeout = _bounded_int("FFMPEGA_EXECUTION_TIMEOUT_SECONDS", 1800, 30, 3600)
    partial = job.destination.with_name(
        f".{job.destination.stem}.{job.prompt_id}.partial.mp4"
    )
    partial.unlink(missing_ok=True)
    pipeline = Pipeline(input_path=str(job.source), output_path=str(partial))
    for effect in job.effects:
        pipeline.add_step(effect["skill"], effect["params"])
    if not any(effect["skill"] == "quality" for effect in job.effects):
        crf = {"draft": 28, "standard": 23, "high": 18, "lossless": 0}[job.quality_preset]
        preset = {
            "draft": "ultrafast",
            "standard": "medium",
            "high": "slow",
            "lossless": "veryslow",
        }[job.quality_preset]
        pipeline.add_step("quality", {"crf": crf, "preset": preset})
    composer = SkillComposer(get_registry())
    valid, _errors = composer.validate_pipeline(pipeline)
    if not valid:
        raise ServiceError("FFMPEGA_PLAN_INVALID")
    command = composer.compose(pipeline)
    manager = ProcessManager()
    dry_run = manager.dry_run(command, timeout=_dry_run_timeout(timeout))
    if not dry_run.success:
        raise ServiceError("FFMPEGA_EXECUTION_FAILED")
    result = manager.execute(command, timeout=timeout)
    if not result.success or not partial.is_file():
        partial.unlink(missing_ok=True)
        raise ServiceError("FFMPEGA_EXECUTION_FAILED")
    try:
        _probe_output(partial)
        os.replace(partial, job.destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


class ServiceState:
    def __init__(self, shared_root: Path) -> None:
        self.shared_root = shared_root
        self.queue: Queue[RenderJob] = Queue(
            maxsize=_bounded_int("FFMPEGA_QUEUE_SIZE", 4, 1, 32)
        )
        self.history: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.lock = threading.Lock()
        self.worker = threading.Thread(
            target=self._work,
            name="openstoryline-ffmpega-worker",
            daemon=True,
        )
        self.worker.start()

    def submit(self, payload: Any) -> str:
        job = parse_prompt_request(payload, shared_root=self.shared_root)
        try:
            self.queue.put_nowait(job)
        except Full as exc:
            raise ServiceError("FFMPEGA_QUEUE_FAILED") from exc
        return job.prompt_id

    def get_history(self, prompt_id: str) -> dict[str, Any]:
        with self.lock:
            record = self.history.get(prompt_id)
            return {prompt_id: dict(record)} if record is not None else {}

    def _record(self, prompt_id: str, record: dict[str, Any]) -> None:
        with self.lock:
            self.history[prompt_id] = record
            self.history.move_to_end(prompt_id)
            while len(self.history) > MAX_HISTORY_ITEMS:
                self.history.popitem(last=False)

    def _work(self) -> None:
        while True:
            job = self.queue.get()
            started = time.monotonic()
            code = "ok"
            try:
                render_with_upstream(job)
                record = {
                    "status": {"status_str": "success", "completed": True},
                    "outputs": {},
                }
            except ServiceError as exc:
                code = exc.code
                record = {
                    "status": {
                        "status_str": "error",
                        "completed": False,
                        "code": exc.code,
                    }
                }
            except Exception:
                code = "FFMPEGA_EXECUTION_FAILED"
                record = {
                    "status": {
                        "status_str": "error",
                        "completed": False,
                        "code": code,
                    }
                }
            finally:
                self._record(job.prompt_id, record)
                self.queue.task_done()
                print(json.dumps({
                    "event": "ffmpega_render",
                    "prompt_id": job.prompt_id,
                    "outcome": code,
                    "effect_count": len(job.effects),
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }, sort_keys=True), flush=True)


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "OpenStorylineFFMPEGA/1"

    @property
    def state(self) -> ServiceState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in {"/health", "/system_stats"}:
            self._json(200, {
                "status": "ok",
                "source_commit": FFMPEGA_SOURCE_COMMIT,
                "queue_depth": self.state.queue.qsize(),
                "worker_alive": self.state.worker.is_alive(),
            })
            return
        prefix = "/history/"
        if self.path.startswith(prefix):
            prompt_id = self.path[len(prefix):]
            if len(prompt_id) == 32 and all(char in "0123456789abcdef" for char in prompt_id):
                self._json(200, self.state.get_history(prompt_id))
                return
        self._json(404, {"code": "NOT_FOUND"})

    def do_POST(self) -> None:
        if self.path != "/prompt":
            self._json(404, {"code": "NOT_FOUND"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 0 < length <= MAX_REQUEST_BYTES:
            self._json(413, {"code": "FFMPEGA_RESPONSE_INVALID"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
            prompt_id = self.state.submit(payload)
        except json.JSONDecodeError:
            self._json(400, {"code": "FFMPEGA_RESPONSE_INVALID"})
            return
        except ServiceError as exc:
            status = 503 if exc.code == "FFMPEGA_QUEUE_FAILED" else 422
            self._json(status, {"code": exc.code})
            return
        self._json(200, {"prompt_id": prompt_id})


def main() -> int:
    raw_root = os.getenv("FFMPEGA_SHARED_ROOT", "").strip()
    if not raw_root.startswith("/"):
        raise SystemExit("FFMPEGA_SHARED_ROOT must be an absolute path")
    shared_root = Path(raw_root).resolve(strict=True)
    if not shared_root.is_dir():
        raise SystemExit("FFMPEGA_SHARED_ROOT must be a directory")
    port = _bounded_int("FFMPEGA_PORT", 8188, 1, 65535)
    server = ThreadingHTTPServer(("0.0.0.0", port), RequestHandler)
    server.state = ServiceState(shared_root)  # type: ignore[attr-defined]
    print(json.dumps({
        "event": "ffmpega_ready",
        "port": port,
        "source_commit": FFMPEGA_SOURCE_COMMIT,
    }, sort_keys=True), flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
