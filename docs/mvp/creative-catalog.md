# Creative Catalog

The remote MVP ships a small, immutable creative catalog inside the production
image. It is designed to improve creative range without making rendering depend
on runtime downloads, paid template marketplaces, or provider availability.

## Runtime contract

- `OPENSTORYLINE_CREATIVE_CATALOG_PATH` points to the checked-in
  `creative_catalog/manifest.json`; production uses
  `/app/creative_catalog/manifest.json`.
- `OPENSTORYLINE_CREATIVE_CATALOG_PLANNING_ENABLED=false` keeps catalog-aware
  model planning behind an independent canary flag. The renderer may still use
  the verified core caption font while this flag is off.
- Startup validates the manifest schema, paths, hashes, matching license files,
  required Spanish/English and marketing-emoji glyphs, and required FFmpeg
  filters. A missing or invalid required entry prevents startup.
- Invalid optional entries are quarantined and excluded from planner candidates.
- The Docker build installs the five bundled fonts into the image, refreshes the
  fontconfig cache, checks that the manifest is reproducible, and validates the
  catalog without network access.
- The core caption renderer uses `font.caption.core` (`Noto Sans`). Common
  marketing emoji are available through the installed monochrome
  `font.emoji.monochrome` fallback.

The initial catalog contains five OFL font families, ten native transition
presets, five color treatments, five caption treatments, six deterministic
recipes, and four generic style profiles. Profiles describe combinations of
stable catalog IDs; they are not copies of branded or marketplace templates.

When planning is enabled, the server sends at most 32 compact candidates chosen
from the prompt tone and target aspect ratio. The structured edit-plan contract
accepts only those IDs and rejects arbitrary paths, URLs, font names, filter
expressions, transition names, and style-inconsistent selections. Rendering
resolves the selected IDs server-side and records their versions, hashes, and
licenses in `creative_catalog_usage.json`.

## Provenance and licensing

Each bundled font and its family-specific OFL text are pinned to Google Fonts
revision `2f6daa88e1e71320a6fe71cc91ecbfc018928737`. The manifest records the
upstream URL and revision, local SHA-256, matching license path and SHA-256,
commercial-use/modification/redistribution review flags, and the review date.

Project-native FFmpeg recipes contain functional filter parameters rather than
downloaded creative files. Their manifest entries use the included Apache-2.0
text and explicitly identify themselves as project-native deterministic
recipes. This engineering review keeps third-party provenance auditable; it is
not a substitute for legal advice for a specific campaign or jurisdiction.

Paid packs, editorial-only resources, non-commercial licenses, Premiere/After
Effects/MOGRT/DaVinci templates, and runtime marketplace downloads are not part
of the catalog.

## Reproducible checks

Regenerate the manifest only after intentionally changing catalog definitions
or bundled files:

```bash
.venv/bin/python scripts/generate_creative_catalog_manifest.py
```

Verify that the checked-in manifest and installed runtime capabilities match:

```bash
.venv/bin/python scripts/generate_creative_catalog_manifest.py --check
PYTHONPATH=src .venv/bin/python -m open_storyline.mvp.catalog
PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_catalog.py -v
```

The application never fetches these resources at startup or while processing a
job. Any catalog update is a reviewed source change followed by a new image
build and normal canary/rollback flow.
