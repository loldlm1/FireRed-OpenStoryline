from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
import hashlib
import json
import math
import os
import re
import subprocess


CREATIVE_CATALOG_SCHEMA_VERSION = "creative_catalog.v1"
ALLOWED_LICENSES = frozenset({"OFL-1.1", "Apache-2.0", "MIT", "CC0-1.0"})
ALLOWED_KINDS = frozenset({
    "font",
    "emoji_font",
    "transition",
    "color_treatment",
    "caption_treatment",
    "recipe",
    "style_profile",
})
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
SPANISH_GLYPHS = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    " áéíóúüñÁÉÍÓÚÜÑ¿¡.,:;!?%€$#@&()/-+"
)
COMMON_MARKETING_EMOJI = "✅✨🔥🚀💡🎯📈💬❤⚡⭐"


class CatalogError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    kind: str
    version: str
    label: str
    required: bool
    license: str
    source_url: str
    source_revision: str
    sha256: str
    file_path: Path | None
    font_family: str
    config: dict[str, Any]
    style_tags: tuple[str, ...]
    compatibility_tags: tuple[str, ...]
    fallback_id: str

    def compact(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "version": self.version,
            "style_tags": list(self.style_tags),
            "compatibility_tags": list(self.compatibility_tags),
            "fallback_id": self.fallback_id,
            "config": self.config,
        }


@dataclass(frozen=True)
class CreativeCatalog:
    version: str
    root: Path
    entries: tuple[CatalogEntry, ...]
    quarantined: tuple[dict[str, str], ...]
    ffmpeg_filters: frozenset[str]
    manifest_sha256: str

    def get(self, entry_id: str) -> CatalogEntry | None:
        return next((entry for entry in self.entries if entry.id == entry_id), None)

    def by_kind(self, kind: str) -> tuple[CatalogEntry, ...]:
        return tuple(entry for entry in self.entries if entry.kind == kind)

    def require(self, entry_id: str) -> CatalogEntry:
        entry = self.get(entry_id)
        if entry is None:
            raise CatalogError(
                "CATALOG_REQUIRED_ENTRY_MISSING",
                f"required catalog entry is unavailable: {entry_id}",
            )
        return entry

    def compact_candidates(
        self,
        *,
        kinds: Iterable[str],
        tags: Iterable[str] = (),
        limit: int = 24,
    ) -> list[dict[str, Any]]:
        allowed_kinds = {str(value) for value in kinds}
        requested_tags = {str(value) for value in tags}
        ranked = sorted(
            (
                entry
                for entry in self.entries
                if entry.kind in allowed_kinds
            ),
            key=lambda entry: (
                -len(requested_tags.intersection(entry.style_tags)),
                entry.kind,
                entry.id,
            ),
        )
        return [entry.compact() for entry in ranked[: max(1, min(int(limit), 64))]]


def catalog_manifest_path() -> Path:
    value = os.getenv(
        "OPENSTORYLINE_CREATIVE_CATALOG_PATH",
        "creative_catalog/manifest.json",
    ).strip()
    if not value:
        raise CatalogError(
            "CATALOG_CONFIG_INVALID",
            "OPENSTORYLINE_CREATIVE_CATALOG_PATH is required",
        )
    return Path(value).expanduser().resolve()


def creative_catalog_planning_enabled() -> bool:
    value = os.getenv(
        "OPENSTORYLINE_CREATIVE_CATALOG_PLANNING_ENABLED",
        "false",
    ).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    raise CatalogError(
        "CATALOG_CONFIG_INVALID",
        "OPENSTORYLINE_CREATIVE_CATALOG_PLANNING_ENABLED must be true or false",
    )


