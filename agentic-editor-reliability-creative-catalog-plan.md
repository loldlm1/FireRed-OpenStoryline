# Agentic Editor Reliability And Creative Catalog Plan

- Status: proposed implementation plan
- Date: 2026-07-21
- Scope: remote-only social-clips MVP
- Out of scope: implementation, deployment, production mutation, and
  paid/premium asset licensing

## 1. Outcome

Make the remote agentic editor reliably produce at least one safe, playable,
high-quality marketing video for eligible jobs while preserving strict and
auditable evidence about anything the output could not satisfy.

The primary reliability target is a 99% playable-output rate. This is not the
same as claiming that every optional creative enhancement, generated asset, or
strict QA preference is fulfilled. Those outcomes remain separately measured.

The implementation must:

- preserve immutable prompt versions and attempt history;
- reuse valid source analysis and durable stage checkpoints;
- convert recoverable creative defects into deterministic fallbacks;
- publish typed limitations instead of hiding degraded behavior;
- make every limitation actionable through a targeted retry;
- expose only installed, verified capabilities to the model;
- provide a small, reproducible catalog of trending open-licensed styles;
- keep paid creative-asset marketplaces, Pexels, generated-image calls, and
  FFMPEGA outside the baseline success path.

## 2. Evidence And Product Decisions

### Verified incident evidence

- The audited production session completed 4 of 34 runs, an 11.76% completion
  rate.
- Version 3, attempt 1 ran for about 9 minutes and 5 seconds before failing at
  planning with `EDIT_PLAN_VISUAL_COVERAGE_INSUFFICIENT`.
- Most failures were strict planning or crop-evidence failures, not missing
  fonts, transitions, effects, or downloadable assets.
- Merged PR #10 intentionally made crop evidence, asset intent, captions, and
  promotion fail closed.
- Attempts currently reuse the immutable source file but repeat transcription,
  scene analysis, visual understanding, and planning work.

### Confirmed product decisions

- A safe baseline output may complete with typed limitations.
- Strict QA remains truthful and auditable; it does not silently become a pass.
- A retry can use the exact limitations from a prior attempt to improve it.
- The project should be safe for commercial marketing use without introducing
  a paid-license management system.
- The bundled font requirement is Spanish and English only.
- Emoji support should be deterministic and may use a monochrome glyph fallback.
- "Premium" means current/trending creative styles built from verified
  open-licensed resources, not paid marketplace packs.
- Native FFmpeg is the required renderer. FFMPEGA remains disabled and optional.

## 3. Success Contract

Keep the existing terminal job states for compatibility. Add an outcome grade
inside authoritative result/audit data rather than introducing a parallel set
of job states.

| Job state | Outcome grade | Meaning |
| --- | --- | --- |
| `completed` | `enhanced` | Playable output passed technical checks and fulfilled the applicable creative contract. |
| `completed` | `with_limitations` | Playable output passed technical checks after one or more declared creative fallbacks. |
| `failed` | `retryable_failure` | No safe output exists, but a new attempt can reuse checkpoints or retry a transient dependency. |
| `failed` | `terminal_failure` | The source, security boundary, or deterministic renderer prevents a safe output. |

Each completed or failed attempt receives an `outcome_report.v1` containing:

- grade and output artifact IDs;
- typed limitations and fatal errors;
- stage, code, severity, and user-safe description;
- requested behavior and executed fallback;
- evidence artifact names and hashes;
- whether retrying can reuse prior work;
- recommended retry action;
- source, prompt-version, catalog, renderer, and QA fingerprints.

Limitations must never be stored only as UI prose. PostgreSQL result data and
the audit artifacts remain authoritative; `job.json` remains a derived snapshot.

## 4. Failure Classification And Fallback Policy

### Fatal conditions

Fail the job only when no safe playable result can be produced, including:

- invalid, expired, changed, or undecodable source media;
- missing transcript after bounded direct-Mistral retries when no valid cached
  transcript exists;
- database or authoritative checkpoint corruption that cannot be recomputed;
- path traversal, artifact-scope, authentication, or integrity failures;
- FFmpeg failure after the minimal baseline plan is preflighted and retried;
- an output that is empty, undecodable, out of source bounds, or violates core
  codec/duration/dimension checks.

