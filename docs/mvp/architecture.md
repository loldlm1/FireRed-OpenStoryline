# Remote-only video MVP

This fork adds an opt-in MVP for turning one source video into several social
clips. The MVP is deliberately CPU-first and does not run local AI models.

## Non-negotiable runtime policy

- LLM and frame understanding are remote through 9Router.
- Speech-to-text is remote through 9Router's OpenAI-compatible
  `/v1/audio/transcriptions` endpoint.
- There is no local ASR, local embedding model, local scene model, or silent
  local fallback in the MVP path.
- FFmpeg is allowed because it performs deterministic media processing rather
  than model inference.
- If every configured STT model fails, the whole job fails with
  `STT_ALL_PROVIDERS_FAILED` and persists the reason for every attempt.
- ComfyUI-FFMPEGA integration is restricted to deterministic FFmpeg operations
  unless a remote inference backend is configured explicitly.
- Generated search images use 9Router's `/v1/images/generations` endpoint and
  only models confirmed by `/v1/models/image`; there is no local model or
  silent Pexels fallback for this source.

The original OpenStoryline workflow remains available. The new MVP path is
isolated so upstream behavior can continue to be merged into this fork.

The generated-image source belongs to that original full-agent workflow:
`SearchMedia` can return the generated files through the same `{"path": ...}`
contract used by Pexels, so downstream media understanding and timeline nodes
can place them in a video. The isolated social-clips web MVP does not currently
search Pexels or insert generated B-roll; it extracts moments from the uploaded
source video. This boundary avoids presenting downloaded support assets as if
they were already composited into a short.

## Data flow

1. The browser uploads a source video and an editing prompt.
2. The server persists a durable job and extracts compressed mono audio with
   FFmpeg.
3. The STT cascade tries the configured remote models in order.
4. GPT-5.6 Sol receives the transcript and sampled frames through 9Router and
   returns a structured clip plan.
5. The server validates duration, bounds, overlap, and output count.
6. FFmpeg renders vertical clips and subtitles on CPU.
7. The browser downloads individual clips, the manifest, or a ZIP bundle.

## Default remote services

| Purpose | Primary | Fallbacks |
| --- | --- | --- |
| Planning and vision | `cx/gpt-5.6-sol` | none in the MVP |
| Speech-to-text | `groq/whisper-large-v3-turbo` | Groq large-v3, Hugging Face large-v3, Hugging Face small |
| Full-agent generated images | First configured model exposed by 9Router | Remaining exposed image candidates |
| Rendering | FFmpeg on CPU | none |

Multiple models on one provider do not protect against a provider outage or a
shared quota. Production-like testing therefore requires at least one Groq key
and one Hugging Face token.

## Persistence and security

- Every job owns a directory under `outputs/mvp_jobs/<job_id>`.
- State changes are written atomically to `job.json`.
- Inputs and outputs are served only through validated job-scoped paths.
- Provider keys are read from environment variables and never written to job
  state, logs, manifests, or Git.
- Error bodies are truncated and sanitized before persistence.
- Interrupted queued or running jobs are recovered when the process restarts.
- Failed jobs expose a sanitized `failure.json` with the stage and provider
  attempts; all artifacts can also be downloaded as one ZIP bundle.
- The single web key is accepted as Bearer or `X-API-Key`; invalid clients,
  authenticated API traffic and job creation have separate persistent
  SQLite-backed RPM/RPD limits.
- Kamal deploys the remote-only image, proxy and persistent output volume to a
  fresh server; Docker Compose is not part of the production workflow.

## MVP completion bar

A clean clone must be able to:

1. start in the remote-only profile;
2. accept a video through HTTP;
3. transcribe it without importing a local ASR package;
4. create a validated multi-clip plan through 9Router;
5. render at least one vertical MP4 with subtitles;
6. expose status, failure reasons, artifacts, and a ZIP download; and
7. pass unit tests plus a local FFmpeg end-to-end smoke test.