def _safe_path(root: Path, value: str, *, code: str) -> Path:
    relative = Path(str(value or ""))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise CatalogError(code, "catalog path is invalid")
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise CatalogError(code, "catalog paths may not use symlinks")
    resolved = (root / relative).resolve(strict=False)
    if root not in resolved.parents:
        raise CatalogError(code, "catalog path escapes its root")
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def catalog_config_sha256(config: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            config,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _validate_config_value(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CatalogError("CATALOG_CONFIG_INVALID", "entry config is not finite")
        return
    if isinstance(value, list):
        for item in value:
            _validate_config_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 80:
                raise CatalogError("CATALOG_CONFIG_INVALID", "entry config key is invalid")
            _validate_config_value(item)
        return
    raise CatalogError("CATALOG_CONFIG_INVALID", "entry config contains an invalid value")


def _font_charset(path: Path) -> tuple[str, set[int]]:
    try:
        result = subprocess.run(
            [
                "fc-query", "--index", "0", "--format",
                "%{family[0]}\n%{charset}\n", str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise CatalogError("CATALOG_FONT_PROBE_UNAVAILABLE", str(exc)) from exc
    if result.returncode != 0:
        raise CatalogError("CATALOG_FONT_INVALID", "fontconfig rejected the font file")
    family, _separator, charset = result.stdout.partition("\n")
    codepoints: set[int] = set()
    for token in charset.split():
        try:
            if "-" in token:
                start, end = token.split("-", 1)
                codepoints.update(range(int(start, 16), int(end, 16) + 1))
            else:
                codepoints.add(int(token, 16))
        except ValueError:
            raise CatalogError(
                "CATALOG_FONT_INVALID", "fontconfig returned an invalid charset"
            ) from None
    return family.strip(), codepoints


def probe_ffmpeg_filters() -> frozenset[str]:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise CatalogError("CATALOG_FFMPEG_UNAVAILABLE", str(exc)) from exc
    if result.returncode != 0:
        raise CatalogError(
            "CATALOG_FFMPEG_UNAVAILABLE", "FFmpeg filter probing failed"
        )
    filters = set()
    for line in result.stdout.splitlines():
        match = re.match(r"^\s*[.A-Z|]{3}\s+([a-z0-9_]+)\s", line)
        if match:
            filters.add(match.group(1))
    return frozenset(filters)


def _validate_entry(
    root: Path,
    raw: dict[str, Any],
    *,
    ffmpeg_filters: frozenset[str],
) -> CatalogEntry:
    entry_id = str(raw.get("id") or "")
    kind = str(raw.get("kind") or "")
    if not ID_PATTERN.fullmatch(entry_id):
        raise CatalogError("CATALOG_ID_INVALID", "catalog ID is invalid")
    if kind not in ALLOWED_KINDS:
        raise CatalogError("CATALOG_KIND_INVALID", f"unsupported catalog kind: {kind}")
    version = str(raw.get("version") or "")
    label = str(raw.get("label") or "")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise CatalogError("CATALOG_VERSION_INVALID", "catalog entry version is invalid")
    if not label or len(label) > 120:
        raise CatalogError("CATALOG_LABEL_INVALID", "catalog entry label is invalid")
    license_id = str(raw.get("license") or "")
    if license_id not in ALLOWED_LICENSES:
        raise CatalogError(
            "CATALOG_LICENSE_BLOCKED", f"catalog license is not allowed: {license_id}"
        )
    if not all(
        raw.get(field) is True
        for field in ("commercial_use", "modification", "redistribution")
    ):
        raise CatalogError(
            "CATALOG_RIGHTS_UNVERIFIED",
            "commercial use, modification, and redistribution must be reviewed",
        )
    source_url = str(raw.get("source_url") or "")
    if not source_url.startswith("https://") or len(source_url) > 1000:
        raise CatalogError("CATALOG_SOURCE_INVALID", "source URL must be HTTPS")
    source_revision = str(raw.get("source_revision") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}", source_revision):
        raise CatalogError("CATALOG_SOURCE_INVALID", "source revision is invalid")
    reviewed_at = str(raw.get("reviewed_at") or "")
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", reviewed_at):
        raise CatalogError("CATALOG_REVIEW_INVALID", "review date is invalid")
    review_note = str(raw.get("review_note") or "")
    if not review_note or len(review_note) > 500:
        raise CatalogError("CATALOG_REVIEW_INVALID", "review note is invalid")
    license_path = _safe_path(
        root,
        str(raw.get("license_path") or ""),
        code="CATALOG_LICENSE_PATH_INVALID",
    )
    if not license_path.is_file():
        raise CatalogError("CATALOG_LICENSE_MISSING", "catalog license file is missing")
    expected_license_hash = str(raw.get("license_sha256") or "")
    if not SHA256_PATTERN.fullmatch(expected_license_hash):
        raise CatalogError("CATALOG_HASH_INVALID", "license hash is invalid")
    if _sha256(license_path) != expected_license_hash:
        raise CatalogError("CATALOG_LICENSE_HASH_MISMATCH", "license hash does not match")
    license_text = license_path.read_text(encoding="utf-8", errors="replace")[:16_384]
    expected_license_marker = {
        "OFL-1.1": "SIL OPEN FONT LICENSE Version 1.1",
        "Apache-2.0": "Apache License",
        "MIT": "MIT License",
        "CC0-1.0": "CC0",
    }[license_id]
    if expected_license_marker not in license_text:
        raise CatalogError("CATALOG_LICENSE_INVALID", "license text does not match SPDX ID")

    file_path = None
    expected_hash = str(raw.get("sha256") or "")
    if not SHA256_PATTERN.fullmatch(expected_hash):
        raise CatalogError("CATALOG_HASH_INVALID", "entry hash is invalid")
    config = raw.get("config") or {}
    if not isinstance(config, dict):
        raise CatalogError("CATALOG_CONFIG_INVALID", "entry config must be an object")
    _validate_config_value(config)
    is_font = kind in {"font", "emoji_font"}
    if is_font != bool(raw.get("file")):
        raise CatalogError(
            "CATALOG_FILE_CONFIG_INVALID",
            "font entries require files and deterministic presets may not use files",
        )
    font_family = ""
    if raw.get("file"):
        file_path = _safe_path(
            root,
            str(raw["file"]),
            code="CATALOG_FILE_PATH_INVALID",
        )
        if not file_path.is_file():
            raise CatalogError("CATALOG_FILE_MISSING", "catalog file is missing")
        if _sha256(file_path) != expected_hash:
            raise CatalogError("CATALOG_FILE_HASH_MISMATCH", "catalog file hash does not match")
        if str(raw.get("expected_type") or "") != "ttf":
            raise CatalogError("CATALOG_FILE_TYPE_INVALID", "font type must be ttf")
        if str(raw.get("expected_type") or "") == "ttf":
            signature = file_path.read_bytes()[:4]
            if signature not in {b"\x00\x01\x00\x00", b"true"}:
                raise CatalogError("CATALOG_FILE_TYPE_INVALID", "font is not a TrueType file")
            family, codepoints = _font_charset(file_path)
            expected_family = str(raw.get("font_family") or "")
            if not expected_family or expected_family.lower() not in family.lower():
                raise CatalogError("CATALOG_FONT_FAMILY_MISMATCH", "font family does not match")
            font_family = expected_family
            glyph_sets = {str(value) for value in raw.get("glyph_sets") or []}
            required_text = ""
            if glyph_sets.intersection({"english", "spanish", "latin", "latin-ext"}):
                required_text += SPANISH_GLYPHS
            if "common_marketing_emoji" in glyph_sets:
                required_text += COMMON_MARKETING_EMOJI
            missing = sorted({ord(value) for value in required_text} - codepoints)
            if missing:
                raise CatalogError(
                    "CATALOG_FONT_GLYPHS_MISSING",
                    f"font is missing {len(missing)} required glyphs",
                )
    elif catalog_config_sha256(config) != expected_hash:
        raise CatalogError("CATALOG_CONFIG_HASH_MISMATCH", "preset hash does not match")

    requirements = {str(value) for value in raw.get("ffmpeg_filters") or []}
    missing_filters = sorted(requirements - ffmpeg_filters)
    if missing_filters:
        raise CatalogError(
            "CATALOG_FFMPEG_FILTER_MISSING",
            f"missing FFmpeg filters: {', '.join(missing_filters)}",
        )
    fallback_id = str(raw.get("fallback_id") or "")
    if fallback_id and not ID_PATTERN.fullmatch(fallback_id):
        raise CatalogError("CATALOG_FALLBACK_INVALID", "fallback ID is invalid")
    if fallback_id == entry_id:
        raise CatalogError("CATALOG_FALLBACK_INVALID", "catalog entry cannot fallback to itself")
    return CatalogEntry(
        id=entry_id,
        kind=kind,
        version=version,
        label=label,
        required=bool(raw.get("required")),
        license=license_id,
        source_url=source_url,
        source_revision=source_revision,
        sha256=expected_hash,
        file_path=file_path,
        font_family=font_family,
        config=dict(config),
        style_tags=tuple(str(value)[:40] for value in raw.get("style_tags") or []),
        compatibility_tags=tuple(
            str(value)[:40] for value in raw.get("compatibility_tags") or []
        ),
        fallback_id=fallback_id,
    )


def load_creative_catalog(
    manifest_path: str | Path | None = None,
    *,
    ffmpeg_filters: Iterable[str] | None = None,
) -> CreativeCatalog:
    path = Path(manifest_path or catalog_manifest_path()).resolve()
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise CatalogError("CATALOG_MANIFEST_UNAVAILABLE", "catalog manifest is unavailable")
    try:
        manifest_bytes = path.read_bytes()
        payload = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise CatalogError("CATALOG_MANIFEST_INVALID", "catalog manifest is invalid") from None
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != CREATIVE_CATALOG_SCHEMA_VERSION
        or not isinstance(payload.get("entries"), list)
    ):
        raise CatalogError("CATALOG_MANIFEST_INVALID", "catalog schema is invalid")
    if len(payload["entries"]) > 128:
        raise CatalogError("CATALOG_MANIFEST_INVALID", "catalog entry limit exceeded")
    catalog_version = str(payload.get("catalog_version") or "")
    if not re.fullmatch(r"20\d{2}\.\d{2}\.[0-9]+", catalog_version):
        raise CatalogError("CATALOG_MANIFEST_INVALID", "catalog version is invalid")
    filters = (
        frozenset(str(value) for value in ffmpeg_filters)
        if ffmpeg_filters is not None
        else probe_ffmpeg_filters()
    )
    root = path.parent
    entries: list[CatalogEntry] = []
    quarantined: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in payload["entries"]:
        if not isinstance(raw, dict):
            raise CatalogError("CATALOG_MANIFEST_INVALID", "catalog entry is invalid")
        entry_id = str(raw.get("id") or "")[:80]
        if entry_id in seen:
            raise CatalogError("CATALOG_ID_DUPLICATE", f"duplicate catalog ID: {entry_id}")
        seen.add(entry_id)
        try:
            entries.append(_validate_entry(root, raw, ffmpeg_filters=filters))
        except CatalogError as exc:
            if raw.get("required") is True:
                raise
            quarantined.append({"id": entry_id, "code": exc.code})
    available_ids = {entry.id for entry in entries}
    required_ids = {
        str(value) for value in payload.get("required_ids") or []
    }
    missing_required = sorted(required_ids - available_ids)
    if missing_required:
        raise CatalogError(
            "CATALOG_REQUIRED_ENTRY_MISSING",
            f"missing required entries: {', '.join(missing_required)}",
        )
    final_entries = list(entries)
    while True:
        available_ids = {entry.id for entry in final_entries}
        rejected_ids: set[str] = set()
        for entry in final_entries:
            references = {
                str(value)
                for value in entry.config.get("catalog_ids") or []
            }
            if entry.fallback_id:
                references.add(entry.fallback_id)
            missing = sorted(references - available_ids)
            if not missing:
                continue
            if entry.required:
                raise CatalogError(
                    "CATALOG_REFERENCE_MISSING",
                    f"{entry.id} references missing entries: {', '.join(missing)}",
                )
            rejected_ids.add(entry.id)
            quarantined.append({"id": entry.id, "code": "CATALOG_REFERENCE_MISSING"})
        if not rejected_ids:
            break
        final_entries = [entry for entry in final_entries if entry.id not in rejected_ids]
    return CreativeCatalog(
        version=catalog_version,
        root=root,
        entries=tuple(final_entries),
        quarantined=tuple(quarantined),
        ffmpeg_filters=filters,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )


_TAG_PATTERNS = (
    (r"\b(?:bold|audaz|impactante|gancho|hook)\b", "bold"),
    (r"\b(?:clean|limpio|minimal|minimalista)\b", "clean"),
    (r"\b(?:product|producto|demo|demostracion)\b", "product"),
    (r"\b(?:launch|lanzamiento|release|estreno)\b", "launch"),
    (r"\b(?:energetic|energetico|dinamico|dynamic)\b", "energetic"),
    (r"\b(?:cinematic|cinematografico|cinematica)\b", "cinematic"),
    (r"\b(?:editorial|elegant|elegante|premium)\b", "editorial"),
)


def catalog_candidate_snapshot(
    catalog: CreativeCatalog,
    *,
    editing_prompt: str,
    aspect_ratio: str = "9:16",
) -> dict[str, Any]:
    text = str(editing_prompt or "").lower()
    tags = {"marketing", "portrait" if aspect_ratio == "9:16" else "landscape"}
    tags.update(
        tag
        for pattern, tag in _TAG_PATTERNS
        if re.search(pattern, text, re.IGNORECASE)
    )
    entries: list[dict[str, Any]] = []
    for kind, limit in (
        ("style_profile", 4),
        ("caption_treatment", 5),
        ("color_treatment", 5),
        ("transition", 10),
        ("recipe", 6),
    ):
        entries.extend(catalog.compact_candidates(
            kinds={kind},
            tags=tags,
            limit=limit,
        ))
    return {
        "version": "catalog_candidates.v1",
        "catalog_version": catalog.version,
        "manifest_sha256": catalog.manifest_sha256,
        "aspect_ratio": aspect_ratio,
        "requested_tags": sorted(tags),
        "entries": entries,
    }


def catalog_transition_presets(catalog: CreativeCatalog) -> dict[str, dict[str, Any]]:
    return {
        entry.id: dict(entry.config)
        for entry in catalog.by_kind("transition")
    }


def catalog_color_filter(catalog: CreativeCatalog, entry_id: str) -> str:
    entry = catalog.require(entry_id)
    if entry.kind != "color_treatment":
        raise CatalogError("CATALOG_KIND_INVALID", "catalog color treatment is invalid")
    config = entry.config
    filter_name = str(config.get("filter") or "")
    if filter_name == "eq":
        contrast = float(config.get("contrast") or 1)
        saturation = float(config.get("saturation") or 1)
        if not 0.5 <= contrast <= 1.5 or not 0 <= saturation <= 2:
            raise CatalogError("CATALOG_CONFIG_INVALID", "catalog EQ values are invalid")
        return f"eq=contrast={contrast:.4f}:saturation={saturation:.4f}"
    if filter_name == "colorbalance":
        red = float(config.get("rs") or 0)
        blue = float(config.get("bs") or 0)
        if not -0.25 <= red <= 0.25 or not -0.25 <= blue <= 0.25:
            raise CatalogError("CATALOG_CONFIG_INVALID", "catalog color balance is invalid")
        return f"colorbalance=rs={red:.4f}:bs={blue:.4f}"
    if filter_name == "curves" and config.get("preset") in {
        "lighter", "darker", "increase_contrast", "linear_contrast",
    }:
        return f"curves=preset={config['preset']}"
    raise CatalogError("CATALOG_CONFIG_INVALID", "catalog color filter is unsupported")


def catalog_caption_font(catalog: CreativeCatalog, caption_treatment_id: str) -> str:
    treatment = catalog.require(caption_treatment_id)
    if treatment.kind != "caption_treatment":
        raise CatalogError("CATALOG_KIND_INVALID", "caption treatment is invalid")
    font_id = str(treatment.config.get("font_id") or "")
    font = catalog.require(font_id)
    if font.kind != "font" or not font.font_family:
        raise CatalogError("CATALOG_KIND_INVALID", "caption font is invalid")
    return font.font_family


def build_catalog_usage(catalog: CreativeCatalog, plan: Any) -> dict[str, Any]:
    clips = []
    all_ids: set[str] = {"font.emoji.monochrome"}
    for clip in plan.clips:
        selection = clip.catalog_selection
        selected_ids = {
            selection.style_profile_id,
            selection.caption_treatment_id,
            selection.color_treatment_id,
            *selection.recipe_ids,
        }
        transition_ids = {
            segment.transition_in.catalog_id
            for segment in clip.segments
            if segment.transition_in.catalog_id
        }
        selected_ids.discard("")
        caption_id = selection.caption_treatment_id or "caption.clean"
        caption = catalog.require(caption_id)
        font_id = str(caption.config.get("font_id") or "font.caption.core")
        selected_ids.add(caption_id)
        selected_ids.add(font_id)
        selected_ids.update(transition_ids)
        all_ids.update(selected_ids)
        clips.append({
            "clip_index": clip.clip_index,
            "style_profile_id": selection.style_profile_id,
            "caption_treatment_id": caption_id,
            "color_treatment_id": selection.color_treatment_id,
            "recipe_ids": list(selection.recipe_ids),
            "transition_ids": sorted(transition_ids),
            "font_id": font_id,
        })
    entries = []
    for entry_id in sorted(all_ids):
        entry = catalog.require(entry_id)
        entries.append({
            "id": entry.id,
            "kind": entry.kind,
            "version": entry.version,
            "sha256": entry.sha256,
            "license": entry.license,
            "source_revision": entry.source_revision,
        })
    return {
        "version": "creative_catalog_usage.v1",
        "catalog_version": catalog.version,
        "manifest_sha256": catalog.manifest_sha256,
        "entries": entries,
        "clips": clips,
    }


def main() -> int:
    catalog = load_creative_catalog()
    print(json.dumps({
        "catalog_version": catalog.version,
        "manifest_sha256": catalog.manifest_sha256,
        "entry_count": len(catalog.entries),
        "quarantined_count": len(catalog.quarantined),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