### Degradable conditions

The following should produce a typed limitation and deterministic fallback:

| Defect | Preferred fallback | Limitation example |
| --- | --- | --- |
| Insufficient crop evidence | Full-frame fit with a blurred/background treatment that preserves source content | `VISUAL_REFRAME_FALLBACK` |
| Invalid or unstable crop target | Last validated static crop, otherwise full-frame fit | `CROP_TARGET_FALLBACK` |
| Unsupported transition | Curated fade, otherwise hard cut | `TRANSITION_FALLBACK` |
| Unsupported effect or parameter | Remove only that effect and preserve the segment | `EFFECT_OMITTED` |
| Missing optional catalog item | Resolve a compatible installed preset, otherwise omit | `CATALOG_ASSET_FALLBACK` |
| Generated/stock asset unavailable | Continue with source media or a catalog-native treatment | `EXTERNAL_ASSET_OMITTED` |
| Caption style/font issue | Use the verified core font and footer-safe layout | `CAPTION_STYLE_FALLBACK` |
| Unsupported emoji glyph | Use verified monochrome glyph or visible replacement symbol | `EMOJI_GLYPH_FALLBACK` |
| Semantic QA unavailable | Preserve deterministic QA and record the unavailable review | `SEMANTIC_QA_UNAVAILABLE` |
| Creative conformance mismatch | Publish the safe output and record the unmet intent | `CREATIVE_INTENT_UNMET` |

Fallbacks must be bounded, deterministic, renderer-valid, and included in
creative conformance evidence. The planner is not allowed to repeatedly repair
an operation when the deterministic fallback can resolve it safely.

## 5. Version, Attempt, And Retry Semantics

Preserve the current immutable prompt-version model.

### Retry paths

1. **Automatic recovery:** Resume the same running job after worker interruption
   from its last valid checkpoint and increment the existing recovery counter.
2. **Retry this version:** Create a new attempt under the same immutable prompt
   version. Reference the prior attempt, load its typed limitations, and reuse
   every compatible checkpoint.
3. **Create improved version:** Create a new immutable version only when the user
   changes the prompt or settings. Reuse source-derived analysis but invalidate
   prompt-dependent plans.

Add lineage fields to attempt request/result data:

- `retry_of_attempt_id`;
- `retry_reason_codes`;
- `resume_policy`;
- `checkpoint_lineage`;
- `prior_outcome_grade`;
- `reused_stage_names` and `recomputed_stage_names`.

Add optional immutable-version lineage for the improved-version action:

- `parent_prompt_version_id`;
- `derived_from_attempt_id`;
- `repair_target_codes`.

Do not mutate or relabel old attempts after a later retry succeeds. The UI may
show that a limitation was resolved by a newer attempt, but the original
evidence remains unchanged.

## 6. Durable Checkpoints And Analysis Reuse

### Storage model

Add two additive PostgreSQL-backed boundaries:

1. `session_analysis_cache`: reusable source-derived analysis scoped to the
   editing session and immutable input video.
2. `job_stage_checkpoints`: attempt/version-derived stage outputs and their
   lineage.

Store checkpoint payloads under the existing persistent output root. PostgreSQL
stores the authoritative relative path, SHA-256, schema version, input
fingerprint, status, timestamps, and retention metadata.

Never reuse a checkpoint based only on a filename. Reuse requires:

- same session and input-video identity;
- expected source SHA-256;
- matching stage contract and schema version;
- matching model, prompt, configuration, or catalog fingerprint where relevant;
- registered path inside the permitted persistent root;
- matching content hash and successful schema validation;
- unexpired media/audit retention.

Do not reuse analysis across sessions, even when source hashes match.

### Stage invalidation matrix

