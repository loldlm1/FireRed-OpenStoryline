from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import os
import time

from open_storyline.utils.remote_image import RemoteImageCascade


ORIGINALITY_SUFFIX = (
    "Create a new composition. Do not deliberately reproduce a named artist's style, "
    "copyrighted characters, celebrity likenesses, recognizable logos, trademarks, "
    "signatures, watermarks, or text."
)

RIGHTS_NOTICE = (
    "AI-generated output is not automatically copyright-free or legally rights-cleared. "
    "Review every asset before publication."
)
MAX_FINAL_IMAGE_PROMPT_LENGTH = 8000


@dataclass(frozen=True)
class GeneratedMediaBatch:
    paths: list[Path]
    models: list[str]
    manifest_path: Path


def build_original_image_prompt(prompt: str, *, orientation: str, index: int, count: int) -> str:
    clean_prompt = " ".join(str(prompt or "").split()).strip()
    if not clean_prompt:
        raise ValueError("image prompt is required")
    composition = "vertical portrait" if orientation == "portrait" else "horizontal landscape"
    final_prompt = (
        f"{clean_prompt}\n"
        f"Composition: {composition}. Variation {index + 1} of {count}.\n"
        f"{ORIGINALITY_SUFFIX}"
    )
    if len(final_prompt) > MAX_FINAL_IMAGE_PROMPT_LENGTH:
        raise ValueError("image prompt is too long after applying required safety guidance")
    return final_prompt


async def generate_remote_media(
    cascade: RemoteImageCascade,
    *,
    media_dir: str | Path,
    prompt: str,
    count: int,
    orientation: str,
    size: str,
) -> GeneratedMediaBatch:
    requested_count = int(count)
    if not 1 <= requested_count <= 10:
        raise ValueError("generated image count must be between 1 and 10")
    if orientation not in {"portrait", "landscape"}:
        raise ValueError("orientation must be portrait or landscape")

    target_dir = Path(media_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    batch_id = time.time_ns()
    created_paths: list[Path] = []
    model_ids: list[str] = []
    assets: list[dict[str, Any]] = []
    manifest_path = target_dir / f"generated_images_{batch_id}.json"
    manifest_tmp = manifest_path.with_suffix(".json.part")

    try:
        for index in range(requested_count):
            final_prompt = build_original_image_prompt(
                prompt,
                orientation=orientation,
                index=index,
                count=requested_count,
            )
            result = await cascade.generate(final_prompt, size=size)
            out_path = target_dir / f"generated_image_{batch_id}_{index}.{result.extension}"
            temp_path = out_path.with_suffix(f".{result.extension}.part")
            temp_path.write_bytes(result.content)
            os.replace(temp_path, out_path)
            created_paths.append(out_path)
            model_ids.append(result.model)
            assets.append({
                "filename": out_path.name,
                "sha256": hashlib.sha256(result.content).hexdigest(),
                "model": result.model,
                "prompt_sha256": hashlib.sha256(final_prompt.encode("utf-8")).hexdigest(),
                "size": size,
                "safety_suffix_applied": True,
            })

        manifest = {
            "version": 1,
            "source": "9router-generated",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "rights_notice": RIGHTS_NOTICE,
            "assets": assets,
        }
        manifest_tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(manifest_tmp, manifest_path)
    except Exception:
        manifest_tmp.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        for path in created_paths:
            path.unlink(missing_ok=True)
        for part in target_dir.glob(f"generated_image_{batch_id}_*.part"):
            part.unlink(missing_ok=True)
        raise

    return GeneratedMediaBatch(paths=created_paths, models=model_ids, manifest_path=manifest_path)
