# Plan: Codex Skills Alignment And Fast Validation

**Generated**: 2026-07-17
**Status**: Planning only; implementation not started
**Estimated Complexity**: Medium

## Overview

Align the repository-owned agent instructions, operator skills, validation
commands, and browser-QA harness with the currently installed Codex production
skills. The implementation will correct the stale PostgreSQL and rate-limit
contracts, remove unsafe secret-bearing commands, require explicit Feishu send
approval, provide current Codex skill discovery, and replace generic browser
ports with the remote MVP's actual local default.

The work is intentionally split into small sprints. Each sprint must be
validated with focused, fast checks and committed independently before the next
sprint begins. Browser validation is limited to the directly affected
Playwright specs in Chromium with one worker. The final Sprint 3 gate runs the
full local non-browser Python suite, but no full browser matrix or hosted CI run
is required.

## Scope

- **In scope**:
  - Make `scripts/update_config.py` safe for secret-valued configuration.
  - Update `.claude/skills/openstoryline-install/` and
    `.claude/skills/openstoryline-use/` to avoid agent-visible secrets and
    unauthorized external sends.
  - Sanitize Feishu helper errors and success output without adding a package.
  - Refresh `AGENTS.md` and `docs/agent-engineering.md` for PostgreSQL authority,
    current throttling behavior, database/release validation, current skill
    routing, and the maintained pull-request base.
  - Add repo-local Codex discovery for the existing operator skills without
    duplicating their contents.
  - Align English and Chinese Codex skill-installation guidance.
  - Standardize `.qa/web` on the remote MVP's local port and add focused
    Chromium commands.
  - Add deterministic tests for the changed helper and operator scripts.
- **Out of scope**:
  - Full Playwright, cross-browser, screenshot, trace, video, or device matrices.
  - Capybara setup or execution; this repository has no Capybara suite.
  - Hosted CI dispatch or rerunning every repository test on every sprint; the
    full local non-browser Python suite runs once at the final gate.
  - Live 9Router, Mistral, Feishu, OpenClaw, or production-provider calls.
  - Production PostgreSQL access, production backup/restore, Kamal deployment,
    VPS mutation, or retention execution.
  - Database schema, Alembic migration, application API, prompt, model, rendering,
    or media-retention behavior changes.
  - Replacing `config.toml` with a new configuration architecture.
  - Packaging the two skills as a Codex plugin; repo-local discovery and the
    current skill installer are sufficient for this patch.
- **Fixed decisions**:
  - Preserve the full local agent and remote-only MVP as separate profiles.
  - Preserve the 9Router/Mistral/FFmpeg provider policy and all public contracts.
  - PostgreSQL remains authoritative for remote MVP application state;
    `job.json` remains a derived rollback snapshot.
  - Only failed password submissions consume persistent rate-limit counters;
    active-job limits remain capacity controls rather than quotas.
  - Keep `.claude/skills` as the canonical checked-in skill content for Claude
    compatibility; expose the same directories to Codex through symlinks.
  - Reuse pinned `httpx`; do not add `requests` or another dependency.
  - Use `.venv/bin/python` in documented repository commands unless the text
    explicitly says an equivalent environment is already activated.
  - Use Chromium, one worker, and a single named Playwright spec or test title
    for implementation-time browser checks.
- **Assumptions**:
  - The implementer starts from a clean worktree and preserves unrelated changes.
  - The repository virtualenv remains available at `.venv/`.
  - Playwright 1.61.1 and its Chromium revision are already installed under
    `.qa/web`; if not, installation is a separate prerequisite, not a sprint task.
  - A disposable local MVP instance and non-production password can be made
    available for the localized browser checks.
  - A disposable `openstoryline_test*` database may be used when a focused
    PostgreSQL check is explicitly needed, but the full PostgreSQL suite is not
    required for these documentation/helper changes.

## Named Resources

- **Project instructions**:
  - `AGENTS.md`
  - `docs/agent-engineering.md`
  - `docs/mvp/architecture.md`
  - `docs/mvp/audit-and-database.md`