| Stage | Reuse fingerprint |
| --- | --- |
| Media probe and extracted audio | Source hash, FFmpeg contract version |
| Transcript | Source/audio hash, STT model, transcription contract version |
| Scene boundaries | Source hash, scene settings, FFmpeg contract version |
| Global frame samples and vision | Source hash, sample settings, vision model/prompt/schema version |
| Short candidates | Transcript, global vision, prompt version, run settings, planner version |
| Clip-local crop evidence | Candidate windows, vision configuration, crop-analysis version |
| Executable edit plan | Candidates, crop evidence, prompt version, catalog snapshot, planner/schema version, retry feedback |
| Resolved assets | Edit plan, catalog/provider snapshot, policy and resolver version |
| Render | Source, executable plan, resolved assets, renderer/profile version |
| QA and promotion | Rendered artifact hashes, QA thresholds and report versions |

### Checkpoint behavior

- Write checkpoints atomically after validating their payload and before moving
  to the next expensive stage.
- A corrupt or incompatible cache entry is quarantined, recorded, and
  recomputed; it must not poison future attempts.
- User retries should display which expensive stages will be reused before the
  attempt is created.
- Record latency, provider calls, tokens, and estimated cost for reused versus
  recomputed stages.

## 7. Baseline-Guaranteed Planning And Rendering

Split planning into two layers:

1. The model proposes creative intent using only advertised capabilities.
2. Deterministic code compiles that intent into a renderer-valid execution plan.

The deterministic compiler must:

- reject invented catalog IDs and unsupported operation types;
- normalize bounded timing and geometry where the correction is unambiguous;
- replace unsafe crops with content-preserving fit/background composition;
- replace incompatible transitions with fade or hard cut;
- strip only unsupported optional effects rather than rejecting the clip;
- enforce subtitle-safe zones and move decorative overlays when possible;
- always be able to emit a minimal plan from the validated short candidates;
- produce a fallback ledger mapping every proposed operation to its executed,
  replaced, or omitted result.

Before the high-quality encode, run a cheap FFmpeg preflight using the exact
compiled filtergraph and representative time windows. A preflight failure gets
one deterministic simplification pass, not another unconstrained model loop.

The minimal renderer remains CPU FFmpeg with H.264, AAC, and yuv420p. Generated
images, Pexels, semantic QA, and FFMPEGA are enhancements and cannot be required
for baseline completion.

## 8. Open-Licensed Creative Catalog

### Catalog policy

Ship a small, versioned core catalog in the production image. Do not download
creative assets at runtime and do not depend on a marketplace API.

The initial license allowlist should be limited to straightforward commercial
and redistribution terms such as:

- SIL Open Font License 1.1;
- Apache-2.0;
- MIT;
- CC0-1.0.

Exclude paid packs, editorial-only assets, non-commercial licenses, licenses
requiring uncertain SaaS terms, and attribution-heavy resources from the first
release. Every bundled file must include its license text and source record.
This is a committed manifest-and-review process, not a license service or a
runtime rights-management system.

### Catalog manifest

Create a versioned manifest with one entry per file or deterministic preset:

- stable catalog ID, kind, version, and human label;
- SHA-256 and expected file type;
- source URL and upstream version/revision;
- SPDX license identifier and included license path;
- commercial-use, modification, and redistribution review flags;
- optional attribution text;
- renderer requirements and minimum supported FFmpeg features;
- Spanish/English glyph coverage where applicable;
- style tags, compatibility tags, and deterministic fallback ID;
- review date and reviewer note without personal credentials.

The loader validates paths, hashes, duplicates, licenses, file signatures, and
renderer compatibility at build/test time and startup. Invalid optional entries
are quarantined. The image build must fail if the required baseline font or core
presets are missing.

### Initial catalog contents

Keep the first catalog deliberately small:

- 3-5 verified OFL font families covering bold hooks, clean captions, compact
  headlines, and editorial accents;
- one verified monochrome emoji font/fallback for common marketing symbols;
- 8-12 FFmpeg-native transition presets selected from features present in the
  pinned production FFmpeg build;
- 4-6 deterministic color treatments built from native filters;
- 4-6 text/caption treatments with safe-zone and contrast rules;
- reusable zoom, punch-in, blur-background, vignette, highlight, and source
  cutaway recipes;
- a small set of synthetic or CC0 textures/overlays only when provenance is
  unambiguous;
