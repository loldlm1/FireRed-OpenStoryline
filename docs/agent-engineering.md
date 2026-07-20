# Agent Engineering Guide

This guide gives coding agents and maintainers the context behind the
repository rules in [`AGENTS.md`](../AGENTS.md). `AGENTS.md` is the enforceable
instruction source; this document explains the architecture, ownership, and
verification choices in more depth.

## Runtime Profiles

The repository contains two applications that share selected configuration and
media helpers but serve different product goals.

### Full local editing agent

```text
CLI or web client
    -> LangChain agent (`src/open_storyline/agent.py`)
    -> MCP client with session header
    -> MCP server (`src/open_storyline/mcp/server.py`)
    -> registered editing nodes (`src/open_storyline/nodes/`)
    -> artifact/session stores + FFmpeg/resources/provider APIs
```

The full agent loads prompt templates from `prompts/tasks/` and runtime editing
skills from `.storyline/skills/`. MCP tools are generated from node metadata and
Pydantic input schemas. Tool and node names are therefore public contracts, not
internal implementation details.

### Remote-only social-clips MVP

```text
Browser/API client
    -> `mvp_fastapi.py` password login, PostgreSQL sessions, and CSRF
    -> failed-password-only PostgreSQL throttling
    -> PostgreSQL editing sessions, jobs, artifacts, and ordered events
    -> sanitized JSON/SRT audit documents and deterministic structural reviews
    -> one advisory-locked and execution-fenced in-process worker
    -> job-scoped filesystem media and rollback snapshots
    -> direct Mistral Voxtral STT
    -> 9Router clip planning from transcript + sampled frames
    -> validated short candidates
    -> deterministic CPU FFmpeg rendering
    -> job-scoped artifacts, manifest, failure report, and ZIP bundle
```

The remote MVP intentionally excludes the full local model/resource stack.
`Dockerfile.remote` and `requirements-remote.txt` enforce that boundary for
production. The original `Dockerfile`, `run.sh`, MCP server, and full agent
remain available independently.

## Source Of Truth By Concern

| Concern | Primary source | Regression evidence |
| --- | --- | --- |
| Runtime configuration | `src/open_storyline/config.py`, `config.toml` | Config load command and affected tests |
| MCP tool exposure | `src/open_storyline/mcp/register_tools.py`, node metadata/schemas | Tool/schema tests and node tests |
| Agent construction | `src/open_storyline/agent.py` | Focused mocked provider/tool tests |
| Prompt behavior | `prompts/tasks/`, prompt loader and consuming node | Schema/argument regression tests; bilingual parity review |
| Runtime editing skills | `.storyline/skills/`, `src/open_storyline/skills/skills_io.py` | Skill metadata/tool-name review and workflow test |
| Session/artifact state | `src/open_storyline/storage/` | Corruption, isolation, restore, and path-safety tests |
| Remote MVP policy | `docs/mvp/architecture.md`, `src/open_storyline/mvp/` | Remote profile, provider, API, job, render, and security tests |
| Remote database state | `migrations/`, `alembic.ini`, `src/open_storyline/mvp/database.py`, `models.py`, `auth.py`, `jobs.py`, `audit.py`, `retention.py` | Auth, database, job, audit, retention, backup, and restore-check tests |
| Deployment | `Dockerfile.remote`, `config/deploy.yml`, env examples, `bin/kamal-mvp` | `tests/test_kamal_config.py`, image build/config checks |
| Operator automation | Canonical `.claude/skills/`, Codex discovery links under `.agents/skills/`, and their scripts | Current skill validator and safe local dry runs |

## Contract Boundaries

### Model and provider boundaries

- The full agent uses OpenAI-compatible LLM/VLM configuration through LangChain.
- The remote MVP uses the small 9Router client for Codex text/vision and a
  fixed direct Mistral client for timestamped STT. Full-agent generated images
  also use the 9Router image route.
- Provider output is untrusted. Parse and validate it before filesystem, render,
  job-state, or tool actions.
- Preserve explicit timeouts, bounded retries, typed error codes, sanitization,
  and fail-closed behavior.