- **Implementation files**:
  - `scripts/update_config.py`
  - `.claude/skills/openstoryline-install/SKILL.md`
  - `.claude/skills/openstoryline-install/agents/openai.yaml`
  - `.claude/skills/openstoryline-use/SKILL.md`
  - `.claude/skills/openstoryline-use/agents/openai.yaml`
  - `.claude/skills/openstoryline-use/scripts/feishu_file_sender.py`
  - `.agents/skills/openstoryline-install` (symlink to canonical skill)
  - `.agents/skills/openstoryline-use` (symlink to canonical skill)
  - `README.md`
  - `README_zh.md`
  - `.qa/web/.env.example`
  - `.qa/web/README.md`
  - `.qa/web/package.json`
  - `.qa/web/playwright.config.ts`
- **Tests and validation**:
  - New `tests/test_update_config.py`
  - New `tests/test_feishu_file_sender.py`
  - `tests/test_kamal_config.py`
  - `tests/test_remote_profile.py`
  - `tests/test_mvp_auth.py`
  - `tests/test_mvp_database.py`
  - `.qa/web/tests/smoke.spec.ts`
  - `.qa/web/tests/mvp-auth-sessions.spec.ts`
  - Current skill validator:
    `/home/loldlm/.codex/skills/.system/skill-creator/scripts/quick_validate.py`
- **Installed skill guidance**:
  - `/home/loldlm/.codex/skills/ai-agent-app-production-engineering/`
  - `/home/loldlm/.codex/skills/devops-release-production-engineering/`
  - `/home/loldlm/.codex/skills/postgres-production-engineering/`
  - `/home/loldlm/.codex/skills/python-django-production-engineering/`
  - `/home/loldlm/.codex/skills/token-efficient-web-qa/`
  - `/home/loldlm/.codex/skills/token-saver-orchestrator/`
- **External documentation**:
  - Current Codex skill authoring and discovery:
    `https://learn.chatgpt.com/docs/build-skills`
  - Playwright focused test CLI and `--grep`/file filtering:
    `https://playwright.dev/docs/test-cli`
- **Operational resources**:
  - `.env.mvp.example` for the canonical local MVP port.
  - `requirements.txt` for the existing `httpx` dependency.
  - `requirements-remote.txt`, `alembic.ini`, and `migrations/` for validation
    context only; no dependency or migration edits are planned.

## Prerequisites

- Confirm `rtk git status` is clean before Sprint 1.
- Confirm `.venv/bin/python` imports `httpx`, `fastapi`, `sqlalchemy`, and
  `alembic` without exposing environment values.
- Confirm `.qa/web/node_modules/.bin/playwright --version` reports the pinned
  Playwright version before attempting browser execution.
- Use only synthetic markers, local test passwords, temporary config copies,
  and disposable database names during validation.
- Do not initialize planner execution state during this planning turn. Before
  implementation, read
  `/home/loldlm/.codex/skills/planner/references/execution-state.md` and
  initialize active-plan state as required by `$planner`.

## Sprint 1: Harden Operator Skill Security

**Goal**: The full-agent install/use skills can configure secrets and optionally
send a Feishu artifact without placing secrets in command arguments, logs, or
provider-response output, and without sending until the user explicitly
authorizes the destination.

**Dependencies**: Clean worktree, repository virtualenv, no provider access.

**Tracked scope**:
`scripts/update_config.py`,
`.claude/skills/openstoryline-install/SKILL.md`,
`.claude/skills/openstoryline-use/SKILL.md`,
`.claude/skills/openstoryline-use/scripts/feishu_file_sender.py`,
`tests/test_update_config.py`, `tests/test_feishu_file_sender.py`

**Commit**: `fix(skills): harden secret and Feishu workflows`

