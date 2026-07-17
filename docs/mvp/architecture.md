# Remote-only video MVP

This fork adds an opt-in MVP for turning one source video into several social
clips. The MVP is deliberately CPU-first and does not run local AI models.

## Non-negotiable runtime policy

- Text planning and frame understanding use `cx/gpt-5.6-sol` through Codex
  OAuth in 9Router.
- Speech-to-text uses only direct Mistral `voxtral-mini-2602`. FireRed sends
  compressed audio to the fixed official transcription endpoint and requires
  finite, non-empty segment `start`/`end` values.
- Generated images in the full-agent `SearchMedia` path use only
  `cx/gpt-5.5-image` through Codex OAuth in 9Router.
- There are no provider, model, or local-inference fallbacks in these layers.
- FFmpeg is allowed because it performs deterministic media processing rather
  than model inference.
- If a selected provider fails, the job fails closed and persists only
  sanitized attempt metadata.

The original OpenStoryline workflow remains available. The new MVP path is
isolated so upstream behavior can continue to be merged into this fork.

## Data flow

1. The browser uploads a source video and an editing prompt.
2. The server persists a durable job and extracts compressed mono audio with
   FFmpeg.
3. Direct Mistral Voxtral transcribes the audio with segment timestamps.
4. `cx/gpt-5.6-sol` receives the transcript and sampled frames through
   9Router and returns a structured clip plan.
5. The server validates duration, bounds, overlap, and output count.
6. FFmpeg renders vertical clips and subtitles on CPU.
7. The browser downloads individual clips, the manifest, or a ZIP bundle.

## Default remote services

| Purpose | Model | Fallbacks |
| --- | --- | --- |
| Planning and vision | `cx/gpt-5.6-sol` | none |
| Speech-to-text | `voxtral-mini-2602` | key-only, same model |
| Full-agent generated images | `cx/gpt-5.5-image` | none |
| Rendering | FFmpeg on CPU | none |

9Router is intentionally not part of the STT path. Its existing user, port
`20128`, launch command, database, and manual process remain unchanged while it
serves Codex text, vision, and image inference.

Before deployment, `scripts/qa_ninerouter.py --strict-models` validates health,
endpoint-key behavior, the exact Codex catalogs, SSH, and Docker. With
`--live-inference`, it validates structured text, vision input, and decodable
image bytes without persisting provider output. Direct-Mistral validation is a
separate release gate so credentials never cross provider boundaries.

`bin/kamal-mvp` enforces the live provider gates before `setup`, `deploy`, or
`redeploy`. The Docker build context is allowlisted by
`.dockerignore`, so local env files, Kamal secrets, outputs, model resources,
and the development venv never enter the remote-image build context.

## Persistence and security

- Every job owns a directory under `outputs/mvp_jobs/<job_id>`.
- State changes are written atomically to `job.json`.
- Inputs and outputs are served only through validated job-scoped paths.
- The 9Router endpoint key and direct `MISTRAL_API_KEYS` key ring are delivered
  through Kamal secrets and never written to job state, logs, manifests, or Git.
- Error bodies are truncated and sanitized before persistence.
- Interrupted queued or running jobs are recovered when the process restarts.
- Failed jobs expose a sanitized `failure.json` with the stage and selected
  model attempt; artifacts can also be downloaded as one ZIP bundle.
- Kamal deploys the remote-only image, proxy, and persistent output volume.
