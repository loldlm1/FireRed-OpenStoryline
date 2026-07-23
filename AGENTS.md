# FireRed-OpenStoryline Agent Rules

FireRed-OpenStoryline is a Python 3.11+ remote conversational video-editing
service. The deprecated full-local CLI/MCP application is not part of this
repository. The supported runtime is `mvp_fastapi.py` plus the code under
`src/open_storyline/mvp/`, deployed with `Dockerfile.remote` and Kamal.

## Instruction Precedence

1. Explicit user instructions for the current task.
2. This file and any closer nested `AGENTS.md`.
3. Project documentation and established tests.
4. Applicable installed skill guidance.
5. Existing local conventions.
6. Current official framework or provider documentation.

## Engineering Posture

- Preserve public HTTP routes, status/error shapes, job states, artifact names,
  config keys, environment names, WebSocket/SSE messages, and DOM hooks unless
  the task explicitly changes them.
- Keep authoritative business state and rules in deterministic application code
  and PostgreSQL. Treat model providers as replaceable integrations.
- Keep model outputs typed, validated, bounded, and separated from raw shell,
  FFmpeg, filesystem, database, and privileged operations.
- Prefer small, explicit changes and existing helpers. Add complexity only when
  it provides concrete product or operational value.
- Include failure modes, privacy, security, observability, rollout, and rollback
  when they materially affect the change.

## Skill Routing

Use the smallest applicable installed skill while preserving local contracts:

- `token-saver-orchestrator`: RTK-first Git, search, and test output.
- `python-django-production-engineering`: Python/ASGI, testing, security, and
  Git discipline; this repository uses FastAPI, not Django.
- `ai-agent-app-production-engineering`: model loops, prompts, structured
  outputs, state, privacy, evals, and agent boundaries.
- `postgres-production-engineering`: migrations, locking, backup/restore,
  transactions, and database safety.
- `devops-release-production-engineering`: Docker, Kamal, secrets, health
  checks, persistent volumes, rollout, and rollback.
- `premium-product-ui-builder`: substantial product UI/UX work under `web/`.
- `token-efficient-web-qa`: Playwright/browser verification only.

## GitHub Routing

- The writable repository is `loldlm1/FireRed-OpenStoryline`; treat
  `FireRedTeam/FireRed-OpenStoryline` as read-only unless explicitly requested.
- Local `origin` must fetch from and push to the fork, with
  `remote.pushDefault=origin` and `push.default=current`.
- Use local Git for checkout, history, status, and diffs. Use authenticated
  GitHub tooling only for authoritative remote state or an authorized write.
- Before a GitHub write, verify login `loldlm1` and the destination owner,
  repository, head branch, and base branch.
- Never print, log, or persist `GITHUB_PAT_TOKEN`.

## Repository Map

- `mvp_fastapi.py`: FastAPI application, lifecycle, middleware, UI routing, and
  public health endpoints.
- `src/open_storyline/mvp/`: remote API, PostgreSQL state, provider clients,
  validation, authentication, rendering, review, repair, retention, and audit.
- `src/open_storyline/mvp/settings.py`: remote-only configuration models and
  `config.toml` loader.
- `migrations/` and `alembic.ini`: explicit PostgreSQL schema history.
- `scripts/mvp-postgres-*.sh`: database bootstrap, backup, and restore checks.
- `creative_catalog/`: licensed creative assets and generated manifest.
- `web/mvp.html` and `web/static/mvp/`: Agentic-only remote UI.
- `Dockerfile.remote`, `Dockerfile.ffmpega`, and `Dockerfile.quality`: separate
  remote web, effect, and quality images.
- `config/deploy.yml`, `.kamal/`, and `bin/kamal-mvp`: Kamal release workflow.
- `.env.mvp.example`, `.env.kamal.example`, and `.kamal/secrets.example`:
  committed variable-name templates only.
- `outputs/`, downloaded media/models, caches, and local env files: generated or
  private runtime data, never source.

## Remote MVP Invariants

- LLM, frame understanding, and generated images use configured 9Router routes;
  speech-to-text uses direct Mistral Voxtral with `MISTRAL_API_KEYS`.
- The remote image must not import or fall back to local ASR, embeddings, scene
  models, or other local inference. Local FFmpeg work is deterministic only.
- PostgreSQL is authoritative for browser sessions, failed-login buckets,
  editing sessions, jobs, artifacts, ordered events, audit evidence, reviews,
  holds, checkpoints, and retention state.
- `outputs/mvp_jobs/<job_id>` remains job-owned work storage. `job.json` is a
  derived rollback snapshot and never the live state source.
- Persistent throttling applies only to failed password submissions.
  `OPENSTORYLINE_MAX_ACTIVE_JOBS` is queue capacity, not a user quota.
- Clip and edit plans remain bounded for duration, source bounds, overlap,
  deduplication, finite scores, capability count, and output count.
- Routes under `/api/mvp` remain session-authenticated and CSRF-protected;
  `/health` and `/up` remain public.
- Artifacts remain job-scoped, traversal-safe, and downloadable only after
  registration. Provider keys never enter logs, database state, manifests,
  responses, screenshots, fixtures, or Git.
- `Dockerfile.remote` must not install local inference resources. Kamal keeps
  persistent output, PostgreSQL data, and backup directories plus a working
  health check.

## External Actions And Private Data

- Treat `config.toml`, `.env*`, `.kamal/secrets`, provider responses, user
  media, transcripts, sessions, and artifacts as private. Commit names and safe
  placeholders only.
- Do not upload media, call live/paid providers, touch a production database,
  deploy, rotate credentials, or mutate a VPS without explicit authorization.
- Do not run destructive Docker cleanup, volume deletion, DNS/TLS/firewall
  changes, or server reboots as validation.
- Resolve and validate filesystem targets before deletion. Never use broad
  recursive targets or unresolved environment variables.

## Documentation

- Keep `README.md` and `README_zh.md` aligned for shared instructions and links.
- Keep current architecture separate from historical plans; completed sprint
  records belong in `docs/mvp/implementation-history.md`.
- Update docs, env examples, tests, and deployment config together when config
  keys or environment variables change.
- Use `rg` to update every inbound reference before renaming or deleting a file.
- Model prompts and schemas are executable contracts; do not rewrite them as
  prose cleanup.

## Verification

Use `.venv/bin/python` and run focused checks before broader ones. Prefer
`rtk test <command>` for noisy output.

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
PYTHONPATH=src .venv/bin/python -c "from open_storyline.mvp.settings import load_mvp_settings; load_mvp_settings('config.toml'); print('config_ok')"
bash -n bin/kamal-mvp scripts/mvp-postgres-init.sh \
  scripts/mvp-postgres-backup.sh scripts/mvp-postgres-restore-check.sh \
  .kamal/hooks/pre-deploy .kamal/hooks/post-deploy
docker build -f Dockerfile.remote .
```

Database-backed tests skip when `TEST_DATABASE_URL` is unset. Connected test
databases must have names beginning with `openstoryline_test`; never paste a
real connection URL into docs, chat, logs, or commits.

`tests/test_mvp_render.py` skips when FFmpeg or FFprobe is unavailable. The
repository has no Ruff, formatter, type checker, coverage, or CI gate; do not
claim those checks passed.

For browser changes, run project-native Python checks first, then the narrowest
one-worker Chromium command under `.qa/web`. Keep screenshots, traces, and
videos failure-only and report artifact paths instead of dumping them.

Before handoff, review status/diff, runtime boundaries, security failures,
secret/private-data absence, bilingual navigation, env names, rollback, and the
exact checks and skips reported.