- A provider or model migration is a separate behavior change. Do not combine it
  with unrelated refactoring or documentation cleanup.

### State and filesystem boundaries

- Full-agent sessions and artifacts are scoped by `session_id`.
- PostgreSQL is authoritative for remote browser sessions, failed-login
  buckets, editing sessions, jobs, artifact metadata, ordered events, audit
  evidence, reviews, holds, and retention state. Media paths remain job-owned
  and filesystem-validated.
- Resolved paths must stay under their expected session/job root.
- Job state transitions and events commit transactionally in PostgreSQL.
  Media/work files remain under `outputs/mvp_jobs/<job_id>`. `job.json` is a
  derived rollback snapshot, not an authoritative state store; queued/running
  jobs recover only under the advisory worker lock.
- Runtime outputs, downloaded models, and resources are ignored because they can
  be large or private. Tests should use temporary directories and synthetic data.

### API and UI boundaries

- The remote MVP protects `/api/mvp/**` with an opaque PostgreSQL-backed browser
  session, except the explicit login and session-status routes. State-changing
  calls also require same-origin CSRF validation.
- Bearer tokens, `X-API-Key`, browser token storage, authenticated API quotas,
  and job-creation quotas are intentionally unsupported. Persistent per-client
  and global counters apply only to failed password submissions.
  `OPENSTORYLINE_MAX_ACTIVE_JOBS` bounds concurrent queue capacity; it is not a
  per-user or time-window quota.
- New jobs use `POST /api/mvp/sessions/{session_id}/jobs`. The retired unscoped
  `POST /api/mvp/jobs` returns `SESSION_REQUIRED`; polling and artifact routes
  remain job-scoped for compatibility.
- `/health` describes the runtime profile; `/up` is the Kamal proxy health check.
- The original web application uses session and WebSocket message contracts that
  are consumed by `.claude/skills/openstoryline-use/scripts/bridge_openstoryline.py`.
- Preserve status codes, error codes, response shapes, WebSocket event names,
  artifact filenames, and DOM hooks unless the requested change includes a
  coordinated migration.

## Prompt And Runtime Skill Changes

Prompts and `.storyline/skills/` are executable product configuration. Treat a
wording-only edit as a behavior change.

Before changing one:

1. Identify the consuming node/tool and its Pydantic schema.
2. Identify required history/artifact lookups and downstream consumers.
3. Preserve system/user prompt keys and language routing.
4. Add deterministic cases for output shape, tool arguments, invalid output,
   and safety failures.
5. Compare both English and Chinese variants when both exist.

Avoid assertions against exact creative prose. Test required fields, bounds,
evidence use, tool selection, parsing, and failure handling instead.

## Validation Matrix

| Change area | Minimum focused validation |
| --- | --- |
| Pure Python/domain logic | Relevant `tests/test_*.py` file |
| Config model or `config.toml` | Config load command plus affected tests |
| MCP node/tool schema | Node/schema test and invalid-input case |
| Prompt/runtime skill | Consumer test, structured-output failure case, language parity review |
| Provider client | Success, bounded same-model/key failover, invalid response, timeout/error, secret-redaction tests |
| Remote MVP API | Password/session/CSRF auth, failed-login limits, status/error shape, traversal and upload-boundary tests |
| Job/storage code | Atomicity, restart recovery, isolation, corruption, path-safety tests |
| Audit/observability | Document versioning/redaction, bounded CLI, deterministic QC, correlated log tests |
| FFmpeg/rendering | Unit validation plus `tests/test_mvp_render.py` when FFmpeg is installed |
| Docker/Kamal/env | `tests/test_remote_profile.py`, `tests/test_kamal_config.py`, shell syntax, image/config check |
| Web UI | Focused browser smoke on desktop/mobile plus API/WebSocket contract checks |
| Documentation only | Link/path search, command syntax review, bilingual navigation parity |

The fast local non-browser baseline is:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

Database-backed classes skip when `TEST_DATABASE_URL` is unset. Report those
skips explicitly; the baseline is not connected-database evidence. When a
disposable PostgreSQL database is available, its name must start with
`openstoryline_test`:

