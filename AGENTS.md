# FireRed-OpenStoryline Agent Rules

This file contains repository-specific engineering instructions. Operator
skills live under `.claude/skills/` and are exposed to Codex through
`.agents/skills/`; product runtime editing skills live under
`.storyline/skills/`.

## Instruction Precedence

When instructions conflict, use this order:

1. Explicit user instructions for the current task.
2. This `AGENTS.md` and any closer nested `AGENTS.md`.
3. Project documentation and established tests.
4. Applicable installed skill guidance selected for the task.
5. Existing local code conventions.
6. Current official framework or provider documentation.

## Project Context

FireRed-OpenStoryline is a Python 3.11+ conversational video-editing system
with two intentionally separate runtime profiles:

| Profile | Entry points | Purpose |
| --- | --- | --- |
| Full local agent | `src/open_storyline/agent.py`, `src/open_storyline/mcp/server.py`, `cli.py`, `run.sh` | LangChain agent, MCP editing nodes, runtime Storyline skills, local resources, and configurable model providers. |
| Remote-only social-clips MVP | `mvp_fastapi.py`, `src/open_storyline/mvp/`, `Dockerfile.remote`, `config/deploy.yml` | Password-authenticated upload/job API using 9Router, direct Mistral STT, PostgreSQL, and deterministic CPU FFmpeg rendering. |

Do not merge the profiles, dependencies, containers, or runtime assumptions as
an incidental refactor. Read `docs/agent-engineering.md` for the contract map
and `docs/mvp/architecture.md` before changing the remote MVP.

## Skill Routing

Use the smallest applicable installed skill while preserving local contracts:

- `token-saver-orchestrator`: RTK-first Git, search, and test output.
- `python-django-production-engineering`: general Python/ASGI, testing,
  security, and Git discipline; this repository uses FastAPI, not Django.
- `ai-agent-app-production-engineering`: MCP, LangChain loops, prompts, tool
  schemas, structured model output, agent state, privacy, and eval regressions.
- `postgres-production-engineering`: migrations, transactions, locking,
  backup/restore, roles, query behavior, and database operational safety.
- `devops-release-production-engineering`: Docker, Kamal, env/secrets, health
  checks, persistent volumes, rollout, and rollback.
- `premium-product-ui-builder`: substantial product UI or UX changes under
  `web/`; do not route ordinary browser verification through it.
- `token-efficient-web-qa`: Playwright or other browser-runner verification,
  failure artifacts, and browser regression triage only.

Reusable guidance stays in installed skills. Keep only FireRed paths,
contracts, commands, and known hazards in this file.

## GitHub Repository Routing

- The writable repository is `loldlm1/FireRed-OpenStoryline`; treat
  `FireRedTeam/FireRed-OpenStoryline` as read-only unless the user explicitly
  requests an upstream contribution.
- Local `origin` must fetch from and push to the fork, with
  `remote.pushDefault=origin` and `push.default=current`.
- Use local Git for checkout, history, status, and diffs. Use authenticated
  GitHub tooling only for authoritative remote state or an authorized write.
- Before a GitHub write, confirm the authenticated login is `loldlm1` and
  verify the destination owner, repository, head branch, and base branch.
- Create fork pull requests against `main` by default. Use another maintained
  integration base only when the user requests it or the work has an unmerged
  dependency branch; read the created pull request back and verify both refs.
- If a write returns `403 Resource not accessible by personal access token`,
  keep refs in the fork and report the missing permission. Do not retry against
  `FireRedTeam`.
- Never expose `GITHUB_PAT_TOKEN`; report only authentication and operation
  success or failure.

## Repository Map And Contracts

- `src/open_storyline/agent.py`: LangChain agent, model clients, MCP client,
  tools, skills, and middleware.
- `src/open_storyline/mcp/`: MCP server and dynamically registered node tools.
- `src/open_storyline/nodes/`: editing nodes and Pydantic input/output contracts.
- `src/open_storyline/storage/`: full-agent session and artifact persistence;
  corruption and cross-session access must fail closed.
- `src/open_storyline/mvp/`: remote API, PostgreSQL state, provider clients,
  validation, authentication, throttling, retention, audit, and CPU rendering.
- `src/open_storyline/mvp/database.py`, `models.py`, `auth.py`, `jobs.py`,
  `audit.py`, and `retention.py`: remote database and lifecycle boundaries.
- `migrations/` and `alembic.ini`: explicit remote MVP schema history.
- `scripts/mvp-postgres-init.sh`, `scripts/mvp-postgres-backup.sh`, and
  `scripts/mvp-postgres-restore-check.sh`: database bootstrap and recovery
  helpers used through the Kamal workflow.
- `prompts/tasks/`: bilingual model prompt contracts used by named nodes.
- `.storyline/skills/`: product-visible editing behavior loaded by `skillkit`.
- `.claude/skills/`: canonical install/use operator automation.
- `.agents/skills/`: Codex repo-discovery links to the canonical operator skills.
- `web/`: original agent UI and remote MVP UI.
- `.env.mvp.example`, `.env.kamal.example`, and `.kamal/secrets.example`:
  committed variable-name templates only.
- `outputs/`, `resource/`, downloaded models, caches, and local env files:
  generated or private runtime data, not source.

Preserve public MCP tool names/schemas, prompt keys, node names, HTTP routes,
status/error shapes, job states, artifact names, config keys, environment
variables, WebSocket messages, and DOM hooks unless the task explicitly changes
the contract. Keep English/Chinese prompt keys aligned where both exist.

