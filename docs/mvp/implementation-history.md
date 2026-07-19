# Remote MVP Implementation History

This is a historical record of the completed atomic implementation sequence.
It is not an active sprint plan. Current engineering work should start from
[`AGENTS.md`](../../AGENTS.md), the
[`Agent Engineering Guide`](../agent-engineering.md), and the current
[`Remote MVP Architecture`](architecture.md).

Each implementation increment was validated before its short commit.

| Increment | Scope | Required validation | Commit |
| --- | --- | --- | --- |
| 0 | Architecture, remote-only policy, key guide | Config examples parse; docs links checked | `docs: define MVP architecture` |
| 1 | Remote STT cascade and compatible ASR node | Unit tests for success, fallback, sanitization, and total failure | `feat: add remote STT cascade` |
| 2 | 9Router planner and frame-analysis adapter | Contract tests for structured output and invalid plans | `feat: integrate 9router models` |
| 3 | Durable job store, queue, upload and download API | API tests and restart recovery test | `feat: add durable video jobs` |
| 4 | Social-short candidate validation and ranking | Boundary, overlap, duration, and deduplication tests | `feat: generate social shorts` |
| 5 | CPU FFmpeg renderer and subtitles | Synthetic-video end-to-end render | `feat: render CPU social clips` |
| 6 | FFMPEGA tool adapter with capability policy | Reject local-model effects; accept deterministic effects | `feat: add FFMPEGA tool adapter` |
| 7 | Failure manifest and user-visible reasons | No secrets in persisted errors; all attempts retained | `feat: expose job failure reasons` |
| 8 | Remote-only container, setup docs and full smoke test | Clean configuration and end-to-end test suite | `chore: package CPU MVP` |
| 9 | Kamal production deployment for IP:port or automatic-HTTPS domain | Render both ERB modes as valid YAML; shell syntax check | `feat: deploy MVP with Kamal` |
| 10 | Persistent API key, brute-force protection and RPM/RPD quotas | Restart, UTC rollover, concurrency and HTTP 429 tests | `feat: enforce persistent API limits` |
| 11 | Kamal operating guide and verified free-provider capacity | Documentation links, clean diff and complete remote-only suite | `docs: align production guide with Kamal` |
| 12 | 9Router image catalog, binary generation and fail-closed model cascade | Discovery, binary/base64, fallback, SSRF and secret-sanitization tests | `feat: add 9router image cascade` |
| 13 | Agent-planned generated-image source for `SearchMedia` | Pexels compatibility, schema, provenance and transactional cleanup tests | `feat: generate search media via 9router` |
| 14 | Kamal variables, provider/copyright guide and complete regression pass | ERB/YAML, config parse, documentation checks and clean-tree suite | `docs: configure remote image generation` |
| 15 | Sprint 1: pinned Kamal toolchain and redacted 9Router/VPS connectivity preflight | Kamal `2.12.0` config, old-version rejection, auth/catalog/SSH/Docker probes and focused tests | `build: align kamal release toolchain` |
| 16 | Sprint 2: non-disruptive 9Router backup and access observation | Root-only SQLite backup/restore integrity, live process/port/health/auth review, and no runtime mutation | `ops: preserve live 9router during qa` |
| 17 | Sprint 3: single-model Codex/Mistral contracts and pinned offline 9Router STT adapter | Focused provider tests, config/Kamal validation, live Codex text/vision/image probes, clean patch application, and a recorded red Mistral catalog gate | `fix: lock ninerouter provider contracts` |
| 18 | Sprint 4: deterministic and live redacted 9Router modality gate | Complete unit suite, local/container catalog checks, live Codex text/vision/image contracts, skipped invalid STT canary, container-to-host route, and incident runbook | `test: add redacted ninerouter qa gate` |
| 19 | Sprint 5 release checkpoint: allowlisted remote image and mandatory live provider gate | Remote image build/profile inspection, local `/health` and `/up` smoke, Kamal config, complete deterministic suite, and a recorded deployment block while Mistral STT is absent | `release: gate remote mvp on ninerouter qa` |
| 20 | Sprint 6: direct Mistral boundary and legacy STT cleanup | Direct timestamp contract, config/Kamal secret tests, public node compatibility, complete deterministic suite, and removal of the obsolete 9Router STT adapter | `refactor: route remote stt directly to mistral` |
| 21 | Sprint 7: quota-aware Mistral key failover | Ordered key ring, bounded retries, `Retry-After` cooldowns, invalid-input fail-closed behavior, process-local serialization, redacted metadata, and full deterministic suite | `feat: add quota-aware mistral key failover` |
| 22 | Sprint 8: split provider release gates and VPS canary | Live Codex text/vision/image and direct Voxtral gates, custom-port stop-first deploy, synthetic end-to-end job, artifact security, restart recovery, and retained-version rollback | `release: gate direct mistral stt` |
| 23 | PostgreSQL Sprint 1: application database foundation and one-file recovery | Additive migration, disposable PostgreSQL integration tests, custom-format backup, isolated restore check, and Kamal accessory validation | `feat(mvp): add postgres application foundation` |
| 24 | PostgreSQL Sprint 2: password login and server-side sessions | Argon2 login, cookie/CSRF lifecycle, login-only throttling, generic failure responses, and focused Chromium authentication QA | `feat(mvp): replace web token with password sessions` |
| 25 | PostgreSQL Sprint 3: resumable editing sessions and durable jobs | Session/job API tests, bounded legacy import with repeat-apply idempotency, restart recovery, and browser resume coverage | `feat(mvp): persist editing sessions and jobs in postgres` |
| 26 | PostgreSQL Sprint 4: persistent audit evidence and agent CLI | JSON/SRT/prompt/plan ingestion, deterministic QC, reviews, bounded backfill, redacted event logs, and PostgreSQL-backed CLI tests | `feat(mvp): add persistent video audit and agent cli` |
| 27 | PostgreSQL Sprint 5: media purge and 30-day audit retention | Bounded retention and holds against PostgreSQL, session-deletion browser QA, complete deterministic suite, FFmpeg smoke, remote image build, and Kamal validation | `feat(mvp): enforce session media and audit retention` |
| 28 | PostgreSQL release hardening: exact-image migration and verified one-file recovery | No-port candidate migration, atomic production dump, isolated restore verification, shell checks, and focused deployment regression tests | `fix(mvp): harden postgres deployment commands` |
| 29 | Agentic editing Sprint 1: versioned contracts and shadow controls | Typed plan/preflight artifacts, additive job controls, legacy/shadow compatibility, config parse, and focused API tests | `feat(mvp): define agentic edit contracts` |
| 30 | Agentic editing Sprint 2: timestamped visual evidence | Deterministic scene boundaries, bounded frame manifests, validated remote observations, audit privacy, and synthetic evidence tests | `feat(mvp): add timestamped visual evidence` |
| 31 | Agentic editing Sprint 3: capability-aware shot planning | Cross-niche plan fixtures, renderer capability allowlists, provider-free shadow mode, and preflight/audit tests | `feat(mvp): plan capability-aware video shots` |
| 32 | Agentic editing Sprint 4: intelligent portrait reframing | Synthetic target visibility, fit/crop choices, smoothing and velocity bounds, duration/audio/subtitle checks, and legacy kill switch | `feat(mvp): render intelligent portrait reframes` |
| 33 | Agentic editing Sprint 5: timeline creative primitives | Typed source cutaways, PiP, zoom, emphasis, cuts/fades/crossfades, filtergraph bounds, and FFMPEGA separation | `feat(mvp): add timeline creative primitives` |
| 34 | Agentic editing Sprint 6: conditional generated assets | Zero-call source-only behavior, 9Router-only generation, transactional cleanup, provenance, rights controls, API/UI tests, and no provider fallback | `feat(mvp): insert conditional generated assets` |
| 35 | Agentic editing Sprint 7: structural, rhythm, and conformance evidence | Synthetic cross-niche fixtures, structural/rhythm/conformance artifacts, advisory semantic QA, and non-blocking failure tests | `test(mvp): add creative conformance gates` |
| 36 | Agentic editing Sprint 8: guarded Pexels sourcing and disabled rollout | Mocked Pexels security/provenance tests, 228 deterministic and connected PostgreSQL tests, 4 FFmpeg renders, remote Docker build, Kamal/shell/config gates, and focused desktop/mobile Chromium QA; live rollout remains disabled pending manual license review and separate authorization | `feat(mvp): add guarded pexels sourcing and rollout` |
| 37 | Agentic production Sprints 9-10: shadow deployment and source-only render activation | Atomic PostgreSQL backup and isolated restore, 228 deterministic tests, live redacted 9Router/Mistral gates, healthy shadow/render deployments, disabled generated/Pexels/semantic providers, and an operator-ready private comparison runbook | `ops(mvp): record agentic production rollout` |
| 38 | Reusable workspace Sprint 1: compatibility bridge and execution baseline | Strict `legacy`/`enabled` setting, legacy default, dual known-revision readiness, focused app/database/Kamal tests, and recorded non-destructive rollout floor | `chore(mvp): prepare reusable workspace rollout` |
| 39 | Reusable workspace Sprint 2: additive PostgreSQL contracts | Workflow versions, immutable prompt/source tables, attempt/favorite constraints, bounded advisory-locked legacy prompt backfill, restore checks, and connected migration/concurrency tests | `feat(mvp): add reusable session workspace schema` |
| 40 | Reusable workspace Sprint 3: immutable resumable session source | Offset-checked chunk upload, FFprobe/SHA-256 completion, preview, path and size bounds, one-source enforcement, expiry renewal, incomplete-upload cleanup, and connected retention/concurrency tests | `feat(mvp): persist immutable session source videos` |
| 41 | Reusable workspace Sprint 4: prompt versions and source-reusing runs | Immutable versions/settings, atomic attempt allocation, no-copy source resolution, restart recovery, audit attribution, completed-run favorite selection, pagination, and legacy isolation tests | `feat(mvp): version prompts and reuse session media` |
| 42 | Reusable workspace Sprint 5: sanitized live activity | Allowlisted public activity schema, monotonic progress, pipeline/tool/render stages, ordered replay, native SSE with heartbeat/reconnect, polling fallback, and redaction tests | `feat(mvp): stream sanitized editing activity` |
| 43 | Reusable workspace Sprint 6: modular premium editing UI | Zero-build ES-module workspace, resumable upload percentage, source/prompt/activity/output hierarchy, complete async/error states, legacy page switch, responsive accessibility, and focused Chromium QA | `feat(web): redesign remote editing workspace` |
| 44 | Reusable workspace Sprint 7: comparison and favorite workflows | Paginated version/attempt history, output preview/comparison, rerun, optimistic favorite rollback, expiry/legacy states, bounded browser cleanup, connected favorite concurrency, and desktop/mobile Chromium QA | `feat(web): compare prompt versions and outputs` |
| 45 | Reusable workspace Sprint 8: release hardening and disabled rollout | Restrictive CSP/security headers, unbuffered SSE proxying, scoped remote packaging, bilingual/operator docs, 272 deterministic tests with 68 expected skips, 272 connected PostgreSQL tests, 5 FFmpeg tests, 9 Chromium scenarios, shell/compile/config gates, and remote Docker build; rollout remains `legacy` pending separate authorization | `chore(mvp): harden reusable workspace rollout` |

The original pull request targeted `main` in
`loldlm1/FireRed-OpenStoryline`. Merge and release state belongs in the hosting
platform, not in this historical implementation checklist.