- optional CC0 sound effects in a later catalog revision after loudness and
  mixing validation.

Prefer native FFmpeg recipes over downloaded effect packs. Downloadable
transitions are unnecessary unless the renderer gains a compatible engine.
Premiere, After Effects, MOGRT, and DaVinci template packs are out of scope.

### Trending style packs

Represent trends as reviewed combinations of catalog IDs and deterministic
parameters, not copied branded templates. Start with a few style profiles such
as bold social, clean product, energetic launch, and restrained cinematic.

Each profile must define:

- supported niches and aspect ratios;
- font roles, caption treatment, transition set, color treatment, and motion
  intensity;
- maximum effect density and overlay count;
- fallbacks for every nonessential element;
- catalog version and a short generic trend rationale.

Update trend profiles through normal reviewed releases. Do not scrape or fetch
"trending" marketplace content in production.

## 9. Planner And Catalog Integration

Do not send the full catalog manifest to the model.

- The server selects a compact candidate set based on requested tone, niche,
  aspect ratio, available renderer features, and installed catalog version.
- The prompt receives stable candidate IDs plus concise capability metadata.
- The model chooses a style profile and bounded creative operations from that
  candidate set.
- The structured output schema rejects arbitrary paths, filters, shell text,
  fonts, licenses, URLs, and unknown IDs.
- The deterministic resolver selects actual files and records every selection
  in `creative_catalog_usage.json`.

Version the planner prompt, structured schema, catalog snapshot, and compiler
independently so checkpoint invalidation remains precise.

## 10. QA, Promotion, And Audit

Keep strict QA reports, but classify blockers by output safety rather than
treating every creative mismatch as a reason to delete a playable video.

The promotion result becomes one of:

- `promote_enhanced`;
- `promote_with_limitations`;
- `block_technical`.

Add an explicit completion policy with a rollback-compatible strict option:

- `baseline_guaranteed`: block only technical/safety failures and publish typed
  creative limitations;
- `strict`: preserve the current fail-closed promotion behavior for comparison
  and rollback.

The default changes to `baseline_guaranteed` only after canary evidence meets
the rollout gates. Until then, calculate both decisions in shadow/report mode.

QA must verify:

- playable codec, dimensions, duration, audio, and non-empty output;
- crop/fit geometry and content-preserving fallback execution;
- caption visibility, safe zones, font resolution, and emoji fallback;
- transition/effect execution versus the fallback ledger;
- catalog asset hash, license, and visibility evidence;
- no unexplained planned-versus-executed differences;
- outcome grade matches the actual artifacts and limitations.

## 11. User Experience

Extend the reusable workspace without replacing existing version/attempt
history.

For each attempt show:

- `Enhanced`, `Completed with limitations`, `Retryable failure`, or `Failed`;
- duration, output count, reused stages, and recomputed stages;
- concise limitation chips with expandable evidence;
- the executed fallback and recommended next action;
- comparison against another attempt, including resolved/new limitations.

Provide two explicit actions:

1. **Retry defects:** new attempt for the same version, automatically selecting
   typed prior limitations and compatible checkpoints.
2. **Create improved version:** prefill a new prompt version when the user wants
   to change creative intent or settings.

Do not label a `completed_with_limitations` result as failed. Do not label strict
QA findings as resolved merely because a fallback output was downloadable.

## 12. Observability And SLOs

### Primary SLO

Target 99% playable-output success over a rolling production window for
eligible jobs: valid source, supported duration/type, configured baseline
providers, and no user cancellation.

The numerator is attempts with at least one registered output passing core
technical QA. Report the sample size and confidence; do not claim 99% from a
small canary.

### Separate indicators

- enhanced-output rate;
- completed-with-limitations rate and top limitation codes;
- external-asset fulfillment rate;
- stage failure and fallback rates;
- retry success rate by prior limitation code;
- checkpoint reuse and invalidation rate;
- time to first playable output and total wall time;
- provider calls, token usage, and estimated cost per playable output;
- repeated model-repair count;
- preflight-to-final-render failure rate;
- catalog load/quarantine status and catalog version.

