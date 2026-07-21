#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import hashlib
import json
import sys


ROOT = Path(__file__).resolve().parents[1]
CATALOG_ROOT = ROOT / "creative_catalog"
MANIFEST_PATH = CATALOG_ROOT / "manifest.json"
UPSTREAM_REVISION = "2f6daa88e1e71320a6fe71cc91ecbfc018928737"
REVIEWED_AT = "2026-07-21"
APACHE_LICENSE = "licenses/Apache-2.0.txt"
PROJECT_SOURCE = "https://github.com/loldlm1/FireRed-OpenStoryline"


def sha256_file(relative_path: str) -> str:
    digest = hashlib.sha256()
    with (CATALOG_ROOT / relative_path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_config(config: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            config,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def font_entry(
    *,
    entry_id: str,
    label: str,
    filename: str,
    source_filename: str | None = None,
    family: str,
    upstream_family: str,
    license_filename: str,
    tags: list[str],
    kind: str = "font",
    required: bool = False,
    glyph_sets: list[str] | None = None,
    fallback_id: str = "font.caption.core",
) -> dict[str, Any]:
    file_path = f"fonts/{filename}"
    license_path = f"licenses/{license_filename}"
    source_name = (source_filename or filename).replace("[", "%5B").replace("]", "%5D")
    return {
        "id": entry_id,
        "kind": kind,
        "version": "1.0.0",
        "label": label,
        "required": required,
        "license": "OFL-1.1",
        "license_path": license_path,
        "license_sha256": sha256_file(license_path),
        "file": file_path,
        "sha256": sha256_file(file_path),
        "expected_type": "ttf",
        "font_family": family,
        "glyph_sets": glyph_sets or ["english", "spanish"],
        "source_url": (
            "https://raw.githubusercontent.com/google/fonts/"
            f"{UPSTREAM_REVISION}/ofl/{upstream_family}/{source_name}"
        ),
        "source_revision": UPSTREAM_REVISION,
        "commercial_use": True,
        "modification": True,
        "redistribution": True,
        "attribution": "",
        "ffmpeg_filters": ["ass"],
        "style_tags": tags,
        "compatibility_tags": ["portrait", "landscape", "spanish", "english"],
        "fallback_id": fallback_id,
        "reviewed_at": REVIEWED_AT,
        "review_note": "Pinned Google Fonts file and matching family license reviewed for bundled commercial marketing use.",
        "config": {"font_family": family},
    }


def preset_entry(
    *,
    entry_id: str,
    kind: str,
    label: str,
    config: dict[str, Any],
    filters: list[str],
    tags: list[str],
    compatibility: list[str] | None = None,
    fallback_id: str = "",
    required: bool = False,
) -> dict[str, Any]:
    return {
        "id": entry_id,
        "kind": kind,
        "version": "1.0.0",
        "label": label,
        "required": required,
        "license": "Apache-2.0",
        "license_path": APACHE_LICENSE,
        "license_sha256": sha256_file(APACHE_LICENSE),
        "sha256": sha256_config(config),
        "source_url": PROJECT_SOURCE,
        "source_revision": "creative-catalog-v1",
        "commercial_use": True,
        "modification": True,
        "redistribution": True,
        "attribution": "",
        "ffmpeg_filters": filters,
        "style_tags": tags,
        "compatibility_tags": compatibility or ["portrait", "landscape"],
        "fallback_id": fallback_id,
        "reviewed_at": REVIEWED_AT,
        "review_note": "Project-native deterministic FFmpeg recipe; no downloaded template or runtime asset.",
        "config": config,
    }


def build_manifest() -> dict[str, Any]:
    entries: list[dict[str, Any]] = [
        font_entry(
            entry_id="font.caption.core",
            label="Noto Sans Core Captions",
            filename="NotoSans-wdth-wght.ttf",
            source_filename="NotoSans[wdth,wght].ttf",
            family="Noto Sans",
            upstream_family="notosans",
            license_filename="OFL-NotoSans-1.1.txt",
            tags=["clean", "caption", "product", "accessible"],
            required=True,
            fallback_id="",
        ),
        font_entry(
            entry_id="font.hook.archivo-black",
            label="Archivo Black Hooks",
            filename="ArchivoBlack-Regular.ttf",
            family="Archivo Black",
            upstream_family="archivoblack",
            license_filename="OFL-ArchivoBlack-1.1.txt",
            tags=["bold", "hook", "social"],
        ),
        font_entry(
            entry_id="font.headline.barlow-condensed",
            label="Barlow Condensed Headlines",
            filename="BarlowCondensed-SemiBold.ttf",
            family="Barlow Condensed",
            upstream_family="barlowcondensed",
            license_filename="OFL-Barlow-1.1.txt",
            tags=["compact", "headline", "launch"],
        ),
        font_entry(
            entry_id="font.editorial.dm-serif",
            label="DM Serif Display Editorial",
            filename="DMSerifDisplay-Regular.ttf",
            family="DM Serif Display",
            upstream_family="dmserifdisplay",
            license_filename="OFL-DMSerifDisplay-1.1.txt",
            tags=["editorial", "cinematic", "premium"],
        ),
        font_entry(
            entry_id="font.emoji.monochrome",
            kind="emoji_font",
            label="Noto Emoji Monochrome",
            filename="NotoEmoji-wght.ttf",
            source_filename="NotoEmoji[wght].ttf",
            family="Noto Emoji",
            upstream_family="notoemoji",
            license_filename="OFL-NotoEmoji-1.1.txt",
            tags=["emoji", "symbol", "marketing"],
            required=True,
            glyph_sets=["common_marketing_emoji"],
        ),
    ]

    transitions = [
        ("hard-cut", "Hard Cut", "hard_cut", "black", [], ""),
        ("fade-black", "Fade Through Black", "fade", "black", ["fade"], "transition.hard-cut"),
        ("fade-white", "Fade Through White", "fade", "white", ["fade"], "transition.fade-black"),
        ("crossfade", "Soft Crossfade", "fade", "black", ["xfade"], "transition.fade-black"),
        ("wipe-left", "Wipe Left", "wipeleft", "black", ["xfade"], "transition.crossfade"),
        ("wipe-right", "Wipe Right", "wiperight", "black", ["xfade"], "transition.crossfade"),
        ("slide-left", "Slide Left", "slideleft", "black", ["xfade"], "transition.crossfade"),
        ("slide-right", "Slide Right", "slideright", "black", ["xfade"], "transition.crossfade"),
        ("circle-open", "Circle Open", "circleopen", "black", ["xfade"], "transition.crossfade"),
        ("dissolve", "Dissolve", "dissolve", "black", ["xfade"], "transition.crossfade"),
    ]
    for slug, label, name, color, filters, fallback in transitions:
        entries.append(preset_entry(
            entry_id=f"transition.{slug}",
            kind="transition",
            label=label,
            config={
                "operation": name,
                "color": color,
                "duration_ms": 220 if name != "hard_cut" else 0,
            },
            filters=filters,
            tags=["transition", "safe" if name in {"hard_cut", "fade"} else "energetic"],
            fallback_id=fallback,
            required=name in {"hard_cut", "fade"},
        ))

    color_presets = [
        ("clean-contrast", "Clean Contrast", {"filter": "eq", "contrast": 1.06, "saturation": 1.02}, ["eq"], ["clean", "product"]),
        ("warm-launch", "Warm Launch", {"filter": "colorbalance", "rs": 0.04, "bs": -0.025}, ["colorbalance"], ["warm", "launch"]),
        ("cool-product", "Cool Product", {"filter": "colorbalance", "rs": -0.02, "bs": 0.035}, ["colorbalance"], ["cool", "product"]),
        ("cinematic-soft", "Cinematic Soft", {"filter": "curves", "preset": "lighter"}, ["curves"], ["cinematic", "editorial"]),
        ("vivid-social", "Vivid Social", {"filter": "eq", "contrast": 1.08, "saturation": 1.12}, ["eq"], ["bold", "social"]),
    ]
    for slug, label, config, filters, tags in color_presets:
        entries.append(preset_entry(
            entry_id=f"color.{slug}",
            kind="color_treatment",
            label=label,
            config=config,
            filters=filters,
            tags=tags,
            fallback_id="color.clean-contrast" if slug != "clean-contrast" else "",
            required=slug == "clean-contrast",
        ))

    captions = [
        ("clean", "Clean Captions", "font.caption.core", "#FFFFFF", "#111111", 2, ["clean", "caption"]),
        ("bold-hook", "Bold Hook Captions", "font.hook.archivo-black", "#FFFFFF", "#D93A2F", 3, ["bold", "hook"]),
        ("compact", "Compact Headlines", "font.headline.barlow-condensed", "#FFFFFF", "#111111", 2, ["compact", "launch"]),
        ("editorial", "Editorial Accent", "font.editorial.dm-serif", "#FFF8E7", "#111111", 2, ["editorial", "cinematic"]),
        ("high-contrast", "High Contrast Captions", "font.caption.core", "#FFFFFF", "#000000", 4, ["accessible", "contrast"]),
    ]
    for slug, label, font_id, foreground, background, outline, tags in captions:
        entries.append(preset_entry(
            entry_id=f"caption.{slug}",
            kind="caption_treatment",
            label=label,
            config={
                "catalog_ids": [font_id, "font.emoji.monochrome"],
                "font_id": font_id,
                "foreground": foreground,
                "background": background,
                "outline": outline,
                "safe_zone": "footer",
                "max_lines": 2,
            },
            filters=["ass"],
            tags=tags,
            fallback_id="caption.clean" if slug != "clean" else "",
            required=slug == "clean",
        ))

    recipes = [
        ("blur-background", "Content-Preserving Blur Background", {"operation": "blur_background", "strength": 18}, ["scale", "gblur", "overlay"], ["safe", "portrait"]),
        ("punch-in", "Bounded Punch In", {"operation": "punch_in", "scale": 1.08, "duration_ms": 420}, ["scale", "crop"], ["energetic", "hook"]),
        ("slow-zoom", "Slow Product Zoom", {"operation": "slow_zoom", "scale": 1.05}, ["zoompan"], ["product", "motion"]),
        ("vignette", "Restrained Vignette", {"operation": "vignette", "angle": 0.45}, ["vignette"], ["cinematic", "editorial"]),
        ("highlight-bar", "Caption Highlight Bar", {"operation": "highlight_bar", "opacity": 0.82}, ["drawtext"], ["caption", "bold"]),
        ("source-cutaway", "Source-Native Cutaway", {"operation": "source_cutaway", "maximum_ms": 1200}, ["trim", "setpts", "concat"], ["source", "safe"]),
    ]
    for slug, label, config, filters, tags in recipes:
        entries.append(preset_entry(
            entry_id=f"recipe.{slug}",
            kind="recipe",
            label=label,
            config=config,
            filters=filters,
            tags=tags,
            fallback_id="recipe.blur-background" if slug != "blur-background" else "",
            required=slug == "blur-background",
        ))

    profiles = [
        ("bold-social", "Bold Social", ["font.hook.archivo-black", "caption.bold-hook", "transition.slide-left", "color.vivid-social", "recipe.punch-in"], ["social", "bold", "marketing"], 3),
        ("clean-product", "Clean Product", ["font.caption.core", "caption.clean", "transition.crossfade", "color.clean-contrast", "recipe.slow-zoom"], ["product", "clean", "marketing"], 2),
        ("energetic-launch", "Energetic Launch", ["font.headline.barlow-condensed", "caption.compact", "transition.wipe-left", "color.warm-launch", "recipe.highlight-bar"], ["launch", "energetic", "marketing"], 4),
        ("restrained-cinematic", "Restrained Cinematic", ["font.editorial.dm-serif", "caption.editorial", "transition.fade-black", "color.cinematic-soft", "recipe.vignette"], ["cinematic", "editorial", "premium"], 2),
    ]
    for slug, label, catalog_ids, tags, density in profiles:
        entries.append(preset_entry(
            entry_id=f"style.{slug}",
            kind="style_profile",
            label=label,
            config={
                "catalog_ids": catalog_ids,
                "aspect_ratios": ["9:16", "1:1", "16:9"],
                "niches": ["marketing", "product", "service"],
                "motion_intensity": density,
                "maximum_effect_density": density,
                "maximum_overlay_count": 2,
                "trend_rationale": "Current short-form hierarchy using readable type, restrained native motion, and deterministic source-first treatments.",
            },
            filters=[],
            tags=tags,
            fallback_id="style.clean-product" if slug != "clean-product" else "",
            required=slug == "clean-product",
        ))

    return {
        "schema_version": "creative_catalog.v1",
        "catalog_version": "2026.07.1",
        "generated_by": "scripts/generate_creative_catalog_manifest.py",
        "upstream_revision": UPSTREAM_REVISION,
        "required_ids": [
            "font.caption.core",
            "font.emoji.monochrome",
            "transition.hard-cut",
            "transition.fade-black",
            "color.clean-contrast",
            "caption.clean",
            "recipe.blur-background",
            "style.clean-product",
        ],
        "entries": entries,
    }


def render_manifest() -> str:
    return json.dumps(build_manifest(), ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = render_manifest()
    if args.check:
        if not MANIFEST_PATH.is_file() or MANIFEST_PATH.read_text(encoding="utf-8") != rendered:
            print("creative catalog manifest is stale", file=sys.stderr)
            return 1
        return 0
    MANIFEST_PATH.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
