# Remote-only video MVP

This fork adds an opt-in MVP for turning one source video into several social
clips. The MVP is deliberately CPU-first and does not run local AI models.

## Non-negotiable runtime policy

- Text planning and frame understanding use `cx/gpt-5.6-sol` through Codex
  OAuth in 9Router.
- Speech-to-text uses only `mistral/voxtral-mini-2602` through 9Router. The
  Mistral adapter requests segment timestamps because FireRed requires finite,
  non-empty `start`/`end` values.
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
3. The single configured Mistral STT model transcribes the audio with segment
   timestamps through 9Router.
4. `cx/gpt-5.6-sol` receives the transcript and sampled frames through
   9Router and returns a structured clip plan.
5. The server validates duration, bounds, overlap, and output count.
6. FFmpeg renders vertical clips and subtitles on CPU.
7. The browser downloads individual clips, the manifest, or a ZIP bundle.

## Default remote services

| Purpose | Model | Fallbacks |
| --- | --- | --- |
| Planning and vision | `cx/gpt-5.6-sol` | none |
| Speech-to-text | `mistral/voxtral-mini-2602` | none |
| Full-agent generated images | `cx/gpt-5.5-image` | none |
| Rendering | FFmpeg on CPU | none |

The current installed 9Router `0.5.35` does not publish Mistral in its STT
catalog. The versioned adapter patch in
`patches/9router/0.5.35-mistral-stt.patch` is prepared and tested offline;
live activation is a maintenance-window operation and is intentionally not
performed while the current manual 9Router process serves Codex inference.

Before deployment, `scripts/qa_ninerouter.py --strict-models` validates health,
endpoint-key behavior, exact catalogs, SSH, and Docker. With
`--live-inference`, it also validates structured text, vision input, decodable
image bytes, and timestamped STT without persisting provider output. Any red
catalog or contract keeps the Kamal canary blocked.

`bin/kamal-mvp` enforces the live form of that gate before `setup`, `deploy`,
or `redeploy` and requires an external non-private fixture through
`NINEROUTER_QA_STT_AUDIO`. The Docker build context is allowlisted by
`.dockerignore`, so local env files, Kamal secrets, outputs, model resources,
and the development venv never enter the remote-image build context.

## Persistence and security

- Every job owns a directory under `outputs/mvp_jobs/<job_id>`.
- State changes are written atomically to `job.json`.
- Inputs and outputs are served only through validated job-scoped paths.
- Provider keys are read from 9Router/Kamal secrets and never written to job
  state, logs, manifests, or Git.
- Error bodies are truncated and sanitized before persistence.
- Interrupted queued or running jobs are recovered when the process restarts.
- Failed jobs expose a sanitized `failure.json` with the stage and selected
  model attempt; artifacts can also be downloaded as one ZIP bundle.
- Kamal deploys the remote-only image, proxy, and persistent output volume.