Connect these metrics to job ID, prompt version, attempt number, model/prompt
versions, renderer profile, catalog version, and outcome grade without storing
raw prompts, transcripts, media, frames, provider bodies, or credentials in
logs.

## 13. Implementation Phases

### Phase 0 - Regression baseline and contracts

- Add sanitized fixtures reproducing insufficient crop coverage, invalid crop
  geometry, missing optional assets, transition incompatibility, caption
  fallback, and provider unavailability.
- Define `outcome_report.v1`, limitation codes, fallback ledger, and eligible-job
  SLO rules.
- Add shadow classification to current attempts without changing promotion.
- Capture current latency, tokens, retries, and completion rates.

Gate: existing behavior is unchanged, and every historical failure category can
be deterministically classified as fatal, retryable, or degradable.

### Phase 1 - Durable checkpoint and resume foundation

- Add additive checkpoint/cache migrations and models.
- Implement atomic writes, hashes, schema validation, invalidation, retention,
  and quarantine.
- Checkpoint transcription, scene analysis, global vision, short planning,
  crop evidence, edit planning, rendering, and QA.
- Reuse source analysis across attempts in the same session.
- Resume interrupted workers from the last valid stage.

Gate: retrying a planning-stage failure does not repeat valid transcription,
scene detection, or global vision calls; corrupt checkpoints safely recompute.

### Phase 2 - Deterministic compiler and baseline fallbacks

- Add the fallback classifier and minimal plan compiler.
- Convert crop-coverage exhaustion into content-preserving fit/background output.
- Add transition/effect/caption/asset fallback handling.
- Add exact-filtergraph FFmpeg preflight and one deterministic simplification.
- Generate outcome and fallback evidence.

Gate: all degradable regression cases register a technically valid video and a
truthful `with_limitations` outcome; fatal cases still fail closed.

### Phase 3 - Verified creative catalog

- Add catalog schema, loader, manifest, licenses, and a small bundled core.
- Probe the production FFmpeg feature set and enable only compatible presets.
- Add Spanish/English font coverage and emoji fallback tests.
- Add initial reviewed trending style profiles.
- Add build/startup catalog validation and configuration documentation.

Gate: the renderer succeeds without network access using only installed catalog
IDs; tampered, missing, or unlicensed entries are rejected or quarantined.

### Phase 4 - Agent integration and targeted retry

- Present a compact catalog candidate set to the planner.
- Extend structured edit-plan contracts with style/catalog IDs and fallbacks.
- Feed typed prior limitations into same-version retries.
- Record reused/recomputed checkpoints and resolved/new limitations.
- Prevent arbitrary paths, filter expressions, URLs, and unknown asset IDs.

Gate: the model cannot request a missing capability, and retrying a known defect
uses its evidence without resending unchanged expensive context.

### Phase 5 - QA promotion and workspace UX

- Calculate enhanced, limited, and technical-block promotion decisions.
- Add limitation, retry, comparison, and lineage UI states in Spanish.
- Keep API additions backward compatible and preserve current DOM hooks where
  existing tests depend on them.
- Add audit queries and operator summaries for outcome/SLO metrics.

Gate: users can download limited outputs, understand every limitation, retry the
same version, and compare whether defects were resolved.

### Phase 6 - Canary, rollout, and catalog maintenance

- Run synthetic and private operator-only regression suites in report/shadow
  mode.
- Canary checkpoint reuse, then fallbacks, then catalog styles as separate flags.
- Compare strict versus baseline-guaranteed decisions on the same evidence.
- Make baseline-guaranteed completion the default only after technical QA,
  artifact integrity, and error-rate gates pass.
- Document a lightweight process for reviewing and updating open-licensed trend
  profiles without runtime downloads.

Gate: no technical-quality regression, no secret/private-data leakage, improved
playable-output rate, materially lower retry latency/cost, and a verified
rollback path.

## 14. Expected Code And Documentation Surfaces

Likely implementation surfaces include:

