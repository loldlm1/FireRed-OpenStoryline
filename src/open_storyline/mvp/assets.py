from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import hashlib
import json
import os
import re

from open_storyline.mvp.edit_plan import AssetPolicy, AssetRequest, EditPlan
from open_storyline.utils.generated_media import (
    RIGHTS_NOTICE,
    build_original_image_prompt,
)
from open_storyline.utils.remote_image import RemoteImageCascade, RemoteImageError


ASSET_MANIFEST_VERSION = "asset_manifest.v1"


class AssetResolutionError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        attempts: Iterable[dict[str, Any]] = (),
    ) -> None:
        self.code = code
        self.attempts = tuple(dict(item) for item in attempts)
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "attempts": list(self.attempts),
        }


@dataclass(frozen=True)
class AssetResolutionResult:
    paths: dict[str, Path]
    manifest_path: Path
    manifest: dict[str, Any]

    @property
    def provider_call_count(self) -> int:
        return int(self.manifest.get("provider_call_count") or 0)


def generated_assets_enabled(config: Any) -> bool:
    raw = os.getenv("OPENSTORYLINE_GENERATED_ASSETS_ENABLED")
    if raw is None:
        return bool(getattr(config, "generated_assets_enabled", False))
    normalized = raw.strip().lower()
    if normalized not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
        raise AssetResolutionError(
            "ASSET_CONFIG_INVALID",
            "OPENSTORYLINE_GENERATED_ASSETS_ENABLED must be true or false",
        )
    return normalized in {"1", "true", "yes", "on"}


def generated_asset_server_cap(config: Any) -> int:
    raw = os.getenv("OPENSTORYLINE_MAX_GENERATED_ASSETS_PER_CLIP")
    try:
        value = int(
            raw
            if raw is not None
            else getattr(config, "max_generated_assets_per_clip", 2)
        )
    except (TypeError, ValueError) as exc:
        raise AssetResolutionError(
            "ASSET_CONFIG_INVALID",
            "OPENSTORYLINE_MAX_GENERATED_ASSETS_PER_CLIP must be an integer",
        ) from exc
    if not 0 <= value <= 8:
        raise AssetResolutionError(
            "ASSET_CONFIG_INVALID",
            "generated asset server cap must be between 0 and 8",
        )
    return value


def generated_asset_size(config: Any) -> str:
    value = str(
        os.getenv("OPENSTORYLINE_IMAGE_SIZE")
        or getattr(config, "size", "1024x1024")
    ).strip()
    match = re.fullmatch(r"([1-9]\d{2,3})x([1-9]\d{2,3})", value)
    if match is None or any(not 256 <= int(item) <= 4096 for item in match.groups()):
        raise AssetResolutionError(
            "ASSET_CONFIG_INVALID",
            "OPENSTORYLINE_IMAGE_SIZE must be WIDTHxHEIGHT between 256 and 4096",
        )
    return value


def _planned_requests(edit_plan: EditPlan) -> list[tuple[int, AssetRequest]]:
    return [
        (clip.clip_index, asset)
        for clip in edit_plan.clips
        for asset in clip.asset_requests
    ]


def _request_metadata(clip_index: int, request: AssetRequest) -> dict[str, Any]:
    return {
        "id": request.id,
        "clip_index": clip_index,
        "kind": request.kind,
        "provider": request.provider,
        "timeline_window": request.timeline_window.model_dump(mode="json"),
        "visual_gap": request.visual_gap,
        "purpose": request.purpose,
        "rationale": request.rationale,
        "orientation": request.orientation,
        "required": request.required,
        "fallback": request.fallback,
        "prompt_sha256": hashlib.sha256(request.prompt.encode("utf-8")).hexdigest(),
    }


def _write_manifest(output_dir: Path, payload: dict[str, Any]) -> Path:
    manifest_path = output_dir / "asset_manifest.json"
    temporary = output_dir / "asset_manifest.json.part"
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, manifest_path)
    return manifest_path


def write_asset_manifest(
    edit_plan: EditPlan,
    *,
    output_dir: str | Path,
    asset_policy: AssetPolicy,
    status: str,
) -> AssetResolutionResult:
    target = Path(output_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)
    requests = _planned_requests(edit_plan)
    payload = {
        "version": ASSET_MANIFEST_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": str(status)[:80],
        "asset_policy": asset_policy,
        "requested_count": len(requests),
        "resolved_count": 0,
        "provider_call_count": 0,
        "rights_notice": RIGHTS_NOTICE,
        "requests": [
            _request_metadata(clip_index, request)
            for clip_index, request in requests
        ],
        "assets": [],
    }
    path = _write_manifest(target, payload)
    return AssetResolutionResult(paths={}, manifest_path=path, manifest=payload)


