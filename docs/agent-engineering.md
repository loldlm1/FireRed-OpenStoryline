# Agent Engineering Guide

This guide explains the architecture and verification choices behind
[`AGENTS.md`](../AGENTS.md). The repository contains one supported application:
the remote social-clips MVP. The former local LangChain/CLI/MCP runtime and its
nodes, skills, resources, and operator automation were removed after the remote
build/import boundary was proven independent.

## Runtime Architecture

```text
Browser/API client
    -> FastAPI password session and CSRF middleware
    -> PostgreSQL editing sessions, prompt versions, jobs, events, and artifacts
    -> job-owned media workspace
    -> direct Mistral Voxtral timestamped STT
    -> 9Router planning and bounded frame understanding
    -> typed, validated edit plan
    -> deterministic CPU FFmpeg render and optional typed FFMPEGA effects
    -> deterministic QA, bounded model review, and promotion policy
```

`Dockerfile.remote` copies only `mvp_fastapi.py`, the MVP package, migrations,
the remote web assets, and the licensed creative catalog. It does not receive a
general source-tree copy or local inference resources.

## Source Of Truth

| Concern | Primary source | Regression evidence |
| --- | --- | --- |
| Runtime settings | `src/open_storyline/mvp/settings.py`, `config.toml` | Settings/import tests |
| API/auth/CSRF | `mvp_fastapi.py`, `mvp/api.py`, `mvp/auth.py` | App/auth/session tests |
| Database state | `mvp/models.py`, `database.py`, `jobs.py`, migrations | Database/job/audit tests |
| Providers | `mvp/ninerouter.py`, `remote_stt.py`, `remote_image.py` | Provider contract tests |
| Planning/review | `mvp/edit_plan.py`, `repair.py`, `creative_qa.py` | Schema, repair, and eval tests |
| Rendering/effects | `mvp/render.py`, `compositor.py`, `ffmpega.py` | Render and capability tests |
| Deployment | `Dockerfile.remote`, `config/deploy.yml`, `bin/kamal-mvp` | Profile/Kamal/build checks |

## Trust Boundaries

Provider output is untrusted. Structured responses must validate before they
can affect plan state, rendering, artifacts, or promotion. Models may choose
creative intent and typed repair strategies, but they cannot issue raw FFmpeg,
shell, filesystem, database, or deployment commands.

Secrets and authorization decisions stay outside model-visible prompts. Logs,
audit documents, manifests, and error responses store bounded metadata and
sanitized reasons rather than provider bodies, frame bytes, transcripts, or
credentials.

## State And Filesystem

PostgreSQL is authoritative for browser sessions, failed-login buckets,
editing sessions, prompt versions, jobs, artifacts, ordered events, audit
evidence, reviews, holds, checkpoints, and retention state.

Media/work files remain under `outputs/mvp_jobs/<job_id>` and reusable uploaded
sources under `outputs/mvp_sessions/<session_id>`. Every resolved path must stay
inside its expected job or session root. `job.json` is a derived rollback
snapshot, not live state.

Job transitions and ordered events commit transactionally. The in-process
worker uses advisory ownership and execution fencing so restart recovery cannot
silently produce concurrent authoritative writers.

## API And UI Contracts

Routes under `/api/mvp` require the opaque PostgreSQL-backed browser session,
except the explicit login and session-status endpoints. State-changing requests
also require same-origin CSRF validation. `/health` and `/up` remain public.

Artifact downloads are job-scoped and require prior registration. Preserve
route paths, status codes, structured error codes, response fields, event names,
artifact names, and DOM hooks unless a coordinated migration explicitly changes
them.

The remote product currently retains `web/mvp-legacy.html` as a compatibility
surface. Its removal belongs to the Agentic-only data and rollout migration,
not to the deleted full-local runtime.

## Model And Render Contracts

- 9Router owns planning, vision, and generated-image routing.
- Direct Mistral Voxtral owns timestamped STT.
- CPU FFmpeg owns deterministic media transforms and technical validation.
- FFMPEGA is optional, pinned, and restricted to typed allowlisted operations.
- Model output never overrides deterministic security or technical blockers.
- Calls are bounded by explicit timeouts, retries, schemas, and evidence budgets.

## Validation Matrix

| Change area | Minimum focused validation |
| --- | --- |
| Pure Python/domain logic | Relevant `tests/test_*.py` |
| Settings/config | Settings load plus import-boundary tests |
| Provider client | Success, invalid response, timeout, retry, redaction |
| API/auth | Session, CSRF, status/error shape, path/upload safety |
| Jobs/storage | Atomicity, recovery, isolation, fencing, corruption |
| Review/repair | Schema failures, budgets, no-op suppression, eval fixture |
| FFmpeg/render | Unit checks plus render test when FFmpeg is installed |
| Docker/Kamal/env | Remote-profile, Kamal, shell syntax, image build |
| Web UI | Focused one-worker browser smoke after Python checks |

Fast deterministic baseline:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

Database-backed classes skip when `TEST_DATABASE_URL` is unset. Connected
evidence must use a disposable database whose name begins with
`openstoryline_test`.

```bash
PYTHONPATH=src .venv/bin/python -c "from open_storyline.mvp.settings import load_mvp_settings; load_mvp_settings('config.toml'); print('config_ok')"
bash -n bin/kamal-mvp scripts/mvp-postgres-init.sh \
  scripts/mvp-postgres-backup.sh scripts/mvp-postgres-restore-check.sh \
  .kamal/hooks/pre-deploy .kamal/hooks/post-deploy
docker build -f Dockerfile.remote .
```

Provider probes and deployment commands are release operations, not routine
unit validation. They can incur cost or mutate external state and require
explicit authorization.

## Deployment And Rollback

Production uses Kamal. The web image runs as fixed non-root UID/GID `65532` and
mounts persistent output storage. PostgreSQL data and the backup directory are
separate persistent volumes.

A release is not verified by a build alone. It needs database readiness,
provider capability gates, `/up` and `/health`, container/log observation,
persistent-volume checks, and a known rollback image. Schema and artifact format
changes must remain compatible with that rollback or explicitly block it.

The full-local removal rollback is the Sprint 2 parent commit plus its previous
dependency environment. Reverting the deletion restores source, but operators
would still need to rebuild the retired local dependency environment. The
validated remote image remains the supported rollback path for the active
product.

## Documentation Lifecycle

- `AGENTS.md`: enforceable repository rules.
- `docs/agent-engineering.md`: architecture and validation rationale.
- `docs/mvp/architecture.md`: current product/runtime policy.
- `docs/mvp/implementation-history.md`: completed historical work.
- `README.md` and `README_zh.md`: aligned user/operator entry points.

Keep current architecture separate from historical plans. Update every inbound
reference before deleting or renaming documents, commands, routes, artifacts,
config keys, or environment variables.
