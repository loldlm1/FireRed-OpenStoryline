from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
import json
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

from open_storyline.mvp.catalog import (
    CatalogError,
    build_catalog_usage,
    catalog_candidate_snapshot,
    catalog_color_filter,
    creative_catalog_planning_enabled,
    load_creative_catalog,
)
from open_storyline.mvp.edit_plan import build_shadow_edit_plan
from open_storyline.mvp.shorts import ShortCandidate
from open_storyline.mvp.render import render_settings_from_config


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "creative_catalog" / "manifest.json"


class CreativeCatalogTests(unittest.TestCase):
    def test_checked_in_manifest_is_reproducible_and_valid(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "generate_creative_catalog_manifest.py"),
                "--check",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        catalog = load_creative_catalog(MANIFEST)
        self.assertEqual(catalog.version, "2026.07.1")
        self.assertGreaterEqual(len(catalog.entries), 30)
        self.assertEqual(catalog.quarantined, ())
        self.assertEqual(catalog.require("font.caption.core").font_family, "Noto Sans")
        self.assertEqual(
            catalog.require("font.emoji.monochrome").font_family,
            "Noto Emoji",
        )
        self.assertEqual(len(catalog.by_kind("style_profile")), 4)

    def test_compact_candidates_rank_matching_tags_without_paths_or_urls(self):
        catalog = load_creative_catalog(MANIFEST)
        candidates = catalog.compact_candidates(
            kinds={"style_profile", "caption_treatment"},
            tags={"bold", "social"},
            limit=4,
        )

        self.assertEqual(candidates[0]["id"], "style.bold-social")
        serialized = json.dumps(candidates)
        self.assertNotIn("creative_catalog/fonts", serialized)
        self.assertNotIn("https://", serialized)
        self.assertNotIn("sha256", serialized)

    def test_planner_snapshot_is_bounded_and_catalog_usage_is_auditable(self):
        catalog = load_creative_catalog(MANIFEST)
        snapshot = catalog_candidate_snapshot(
            catalog,
            editing_prompt="Crea un lanzamiento energetico y audaz",
        )

        self.assertEqual(snapshot["version"], "catalog_candidates.v1")
        self.assertLessEqual(len(snapshot["entries"]), 32)
        self.assertEqual(snapshot["entries"][0]["id"], "style.energetic-launch")
        self.assertIn("recipe", {entry["kind"] for entry in snapshot["entries"]})
        serialized = json.dumps(snapshot)
        self.assertNotIn("creative_catalog/fonts", serialized)
        self.assertNotIn("https://", serialized)

        plan = build_shadow_edit_plan(
            [ShortCandidate(0, 4_000, "Title", "Hook", "Reason", 0.9)],
            source_duration_ms=4_000,
        )
        usage = build_catalog_usage(catalog, plan)
        self.assertEqual(usage["version"], "creative_catalog_usage.v1")
        self.assertEqual(usage["manifest_sha256"], catalog.manifest_sha256)
        self.assertEqual(usage["clips"][0]["font_id"], "font.caption.core")
        self.assertTrue(all(entry["sha256"] for entry in usage["entries"]))

    def test_catalog_compiles_only_allowlisted_filters_and_flag_values(self):
        catalog = load_creative_catalog(MANIFEST)
        self.assertEqual(
            catalog_color_filter(catalog, "color.clean-contrast"),
            "eq=contrast=1.0600:saturation=1.0200",
        )
        self.assertEqual(
            catalog_color_filter(catalog, "color.warm-launch"),
            "colorbalance=rs=0.0400:bs=-0.0250",
        )
        with self.assertRaises(CatalogError):
            catalog_color_filter(catalog, "transition.hard-cut")

        with patch.dict(
            "os.environ",
            {"OPENSTORYLINE_CREATIVE_CATALOG_PLANNING_ENABLED": "true"},
        ):
            self.assertTrue(creative_catalog_planning_enabled())
        with patch.dict(
            "os.environ",
            {"OPENSTORYLINE_CREATIVE_CATALOG_PLANNING_ENABLED": "invalid"},
        ):
            with self.assertRaises(CatalogError):
                creative_catalog_planning_enabled()

    def test_optional_incompatible_entry_is_quarantined(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "creative_catalog"
            copytree(MANIFEST.parent, root)
            payload = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            entry = next(item for item in payload["entries"] if item["id"] == "transition.dissolve")
            entry["ffmpeg_filters"] = ["not_a_real_filter"]
            (root / "manifest.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            catalog = load_creative_catalog(root / "manifest.json")

        self.assertIsNone(catalog.get("transition.dissolve"))
        self.assertIn(
            {"id": "transition.dissolve", "code": "CATALOG_FFMPEG_FILTER_MISSING"},
            catalog.quarantined,
        )

    def test_tampered_required_font_fails_closed(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "creative_catalog"
            copytree(MANIFEST.parent, root)
            payload = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            entry = next(item for item in payload["entries"] if item["id"] == "font.caption.core")
            entry["sha256"] = "0" * 64
            (root / "manifest.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(CatalogError, "CATALOG_FILE_HASH_MISMATCH"):
                load_creative_catalog(root / "manifest.json")

    def test_renderer_accepts_the_catalog_caption_family(self):
        catalog = load_creative_catalog(MANIFEST)
        settings = render_settings_from_config(
            object(),
            caption_font_family=catalog.require("font.caption.core").font_family,
        )
        self.assertEqual(settings.caption_font_family, "Noto Sans")

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required")
    def test_enabled_xfade_transition_names_are_supported(self):
        catalog = load_creative_catalog(MANIFEST)
        transitions = [
            entry
            for entry in catalog.by_kind("transition")
            if entry.config.get("operation") not in {"hard_cut", "fade"}
        ]
        for entry in transitions:
            with self.subTest(entry=entry.id):
                result = subprocess.run(
                    [
                        "ffmpeg", "-hide_banner", "-v", "error",
                        "-f", "lavfi", "-i", "color=red:s=64x64:r=25:d=1",
                        "-f", "lavfi", "-i", "color=blue:s=64x64:r=25:d=1",
                        "-filter_complex",
                        (
                            "[0:v][1:v]xfade="
                            f"transition={entry.config['operation']}:duration=0.2:offset=0.4"
                        ),
                        "-frames:v", "2", "-f", "null", "-",
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