```bash
TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1/openstoryline_test' \
  PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

Validate every current deployment shell entry point without contacting a live
server:

```bash
bash -n run.sh build_env.sh download.sh bin/kamal-mvp \
  scripts/mvp-postgres-init.sh scripts/mvp-postgres-backup.sh \
  scripts/mvp-postgres-restore-check.sh .kamal/hooks/pre-deploy \
  .kamal/hooks/post-deploy
```

For browser-visible work, run the relevant Python/API checks first, then select
the narrowest local Chromium command:

```bash
cd .qa/web && QA_FAIL_ON_CONSOLE=1 npm run test:smoke
cd .qa/web && QA_PASSWORD='local test password' npm run test:auth:desktop
cd .qa/web && QA_PASSWORD='local test password' npm run test:auth:mobile
```

Use smoke plus at most one affected auth/layout flow by default. All focused
scripts use one worker. Full Playwright and cross-browser runs are explicit
coverage requests, not routine validation in this environment. Capture
screenshots, traces, and videos only on failure; compact JSON/JUnit summaries
may remain under `.qa/web/artifacts/`. Report paths without dumping artifacts.

This project currently has no checked-in formatter, linter, type checker,
coverage threshold, or CI workflow. Adding one should be an explicit tooling
change with dependency and contributor-workflow discussion, not an incidental
part of a feature.

## Deployment And Operations

- Production uses Kamal and `Dockerfile.remote`; Docker Compose is not part of
  the documented production path.
- `.env.kamal.example` documents deploy-machine variables. `.kamal/secrets`
  stores references and is ignored. Never commit resolved secret values.
- `outputs/` is mounted persistently because it contains inputs, work files,
  generated artifacts, and rollback snapshots. The private PostgreSQL
  accessory separately persists database data and the one-file backup
  directory. PostgreSQL remains authoritative for application and audit state.
- The web image runs as fixed non-root UID/GID `65532`. The pre-deploy hook
  prepares only the dedicated outputs directory, preserving root-image
  rollback access and leaving PostgreSQL ownership untouched.
- `src/open_storyline/mvp/audit.py` owns bounded JSON/SRT ingestion, structural
  QC, reviews, filters, and backfill. `kamal app logs` is recent diagnostic
  context only; agents should query the PostgreSQL audit CLI for durable history.
- A release is not verified by a successful build alone. It also needs working
  `/up` and `/health` checks, container/log review, persistent volume checks,
  and a known rollback command.
- Code rollback can still be unsafe when persistent state formats change. Use
  an explicit rollback image version and run its database readiness contract
  before selection; keep job and manifest formats backward compatible.

Agents may validate configuration and build artifacts, but they must not deploy
or modify live servers without explicit authorization.

## Documentation Lifecycle

- `AGENTS.md`: concise enforceable engineering rules.
- `docs/agent-engineering.md`: architecture and validation rationale for agents.
- `docs/mvp/architecture.md`: current remote MVP product/runtime policy.
- `docs/mvp/implementation-history.md`: completed implementation record, not an active plan.
- `.claude/skills/`: operator automation for installation and use.
- `.agents/skills/`: repo-local Codex discovery links to the canonical operator
  skills; do not fork or duplicate the skill contents.
- `.storyline/skills/`: runtime editing behavior loaded by the product.
- `README.md` and `README_zh.md`: user-facing navigation and quick starts.

Delete stale documentation only after moving any still-useful historical
context and updating every inbound link. Do not preserve an obsolete plan in a
location that coding agents may interpret as current instructions.

## Known Repository Constraints

- `agent_fastapi.py` is an opaque tracked binary artifact with a `.py`
  extension. It is not a normal editable Python source file.
- `hf_space.sh` performs destructive branch recreation and a force push. It is
  a publishing script, never a normal verification command.
- `download.sh` retrieves large model/resource archives and should run only for
  an explicitly requested full local installation.
- Full-agent startup requires configured external providers and downloaded
  resources; deterministic unit tests should not require either.
- Real provider checks can incur cost, expose private media, and fail because of
  quota or service state. Run them only with explicit authorization and report
  them separately from the deterministic suite.