- `src/open_storyline/mvp/pipeline.py`
- `src/open_storyline/mvp/prompt_versions.py`
- `src/open_storyline/mvp/models.py`
- `src/open_storyline/mvp/jobs.py`
- `src/open_storyline/mvp/edit_plan.py`
- `src/open_storyline/mvp/compositor.py`
- `src/open_storyline/mvp/render.py`
- `src/open_storyline/mvp/promotion.py`
- `src/open_storyline/mvp/creative_qa.py`
- `src/open_storyline/mvp/audit.py`
- `src/open_storyline/mvp/activity.py`
- new focused modules for outcomes, checkpoints, fallbacks, and catalog loading;
- additive Alembic migrations under `migrations/versions/`;
- `web/static/mvp/app.js`, `views.js`, and `styles.css`;
- `Dockerfile.remote`, `.dockerignore`, `config/deploy.yml`, and env examples;
- `docs/mvp/architecture.md`, `render-quality.md`, `guia-es.md`, and
  `implementation-history.md` after implementation;
- focused unit, database, renderer, catalog, QA, and browser tests.

Do not modify or merge the full local-agent profile as part of this work.

## 15. Verification Strategy

### Unit and contract coverage

- outcome classification and stable limitation codes;
- checkpoint fingerprints, corruption, invalidation, retention, and lineage;
- deterministic fallback selection and fallback ledger;
- catalog manifest/path/hash/license validation;
- planner rejection of unknown IDs and arbitrary filter/path input;
- Spanish/English font coverage and emoji glyph fallback;
- promotion classification and API serialization.

### Media integration coverage

- FFmpeg feature probe for every enabled transition/effect;
- exact-filtergraph preflight and simplification;
- landscape-to-portrait content-preserving fallback;
- caption safe-zone and font-resolution checks;
- valid H.264/AAC/yuv420p output after every degradable fixture;
- deterministic output evidence across repeated runs.

### Database coverage

- connected PostgreSQL tests for checkpoint uniqueness, locking, atomic state
  transitions, cross-session isolation, cleanup, and concurrent retry creation;
- additive migration compatibility and rollback review;
- audit ingestion of outcome, fallback, catalog, and checkpoint evidence.

### Regression and browser coverage

- synthetic version/attempt histories with enhanced, limited, retryable, and
  terminal outcomes;
- retry-defects flow and version comparison;
- artifact preview/download for limited outputs;
- mobile and desktop accessibility for limitation and retry controls;
- no browser console or network errors in the affected flow.

Use the project-native Python suite first, connected PostgreSQL evidence for the
new tables, focused FFmpeg render tests, then the narrowest Chromium smoke and
affected auth/layout test.

## 16. Release And Rollback

Use additive schema changes and deploy a compatibility bridge before enabling
new behavior.

Feature flags should independently control:

- checkpoint reads/writes;
- baseline fallback compilation;
- creative catalog planning;
- limited-output promotion;
- targeted retry UX.

Rollback order:

1. disable catalog planning while retaining core renderer fallbacks;
2. disable limited-output promotion and restore strict promotion behavior;
3. disable checkpoint reads while retaining stored evidence for diagnosis;
4. roll back the application image only after confirming additive schema
   compatibility.

Do not delete checkpoint/catalog data during normal rollback. Retention should
remove it through the existing scoped lifecycle after references expire.

## 17. Final Acceptance Criteria

- The audited crop-coverage failure class produces a playable, content-preserving
  output labeled with its exact limitation.
- A same-version retry reuses valid transcription, scene, and global-vision
  checkpoints and records the saved work.
- A changed prompt creates a new immutable version while still reusing eligible
  source analysis.
- Strict QA reports every unmet creative intent even when the baseline output is
  downloadable.
- The planner cannot select unavailable fonts, transitions, effects, overlays,
  or arbitrary filesystem resources.
- The bundled catalog works without runtime network access and contains only
  verified, included licenses suitable for commercial marketing use.
- Spanish/English captions and common emoji fallbacks render deterministically.
- No paid marketplace pack, Pexels response, generated image, semantic QA call,
  or FFMPEGA service is required to complete the baseline render.
- Technical/safety failures remain fail closed.
- Production rollout reports playable, enhanced, limited, retry, cost, token,
  latency, and checkpoint-reuse metrics separately.