**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_update_config.py -v`
- `PYTHONPATH=src .venv/bin/python -m unittest tests/test_feishu_file_sender.py -v`
- `python /home/loldlm/.codex/skills/.system/skill-creator/scripts/quick_validate.py .claude/skills/openstoryline-install`
- `python /home/loldlm/.codex/skills/.system/skill-creator/scripts/quick_validate.py .claude/skills/openstoryline-use`
- `rg -n -- '--set .*api_key|--set .*access_token|Send success:.*result' .claude/skills scripts`
- Expected: focused tests pass, both skills validate, and the search finds no
  secret-bearing example command or full Feishu response print.

**Rollback point**: The pre-Sprint-1 commit. Reverting the single sprint commit
restores the old helper and skill instructions without touching user config or
external services.

### Task 1.1: Add Secret-Safe Config Input

- **Location**: `scripts/update_config.py`, `tests/test_update_config.py`
- **Description**:
  - Add a mutually exclusive stdin-based option such as `--set-stdin KEY` while
    preserving `--set KEY=VALUE` for non-secret automation.
  - Read the value without including it in the process argument list.
  - Stop printing configured values for both modes; report only the updated key.
  - Preserve current scalar coercion, comment preservation, path validation,
    and exit codes.
  - Use temporary config copies in tests; never read or write real local keys.
- **Dependencies**: Existing `parse_assignment`, `coerce_value`, and
  `update_text` helpers.
- **Acceptance criteria**:
  - Secret values never appear in stdout or stderr on success.
  - `--set` and `--set-stdin` cannot be supplied together or both omitted.
  - Existing boolean, integer, float, and string updates remain compatible.
  - Invalid key paths and malformed TOML retain actionable, non-secret errors.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_update_config.py -v`
  - Run the helper against a temporary config with a synthetic marker and use
    `rg` to confirm the marker is absent from captured output.
- **Rollback**: Revert Task 1.1 changes; no migration or persistent state exists.

### Task 1.2: Update Install And Use Skill Secret Workflows

- **Location**:
  `.claude/skills/openstoryline-install/SKILL.md`,
  `.claude/skills/openstoryline-use/SKILL.md`
- **Description**:
  - Keep model names and base URLs configurable with non-secret `--set` examples.
  - Replace API-key, Pexels-key, TTS-key, token, and access-token examples with
    the stdin-based flow or a clearly user-local manual-edit instruction.
  - State that agents must verify presence/config loading without reading or
    echoing the values.
  - Remove wording that asks users to paste actual API keys into chat.
  - Preserve full-agent-only scope, bilingual triggers, ports, session workflow,
    and output verification.
- **Dependencies**: Task 1.1 CLI contract.
- **Acceptance criteria**:
  - No skill example places a secret literal in command arguments.
  - The install and usage workflows remain executable for non-secret settings.
  - Both skills retain valid frontmatter and matching `agents/openai.yaml`.
- **Validation**:
  - Run `quick_validate.py` for both skill directories.
  - `rg -n -- '--set .*api_key|--set .*access_token|REPLACE_WITH_REAL_KEY' .claude/skills`
    returns no unsafe command example.
- **Rollback**: Revert the two skill files with the Sprint 1 commit.

### Task 1.3: Gate And Sanitize Feishu Sending

- **Location**:
  `.claude/skills/openstoryline-use/SKILL.md`,
  `.claude/skills/openstoryline-use/scripts/feishu_file_sender.py`,
  `tests/test_feishu_file_sender.py`
- **Description**:
  - Require explicit user confirmation of the artifact path, receive ID, and
    receive-ID type before the documented send command is allowed.
  - Add a required mechanical confirmation flag such as `--confirm-send` so an
    accidental invocation fails before loading credentials or making HTTP calls.
  - Replace `requests` with the already-pinned `httpx` client and preserve bounded
    token, upload, and message timeouts.
  - Normalize Feishu failures to stage, HTTP status, provider code, and a bounded
    sanitized message; never include token payloads, headers, credentials, or
    complete provider bodies.
  - Emit a minimal machine-readable success record containing only `ok`, the
    non-secret destination type, and a stable message identifier when available.
  - Remove the ad-hoc `pip install requests` instruction.
