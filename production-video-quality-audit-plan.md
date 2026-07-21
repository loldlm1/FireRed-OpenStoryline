# Production Agentic Video Quality Audit And Hardening Plan

## Overview

This plan addresses the first audited production agentic edit after the latest
remote-MVP updates. The production job completed, but it omitted the expected
generated-image and Pexels-video inserts, rendered the selected horizontal
source as a small letterboxed picture inside a portrait canvas, and burned
oversized subtitles through the middle of the visible content.

The evidence shows that this is primarily an editing-engine, contract, and
quality-gating failure. There is also an agent-plan consistency gap, but there
was no GPT image or Pexels provider outage: the executable edit plan contained
zero asset requests, so the resolver correctly made zero provider calls.

The implementation should fix deterministic correctness before adding another
semantic reviewer. The target flow is:

```text
user requirements
  -> structured creative intent
  -> clip selection
  -> clip-local visual evidence
  -> executable edit plan
  -> deterministic preflight
  -> render candidate
  -> frame/caption/asset quality gate
  -> promote output
  -> advisory semantic and human review
```

**Plan status**: Implemented and production-validated; pull request #10 open.

**Created**: 2026-07-20.

**Implementation branch**: `fix/production-video-quality-hardening`.

**Production-deployed code revision**: `824886c`.

**Current local baseline**: `8e2a71e`.

**Audited production application commit**: `0d1431c`.

The commit difference only removes completed planning documents; no behavioral
deployment drift was found.

**Original research boundary**: This document was the only file created during
the research phase. The authorized implementation and production validation
described below followed in ordered, validated sprint commits. Private prompts,
media, transcripts, provider responses, frames, and credentials remain outside
Git.

## Confirmed Operator Decisions

- Implementation is authorized to deploy through the current Kamal production
  workflow and make the necessary live 9Router image, Pexels, Mistral, and
  planning/vision calls until the acceptance contract is satisfied.
- This authorization removes an operator-imposed call quota, but implementation
  must retain bounded retries, timeouts, attribution, fail-closed behavior, and
  cost/latency evidence. It does not authorize an unbounded agent loop.
- The primary production canary must rerun the same editing session, immutable
  source, prompt version, settings snapshot, and source hash as a new attempt.
  The original attempt/output remains the comparison baseline and is never
  overwritten.
- A second prompt variant is unnecessary for the primary engine proof because
  the stored prompt already requires both external asset types. Create another
  prompt version only if the desired Pexels media kind or creative instructions
  intentionally change.
- The current direct-port deployment is accepted as a private personal-use
  boundary. A domain/TLS migration is not required by this plan. Database/schema
  readiness, secure handling, redaction, and rollback checks still apply.

## Stored-Prompt Acceptance Contract

The private production prompt was inspected read-only through the bounded audit
CLI. Its text and session identifiers remain outside this document; only the
behavioral contract is recorded:

- Produce exactly one vertical clip lasting 18-25 seconds.
- Select one complete, self-contained idea.
- Start with a strong phrase quoted literally from the source content and end
  after a clear conclusion.
- Select a source interval containing both required, evidence-backed visual
  gaps. If the selected interval lacks either gap, choose a different interval.
- Use exactly one vertical Pexels video for approximately 3-5 seconds to show a
  real action, place, or situation mentioned but not visible in the source.
- Use exactly one generated editorial image for approximately 2-4 seconds to
  show an abstract idea, process, or concept that cannot be shown from source
  footage.
- Do not invent concepts merely to justify external assets.
- Keep the source speaker/video as the primary layer; external assets support
  the explanation rather than replacing the speaker.
- Do not cover the face, important source text, or subtitles.
- Preserve the exact meaning of the spoken content.
- Use readable footer-safe subtitles, intelligent portrait reframing, and
  discreet transitions.

The stored prompt requires a Pexels **video**, not a Pexels image. The exact
replay must follow the stored prompt unless the operator deliberately creates a
new prompt version requesting a still image instead.

## Resolved Operator Decision

The operator approved following the immutable stored prompt for the exact
replay: one generated editorial image plus one vertical Pexels video. No prompt
variant was introduced, so the result remains attributable to engine changes
rather than a changed creative requirement.

## Implementation And Production Validation

### Ordered Sprint Commits

The requested sprint batch was implemented and validated in order:

| Sprint | Commit | Outcome |
| --- | --- | --- |
| 1 | `4e8f37d` | Required creative intent became typed, versioned, executable, and fail-closed. |
| 2 | `2709e2d` | Selected clips gained bounded local visual evidence and crop-coverage preflight. |
| 3 | `3505770` | Captions became resolution-aware and footer-bounded; explicit quality profiles replaced implicit encoding settings. |
| 4 | `e583bf0` | Frame-level evidence and deterministic `off|report|enforce` promotion decisions were added. |
| 5 | `24d5b88` | Pinned, isolated, read-only reference-quality tooling was added outside the remote web image. |
| 6 | `9633cfd` | Kamal readiness, non-root runtime ownership, release observation, and rollback controls were hardened. |

Subsequent production canary findings were repaired in focused commits through
`824886c`. These changes normalize bounded provider/model aliases, preserve
required-asset failure semantics, tighten crop evidence and safety bounds, and
verify that resolved assets are actually visible in the rendered timeline.

### Exact Replay Result

Production Attempt 25 replayed the same immutable session lineage, prompt
version, settings version, source hash, and source media under promotion mode
`report`. It passed the same deterministic promotion checks used by `enforce`:

- Exactly one GPT editorial image was requested, resolved, called once, and
  visibly rendered for 3 seconds.
- Exactly one portrait Pexels video was requested, resolved, called once, and
  visibly rendered for 4 seconds.
- Asset visibility checks passed with SSIM `0.983` and `0.987`.
- The output filled a 1080x1920 portrait canvas, preserved the speaker under
  manual frame inspection, and encoded as H.264/AAC at 60 fps.
- The 21.8-second output was 7.6 MB. Reference-aligned quality passed with
  median SSIM `0.984`, minimum SSIM `0.983`, and median PSNR `43.74`.
- Captions used at most two lines, a resolved 46 px font, and no more than 4.5%
  of frame height inside the footer-safe region.
- The deterministic decision was `promote` with zero blockers. Manual sampled
  frames and a one-frame-per-second contact sheet also passed review.

The private QA workspace is retained temporarily at
`/tmp/openstoryline-attempt25.goYQNI/` for operator review and remains outside
the repository.

### Enforcement Result

