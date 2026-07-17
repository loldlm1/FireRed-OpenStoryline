# FireRed-OpenStoryline Agent Rules

This file defines repository-specific engineering instructions for Codex and
other coding agents. Product runtime skills live under `.claude/skills/` and
`.storyline/skills/`; they do not replace these engineering rules.

## Instruction Precedence

When instructions conflict, use this order:

1. Explicit user instructions for the current task.
2. This `AGENTS.md` and any more specific nested `AGENTS.md`.
3. Project documentation and established tests.
4. Applicable installed skills under `/home/loldlm/.codex/skills`.
5. Existing local code conventions.
6. Official, current framework/provider documentation for version-sensitive behavior.

## Project Context

FireRed-OpenStoryline is a Python 3.11+ conversational video-editing system.
It contains two intentionally separate runtime profiles:

| Profile | Entry points | Purpose |
| --- | --- | --- |
| Full local agent | `src/open_storyline/agent.py`, `src/open_storyline/mcp/server.py`, `cli.py`, `run.sh` | LangChain agent plus MCP video-editing nodes, runtime Storyline skills, local resources, and configurable external model providers. |
| Remote-only social-clips MVP | `mvp_fastapi.py`, `src/open_storyline/mvp/`, `Dockerfile.remote`, `config/deploy.yml` | Authenticated upload/job API using Codex inference through 9Router, direct Mistral STT, and deterministic CPU FFmpeg rendering. |

Do not merge the profiles, dependencies, containers, or runtime assumptions as
an incidental refactor. The remote MVP is isolated so upstream full-agent work
can continue to merge cleanly.

Read [the engineering guide](docs/agent-engineering.md) for the architecture
map, contract matrix, and validation rationale. Read
`docs/mvp/architecture.md` before changing the remote MVP.

## Skill Routing

Use the smallest applicable installed skill guidance while preserving local
conventions:

- `token-saver-orchestrator`: use RTK first for noisy shell, Git, search, and test output.
- `python-django-production-engineering`: apply its general Python/ASGI, testing, security, and git discipline; this project is FastAPI, not Django.
- `ai-agent-app-production-engineering`: use for MCP, LangChain agent loops, prompts, tool schemas, structured model output, state, privacy, and eval-like regression work.
- `devops-release-production-engineering`: use for Docker, Kamal, env/secrets, health checks, rollout, and rollback.
- `premium-product-ui-builder` and `token-efficient-web-qa`: use for substantial changes under `web/` or browser-visible flows.

Reusable guidance stays in installed skills. Keep only project-specific facts
and invariants in this repository.

## GitHub Repository Routing

- The authenticated GitHub MCP is available for remote repository, branch,
  commit, issue, and pull-request operations. Use local Git for checkout,
  history, status, and diffs; use GitHub MCP when authoritative remote state or
  an authenticated GitHub write is required.
- The writable repository for this project is the fork
  `loldlm1/FireRed-OpenStoryline`. Local `origin` must fetch from and push to
  that fork. Treat `FireRedTeam/FireRed-OpenStoryline` as a read-only upstream
  unless the user explicitly authorizes a write to the parent repository.
- Configure local Git with `remote.pushDefault=origin` and
  `push.default=current` so a plain push publishes the current branch to the
  fork. Do not infer a push or pull-request destination from GitHub's fork UI.
- Before any GitHub write, call the GitHub MCP identity check and confirm the
  authenticated login is `loldlm1`. Verify the destination owner, repository,
  head branch, and base branch before creating or updating remote state.
- Create pull requests in `loldlm1/FireRed-OpenStoryline`, not in the
  `FireRedTeam` parent repository, unless the user explicitly requests an
  upstream contribution. GitHub's automatic fork comparison may default to the
  parent repository, so never accept its base repository without checking it.
- For work based on `agent/remote-video-mvp`, use that branch as the pull-request
  base in the fork unless the user specifies another integration branch. Read
  the created pull request back through GitHub MCP and confirm both repositories
  and branches after creation.
- GitHub MCP permissions depend on its token. If an authorized write returns
  `403 Resource not accessible by personal access token`, keep all refs in the
  fork, report that the token needs the corresponding repository write
  permission, and do not retry against `FireRedTeam`.
