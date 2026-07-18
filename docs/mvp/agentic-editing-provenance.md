# Agentic Editing Provenance

The general-purpose agentic editing roadmap uses selected design ideas from the
local reference repository at `/home/loldlm/python_projects/video-editing-skill`.
FireRed-OpenStoryline does not import that repository at runtime and does not
reuse its CLI, local-model, or file-authoritative architecture.

## Adaptation Record

| FireRed component | Reference path | Adaptation |
| --- | --- | --- |
| `src/open_storyline/mvp/preflight.py` | `scripts/edit_preflight.py` | Reimplemented the pre-render gate concept against typed MVP edit-plan contracts and PostgreSQL/job-scoped artifact boundaries. No source code was copied verbatim. |
| `src/open_storyline/mvp/scene_boundaries.py` | `scripts/scene_boundaries.py` | Reimplemented FFmpeg scene-score parsing, minimum-gap deduplication, and interval artifacts with bounded output and sanitized errors. |
| `src/open_storyline/mvp/frame_sampling.py` | `scripts/video_understanding.py` | Adapted scene-plus-uniform sampling ideas into an in-memory, byte-bounded frame manifest with stable IDs and no local detector. |
| `src/open_storyline/mvp/visual_understanding.py` | `scripts/video_understanding.py` | Replaced local YOLO/class logic with validated, general-purpose remote observations and tracklets grounded in ordered frame IDs and timestamps. |
| `src/open_storyline/mvp/compositor.py` | `scripts/smart_reframe.py` | Reimplemented subject-union portrait framing with typed visual evidence, fit/letterbox decisions, bounded crop motion, and explicit fallback evidence. |
| `src/open_storyline/mvp/edit_plan.py`, `src/open_storyline/mvp/ffmpeg_filters.py` | `scripts/pip_overlay.py`, `scripts/screen_focus.py`, `scripts/transition_bridge.py` | Adapted typed timeline-window, focus, overlay, and transition concepts into a server-generated FFmpeg filtergraph. Platform presets, arbitrary filter input, and provider-specific behavior were not copied. |
| `src/open_storyline/mvp/assets.py` | `scripts/storyboard_assets.py`, `scripts/asset_provenance.py` | Adapted request-to-timeline provenance and transactional cleanup concepts to the approved 9Router image route, job-scoped output storage, prompt hashes, rights notices, and PostgreSQL artifact registration. |

Future sprints must append materially adapted reference paths here. Provider,
platform, local-inference, and niche-specific logic must not be copied into the
remote MVP unless the plan explicitly changes those product boundaries.

## License Note

No `LICENSE`, `COPYING`, or `NOTICE` file was present at the reference repository
root or one directory below when this record was created on 2026-07-18. The
project owner authorized internal adaptation for this implementation. Keep the
result private until the reference repository's redistribution terms are
confirmed before any public release.