- **Dependencies**: Existing OpenClaw account resolution and Feishu API contract.
- **Acceptance criteria**:
  - Invocation without confirmation performs no config read or network call.
  - Mocked token, upload, and send failures expose no synthetic secret marker.
  - Successful mocked send output excludes the complete Feishu response.
  - No real Feishu or OpenClaw endpoint is contacted by tests.
- **Validation**:
  - `PYTHONPATH=src .venv/bin/python -m unittest tests/test_feishu_file_sender.py -v`
  - `PYTHONPATH=src .venv/bin/python .claude/skills/openstoryline-use/scripts/feishu_file_sender.py --help`
- **Rollback**: Revert the Feishu script and instructions; no external message is
  sent during implementation validation.

### Sprint 1 Gate

- [ ] All Sprint 1 tasks complete.
- [ ] Both focused Python test files pass.
- [ ] Both checked-in skills pass the current skill validator.
- [ ] Synthetic secret markers are absent from command output and diffs.
- [ ] No external HTTP request or file upload occurred.
- [ ] Residual risks are documented.
- [ ] Exactly one Sprint 1 commit is created with the proposed sprint message.
- [ ] The pre-Sprint-1 rollback point is recorded.
- [ ] Sprint 2 has not started before this gate completes.

## Sprint 2: Align Repository And Codex Instructions

**Goal**: Repository guidance accurately describes the current PostgreSQL MVP,
routes work to the installed skill stack without personal paths or generic
duplication, uses the maintained fork base, and exposes the existing operator
skills through current Codex repo discovery.

**Dependencies**: Sprint 1 gate complete.

**Tracked scope**:
`AGENTS.md`, `docs/agent-engineering.md`, `README.md`, `README_zh.md`,
`.agents/skills/openstoryline-install`, `.agents/skills/openstoryline-use`

**Commit**: `docs(agents): align repository guidance with current Codex stack`

**Demo/Validation**:

- `PYTHONPATH=src .venv/bin/python tests/test_kamal_config.py`
- `PYTHONPATH=src .venv/bin/python tests/test_remote_profile.py`
- `PYTHONPATH=src .venv/bin/python tests/test_mvp_auth.py`
- `PYTHONPATH=src .venv/bin/python tests/test_mvp_database.py`
- `test -L .agents/skills/openstoryline-install && test -L .agents/skills/openstoryline-use`
- `python /home/loldlm/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/openstoryline-install`
- `python /home/loldlm/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/openstoryline-use`
- `rg -n 'Jobs remain durable under|protected by persistent rate limits|agent/remote-video-mvp|/home/loldlm/.codex/skills' AGENTS.md docs README.md README_zh.md`
- Expected: fast structural/security tests pass, symlinked skills validate, and
  stale contract/base/path text is absent.

**Rollback point**: Sprint 1 commit. Reverting the Sprint 2 commit removes only
documentation and repo-local skill discovery; canonical `.claude` skill content
and application behavior remain intact.

### Task 2.1: Refresh PostgreSQL And Rate-Limit Contracts

- **Location**: `AGENTS.md`, `docs/agent-engineering.md`
- **Description**:
  - State that PostgreSQL is authoritative for browser sessions, editing
    sessions, jobs, artifact metadata, ordered events, audit evidence, and
    retention state.
  - Describe `outputs/mvp_jobs/<job_id>` as job-owned media/work storage and
    `job.json` as a derived rollback snapshot rather than authoritative state.
  - State that persistent throttling protects failed password submissions only;
    authenticated reads and job creation do not consume time-window quotas.
  - State that `OPENSTORYLINE_MAX_ACTIVE_JOBS` is queue capacity.
  - Name `migrations/`, `alembic.ini`, database modules, audit/retention modules,
    and PostgreSQL backup/restore scripts in the repository map where useful.
  - Extend release invariants to cover PostgreSQL data and backup volumes without
    duplicating the reusable backup/restore skill policy.
- **Dependencies**: Current architecture and tests; no application change.
- **Acceptance criteria**:
  - `AGENTS.md`, `docs/agent-engineering.md`, and `docs/mvp/architecture.md` agree
    on state authority, snapshots, throttling, and capacity.
  - No wording can reasonably instruct an implementer to restore retired
    filesystem-authoritative or authenticated-quota behavior.
