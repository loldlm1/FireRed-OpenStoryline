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

The original pull request targeted `main` in
`loldlm1/FireRed-OpenStoryline`. Merge and release state belongs in the hosting
platform, not in this historical implementation checklist.