Production was then redeployed with
`OPENSTORYLINE_RENDER_PROMOTION_MODE=enforce`. Attempts 26 and 27 both failed
safely during edit-plan preflight with
`EDIT_PLAN_VISUAL_COVERAGE_INSUFFICIENT`, before any image or Pexels provider
call. Attempt 26 demonstrated that bounded stable-track continuity accepts an
11.25-second sampling bracket while a 16.875-second bracket remains blocked by
the 12-second safety ceiling. Attempt 27 consumed only sanitized prior-attempt
feedback and remained safely blocked.

The safety ceiling was not weakened to force another passing canary. Attempt 25
already proves that an acceptable exact-lineage candidate passes the identical
deterministic gate; Attempts 26 and 27 prove that enforcement rejects unsafe
agent plans without incurring provider side effects.

### Validation Evidence

- Focused compositor, render, pipeline, and visual-coverage suite: 33 tests
  passed.
- Full local suite without PostgreSQL: 342 tests passed with 70 expected
  database-backed skips.
- Full suite against disposable PostgreSQL 17: 342 tests passed with no skips.
- Release, quality-sidecar, and remote-profile suite: 25 tests passed.
- Shell syntax checks, `Dockerfile.quality`, and the exact
  `Dockerfile.remote` build/deploy path passed.
- Guarded 9Router text, vision, and image gates plus direct Mistral gates passed.
- Chromium smoke and authenticated desktop login, CSRF, session, and
  required-asset UI flows passed.
- Production `/up`, `/health`, container state, recent logs, provider
  readiness, queue recovery, and the Attempt 25 structural audit passed.

Production currently runs revision `824886c` as UID `65532` with promotion mode
`enforce`. No schema, persistent-volume, DNS, TLS, or firewall change was
required. The operational rollback remains to set the private Kamal environment
to promotion mode `report` or `off` and deploy the prior compatible revision.

## Audited Incident Findings

### High: Requested Assets Never Became Executable Requests

- The clip-selection result described both intended image inserts.
- The edit-planning stage returned `asset_requests: []`.
- `asset_manifest.json` consequently recorded `no_requests`, zero 9Router
  calls, zero Pexels calls, and zero resolved assets.
- The resolver behaved correctly according to the typed plan. The broken
  boundary is between user/clip-plan intent and the executable edit plan.
- `asset_policy=auto` and `stock_policy=auto` currently mean permission and a
  maximum budget, not a guaranteed count. The UI and API do not distinguish
  clearly between optional and required assets.
- After two invalid planner responses, render mode may silently substitute a
  minimal source-only template. That behavior can discard intended operations
  and assets while still completing the job.
- The edit planner forces `reasoning_effort="low"` even though production is
  configured for `medium`.

Primary code surfaces:

- `src/open_storyline/mvp/edit_plan.py`
- `src/open_storyline/mvp/assets.py`
- `src/open_storyline/mvp/pipeline.py`
- `src/open_storyline/mvp/jobs.py`
- `src/open_storyline/mvp/prompt_versions.py`
- `web/mvp.html`
- `web/static/mvp/app.js`

### High: Global Frame Sampling Did Not Cover The Selected Clip

- The source was analyzed globally before clip selection.
- Twelve sampled frames were available, but none fell inside the selected
  513.2-535.7 second source window.
- The edit plan requested a portrait crop around a tracked speaker.
- The compositor requires a matching observation inside the exact source
  window. It found none and deterministically applied the plan's `fit`
  fallback.
- The fallback was recorded, but there was no pre-render gate preventing a
  visibly unacceptable portrait composition.

Primary code surfaces:

- `src/open_storyline/mvp/frame_sampling.py`
- `src/open_storyline/mvp/visual_understanding.py`
- `src/open_storyline/mvp/pipeline.py`
- `src/open_storyline/mvp/preflight.py`
- `src/open_storyline/mvp/compositor.py`

### High: Portrait Output Contained Only A Small Active Picture

- Input video: 1920x1080, 60 fps, approximately 1.97 Mbps video.
- Output video: 1080x1920, 30 fps, approximately 296 Kbps video, 1.21 MB for
  22.5 seconds.
- `cropdetect` measured an active picture of approximately 1064x574 at vertical
  offset 656. Only about 30% of the portrait frame height contained picture;
  the rest was black letterboxing.
- The output looked like low-resolution content because a 16:9 frame was fit
  inside a 9:16 canvas and then viewed full-screen. Encoding quality alone does
  not explain the incident, although the current CRF 23/veryfast/30 fps profile
  also deserves measured improvement.

Primary code surfaces:

- `src/open_storyline/mvp/compositor.py`
- `src/open_storyline/mvp/ffmpeg_filters.py`
- `src/open_storyline/mvp/render.py`

### High: Subtitle Styling Used The Wrong Coordinate System

- The renderer writes SRT and burns it with `FontSize=20, MarginV=100`.
- FFmpeg/libass converts SRT to ASS with a default 384x288 script resolution.
- On a 1080x1920 target this scales to roughly 133 px text and a roughly 667 px
  vertical margin.
- This places oversized captions around the content center instead of a small,
  readable footer.
- Raw STT segments are used directly as cues, so long segments are not bounded
  consistently by line count, width, or reading speed.

Primary code surfaces:

- `src/open_storyline/mvp/render.py`
- a new focused subtitle module under `src/open_storyline/mvp/`

### High: Quality Checks Passed The Failure

- Structural QA checks dimensions, codecs, duration, full-frame black,
  freezing, and silence.
- It does not measure active-picture area, unintended letterboxing, crop target
  coverage, caption bounds, blockiness, blur, bitrate context, duplicated
  frames, or perceptual degradation.
- Rhythm QA correctly reported a blocker for a 22.5-second visual hold with no
  scene or overlay change, plus caption cadence warnings.
- The job still completed because creative QA is explicitly advisory and the
  deterministic audit verdict excludes creative quality.
- The product needs a distinction between advisory creative findings and
  deterministic promotion blockers. A rendered candidate should not become a
  completed downloadable output when its geometry, captions, or required asset
  use violates validated contracts.

Primary code surfaces:

- `src/open_storyline/mvp/creative_qa.py`
- `src/open_storyline/mvp/audit.py`
- `src/open_storyline/mvp/pipeline.py`

### Medium: Kamal Direct-Port Mode Bypasses Important Release Controls

- Production is using Kamal's direct-port mode with `servers.web.proxy: false`.
- The Kamal proxy `/up` health check, TLS, response buffering policy, and proxy
  readiness behavior are inactive in that mode.
- The container health check calls `/health`, which proves only process/profile
  liveness. `/up` is the database/schema-aware readiness endpoint.
- Direct HTTP requires insecure mode, disables secure cookies, and exposes the
  password/session exchange unless the port is reachable only through a trusted
  private tunnel or network.