- **Validation**:
  - Focused `test_mvp_auth.py`, `test_mvp_database.py`, and
    `test_kamal_config.py` runs.
  - Targeted `rg` comparison across the three architecture/instruction files.
- **Rollback**: Revert documentation only.

### Task 2.2: Correct Skill Routing, Scope, And GitHub Base

- **Location**: `AGENTS.md`
- **Description**:
  - Remove the hardcoded `/home/loldlm/.codex/skills` path from precedence.
  - Add `postgres-production-engineering` for migrations, locking, backup,
    restore, and database operational safety.
  - Route `premium-product-ui-builder` to UI/UX work and
    `token-efficient-web-qa` only to browser-runner verification.
  - Replace the retired `agent/remote-video-mvp` PR-base rule with fork `main` as
    the default unless the user identifies another maintained integration base.
  - Prune generic Git, minimal-change, model-security, secret, and handoff prose
    already supplied by installed personal skills, while retaining FireRed
    contracts, dangerous script names, external-action boundaries, bilingual
    prompt parity, and exact project commands.
- **Dependencies**: Task 2.1 so retained local invariants are known.
- **Acceptance criteria**:
  - Every retained rule names a FireRed path, contract, profile, provider,
    artifact, command, or known project hazard, except minimal precedence text.
  - No personal filesystem path or deleted remote base remains.
  - The file remains concise enough to avoid duplicating skill manuals.
- **Validation**:
  - `rg` stale-path/base search from Sprint 2 validation.
  - Manual comparison against the installed skill routing list.
- **Rollback**: Revert `AGENTS.md` within the Sprint 2 commit.

### Task 2.3: Add Current Codex Skill Discovery And Bilingual Guidance

- **Location**:
  `.agents/skills/openstoryline-install`,
  `.agents/skills/openstoryline-use`, `README.md`, `README_zh.md`
- **Description**:
  - Add relative symlinks from `.agents/skills/` to the canonical
    `.claude/skills/` directories; do not copy or fork skill contents.
  - Document that Codex discovers repo skills under `.agents/skills` when
    launched in the repository.
  - Replace or clearly de-emphasize the third-party `npx skills add` examples in
    favor of repo discovery and `$skill-installer` for GitHub-path installation.
  - Keep Claude Code and OpenClaw instructions intact.
  - Keep English and Chinese navigation and commands semantically aligned.
- **Dependencies**: Current Codex Build Skills documentation; Sprint 1 leaves
  canonical skills valid.
- **Acceptance criteria**:
  - Both symlinked skills resolve inside the repository and pass
    `quick_validate.py`.
  - README guidance clearly separates Claude, Codex repo discovery, and optional
    global installation.
  - English and Chinese instructions name the same paths and supported flow.
- **Validation**:
  - `readlink` and `test -L` for both symlinks.
  - Run the current skill validator through both `.agents/skills` paths.
  - `rg` both README sections for path and command parity.
- **Rollback**: Delete the two symlinks and revert the bilingual README edits.

### Task 2.4: Replace The Misleading Complete-Test Command

- **Location**: `AGENTS.md`, `docs/agent-engineering.md`
- **Description**:
  - Use `.venv/bin/python` or explicitly require an activated equivalent.
  - Label the no-database suite as a fast baseline and state that skipped
    `TEST_DATABASE_URL` tests are not connected-database evidence.
  - Document the disposable-test-database command separately, with the required
    `openstoryline_test*` database-name guard and a placeholder-only URL.
  - Extend shell syntax validation to
    `scripts/mvp-postgres-init.sh`, `scripts/mvp-postgres-backup.sh`,
    `scripts/mvp-postgres-restore-check.sh`, and `.kamal/hooks/pre-deploy`.
  - Keep live provider, deployment, production database, and destructive
    commands outside deterministic validation.
- **Dependencies**: Task 2.1 current database contract.
- **Acceptance criteria**:
  - No command is described as complete when it silently skips database tests.
  - Fast and connected-database evidence are clearly distinguished.
  - Every current tracked deployment shell entry point is included in syntax
    validation.
