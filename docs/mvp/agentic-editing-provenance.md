# Agentic Editing Provenance

The general-purpose agentic editing roadmap uses selected design ideas from the
local reference repository at `/home/loldlm/python_projects/video-editing-skill`.
FireRed-OpenStoryline does not import that repository at runtime and does not
reuse its CLI, local-model, or file-authoritative architecture.

## Adaptation Record

| FireRed component | Reference path | Adaptation |
| --- | --- | --- |
| `src/open_storyline/mvp/preflight.py` | `scripts/edit_preflight.py` | Reimplemented the pre-render gate concept against typed MVP edit-plan contracts and PostgreSQL/job-scoped artifact boundaries. No source code was copied verbatim. |

Future sprints must append materially adapted reference paths here. Provider,
platform, local-inference, and niche-specific logic must not be copied into the
remote MVP unless the plan explicitly changes those product boundaries.

## License Note

No `LICENSE`, `COPYING`, or `NOTICE` file was present at the reference repository
root or one directory below when this record was created on 2026-07-18. The
project owner authorized internal adaptation for this implementation. Keep the
result private until the reference repository's redistribution terms are
confirmed before any public release.