async def resolve_generated_assets(
    edit_plan: EditPlan,
    *,
    output_dir: str | Path,
    asset_policy: AssetPolicy,
    max_generated_assets_per_clip: int,
    cascade: RemoteImageCascade | None,
    size: str,
) -> AssetResolutionResult:
    target = Path(output_dir).resolve()
    target.mkdir(parents=True, exist_ok=True)
    requests = _planned_requests(edit_plan)
    if not requests:
        return write_asset_manifest(
            edit_plan,
            output_dir=target,
            asset_policy=asset_policy,
            status="no_requests",
        )
    if asset_policy != "auto":
        raise AssetResolutionError(
            "ASSET_POLICY_BLOCKED",
            "the job does not permit generated assets",
        )
    if not 0 <= int(max_generated_assets_per_clip) <= 8:
        raise AssetResolutionError(
            "ASSET_LIMIT_INVALID",
            "generated asset limit must be between 0 and 8",
        )
    by_clip: dict[int, int] = {}
    for clip_index, request in requests:
        if request.kind != "generated_image" or request.provider != "9router":
            raise AssetResolutionError(
                "ASSET_PROVIDER_UNAVAILABLE",
                "only 9Router generated images are executable in this sprint",
            )
        by_clip[clip_index] = by_clip.get(clip_index, 0) + 1
    if any(count > max_generated_assets_per_clip for count in by_clip.values()):
        raise AssetResolutionError(
            "ASSET_LIMIT_EXCEEDED",
            "the edit plan exceeds the effective generated asset cap",
        )
    if cascade is None:
        raise AssetResolutionError(
            "ASSET_PROVIDER_UNAVAILABLE",
            "the generated image provider is not configured",
        )

    created: list[Path] = []
    assets: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    paths: dict[str, Path] = {}
    provider_calls = 0
    clip_positions: dict[int, int] = {}
    try:
        for clip_index, request in requests:
            clip_positions[clip_index] = clip_positions.get(clip_index, 0) + 1
            final_prompt = build_original_image_prompt(
                request.prompt,
                orientation=request.orientation,
                index=clip_positions[clip_index] - 1,
                count=by_clip[clip_index],
            )
            provider_calls += 1
            try:
                result = await cascade.generate(final_prompt, size=size)
            except RemoteImageError as exc:
                attempts.extend({"asset_id": request.id, **item.to_dict()} for item in exc.attempts)
                raise AssetResolutionError(
                    exc.code,
                    "generated image acquisition failed",
                    attempts=attempts,
                ) from exc
            attempts.extend({"asset_id": request.id, **item.to_dict()} for item in result.attempts)
            path = target / f"asset-{request.id}.{result.extension}"
            temporary = path.with_suffix(f".{result.extension}.part")
            temporary.write_bytes(result.content)
            os.replace(temporary, path)
            created.append(path)
            paths[request.id] = path
            assets.append({
                **_request_metadata(clip_index, request),
                "filename": path.name,
                "content_type": result.content_type,
                "bytes": len(result.content),
                "sha256": hashlib.sha256(result.content).hexdigest(),
                "model": result.model,
                "final_prompt_sha256": hashlib.sha256(final_prompt.encode("utf-8")).hexdigest(),
                "safety_suffix_applied": True,
            })

        payload = {
            "version": ASSET_MANIFEST_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "resolved",
            "asset_policy": asset_policy,
            "requested_count": len(requests),
            "resolved_count": len(assets),
            "provider_call_count": provider_calls,
            "rights_notice": RIGHTS_NOTICE,
            "requests": [
                _request_metadata(clip_index, request)
                for clip_index, request in requests
            ],
            "assets": assets,
            "attempts": attempts,
        }
        manifest_path = _write_manifest(target, payload)
    except Exception:
        (target / "asset_manifest.json.part").unlink(missing_ok=True)
        (target / "asset_manifest.json").unlink(missing_ok=True)
        for path in created:
            path.unlink(missing_ok=True)
        for part in target.glob("asset-*.part"):
            part.unlink(missing_ok=True)
        raise

    return AssetResolutionResult(
        paths=paths,
        manifest_path=manifest_path,
        manifest=payload,
    )