- **Validation**:
  - Run only the focused test files named in Sprint 2 Demo/Validation.
  - `bash -n run.sh build_env.sh download.sh bin/kamal-mvp scripts/mvp-postgres-init.sh scripts/mvp-postgres-backup.sh scripts/mvp-postgres-restore-check.sh .kamal/hooks/pre-deploy`
- **Rollback**: Revert the validation documentation; no database is mutated.

### Sprint 2 Gate

- [ ] All Sprint 2 tasks complete.
- [ ] Focused Python structural/security tests pass; skips are recorded exactly.
- [ ] Both repo-discovered skills validate through `.agents/skills`.
- [ ] English and Chinese README guidance is aligned.
- [ ] Stale PostgreSQL, quota, personal-path, and PR-base text is absent.
- [ ] No production database, provider, GitHub write, or deploy command ran.
- [ ] Residual risks are documented.
- [ ] Exactly one Sprint 2 commit is created with the proposed sprint message.
- [ ] The Sprint 1 commit is recorded as the rollback point.
- [ ] Sprint 3 has not started before this gate completes.

## Sprint 3: Localize Remote MVP Browser QA

**Goal**: The checked-in Playwright harness defaults to the actual local MVP
port and exposes fast commands for one smoke, desktop-auth, or mobile-auth test
without running the full suite or additional browsers.

**Dependencies**: Sprint 2 gate complete, local Chromium installed, disposable
local MVP server available only for actual browser execution.

**Tracked scope**:
`.qa/web/.env.example`, `.qa/web/README.md`, `.qa/web/package.json`,
`.qa/web/playwright.config.ts`, `AGENTS.md`, `docs/agent-engineering.md`

**Commit**: `test(web): localize remote MVP Playwright checks`

**Demo/Validation**:

- `cd .qa/web && ./node_modules/.bin/playwright test tests/smoke.spec.ts --project=chromium --workers=1 --list`
- `cd .qa/web && ./node_modules/.bin/playwright test tests/mvp-auth-sessions.spec.ts --project=chromium --workers=1 --grep 'mobile login' --list`
- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v`
- With a disposable local server:
  `cd .qa/web && BASE_URL=http://127.0.0.1:8000 QA_PASSWORD='<local-test-password>' QA_FAIL_ON_CONSOLE=1 npm run test:smoke`
- If the UI-affecting documentation/script change needs authenticated coverage,
  run exactly one of `npm run test:auth:desktop` or
  `npm run test:auth:mobile`, not both by default.
- Expected: collection succeeds without starting all projects; the selected
  Chromium test passes against the local server; no cross-browser suite runs.

**Rollback point**: Sprint 2 commit. Reverting Sprint 3 restores only browser-QA
defaults, scripts, and their repository documentation.

### Task 3.1: Standardize The Local QA URL

- **Location**:
  `.qa/web/.env.example`, `.qa/web/README.md`,
  `.qa/web/playwright.config.ts`
- **Description**:
  - Replace generic `127.0.0.1:3000` and undocumented `127.0.0.1:18000`
    defaults with `http://127.0.0.1:8000`, matching `.env.mvp.example`.
  - Preserve `BASE_URL` overrides for intentionally different local ports.
  - Keep production URLs and real credentials explicitly prohibited.
- **Dependencies**: `.env.mvp.example` remains the canonical local port source.
- **Acceptance criteria**:
  - Every default/example URL in `.qa/web` uses port 8000 unless explicitly
    demonstrating an override.
  - Playwright configuration still honors an explicit `BASE_URL` first.
- **Validation**:
  - `rg -n '127\.0\.0\.1:(3000|18000)' .qa/web`
  - Playwright `--list` commands from Sprint 3 validation.
- **Rollback**: Revert the three QA files.

### Task 3.2: Add Focused Chromium Scripts