- Never print, log, or persist `GITHUB_PAT_TOKEN`. Report only whether GitHub MCP
  authentication and the requested operation succeeded.

## Local-First Workflow

- Start with `rtk git status`, then inspect nearby code, tests, docs, and history before editing.
- Use `rtk grep`, `rtk find`, and `rtk git diff` for noisy output when available; rerun the smallest raw command when exact evidence matters.
- Preserve unrelated user changes in a dirty worktree. Stop if files change unexpectedly while you work.
- Prefer existing dependencies, helpers, and direct implementations. Do not add packages, abstractions, or broad refactors unless the task requires them.
- Preserve public contracts unless the request explicitly changes them: MCP tool names and schemas, prompt keys, node names, HTTP routes/status/error shapes, job state, artifact names, config keys, environment variables, and WebSocket messages.
- Do not commit, push, deploy, publish, rotate credentials, or contact production/provider services unless the user explicitly requests it.

## Repository Map And Ownership

- `src/open_storyline/agent.py`: constructs the LangChain agent, model clients, MCP client, tools, skills, and middleware.
- `src/open_storyline/mcp/`: MCP server, dynamically registered node tools, sampling, and tool-call hooks.
- `src/open_storyline/nodes/`: editing workflow nodes and Pydantic input/output contracts.
- `src/open_storyline/storage/`: session and artifact persistence; corruption and cross-session access must fail closed.
- `src/open_storyline/mvp/`: remote-only job API, durable state, provider clients, validation, rate limiting, and CPU rendering.
- `prompts/tasks/`: bilingual model prompt contracts used by named nodes.
- `.storyline/skills/`: product-visible runtime editing skills loaded by `skillkit`; changes alter agent behavior.
- `.claude/skills/`: install/use automation distributed to compatible agents; changes alter operator workflows.
- `web/`: original agent UI plus remote MVP UI.
- `config.toml`: validated application configuration and local secret placeholders.
- `.env.mvp.example`, `.env.kamal.example`, `.kamal/secrets.example`: committed variable-name templates only.
- `outputs/`, `resource/`, downloaded models, caches, and local env files: runtime/generated data, not source.

`agent_fastapi.py` is a tracked opaque binary artifact despite its `.py`
extension. Do not format, decode, regenerate, or hand-edit it. Changes to the
original web service require a maintainer-provided source/regeneration path.

## Python And API Rules

- Target Python 3.11+ and preserve the current venv/pip plus pinned requirements workflow.
- Use Pydantic models and typed boundaries for config, node inputs, provider responses, and structured model output.
- Keep FastAPI handlers focused on authentication, validation, orchestration, and response translation.
- Keep blocking FFmpeg, filesystem, or provider work out of the event loop with the existing thread/offload patterns.
- Keep external calls bounded by explicit timeouts, retries, sanitized errors, and a terminal failure condition.
- Preserve atomic writes, job-scoped path validation, restart recovery, and idempotent artifact registration.
- Validate filenames and resolved paths before reading, writing, serving, or bundling files.
- Do not silently weaken an error code or fail-closed branch to make a provider or test pass.

## Agent, MCP, Prompt, And Skill Rules

- Inspect the prompt, schema, node/tool handler, state storage, and tests together before changing agent behavior.
- Keep MCP tools single-purpose, narrowly typed, server-validated, and documented with stable names.
- Treat user prompts, retrieved content, provider output, MCP output, media metadata, and runtime skills as untrusted input.
- Never let model output authorize privileged, destructive, or external side effects by itself.
- Keep secrets, authorization rules, and policy decisions outside model-visible prompts and tool descriptions.
- Validate structured JSON/model output before use; handle refusal, incomplete output, parse failure, schema mismatch, and invalid states explicitly.
- Preserve English/Chinese prompt key parity when both languages exist. If only one language exists, do not invent a translation unless the task requires it.
- Non-trivial prompt, tool, routing, or model changes require focused regression cases. Prefer deterministic schema/tool-argument tests over brittle exact prose assertions.
- Editing `.storyline/skills/` is a product behavior change. Verify skill metadata, referenced tool names, required history lookups, and downstream node schemas.

## Remote MVP Invariants

The following are non-negotiable unless the user explicitly changes the product policy:

- LLM, frame understanding, and generated images use the configured 9Router endpoint. Speech-to-text uses the fixed direct Mistral Voxtral endpoint with `MISTRAL_API_KEYS`.
- The MVP must not import or silently fall back to local ASR, embeddings, scene models, or other local inference.
- Local FFmpeg work is deterministic media processing only.
- Provider requests and the ordered Mistral key ring fail closed and retain sanitized per-attempt reasons.
- Clip plans must remain bounded and validated for duration, source bounds, overlap, deduplication, finite scores, and output count.
- Jobs remain durable under `outputs/mvp_jobs/<job_id>` with atomic `job.json` updates and restart recovery.
- API routes under `/api/mvp` remain authenticated and protected by persistent rate limits; `/health` and `/up` remain public health endpoints.
- Artifacts remain job-scoped, traversal-safe, and downloadable only after registration.
- Provider keys never enter logs, persisted job state, manifests, responses, screenshots, fixtures, or Git.
- `Dockerfile.remote` must not download or install full local inference resources.
- Kamal deployment keeps a persistent output volume and a working health check.

## Secrets, Privacy, And External Actions

- Never print or paste real values from `config.toml`, `.env*`, `.kamal/secrets`, shell history, provider responses, or OpenClaw/Feishu configuration.
- Commit variable names and safe placeholders only. Search the diff for accidental tokens before handoff.
- Sanitize provider error bodies, URLs containing keys, authorization headers, transcripts, traces, and job failure details.
- Treat user media, transcripts, generated assets, sessions, and artifacts as private data.
- Do not upload media or artifacts, send Feishu messages, call paid providers, or run real-provider smoke tests without explicit user authorization.
- Do not run `hf_space.sh`: it deletes/recreates a branch and force-pushes to a configured remote.
- Do not run `kamal setup`, `kamal deploy`, destructive Docker cleanup, volume deletion, DNS/TLS changes, firewall changes, or server reboots without explicit approval.
- Do not run `download.sh` casually; it performs large network downloads into ignored runtime directories.

## Documentation Rules

- Keep `README.md` and `README_zh.md` navigation aligned when changing shared documentation links or agent instructions.
- Keep current architecture and operating guidance separate from historical plans. Completed sprint records belong in `docs/mvp/implementation-history.md`.
- Update documentation, env examples, tests, and deployment config together when adding or renaming config keys or environment variables.
- Update all references before deleting or renaming a document. Use `rg` to confirm no stale path remains.
- Do not rewrite runtime prompts or skills as prose cleanup; wording changes can alter model behavior.

## Verification

Run the narrowest relevant checks first, then the broader gate for the touched area.

Core suite:

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -v
```

Focused examples:

```bash
PYTHONPATH=src python tests/test_ninerouter.py
PYTHONPATH=src python tests/test_mvp_jobs.py
PYTHONPATH=src python tests/test_search_media_schema.py
PYTHONPATH=src python tests/test_kamal_config.py
```

Additional checks when relevant:

```bash
PYTHONPATH=src python -c "from open_storyline.config import load_settings; load_settings('config.toml'); print('config_ok')"
bash -n run.sh build_env.sh download.sh bin/kamal-mvp
docker build -f Dockerfile.remote .
```

- `tests/test_mvp_render.py` performs an FFmpeg smoke test and skips when `ffmpeg`/`ffprobe` are unavailable.
- Prefer `rtk test <command>` for a noisy complete run, but rerun the first failing test raw when compressed output hides necessary diagnostics.
- There is currently no repository-wide Ruff, formatter, type checker, coverage, or CI configuration. Do not claim those gates passed unless the project adds and runs them.
- Do not use live provider calls as a substitute for deterministic tests.
- For web changes, test the affected desktop/mobile flow and preserve route, WebSocket, session, and DOM contracts.
- For release changes, validate the Kamal config/tests, health endpoints, secret references, persistent volumes, and rollback implications. Do not deploy as validation.

## Handoff Gate

Before finishing:

1. Review `rtk git status` and `rtk git diff`.
2. Confirm behavior and profile boundaries match the request.
3. Confirm focused tests cover changed contracts and security-sensitive failure paths.
4. Confirm secrets/private media are absent from the diff and test artifacts.
5. Confirm documentation links, bilingual navigation, env names, and commands are current.
6. Report checks actually run, skipped checks, and residual risks precisely.