`agent_fastapi.py` is a tracked opaque binary artifact despite its `.py`
extension. Do not format, decode, regenerate, or hand-edit it without a
maintainer-provided source/regeneration path.

## Remote MVP Invariants

The following remain fixed unless the user explicitly changes product policy:

- LLM, frame understanding, and generated images use configured 9Router routes;
  speech-to-text uses direct Mistral Voxtral with `MISTRAL_API_KEYS`.
- The remote image must not import or fall back to local ASR, embeddings, scene
  models, or other local inference. Local FFmpeg work is deterministic media
  processing only.
- PostgreSQL is authoritative for browser sessions, failed-login buckets,
  editing sessions, jobs, artifact metadata, ordered events, audit evidence,
  reviews, holds, and retention state.
- `outputs/mvp_jobs/<job_id>` remains job-owned media/work storage.
  `job.json` is a derived rollback snapshot and never the live state source.
- Persistent throttling applies only to failed password submissions. Successful
  logins, authenticated reads, and job creation do not consume time-window
  quotas. `OPENSTORYLINE_MAX_ACTIVE_JOBS` is queue capacity, not a user quota.
- Clip plans remain bounded for duration, source bounds, overlap,
  deduplication, finite scores, and output count.
- Routes under `/api/mvp` remain session-authenticated and CSRF-protected;
  `/health` and `/up` remain public health endpoints.
- Artifacts remain job-scoped, traversal-safe, and downloadable only after
  registration. Provider keys never enter logs, database state, manifests,
  responses, screenshots, fixtures, or Git.
- `Dockerfile.remote` must not install full local inference resources. Kamal
  keeps persistent output, PostgreSQL data, and backup directories plus a
  working health check.

## Project Hazards And External Actions

- Treat `config.toml`, `.env*`, `.kamal/secrets`, OpenClaw/Feishu config,
  provider responses, user media, transcripts, sessions, and artifacts as
  private. Commit names and safe placeholders only.
- Do not upload media, send Feishu messages, call live/paid providers, touch a
  production database, deploy, rotate credentials, or mutate a VPS unless the
  user explicitly authorizes that action.
- Do not run `hf_space.sh`; it deletes/recreates a branch and force-pushes.
- Do not run `download.sh` as routine validation; it downloads large ignored
  model/resource archives.
- Do not run `kamal setup`, `kamal deploy`, destructive Docker cleanup, volume
  deletion, DNS/TLS/firewall changes, or server reboots as validation.

## Documentation Rules

- Keep `README.md` and `README_zh.md` aligned when changing shared links or
  agent instructions.
- Keep current architecture separate from historical plans; completed sprint
  records belong in `docs/mvp/implementation-history.md`.
- Update docs, env examples, tests, and deployment config together when config
  keys or environment variables change.
- Use `rg` to update every inbound reference before renaming or deleting a
  document. Do not rewrite runtime prompts or `.storyline/skills/` as prose
  cleanup because wording changes product behavior.

## Verification

Run focused checks first. Use `.venv/bin/python` unless an equivalent activated
environment is explicitly documented.

Fast local non-browser baseline; database-backed classes skip when
`TEST_DATABASE_URL` is unset, so report those skips rather than calling this
connected-database evidence:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

Focused examples:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests/test_ninerouter.py -v
PYTHONPATH=src .venv/bin/python -m unittest tests/test_mvp_jobs.py -v
PYTHONPATH=src .venv/bin/python -m unittest tests/test_search_media_schema.py -v
PYTHONPATH=src .venv/bin/python -m unittest tests/test_kamal_config.py -v
```

For connected PostgreSQL evidence, use a disposable database whose name starts
with `openstoryline_test`; replace placeholders locally and never paste a real
connection URL into docs, chat, logs, or commits:

```bash
TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1/openstoryline_test' \
  PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

Additional checks when relevant:

```bash
PYTHONPATH=src .venv/bin/python -c "from open_storyline.config import load_settings; load_settings('config.toml'); print('config_ok')"
bash -n run.sh build_env.sh download.sh bin/kamal-mvp \
  scripts/mvp-postgres-init.sh scripts/mvp-postgres-backup.sh \
  scripts/mvp-postgres-restore-check.sh .kamal/hooks/pre-deploy
docker build -f Dockerfile.remote .
```

- `tests/test_mvp_render.py` skips when `ffmpeg` or `ffprobe` is unavailable.
- Prefer `rtk test <command>` for noisy runs; rerun only the first failing test
  raw when compressed output hides the diagnostic.
- The repository has no Ruff, formatter, type checker, coverage, or CI gate;
  do not claim those checks passed.
- Run project-native Python checks before browser QA. For local browser changes,
  select the narrowest Chromium command and keep one worker:

  ```bash
  cd .qa/web && QA_FAIL_ON_CONSOLE=1 npm run test:smoke
  cd .qa/web && QA_PASSWORD='local test password' npm run test:auth:desktop
  cd .qa/web && QA_PASSWORD='local test password' npm run test:auth:mobile
  ```

  Run smoke plus at most one affected auth/layout test by default. Full
  Playwright, cross-browser, and production-URL runs are opt-in. Keep
  screenshots, traces, and videos failure-only; compact JSON/JUnit summaries
  may be written under `.qa/web/artifacts/`. Report artifact paths rather than
  dumping their contents.
- Release changes require Kamal config/tests, health endpoints, secret
  references, persistent volumes, and rollback review; do not deploy to validate.

Before handoff, review status/diff, profile boundaries, focused security
failures, secret/private-data absence, bilingual navigation, env names, and the
exact checks and skips reported.