- **Location**: `.qa/web/package.json`, `.qa/web/README.md`
- **Description**:
  - Add compact one-worker scripts for:
    - generic smoke only;
    - desktop authentication/session flow only;
    - mobile login/layout flow only.
  - Keep existing broader scripts for explicit use, but document that they are
    not the default validation path for this repository-alignment work.
  - Use Playwright file filters and `--grep` supported by the pinned CLI rather
    than copying test code.
- **Dependencies**: Existing spec titles remain stable.
- **Acceptance criteria**:
  - Each new npm script collects exactly the intended test(s) under Chromium.
  - Scripts default to one worker and compact output.
  - No new browser, reporter, or package dependency is introduced.
- **Validation**:
  - Run each new script with Playwright `--list` or equivalent direct command.
  - Run only `test:smoke` plus at most one affected auth test against the local
    disposable server.
- **Rollback**: Remove the added scripts and README examples.

### Task 3.3: Document The Localized Browser Gate

- **Location**: `AGENTS.md`, `docs/agent-engineering.md`
- **Description**:
  - Replace generic desktop/mobile browser guidance with the exact `.qa/web`
    focused commands.
  - Require project-native unit/structural checks before Playwright.
  - State that Chromium is the default and that full cross-browser coverage is
    opt-in, not part of routine validation in this environment.
  - Require failure-only artifacts and concise reporting consistent with the
    installed token-efficient web QA skill.
- **Dependencies**: Tasks 3.1 and 3.2 define the commands.
- **Acceptance criteria**:
  - Implementers can identify one exact command for smoke, desktop auth, or
    mobile auth without reading the whole harness.
  - Instructions do not require Capybara, full Playwright, or production access.
- **Validation**:
  - `rg -n 'test:smoke|test:auth:desktop|test:auth:mobile' AGENTS.md docs/agent-engineering.md .qa/web/README.md .qa/web/package.json`
- **Rollback**: Revert only the two documentation files.

### Sprint 3 Gate

- [ ] All Sprint 3 tasks complete.
- [ ] Playwright collection targets only Chromium and the named spec/test.
- [ ] One localized browser test passes against a disposable local server.
- [ ] The full local non-browser Python suite passes, with connected-database
      skips reported accurately.
- [ ] No full Playwright, cross-browser, Capybara, or hosted CI run occurred.
- [ ] Failure artifacts, if any, are recorded by path without dumping full logs.
- [ ] Residual risks are documented.
- [ ] Exactly one Sprint 3 commit is created with the proposed sprint message.
- [ ] The Sprint 2 commit is recorded as the rollback point.

## Testing Strategy

- **Unit**:
  - Add deterministic tests for stdin config updates, redacted output, CLI
    exclusivity, Feishu confirmation gating, sanitized errors, and minimal
    success output.
  - Run only the new test files during Sprint 1.
- **Integration**:
  - Use the existing fast `test_kamal_config.py`, `test_remote_profile.py`,
    `test_mvp_auth.py`, and `test_mvp_database.py` modules to verify that edited
    instructions match executable contracts.
  - Record `TEST_DATABASE_URL` skips; do not represent them as connected evidence.
  - No full PostgreSQL integration run is required because no database code or
    migration changes are planned.
  - Run the full local non-browser Python suite once at the final Sprint 3 gate;
    do not repeat it after every sprint.
- **End-to-end/browser**:
  - Use Chromium only, one worker, one spec or `--grep` target.
  - Run generic smoke and at most one directly affected auth/mobile test.
  - Use a local disposable password and URL only.
- **Security/privacy**:
  - Use synthetic secret markers and assert they are absent from stdout, stderr,
    exceptions, skill examples, and diffs.
  - Mock all Feishu HTTP operations.
  - Confirm no provider response body or OpenClaw credential content is emitted.
- **Accessibility/responsiveness**:
  - Reuse the existing mobile login/layout test only; do not add a broad audit.
  - Preserve focus and horizontal-overflow assertions already present.
- **Migration/operations**:
  - No migration is planned.
  - Run shell syntax validation for all current database/deploy scripts.
  - Do not run Kamal, backup, restore, retention, or production health commands.
- **Performance**:
  - No load or media-render benchmark is needed.
  - Keep browser execution serial and focused to minimize local resource use.