- `Dockerfile.remote` runs the application as root.
- Adding heavy quality dependencies directly to the web image would weaken the
  remote-only profile and increase release risk.

Primary code surfaces:

- `config/deploy.yml`
- `Dockerfile.remote`
- `mvp_fastapi.py`
- `src/open_storyline/mvp/auth.py`
- `bin/kamal-mvp`
- `.kamal/hooks/pre-deploy`

## Root-Cause Decision

The incident is not best described as a provider failure or solely as an
agentic choice:

1. Missing assets started as a model-to-schema inconsistency, but the engine
   lacked a required-intent invariant and allowed the contradiction to pass.
2. Bad framing was deterministic engine behavior after clip-local visual
   evidence was unavailable.
3. Bad subtitle placement was deterministic rendering behavior.
4. Completion despite obvious quality defects was a QA and release-gating
   policy failure.

The robust response is to make creative intent executable and testable, gather
evidence for the actual selected clip, reject unsafe composition before output
promotion, and use frame-level metrics as evidence rather than asking a model
to guess whether an opaque render is acceptable.

## Open-Source Research

Research was checked against the projects' public GitHub repositories on
2026-07-20. These tools solve different parts of the problem; none can infer a
correct edit by itself.

| Project | Best use here | License and fit | Decision |
| --- | --- | --- | --- |
| [Netflix VMAF](https://github.com/Netflix/vmaf) | Full-reference perceptual quality with per-frame and pooled results | BSD-2-Clause-Patent; production-capable, but requires aligned reference/distorted frames and an FFmpeg/libvmaf build | Adopt in an optional pinned quality image after deterministic gates |
| [ffmpeg-quality-metrics](https://github.com/slhck/ffmpeg-quality-metrics) | Practical JSON/CSV wrapper for per-frame PSNR, SSIM, VIF, and VMAF | MIT; current releases require FFmpeg 7.1+, while the current remote image has no `libvmaf` | Preferred operator/eval wrapper in a separate quality image |
| [QCTools](https://github.com/bavc/qctools) | Human incident inspection, frame graphs, crop/signal/PSNR metadata, and `qcli` reports | GPLv3 deliverable; useful operationally but less attractive to embed in the product image | Keep as an operator workstation/one-shot investigation tool |
| [PySceneDetect](https://github.com/Breakthrough/PySceneDetect) | Scene boundary comparison and scene-aware regression sampling | BSD-3-Clause and active; not a perceptual quality metric and adds OpenCV beside existing FFmpeg scene logic | Use as a benchmark/reference, not a required runtime dependency |
| [DOVER](https://github.com/VQAssessment/DOVER) | No-reference aesthetic and technical quality research | S-Lab non-commercial license, model weights, heavier runtime, last active upstream work observed in 2024 | Do not ship; optional legal-approved offline experiment only |
| [FAST-VQA/FasterVQA](https://github.com/VQAssessment/FAST-VQA-and-FasterVQA) | No-reference video quality research | S-Lab non-commercial license, model weights, heavier runtime, last active upstream work observed in 2024 | Do not ship; optional legal-approved offline experiment only |

### Immediate Tooling Choice

Use the existing production FFmpeg filters first:

- `cropdetect` for active-picture bounds and unintended letterboxing.
- `blurdetect` and `blockdetect` for severe clarity/compression defects.
- `signalstats` for luma/chroma and temporal outlier evidence.
- `blackdetect` and `freezedetect` for the checks already in place.
- `ssim`, `psnr`, and `xpsnr` for aligned full-reference comparisons.
- FFprobe for frame rate, duration, codecs, dimensions, bit rate, color metadata,
  and duplicate/drop evidence where available.

The audited production FFmpeg build exposes these filters but not `libvmaf`.
That is sufficient for the first production-quality gate without enlarging the
web image.

### Correct Use Of Full-Reference Metrics

Do not compare the original horizontal source directly with the final portrait
edit. VMAF, SSIM, and PSNR would penalize intended crops, overlays, Pexels/GPT
images, and subtitles as if they were distortion.

The edit plan must provide a source-to-output mapping for every timeline
interval. Reference comparison should then use one of these bounded methods:

1. Render a lossless or near-lossless reference with the same cuts, crop,
   scale, assets, overlays, frame rate, and subtitle layout; compare the final
   delivery encode against that reference.
2. For faster production checks, reconstruct and compare only sampled frames at
   deterministic timestamps.
3. Mask intentional overlay and caption regions when comparing source-derived
   pixels, while separately validating those regions through asset and caption
   conformance checks.

The first rollout should use stable released VMAF tooling and a pinned model.
VMAF v1 models were newly published in June 2026 and currently require newer
libvmaf support than released distributions commonly provide. Evaluate them in
the quality image before replacing a stable VMAF v0/NEG baseline.

## Target Quality Contract

### Deterministic Blockers

These findings may block promotion of a temporary render candidate to a
completed output because they are objective contract failures:

- required generated/Pexels asset intent is missing, unresolved, or unused;
- selected clip lacks the visual evidence required by its crop/focus plan;
- an automatic crop fallback becomes fit/letterbox without explicit permission;
- a portrait fill/crop output does not occupy the expected canvas bounds;
- caption footprint exceeds the configured safe zone, line count, or cue bounds;
- output dimensions, duration, codecs, decodability, or A/V mapping are invalid;
- full-reference sampled metrics cross calibrated catastrophic-degradation
  thresholds;
- required quality evidence cannot be generated in enforce mode.

### Advisory Findings

These remain review evidence until thresholds are validated across niches:

- long visual holds and low scene-change cadence;
- hook-window activity and general pacing heuristics;
- no-reference aesthetic scores;
- semantic model opinions about visual appeal;
- moderate blur, blockiness, or reference metric warnings above the blocker
  floor;
- intentionally permitted letterboxing.

### Initial Caption Contract

- Preserve the registered SRT artifact for compatibility and downloads.
- Generate a render-only ASS file with explicit `PlayResX=1080` and
  `PlayResY=1920` or the configured output dimensions.
- Keep captions within a bounded footer safe zone.
- Use no more than two lines per cue.
- Bound line width, cue duration, and reading speed before rendering.
- Measure the rendered caption matte or equivalent deterministic bounds rather
  than trusting style values alone.
- Record the resolved font, size, outline, margins, cue count, and maximum
  footprint in QA evidence.

### Initial Framing Contract

- Global scene samples may guide clip selection, but they cannot authorize a
  crop for a selected interval they do not cover.
- Every selected clip receives a second bounded, clip-local sampling pass.
- Each crop/focus segment declares minimum observation count, maximum timestamp
  gap, and target IDs scoped to the same source window.
- Missing coverage triggers one bounded re-analysis/replan before provider calls
  or rendering.
- If coverage remains insufficient, render mode fails preflight or requires an
  explicit user-approved fallback. It does not silently produce a tiny fit
  composition.

### Initial Asset Contract

- Separate `optional` and `required` asset intent.
- A maximum asset count remains a budget, not a requested count.
- Required intent names provider/kind/count/purpose and the clip or timeline
  target.
- Each required intent must map to one asset request and at least one executed
  overlay or cutaway interval.
- The plan may omit an optional intent only with an allowlisted structured
  reason.
- Render mode does not substitute a minimal source-only plan after invalid
  structured responses.

## Versioned Evidence Artifacts

Prefer additive JSON artifacts and existing PostgreSQL audit ingestion. Avoid a
database migration unless implementation discovery proves a relational query or
constraint is required.

| Artifact | Purpose | Retention/privacy rule |
| --- | --- | --- |
| `creative_intent.json` | Required/optional operations and assets with provenance from UI, prompt, and planner | Sanitized text only; no provider payloads or secrets |
| `clip_visual_coverage.json` | Per-clip sample timestamps, target coverage, gaps, and re-analysis result | No frame bytes; stable frame IDs and timestamps only |
| `render_quality_profile.json` | Resolved dimensions, fps policy, codec, CRF/preset, color/pixel format, and expected safe zones | Configuration evidence only |
| `frame_quality_qa.json` | Active area, caption bounds, blur/block/signal results, sampled/full-reference metrics, aggregates, and worst timestamps | Bounded JSON; no frame bytes; cap detailed frame records |
| `render_promotion.json` | Candidate gate decision, blocker codes, warnings, and promoted artifact hash | Must distinguish deterministic decision from advisory review |

If full per-frame data exceeds `OPENSTORYLINE_AUDIT_MAX_DOCUMENT_BYTES`, keep
aggregates and the worst bounded records in the registered artifact. Store an
optional compressed detailed report as a job-scoped downloadable QA artifact
only when retention and size rules explicitly allow it.

## Scope

### In Scope

- Remote-only social-clips MVP and its Kamal production profile.
- Agent/edit-plan consistency, clip-local visual evidence, portrait crop safety,
  subtitles, encoding quality, deterministic QA, audit artifacts, UI controls,
  and release gates.
- Synthetic incident regression media and private operator-only production
  comparison.
- Optional deterministic quality tooling in a separate image or one-shot
  operator workflow.

### Out Of Scope

- Merging or changing the full local LangChain/MCP agent profile.
- Adding local ASR, object detection, embeddings, scene models, DOVER, or
  FAST-VQA to `Dockerfile.remote`.
- Uploading private media, calling live/paid providers, deploying, modifying the
  VPS, or changing TLS/firewall/DNS during implementation without separate
  authorization.
- Treating VMAF or a semantic model as a universal judge of editing quality.
- Automatically rewriting a completed render through an unbounded agent loop.

## Fixed Decisions

- PostgreSQL remains authoritative for job state and audit evidence.
- Media remains job/session scoped under the existing output roots.
- Provider failure remains fail-closed; there is no cross-provider fallback.
- The renderer remains deterministic CPU FFmpeg.
- Existing routes, error shapes, job states, artifact safety, and retention stay
  compatible unless a later task explicitly documents a contract change.
- Candidate promotion happens before terminal completion; post-completion QA
  never rewrites historical state.
- Private production prompts, transcripts, frames, media, and reports never
  become committed fixtures.

## Success Measures

- The audited job produces exactly one generated editorial image and one
  vertical Pexels video, uses them for approximately 2-4 and 3-5 seconds in the
  evidence-backed timeline windows, or fails before rendering with a specific
  code.
- Every selected clip has timestamped local visual evidence covering each
  requested crop/focus segment.
- No automatic portrait render is promoted with the incident's approximately
  30% active-picture height.
- Captions remain in the footer safe zone, use at most two lines, and do not
  cover the primary subject in the synthetic incident fixture.
- A high-quality profile materially improves reference-aligned frame metrics
  without exceeding an accepted CPU/latency budget.
- Deterministic blocker failures prevent candidate promotion; rhythm and
  semantic findings remain attributable advisory evidence.
- The lean remote image keeps local inference absent and does not gain VMAF
  dependencies unless a measured release decision explicitly approves them.
- Kamal release health proves database/schema readiness, and the accepted
  private-only direct-port trust boundary remains documented and verified.

## Sprint 0: Incident Fixture And Quality Baseline

**Goal**: Convert the private incident into reproducible, non-private failure
contracts before changing behavior.

**Dependencies**: Current remote-MVP baseline.

**Tracked scope**:

- `tests/fixtures/mvp_agentic/`
- `tests/test_mvp_edit_plan.py`
- `tests/test_mvp_compositor.py`
- `tests/test_mvp_render.py`
- `tests/test_mvp_creative_qa.py`
- `tests/test_mvp_pipeline.py`
- a small generated test-media helper under `tests/` if existing helpers cannot
  express the scenario

**Commit**: `test(mvp): capture production quality regression`

### Task 0.1: Create A Synthetic Late-Window Source Fixture

- Generate a deterministic 1920x1080 source with a visually identifiable
  subject, scene boundaries, motion, and a selected window late in the timeline.
- Keep the fixture short enough for normal FFmpeg tests while preserving the
  important property: the global sample set does not cover the selected clip.
- Generate or reuse local synthetic image assets representing 9Router and
  Pexels results; do not call providers.
- Add subtitle text long enough to prove cue wrapping and footprint bounds.

**Acceptance criteria**:

- The fixture contains no private media, transcript, prompt, URL, or identifier.
- The current global-only sampling/fallback behavior is reproducible in a
  focused test.
- Test generation is deterministic and skips cleanly when FFmpeg is unavailable.

### Task 0.2: Capture Current And Target Measurements

- Record input/output structure, active-picture ratio, caption footprint,
  executed fallback, asset intent/use, encode duration, file size, and available
  FFmpeg metrics.
- Define named finding codes and severity categories before implementation.
- Calibrate catastrophic blocker floors from the synthetic fixture plus several
  existing cross-niche fixtures; do not invent VMAF/SSIM thresholds from one
  video.

**Acceptance criteria**:

- Tests fail for the incident geometry, captions, and required-asset mismatch.
- The baseline distinguishes encoding degradation from intentional transforms
  and from empty letterbox regions.
- Threshold rationale and fixture coverage are documented in test names or a
  focused QA reference document.

### Sprint 0 Gate

- Focused tests reproduce all three user-visible failures.
- No private production artifact is committed.
- Baseline metrics are stable across two local runs.

**Rollback**: Remove only synthetic fixtures/tests; no runtime or persisted
contract changes exist.

## Sprint 1: Executable Creative Intent And Planner Conformance

**Goal**: Make explicit user and clip-plan requirements impossible to silently
lose between planning and execution.

**Dependencies**: Sprint 0 gate.

**Tracked scope**:

- `src/open_storyline/mvp/edit_plan.py`
- `src/open_storyline/mvp/shorts.py`
- `src/open_storyline/mvp/preflight.py`
- `src/open_storyline/mvp/assets.py`
- `src/open_storyline/mvp/pipeline.py`
- `src/open_storyline/mvp/jobs.py`
- `src/open_storyline/mvp/prompt_versions.py`
- `web/mvp.html`
- `web/static/mvp/app.js`
- `web/static/mvp/views.js`
- related API, planner, asset, pipeline, and browser tests

**Commit**: `fix(mvp): enforce executable creative intent`

### Task 1.1: Add A Versioned Creative-Intent Ledger

- Represent required and optional operations/assets as typed internal data.
- Record intent source: explicit UI control, user prompt, clip-selection plan, or
  planner recommendation.
- Add structured asset kind, provider, count, clip/timeline purpose, and
  required/optional status.
- Keep current `auto` policies backward compatible as optional budgets.
- Add an additive UI/API control for exact required asset mixes so "up to two"
  and "must use one generated image plus one Pexels video" are unambiguous.

**Acceptance criteria**:

- One generated plus one Pexels requirement survives prompt versioning and rerun
  attempts unchanged.
- Disabled provider capabilities reject incompatible required intent before a
  provider call.
- Existing jobs without the new field retain optional current behavior.

### Task 1.2: Enforce Intent-To-Plan Coverage

- Extend the validated edit plan with intent decisions and structured omission
  reasons.
- Require every mandatory intent to map to executable `asset_requests` and a
  timeline operation using the asset.
- Reject dangling requests, requested-but-unused assets, and narrative text that
  claims an operation absent from the structured plan.
- Persist `creative_intent.json` before provider resolution.

**Acceptance criteria**:

- The incident fixture produces one `generated_image` request and one Pexels
  request with bounded timeline purposes.
- `no_requests` is valid only when no required intent exists.
- Conformance evidence reports exact required, requested, resolved, and used
  counts by provider/kind.

### Task 1.3: Remove Silent Render-Mode Degradation

- Pass the configured 9Router reasoning effort to the edit planner instead of
  forcing `low`.
- Keep one bounded schema-repair retry with explicit validation feedback.
- In render mode, fail planning after exhausted invalid responses rather than
  substituting the minimal source-only template.
- Preserve the permissive minimal fallback only in shadow mode if it remains
  useful, and mark it explicitly as degraded evidence.
- Ensure all retries occur before provider calls or other side effects.

**Acceptance criteria**:

- Planner tests assert configured `medium` effort reaches initial and repair
  calls.
- Invalid required-asset plans fail with a stable sanitized error code.
- Shadow fallback and render failure behavior are independently tested.

### Sprint 1 Gate

- Planner, API/job controls, asset resolver, conformance, and pipeline tests pass.
- The synthetic required-asset case cannot complete source-only.
- Browser copy states clearly whether asset counts are optional maxima or
  required counts.

**Rollback**: Disable/hide additive required-intent controls and return to the
prior planner artifact version. Existing optional policies remain readable.

## Sprint 2: Clip-Local Visual Evidence And Crop Safety

**Goal**: Ensure every selected crop/focus segment has evidence from its own
source window and cannot silently become a tiny portrait fit.

**Dependencies**: Sprint 1 gate.

**Tracked scope**:

- `src/open_storyline/mvp/frame_sampling.py`
- `src/open_storyline/mvp/visual_understanding.py`
- `src/open_storyline/mvp/pipeline.py`
- `src/open_storyline/mvp/preflight.py`
- `src/open_storyline/mvp/compositor.py`
- `src/open_storyline/config.py`
- `config.toml`
- env/Kamal examples and focused tests

**Commit**: `fix(mvp): require clip-local crop evidence`

### Task 2.1: Split Coarse And Fine Visual Sampling

- Keep bounded global scene sampling for clip selection.
- After clip selection, sample each selected source window independently.
- Always include safe offsets near clip start/end, midpoint, quartiles, and
  relevant scene boundaries, then fill remaining budget uniformly.
- Scope frame, region, and track IDs to the selected clip to prevent accidental
  cross-window references.
- Add a bounded per-clip sample configuration without changing the meaning of
  the existing global frame-count setting silently.

**Acceptance criteria**:

- Every selected clip has a minimum sample count even when it appears near the
  end of a long source.
- Sample timestamps are inside the selected source bounds and stable across
  runs.
- Total frames/model payload remain bounded by clip count and configuration.

### Task 2.2: Add Visual-Coverage Preflight

- Calculate observation count, temporal coverage ratio, and maximum gap for
  each crop/focus segment.
- Reject targets whose region/track observations are outside the segment source
  window.
- Allow one bounded clip-local re-analysis/replan when coverage is insufficient.
- Persist `clip_visual_coverage.json` without frame bytes.

**Acceptance criteria**:

- The incident's late selected window gains valid local observations.
- A crop plan cannot pass preflight by referring to a track seen only elsewhere
  in the source.
- Missing coverage after repair fails before provider resolution and render.

### Task 2.3: Make Fallback Policy Explicit And Quality-Aware

- Do not convert an automatic crop/focus request into `fit` or `letterbox`
  unless the plan/user explicitly permits that strategy.
- Keep deterministic center crop only as an explicit source-safe strategy, not
  as proof of subject-aware framing.
- Record fallback permission, cause, active-area expectation, and executed
  strategy in render evidence.
- Preserve crop smoothing, hysteresis, and velocity bounds.

**Acceptance criteria**:

- An unapproved fit/letterbox fallback blocks candidate promotion.
- Explicitly allowed letterbox output remains possible and produces an advisory
  finding rather than a false crop success.
- The synthetic subject remains inside the resolved crop across sampled frames.

### Sprint 2 Gate

- Frame-sampling, visual-understanding, preflight, compositor, and pipeline tests
  pass.
- The incident fixture renders as a real portrait crop or fails before render;
  it never completes as a small centered 16:9 picture.
- No local inference dependency is added.

**Rollback**: Set agentic mode to shadow/off and restore the global-only visual
artifact version. No provider or database rollback is required.

## Sprint 3: Caption Layout And Measured Render Quality

**Goal**: Produce readable footer captions and a measured high-quality portrait
encode without hiding geometry defects behind bitrate changes.

**Dependencies**: Sprint 2 gate.

**Tracked scope**:

- `src/open_storyline/mvp/render.py`
- `src/open_storyline/mvp/ffmpeg_filters.py`
- a focused subtitle module under `src/open_storyline/mvp/`
- `src/open_storyline/config.py`
- `config.toml`
- `config/deploy.yml`
- env examples and render/config tests

**Commit**: `fix(mvp): render bounded captions and quality profiles`

### Task 3.1: Build A Resolution-Aware Caption Pipeline

- Keep SRT generation as the public artifact contract.
- Normalize STT segments into bounded display cues with maximum duration,
  reading speed, line width, and two-line layout.
- Generate a render-only ASS file with explicit output PlayRes, footer alignment,
  pixel-scaled font/outline/margins, and safe escaping.
- Keep style deterministic and record resolved values in render evidence.

**Acceptance criteria**:

- Caption size and margins scale correctly at 1080x1920 and another tested
  output resolution.
- Long transcript segments split without overlap, missing text, or more than two
  lines.
- SRT remains downloadable and ordered.

### Task 3.2: Measure Caption Footprint Before Promotion

- Render a caption-only matte or equivalent deterministic representation at
  representative cue times.
- Measure the active caption bounds independently from source pixels.
- Enforce footer safe-zone, maximum height/width, and frame containment.
- Record worst cue/timestamp and bounds without storing frames.

**Acceptance criteria**:

- The incident caption style fails the new footprint test.
- The corrected style stays inside the configured footer band for every cue in
  the synthetic fixture.
- Empty/no-subtitle clips do not produce false failures.

### Task 3.3: Introduce Explicit Render Quality Profiles

- Replace implicit CRF/preset/fps combinations with named, recorded profiles.
- Benchmark a balanced profile and a high-quality canary profile against the
  current CRF 23/veryfast/30 fps baseline.
- Evaluate preserving source frame rate up to a cap for 50/60 fps content rather
  than always forcing 30 fps.
- Record encode time, output size, fps conversion, bit rate, and reference
  metrics. Do not use a raw bitrate floor as the only quality decision.
- Keep H.264/AAC/yuv420p social compatibility unless measured platform needs
  justify a separate profile.

**Acceptance criteria**:

- The selected production profile has documented CPU/latency and quality tradeoffs.
- Reference-aligned clarity improves materially on the synthetic fixture.
- High-quality settings remain bounded by job timeout and capacity.

### Sprint 3 Gate

- Subtitle, FFmpeg filter, render, config, and pipeline tests pass.
- Captions render in the footer without covering the synthetic subject.
- The selected profile improves clarity while meeting an accepted render-time
  budget.

**Rollback**: Select the prior legacy render profile and subtitle path through a
temporary feature flag, then revert the sprint commit.

## Sprint 4: Frame-Level Quality Evidence And Promotion Gate

**Goal**: Prevent objective video-quality contract failures from becoming
completed outputs while keeping creative heuristics advisory.

**Dependencies**: Sprint 3 gate.

**Tracked scope**:

- `src/open_storyline/mvp/creative_qa.py`
- a focused frame-quality/promotion module under `src/open_storyline/mvp/`
- `src/open_storyline/mvp/pipeline.py`
- `src/open_storyline/mvp/audit.py`
- `src/open_storyline/mvp/activity.py`
- `src/open_storyline/config.py`
- audit/config/env/Kamal docs and tests

**Commit**: `feat(mvp): gate renders on deterministic frame quality`

### Task 4.1: Add Active-Picture And Frame-Defect Analysis

- Run bounded temporal `cropdetect` analysis and calculate median/minimum active
  width, height, and area ratios.
- Add severe blur/blockiness/signal checks with current FFmpeg filters.
- Record frame rate conversion, duplicated/dropped-frame evidence when
  available, and representative worst timestamps.
- Interpret active-area results against the planned strategy: fill/crop,
  explicit letterbox, asset cutaway, or transition.

**Acceptance criteria**:

- The incident's approximately 30% active-height output is a deterministic
  blocker for an expected portrait fill/crop.
- Intentional transitions and explicit letterbox segments do not create false
  whole-clip failures.
- Commands, timeouts, findings, and artifact sizes are bounded.

### Task 4.2: Add Reference-Aligned Sample Metrics

- Use the validated timeline transform to reconstruct reference frames for
  selected timestamps.
- Compare delivery frames with `ssim`, `psnr`, and `xpsnr` using aligned PTS,
  resolution, frame rate, and pixel format.
- Mask or separately classify intentional caption/overlay regions.
- Store per-frame values only within configured bounds plus aggregates and worst
  records.

**Acceptance criteria**:

- Intended crops/assets do not appear as unexplained codec distortion.
- Deliberately degraded encodes fail the catastrophic blocker floor.
- A normal high-quality encode passes with stable results across two runs.

### Task 4.3: Gate Candidate Promotion

- Render to job work storage first.
- Run deterministic geometry, caption, asset, media-structure, and required
  quality checks before registering/promoting the output.
- Add an additive `off|report|enforce` promotion mode, defaulting to report for
  rollout.
- In enforce mode, fail the active attempt with stable sanitized blocker codes
  before terminal completion; never rewrite a completed job.
- Keep rhythm and semantic findings advisory unless later evidence supports a
  separately approved policy change.

**Acceptance criteria**:

- Required-asset, crop, and caption failures cannot produce a completed
  downloadable output in enforce mode.
- Report mode preserves current completion behavior while emitting identical
  evidence.
- Candidate cleanup, retention, artifact registration, events, and rollback
  snapshots remain consistent on pass and fail paths.

### Sprint 4 Gate

- Creative QA, audit, jobs/pipeline, retention, and activity tests pass.
- Report and enforce modes are both covered.
- `frame_quality_qa.json` and `render_promotion.json` stay within audit limits
  and contain no frames, prompts, transcripts, provider bodies, or secrets.

**Rollback**: Set promotion mode to `report` or `off`; revert the code after
active attempts drain. Additive artifacts remain readable until normal expiry.

## Sprint 5: Optional VMAF Sidecar And Agent Feedback

**Goal**: Add stronger reference metrics and useful model feedback without
turning the web container into a heavy quality-research image.

**Dependencies**: Sprint 4 gate and calibrated local reference fixtures.

**Tracked scope**:

- a separate quality Dockerfile and pinned requirements if justified
- a read-only operator quality command under `scripts/` or `bin/`
- `src/open_storyline/mvp/observability.py`
- planner input contracts for compact prior-attempt feedback
- quality/eval tests and operator documentation

**Commit**: `feat(mvp): add isolated reference quality analysis`

### Task 5.1: Build A Pinned Deterministic Quality Image

- Keep `Dockerfile.remote` free of VMAF and research-model dependencies.
- Build a separate image with a pinned FFmpeg/libvmaf combination and pinned
  `ffmpeg-quality-metrics` version.
- Start with a stable VMAF v0/NEG model; benchmark VMAF v1 only after compatible
  released libvmaf support or an explicitly accepted source-build policy.
- Run with network disabled, read-only source/output mounts, bounded CPU/time,
  and no access to provider secrets.

**Acceptance criteria**:

- Image provenance, versions, model hash, and license notices are recorded.
- Per-frame JSON/CSV is reproducible on synthetic fixtures.
- The image cannot mutate production media or call external services.

### Task 5.2: Add Offline And Canary VMAF Evaluation

- Compare candidate delivery encodes with plan-aligned reference renders.
- Track pooled and worst-frame VMAF alongside SSIM/XPSNR and encode cost.
- Establish thresholds from cross-niche fixtures and private operator review,
  not from a single incident.
- Use QCTools/qcli only as optional human incident evidence.

**Acceptance criteria**:

- Metric changes correlate with known synthetic degradations.
- Reports identify timestamps and planned operations for worst frames.
- QCTools, DOVER, and FAST-VQA are not imported into production Python runtime.

### Task 5.3: Feed Compact Quality Evidence Into Re-Editing

- Expose only sanitized structured findings to a new attempt: missing intent,
  invalid crop windows, active-area ratios, caption footprint, and worst metric
  timestamps.
- Allow one bounded pre-render plan repair for objective preflight findings.
- Require an explicit new attempt for post-render creative revision; do not hide
  an unbounded multi-encode loop inside one job.
- Attribute model, reasoning effort, prompt version, prior attempt, and quality
  evidence version.

**Acceptance criteria**:

- The planner can correct a crop/asset contract using compact evidence without
  receiving secrets or raw private reports.
- Replay is deterministic enough to explain why a later attempt differs.
- Cost, retries, latency, and success/failure metrics are recorded.

### Sprint 5 Gate

- Sidecar image and operator command pass local synthetic evaluation.
- VMAF remains optional and cannot make the remote web service unavailable.
- Legal review explicitly excludes non-commercial DOVER/FAST-VQA from shipped
  production components.

**Rollback**: Stop invoking the quality image and remove it from release
artifacts. Built-in FFmpeg quality gates remain available.

## Sprint 6: Kamal Security, Readiness, And Production Rollout

**Goal**: Deploy the quality changes through a secure, observable, reversible
Kamal path.

**Dependencies**: Sprints 1-5 gates as applicable and database backup/restore
verification. Deployment and required live-provider authorization are recorded
in Confirmed Operator Decisions.

**Tracked scope**:

- `Dockerfile.remote`
- optional quality Dockerfile
- `config/deploy.yml`
- `bin/kamal-mvp`
- `.kamal/hooks/pre-deploy`
- `.env.mvp.example`
- `.env.kamal.example`
- `.kamal/secrets.example`
- `docs/mvp/architecture.md`
- `docs/mvp/audit-and-database.md`
- `docs/mvp/guia-es.md`
- `docs/agent-engineering.md`
- Kamal, remote-profile, app, auth, and shell tests

**Commit**: `chore(mvp): harden quality rollout and readiness`

### Task 6.1: Verify The Accepted Private Direct-Port Boundary

- Preserve the current direct-port mode for this personal deployment; do not
  introduce a domain, TLS, or shared `kamal-proxy` change as incidental work.
- Verify and document that access remains limited to the trusted operator
  boundary described by the user.
- Keep insecure HTTP explicit, proxy-header trust disabled, and the published
  port/config coherent with direct mode.
- Retain domain/HTTPS mode as a supported future option, not a current rollout
  requirement.

**Acceptance criteria**:

- The release guide states the accepted private-use trust boundary and rollback
  path without claiming public HTTP is generally production-safe.
- Direct mode keeps proxy-header trust disabled and passes auth/session tests.
- Direct and domain config rendering tests remain valid.

### Task 6.2: Align Liveness And Readiness

- Keep `/health` as shallow public liveness/profile evidence.
- Use `/up` for deployment readiness in both proxy and direct-port release
  workflows.
- Update the container/release check so a missing database or incompatible
  schema cannot appear production-ready.
- Preserve enough grace/retries to avoid flapping during normal database start.

**Acceptance criteria**:

- Database unavailable/outdated scenarios fail readiness without leaking
  backend details.
- Kamal config and post-deploy smoke test `/up` and `/health` intentionally.
- Rollback checks schema compatibility before selecting an old image.

### Task 6.3: Move The Web Container Toward Non-Root Safely

- Add a fixed application UID/GID only after defining ownership for the
  persistent output volume.
- Add an idempotent host/bootstrap or deploy preparation step for directory
  ownership and permissions.
- Verify uploads, render work, audit artifacts, retention purge, and rollback
  under the non-root user.
- Do not change PostgreSQL accessory ownership or backup directories
  incidentally.

**Acceptance criteria**:

- The application writes only required paths as the non-root user.
- A rollback image that previously ran as root can still read valid media and
  audit state.
- The image contains no secret/env/private output data.

### Task 6.4: Execute Report-Then-Enforce Rollout

1. Build and test the exact remote and optional quality images.
2. Run deterministic unit/FFmpeg/Kamal/shell gates.
3. Verify a current PostgreSQL backup and isolated restore.
4. Deploy with promotion mode `report` so the first exact replay records the new
   evidence without enforcing uncalibrated blocker thresholds.
5. Run a synthetic production smoke without live providers.
6. Using the recorded authorization, rerun the same private session, immutable
   prompt version, settings, source hash, and source media as a new attempt. The
   expected asset mix is one generated editorial image and one vertical Pexels
   video.
7. Compare framing, captions, assets, metrics, render time, container health,
   logs, queue recovery, audit ingestion, and downloads.
8. Enable required-asset UI controls for the operator.
9. Enable deterministic promotion enforcement for a canary scope.
10. Expand only after no blocker false positives and acceptable CPU/latency.

**Rollback criteria**:

- readiness or database compatibility failure;
- output promotion false positives on approved canaries;
- material render-time or queue-capacity regression;
- caption/framing regression;
- asset/provider conformance regression;
- audit artifact size/retention regression;
- accepted private-access or session-cookie regression.

**Rollback**:

- Set promotion mode to `report` or `off`.
- Disable required-asset controls and optional quality-image invocation.
- Roll back to the last known image only after schema compatibility review.
- Retain additive quality artifacts through normal audit expiry.
- Restore the prior proxy/direct-port configuration only through the documented
  transport rollback procedure.

### Sprint 6 Gate

- Production `/up`, `/health`, container state, logs, queue, audit, retention,
  and artifact downloads pass post-deploy observation.
- Access remains within the accepted private-only direct-port boundary.
- The private canary either uses both required assets with valid framing and
  captions or fails before promotion with an attributable blocker.

## Verification Strategy

Run focused checks first with `.venv/bin/python` and RTK wrappers for noisy
output.

### Focused Python And FFmpeg Checks

```bash
PYTHONPATH=src .venv/bin/python -m unittest \
  tests/test_mvp_edit_plan.py \
  tests/test_mvp_compositor.py \
  tests/test_mvp_creative_qa.py \
  tests/test_mvp_render.py \
  tests/test_mvp_pipeline.py -v
```

Add focused tests for the new intent, caption, frame-quality, and promotion
modules rather than relying only on the full suite.

### Full Deterministic Baseline

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

Report database-backed skips when `TEST_DATABASE_URL` is unset. For connected
evidence, use only a disposable database named with the required
`openstoryline_test` prefix and never expose the connection URL.

### Release Checks

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests/test_kamal_config.py -v
bash -n run.sh build_env.sh download.sh bin/kamal-mvp \
  scripts/mvp-postgres-init.sh scripts/mvp-postgres-backup.sh \
  scripts/mvp-postgres-restore-check.sh .kamal/hooks/pre-deploy \
  .kamal/hooks/post-deploy
docker build -f Dockerfile.remote .
```

If the optional quality image is implemented, build it separately and run only
synthetic, read-only metric fixtures by default.

### Browser Checks

Only when required-asset controls or status evidence change:

```bash
cd .qa/web && QA_FAIL_ON_CONSOLE=1 npm run test:smoke
cd .qa/web && QA_PASSWORD='local test password' npm run test:auth:desktop
```

Run mobile auth/layout instead of desktop when the affected layout is mobile
specific. Keep screenshots/traces/videos failure-only.

### Authorized Production Checks

The operator has authorized these actions once their preceding implementation
and release gates pass. Continue to redact secrets/private evidence and preserve
bounded retries, rollback, and observability:

- live 9Router text/vision/image inference;
- live Mistral transcription;
- live Pexels search/download;
- private production canary media;
- Kamal deploy/redeploy/rollback;
- required non-destructive VPS volume-ownership preparation for the non-root
  migration.

Proxy/TLS/DNS/firewall changes remain outside the current direct-port rollout
unless the operator separately changes the accepted transport decision.

## Eval Matrix

Every planner/render change should be compared against attributable settings:

| Dimension | Baseline | Treatments |
| --- | --- | --- |
| Planner | current prompt, forced low effort | configured medium effort; one schema-repair retry |
| Visual evidence | global 12-frame sampling | global selection plus bounded per-clip sampling |
| Framing | fit fallback allowed | coverage preflight; explicit fallback permission |
| Captions | SRT implicit ASS resolution | explicit target-resolution ASS and bounded cues |
| Encoding | CRF 23, veryfast, fixed 30 fps | measured balanced and high profiles |
| QA | advisory structural/rhythm evidence | report then enforce deterministic promotion gate |
| Reference metrics | none | sampled SSIM/PSNR/XPSNR; optional sidecar VMAF |

Measure task success, structured-plan validity, required/requested/resolved/used
assets, fallback count, active-picture ratio, caption footprint, per-frame and
pooled metrics, encode time, total attempt latency, retries, provider calls,
output size, CPU load, and final promotion decision.

## Risks And Gotchas

- VMAF and SSIM are invalid when source and output transforms are not aligned.
- Metric thresholds calibrated on talking-head footage may fail tutorials,
  gameplay, slides, cooking, or high-motion content.
- A center crop fills the canvas but may still cut off the subject; full-frame
  occupancy is necessary but not sufficient.
- Preserving 60 fps can improve motion but materially increases CPU, output
  size, and quality-analysis cost.
- Subtitle bounding must be measured from the rendered style, not inferred only
  from SRT text or ASS settings.
- Required assets can increase provider cost and failure rate; the UI must make
  this tradeoff explicit.
- A new quality gate must distinguish candidate failure from post-completion
  audit evidence to preserve state-machine invariants.
- Running VMAF inside the web worker can starve the queue. Keep it optional,
  bounded, and isolated until measured.
- Changing the container to non-root without migrating output-volume ownership
  will break uploads/renders/retention.
- Switching a shared VPS to Kamal proxy/domain mode can affect other services;
  review the existing proxy before any change.
- DOVER and FAST-VQA licenses do not permit normal commercial production use
  without contacting contributors.

## Execution Order

1. Sprint 0 incident fixture and baseline.
2. Sprint 1 executable intent and planner fail-closed behavior.
3. Sprint 2 clip-local sampling and crop preflight.
4. Sprint 3 subtitles and measured render profiles.
5. Sprint 4 deterministic frame-quality promotion gate in report mode.
6. Sprint 5 optional VMAF/operator tooling after thresholds are credible.
7. Sprint 6 Kamal hardening and report-to-enforce canary rollout.

Sprints 1-4 are the minimum robust fix for the reported incident. Sprint 5 is a
quality-observability enhancement, not a prerequisite for correcting missing
assets, framing, or captions. Sprint 6 separates release/security changes from
editing-engine behavior so rollback remains clear.

## Completion Checklist

- [x] Synthetic incident reproduces missing assets, uncovered crop, letterbox,
      and caption footprint failures.
- [x] Required creative intent is versioned and executable.
- [x] Planner uses configured reasoning effort and cannot silently degrade in
      render mode.
- [x] Selected clips receive bounded local visual sampling.
- [x] Crop/focus plans require same-window coverage.
- [x] Unapproved fit/letterbox fallback blocks promotion.
- [x] Captions use explicit target-resolution ASS and bounded cues.
- [x] Caption footprint is measured in the footer safe zone.
- [x] Render profiles are benchmarked for quality, CPU, latency, and size.
- [x] Active-picture and aligned frame metrics are persisted without frame bytes.
- [x] Deterministic promotion blockers are distinct from advisory creative QA.
- [x] Optional VMAF tooling is isolated, pinned, read-only, and license-reviewed.
- [x] `/up` gates production readiness in the selected Kamal mode.
- [x] Production access remains within the accepted private-only direct-port
      boundary.
- [x] Non-root migration includes persistent-volume ownership and rollback.
- [x] Full deterministic, connected PostgreSQL, FFmpeg, Kamal, shell, Docker,
      and affected browser checks are reported accurately.
- [x] Private production evidence and provider credentials remain outside Git.
