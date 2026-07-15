# Atomic MVP sprints

Each sprint is validated before its single, short commit is created.

| Sprint | Scope | Required validation | Commit |
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

The pull request targets `main` in `loldlm1/FireRed-OpenStoryline`. It remains a
draft until the owner completes a real-provider test and chooses to merge it.