## Risks And Gotchas

| Risk | Impact | Mitigation | Validation signal |
| --- | --- | --- | --- |
| A secret is still passed through argv or printed | Credential exposure through logs, shell history, or agent output | Add stdin mode, suppress values, use synthetic-marker tests and `rg` | Marker absent from captured output and skill examples |
| Feishu helper still sends without informed approval | Private media may reach the wrong chat/user | Require explicit instruction text and `--confirm-send` before config/network access | No-confirmation unit test observes zero HTTP calls |
| Provider errors leak response content | Tokens, IDs, or private metadata may appear in logs | Normalize errors to bounded fields and mock hostile bodies | Secret-marker failure tests pass |
| Symlinked skills break another agent | Repo skill discovery could become inconsistent | Keep `.claude` canonical, use relative links, validate both link paths | `readlink`, `test -L`, and `quick_validate.py` pass |
| `AGENTS.md` becomes generic again | Context bloat and future drift | Retain only project-specific nouns/contracts and route reusable policy to skills | Manual line-level review plus stale/generic search |
| Fast suite is mistaken for full DB evidence | PostgreSQL regressions may be missed in unrelated work | Label baseline and disposable-DB gates separately and report skips | Test output and handoff explicitly count DB skips |
| QA port changes break intentional custom ports | Local browser tests target the wrong server | Preserve `BASE_URL` precedence and document overrides | Config test plus explicit override smoke |
| Browser test is still too slow | Implementation stalls in constrained environment | Chromium, one worker, one file/title, failure-only artifacts | Command line and report show one selected test/project |
| README translations diverge | Operators receive different install instructions | Update English and Chinese together in Sprint 2 | Targeted link/path parity search |

## Rollback Plan

- **Sprint 1**: Revert the single Sprint 1 commit. No external requests, config
  migrations, or user configuration writes are part of validation.
- **Sprint 2**: Revert the single Sprint 2 commit to restore prior instructions
  and remove `.agents/skills` symlinks. Canonical `.claude` skills remain intact.
- **Sprint 3**: Revert the single Sprint 3 commit to restore prior QA defaults
  and scripts. No browser-generated artifact is source-controlled.
- Roll back in reverse sprint order when multiple sprints must be undone.
- Do not use database downgrade, restore, deploy rollback, or provider changes;
  none are required by this plan.
- Record each sprint commit SHA as its rollback point before starting the next
  sprint.

## Execution Order

1. Before implementation, read the planner execution-state reference and
   initialize active-plan state.
2. Implement Sprint 1 only.
3. Run and record all focused Sprint 1 validation.
4. Create exactly one Sprint 1 commit and record its SHA as the rollback point.
5. Start Sprint 2 only after the Sprint 1 gate passes.
6. Repeat validation, one-commit, and rollback-point recording for Sprint 2.
7. Start Sprint 3 only after the Sprint 2 gate passes.
8. Run only the localized Playwright checks defined by Sprint 3.
9. Run the full local non-browser Python suite and record skips separately from
   connected-database evidence.
10. Create exactly one Sprint 3 commit and record completion evidence.

## Completion Checklist

- [ ] Every sprint has passed its focused validation gate.
- [ ] Every sprint has exactly one sprint-specific commit.
- [ ] Secret-bearing commands and outputs are removed.
- [ ] Feishu send approval and sanitized output are enforced.
- [ ] PostgreSQL, throttling, capacity, and rollback-snapshot instructions agree.
- [ ] Current Postgres and web-QA skills are routed only when applicable.
- [ ] Current fork `main` is the documented default PR base.
- [ ] Both operator skills are discoverable through `.agents/skills` and valid.
- [ ] English and Chinese Codex guidance is aligned.
- [ ] Browser QA uses port 8000, Chromium, one worker, and named tests.
- [ ] The full local non-browser Python suite passed at the final gate.
- [ ] No hosted CI, full Playwright, cross-browser, Capybara, live provider,
  production database, or deployment action was performed.
- [ ] Residual risks and rollback SHAs are current.
