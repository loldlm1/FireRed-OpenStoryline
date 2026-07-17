# Plan: 9Router And Kamal VPS QA Readiness

**Generated**: 2026-07-16
**Status**: Execution in progress; Sprints 1-2 complete
**Estimated Complexity**: High

## Overview

Prepare the FireRed-OpenStoryline remote MVP and the 9Router instance on
`root@82.39.186.26` for repeatable personal-server QA. The work separates four
independent concerns: local Kamal version/configuration, 9Router process
supervision and data protection, provider credentials/model availability, and
an automated modality preflight before deployment.

Discovery shows that the VPN is not the cause of the current Kamal failure:
SSH reaches the VPS, 9Router answers on both loopback and its public address,
and the failure occurs locally because Kamal `2.10.1` rejects the
`stop_timeout` key while the repository requires `2.12.0`. All three active
Codex OAuth connections are serving successful requests. The user has also
enabled a Mistral Free-mode API key in 9Router. Mistral exposes the required
`voxtral-mini-2602` offline transcription model, but the installed 9Router
`0.5.35` does not yet publish Mistral models through `/v1/models/stt`.

The fixed provider policy is intentionally small: Codex OAuth owns text,
vision, and image generation; Mistral owns speech-to-text; there are no runtime
provider fallbacks. A missing, expired, rate-limited, or incompatible selected
provider is a release-blocking failure rather than permission to route private
media or prompts to another provider.

The remote MVP must remain remote-inference-only. FFmpeg remains the only local
media processing dependency. No provider token is written to this repository,
FireRed job state, or logs.

## Scope

- **In scope**:
  - Make Kamal configuration parse and provide a network preflight that proves
    the deploy machine, VPS, Docker, and 9Router paths are reachable.
  - Run 9Router as a supervised service instead of from an XFCE/RDP terminal.
  - Protect 9Router SQLite data and endpoint access while preserving the
    personal HTTP workflow.
  - Reconcile the three required model contracts with live 9Router catalogs:
    Codex text/vision, Codex image generation, and Mistral STT.
  - Add a redacted modality preflight and regression coverage for the exact
    FireRed contracts: JSON chat, multimodal chat, binary image output, and
    timestamped `verbose_json` STT.
  - Execute a canary Kamal deployment and one synthetic end-to-end video QA
    run after the prerequisites are satisfied.
- **Out of scope**:
  - Merging the full local agent and remote MVP profiles.
  - Adding local ASR, embeddings, scene models, or other local inference.
  - Replacing 9Router with a different proxy or changing the product's public
    API/WebSocket/job contracts.
  - Adding TTS, general audio understanding, or any text, vision, image, or STT
    fallback provider.
  - Deploying, rotating, revoking, or publishing credentials during planning.
  - Making OAuth account choices on behalf of the user when a browser login or
    provider-admin action is required.
- **Fixed decisions**:
  - Use only `cx/gpt-5.6-sol` through Codex OAuth for text planning, structured
    JSON, and frame/vision understanding.
  - Use only `cx/gpt-5.5-image` through Codex OAuth for image generation while
    that model remains in `/v1/models/image` and passes the binary image
    contract. Do not configure a second image model.
  - Use only `mistral/voxtral-mini-2602` for STT. 9Router must request Mistral
    segment timestamps and preserve non-empty `text` plus finite `start`/`end`
    segment fields accepted by `RemoteSttCascade.normalize_segments`.
  - Do not configure provider fallbacks. Each layer fails closed with a
    sanitized, actionable error when its selected provider is unavailable.
  - Keep provider credentials inside 9Router. FireRed receives only the
    9Router endpoint URL and endpoint key.
  - HTTP is acceptable for this personal deployment, but the public 9Router
    port should be restricted where practical; the app container should use a
    host-local route when 9Router runs on the same VPS.
- **Assumptions**:
  - The 9Router service and FireRed remote MVP will run on the same VPS.
  - The existing Mistral Free-mode key remains active in 9Router. The agent can
    configure and verify the connection after execution authorization without
    copying the provider key into FireRed or command output.
  - The current Codex OAuth sessions remain active. The user is needed only if
    Codex requires interactive browser login/consent or the existing Mistral
    key is invalid, revoked, or expired.
  - A short non-private synthetic audio/video fixture is available for canary
    testing. No private media is uploaded during preflight.
  - The local deploy machine may install or select Kamal `2.12.0`, but no
    remote deploy is performed until the user explicitly authorizes execution.

## Named Resources

- **Project instructions**: `AGENTS.md`, `docs/agent-engineering.md`,
  `docs/mvp/architecture.md`.
- **Deployment/configuration**: `config/deploy.yml`, `bin/kamal-mvp`,
  `Dockerfile.remote`, `config.toml`, `.env.kamal.example`,
  `.kamal/secrets.example`.
- **Provider clients/contracts**: `src/open_storyline/mvp/ninerouter.py`,
  `src/open_storyline/utils/remote_image.py`,
  `src/open_storyline/utils/remote_stt.py`,
  `src/open_storyline/mvp/pipeline.py`.
- **Tests/docs**: `tests/test_ninerouter.py`,
  `tests/test_remote_image.py`, `tests/test_remote_stt.py`,
  `tests/test_kamal_config.py`, `tests/test_remote_profile.py`,
  `docs/mvp/api-keys.md`, `docs/mvp/imagenes-generadas.md`.
- **VPS resources**: `/home/admin/.9router/db/data.sqlite`, its WAL/SHM files,
  `/home/admin/.9router/jwt-secret`, `/home/admin/.9router/auth/cli-secret`,
  and the current `admin`-owned `9router -n -l` process on port `20128`.
- **Current official references**:
  - [9Router STT skill](https://github.com/decolua/9router/blob/master/skills/9router-stt/SKILL.md)
  - [9Router image skill](https://github.com/decolua/9router/blob/master/skills/9router-image/SKILL.md)
  - [9Router provider registry](https://github.com/decolua/9router/tree/master/open-sse/providers/registry)
  - [Kamal configuration reference](https://github.com/basecamp/kamal/blob/main/lib/kamal/configuration/docs/configuration.yml)
  - [Mistral offline transcription](https://github.com/mistralai/platform-docs-public/blob/main/public/studio-api/audio/speech_to_text/offline_transcription.md)
  - [Mistral Free-mode API keys](https://github.com/mistralai/platform-docs-public/blob/main/public/getting-started/quickstarts/studio/activate-and-generate-api-key.md)
  - [Mistral usage and limits](https://github.com/mistralai/platform-docs-public/blob/main/src/content/en/docs/admin/billing-usage/usage-limits/page.mdx)

## Prerequisites

- The provider policy is already approved: `cx/gpt-5.6-sol` for text/vision,
  `cx/gpt-5.5-image` for image generation, and
  `mistral/voxtral-mini-2602` for STT, with no provider fallbacks.
- A root-only backup destination exists for the 9Router SQLite database before
  permission or supervision changes.
- The deploy machine has Docker and can select/install Kamal `2.12.0`.
- Provider tokens remain in 9Router's dashboard/database; values are never
  pasted into this plan, Git, command output, or FireRed `.env` files.
- The user separately authorizes any `kamal deploy`, service restart, firewall
  change, OAuth reconnect, provider inference canary, or provider-account
  mutation. Repository-only implementation follows the sprint gates.

## Discovery Baseline

These are read-only observations used to design the plan. They are not a claim
that any sprint validation has passed:

- SSH access to the VPS works with key authentication, and remote Docker is
  available. Local-to-VPS SSH latency was normal for the current VPN route.
- 9Router answers `/api/health` through loopback and the VPS public address.
  Its endpoint-key policy is enabled: unauthenticated/invalid requests return
  `401`, while the configured FireRed endpoint key authenticates successfully.
- The installed 9Router package is the current published `0.5.35`, but it is
  launched by `9router -n -l` from an XFCE/RDP terminal and writes stdout/stderr
  to that terminal. No systemd, PM2, or Docker supervisor owns the process.
- Three Codex OAuth connections are active, contain refresh credentials, and
  recent request history shows successful traffic through each connection.
- `cx/gpt-5.6-sol` is the configured text/vision model and has passed text,
  structured JSON, and vision probes through Codex OAuth.
- `/v1/models/image` currently exposes `cx/gpt-5.5-image`,
  `cx/gpt-5.4-image`, and `cx/gpt-5.3-image`. The selected policy uses only
  `cx/gpt-5.5-image`; the current FireRed Gemini/Grok image defaults are stale.
- The Mistral Free-mode organization shows `voxtral-mini-2602` with 50,000
  tokens per minute and 1 request per second. Its organization-wide audio
  limit shows 3,600 audio seconds per minute and no numeric monthly value.
  The dash is not treated as a permanent unlimited-use guarantee.
- 9Router registers Mistral in its general catalog, but its authenticated
  `/v1/models/stt` catalog currently contains only Groq Whisper models. This
  is a 9Router provider-adapter/catalog gap, not a VPN, Kamal, endpoint-key, or
  FireRed URL problem.
- OpenRouter, Gemini CLI, Groq, Hugging Face, Deepgram, Cloudflare, and
  Nemotron are not selected for any FireRed runtime layer under this plan.
- The local deploy machine selects Kamal `2.10.1`. `kamal config` fails locally
  on `stop_timeout` before any SSH/network action; the repository requires
  Kamal `2.12.0`.
- Local `.env.kamal` exists but is mode `664`, and `.kamal/secrets` is not yet
  present. On the VPS, the 9Router SQLite database is mode `644` inside
  group/world-traversable directories. Both require secret-hygiene work before
  treating the setup as robust.

## Agentic Ownership Boundary

| Action | Agent can perform after execution authorization | User action required |
| --- | --- | --- |
| Repository config/tests/docs/preflight | Yes | None; provider policy is approved |
| Kamal version selection and config validation | Yes | Approve installation if the required gem is absent |
| Read-only SSH/log/database metadata inspection | Yes | None while existing SSH access remains valid |
| 9Router backup and read-only process/UFW inspection | Yes | None while the live process remains untouched |
| Codex OAuth model setup and health verification | Yes | Complete interactive provider login/consent only if Codex requests it |
| Mistral STT model setup and health verification | Yes with the existing 9Router connection | Replace/re-enter the key only if it is invalid, revoked, or expired |
| 9Router Mistral STT adapter or pinned upgrade | Yes, using a maintained source/package path | Approve the service restart window; no hand-edit of the global package |
| Add provider keys to FireRed `.env.kamal` | Not needed | Do not add them; FireRed should retain only the 9Router endpoint key |
| Canary deploy and live provider smoke | Yes after all gates | Explicit deploy/provider-call authorization |

No additional provider key should be added to FireRed environment files. The
existing Mistral provider key remains inside 9Router. The only FireRed-side
updates are the 9Router URL/key references, the three approved model IDs,
timeouts, and application web token already represented by the Kamal
configuration.

## Sprint 1: Release Toolchain And Network Preflight

**Goal**: Make the deployment configuration parse locally and prove that a
future deploy will not be blocked by the VPN or basic VPS reachability.

**Dependencies**: None. Preserve the current deployed services and 9Router
process while this sprint runs.

**Tracked scope**: `bin/kamal-mvp`, `config/deploy.yml`,
`tests/test_kamal_config.py`, and a new redacted preflight script under
`scripts/` if the implementation chooses to add one.

**Commit**: `build: align kamal release toolchain`

**Demo/Validation**:

- Select Kamal `2.12.0` and run `kamal config` with `.env.kamal`; expected
  result is a successful parse with no secret values in output.
- Run `ssh -o BatchMode=yes root@82.39.186.26 true`; expected result is zero.
- Run remote `docker version` and authenticated `/api/health`/`/v1/models`
  checks; expected result is reachable Docker and HTTP responses.
- Confirm that no network request is attempted before local config parsing by
  preserving a sanitized config-error log for negative tests.

**Rollback point**: Restore the previous `bin/kamal-mvp` and `config/deploy.yml`
from the Sprint 1 commit; no remote state is changed by the validation.

### Task 1.1: Enforce the Required Kamal Version

- **Location**: `bin/kamal-mvp`, optional `tests/test_kamal_config.py` coverage.
- **Description**: Compare the installed Kamal version with the repository's
  `minimum_version`/pinned version and fail with an actionable message instead
  of silently using an older installed executable. Keep installation explicit;
  do not auto-upgrade a production tool during an unrelated deploy.
- **Dependencies**: None.
- **Acceptance criteria**:
  - Kamal `2.10.1` is rejected before deployment with a clear upgrade command.
  - Kamal `2.12.0` parses the existing `stop_timeout` and `drain_timeout` keys.
  - The version check does not print environment values.
- **Validation**:
  - `bash -n bin/kamal-mvp`
  - `KAMAL_VERSION=<installed-old-version> ./bin/kamal-mvp config` in a sanitized
    fixture environment, expecting a version failure.
  - `kamal config` using the selected `2.12.0` executable.
- **Rollback**: Revert the version-check change and keep the old executable
  only for local diagnostics; do not deploy until the gate is restored.

### Task 1.2: Add A Redacted Connectivity Preflight

- **Location**: New `scripts/qa_ninerouter.py` or `scripts/qa_ninerouter.sh`,
  plus `docs/mvp/api-keys.md` and focused tests.
- **Description**: Validate URL normalization, endpoint-key authentication,
  SSH reachability, `/api/health`, `/v1/models`, `/v1/models/image`,
  `/v1/models/stt`, and the future container-to-host route.
  Emit status/model ordinal and sanitized error categories only.
- **Dependencies**: Task 1.1; endpoint key loaded by the caller, never stored
  in the script or output.
- **Acceptance criteria**:
  - Missing and invalid endpoint keys produce `401`; the configured key produces
    an authenticated response.
  - A trailing slash in `NINEROUTER_URL` is normalized consistently.
  - The script distinguishes transport failure, auth failure, catalog mismatch,
    provider failure, invalid media, and missing timestamp segments.
- **Validation**:
  - Run the catalog/auth/health portion from the deploy machine without making
    provider inference calls.
  - Run the same HTTP checks from a disposable app-like container after the
    container route is available.
  - Scan captured output for `Bearer`, API-key-shaped strings, emails, and
    private prompts; expect no matches.
- **Rollback**: Remove or disable the preflight script; it has no runtime side
  effects and does not alter provider state.

### Sprint 1 Gate

- [x] All Sprint 1 tasks complete.
- [x] Kamal config validation and network preflight evidence are recorded.
- [x] No provider or VPS state was changed by validation.
- [x] Exactly one Sprint 1 commit is created with the proposed message.
- [x] The rollback point is recorded.
- [x] Sprint 2 has not started before this gate completes.

## Sprint 2: Non-Disruptive 9Router Protection And Observation

**Goal**: Protect and document the live 9Router process without restarting it,
changing its `admin` user, changing port `20128`, replacing its manual launch,
or changing its UFW exposure during an active inference session.

**Dependencies**: Sprint 1 gate; root access; a verified database backup.

**Tracked scope**: A live-process backup/access runbook under `docs/mvp/` and
read-only validation evidence. No systemd unit, process supervisor, port,
user, or UFW mutation is in scope for this sprint.

**Commit**: `ops: preserve live 9router during qa`

**Demo/Validation**:

- Capture a root-only backup of `/home/admin/.9router/db` and verify it can be
  opened read-only.
- Verify the existing `admin` process, port `20128`, `/api/health`,
  `/v1/models`, and current launch command without restarting it.
- Capture a root-only SQLite backup and read-only restore/integrity evidence.
- Record current UFW and file-mode state without changing either.

**Rollback point**: Preserve the current process command (`9router -n -l`),
the current data directory, backup identifier, and UFW rule snapshot. No
service rollback is performed because the live process is not replaced.

### Task 2.1: Back Up And Tighten 9Router Data Permissions

- **Location**: VPS paths under `/home/admin/.9router` and a new operator
  runbook in `docs/mvp/`.
- **Description**: Back up `data.sqlite`, `data.sqlite-wal`, and
  `data.sqlite-shm` with a consistent SQLite snapshot; test a disposable
  read-only restore; record current directory/database/credential modes; and
  do not print database contents or change live permissions.
- **Dependencies**: Sprint 1 gate.
- **Acceptance criteria**:
  - Restore procedure is documented and tested against a copy, not the live
    database.
  - The backup is root-only and passes `PRAGMA integrity_check` both before and
    after copying it to a disposable restore path.
  - Current permissions are recorded for a later maintenance window rather
    than changed while the process is serving inference.
- **Validation**:
  - `stat` checks for directory/database/secret modes.
  - Read-only SQLite integrity query against the backup.
  - Authenticated catalog and health checks while the existing process remains
    untouched.
- **Rollback**: No live state rollback is needed; preserve the backup and
  discard only disposable restore copies.

### Task 2.2: Document The Existing Manual Service And Log Path

- **Location**: The current process metadata and a tracked runbook under
  `docs/mvp/`.
- **Description**: Record the resolved Node/9Router command, `admin` owner,
  working directory, port `20128`, parent/child process relationship, and
  available journal/terminal evidence. Do not install, enable, restart, or
  replace the manual launch process.
- **Dependencies**: Task 2.1.
- **Acceptance criteria**:
  - The current process is owned by `admin`, listens on `20128`, and keeps
    `/api/health` available during read-only inspection.
  - Any captured logs are checked for tokens, prompts, and authorization
    headers without exposing their contents.
  - The runbook records that a future supervisor requires a separate
    maintenance window and is not part of this active inference run.
- **Validation**:
  - `ps`, `ss`, `/api/health`, authenticated catalog probes, and redacted
    process/log metadata.
- **Rollback**: None; the existing launch command remains authoritative.

### Task 2.3: Record Existing App Connectivity And Public Access

- **Location**: VPS UFW rules, 9Router bind/proxy settings, and
  `docs/mvp/9router-vps-runbook.md`.
- **Description**: Record the current public/VPS/container access paths and
  endpoint-key behavior. Do not alter UFW, bind addresses, port `20128`, or
  the public HTTP workflow during this active inference session.
- **Dependencies**: Task 2.2.
- **Acceptance criteria**:
  - The existing FireRed/container route and dashboard access path are
    documented without changing the live route.
  - `/v1` requests still require the endpoint key.
- **Validation**:
  - Read-only UFW rule review and unauthenticated/authenticated HTTP checks.
  - Existing container route probe only if it does not restart or reconfigure
    9Router.
- **Rollback**: No firewall or endpoint rollback is performed in this sprint.

### Sprint 2 Gate

- [x] Database backup and disposable restore evidence are recorded.
- [x] The existing `admin`/`20128` manual process remains healthy and untouched.
- [x] Current permissions, logs, endpoint auth, and firewall scope are reviewed.
- [x] Exactly one Sprint 2 commit is created with the proposed message.
- [x] The backup identifier and UFW/process snapshots are recorded.
- [x] Sprint 3 has not started before this gate completes.

## Sprint 3: Codex And Mistral Model Contract Setup

**Goal**: Configure exactly one approved provider/model for each required
FireRed inference layer and make all three contracts available through 9Router.

**Dependencies**: Sprint 1 and Sprint 2 gates. Remote 9Router package/service
changes require explicit execution authorization and the Sprint 2 backup.

**Tracked scope**: `config.toml`, `.env.kamal.example`, `.env.mvp.example`,
`config/deploy.yml`, `src/open_storyline/config.py`, provider docs, and tests.
External scope is the 9Router provider dashboard, persisted connection records,
and a versioned 9Router source/package path if Mistral STT is still absent from
the latest supported release. Do not hand-edit the global compiled package.

**Commit**: `fix: lock ninerouter provider contracts`

**Demo/Validation**:

- `/v1/models` contains `cx/gpt-5.6-sol`; text JSON and vision probes pass
  through Codex OAuth.
- `/v1/models/image` contains `cx/gpt-5.5-image`; that exact model returns
  decodable image bytes under the configured size limit.
- `/v1/models/stt` contains `mistral/voxtral-mini-2602`; that exact model
  returns non-empty text and timestamped segments through FireRed's existing
  OpenAI-compatible transcription request.
- FireRed has no configured text, vision, image, or STT fallback. A selected
  provider failure remains sanitized and release-blocking.

**Rollback point**: Record the previous 9Router version/package, service unit,
database backup, and FireRed model lists. Restore the previous package/service
and configuration if the Mistral adapter or any Codex contract regresses.

### Task 3.1: Lock Text And Vision To Codex OAuth

- **Location**: 9Router Codex connections; `config.toml`; environment templates;
  `docs/mvp/api-keys.md`.
- **Description**: Keep the active Codex OAuth pool and select only
  `cx/gpt-5.6-sol` for text planning, structured JSON, and frame/vision input.
  Do not route these workloads through OpenRouter, Gemini, Mistral, or another
  API-key provider.
- **Dependencies**: Sprint 2 service supervision.
- **Acceptance criteria**:
  - Each intended active Codex connection passes a redacted one-by-one health
    check or is marked unavailable with an interactive re-auth action.
  - `cx/gpt-5.6-sol` passes text, JSON-object, and image-input probes.
  - The 9Router endpoint key remains the only credential FireRed needs.
  - No OAuth access/refresh token enters logs, plan files, or `.env.kamal`.
- **Validation**:
  - 9Router dashboard one-by-one connection tests.
  - Redacted `/v1/chat/completions` text JSON and vision probes.
  - Query only provider/auth-type/active metadata from the read-only SQLite DB.
- **Rollback**: Restore the previous active Codex connection ordering without
  deleting OAuth records. Pause deployment if Codex requires re-authentication.

### Task 3.2: Lock Image Generation To Codex OAuth

- **Location**: 9Router Codex image catalog; `.env.kamal.example`,
  `.env.mvp.example`, `config.toml`, `config/deploy.yml`, image docs, and tests.
- **Description**: Select only `cx/gpt-5.5-image`, the highest currently
  cataloged Codex image model, and remove the stale Gemini/Grok defaults. Do not
  add `cx/gpt-5.4-image` or `cx/gpt-5.3-image` as fallback models.
- **Dependencies**: Task 3.1.
- **Acceptance criteria**:
  - `/v1/models/image` advertises `cx/gpt-5.5-image`.
  - The model returns valid decodable image bytes within the configured limit.
  - No unapproved image model remains in runtime defaults or env examples.
- **Validation**:
  - Live catalog and one explicitly authorized synthetic binary image probe.
  - `PYTHONPATH=src python tests/test_remote_image.py`.
  - Search source, docs, and env examples for removed stale IDs.
- **Rollback**: Restore the previous configuration only for diagnosis; keep
  deployment paused rather than silently selecting another image provider.

### Task 3.3: Expose Mistral Voxtral Through 9Router STT

- **Location**: Maintained 9Router source/package and provider registry,
  Mistral connection record, `/v1/audio/transcriptions`, and `/v1/models/stt`.
- **Description**: First check whether a supported 9Router release exposes
  Mistral STT. If not, implement and pin the smallest maintained provider
  adapter instead of modifying generated/global package files. Publish
  `mistral/voxtral-mini-2602`, translate FireRed's
  `response_format=verbose_json` request into Mistral
  `timestamp_granularities=["segment"]`, and preserve `text`, `language`, and
  segment `start`/`end` values in the OpenAI-compatible response. Diarization
  is optional and disabled unless later product requirements need speaker IDs.
- **Dependencies**: Sprint 2 backup/supervision and the existing Mistral
  Free-mode connection. A service restart requires explicit authorization.
- **Acceptance criteria**:
  - `/v1/models/stt` advertises only the selected Mistral model for FireRed.
  - A synthetic audio request returns HTTP `200`, non-empty text, and finite
    segments with `end > start`.
  - The FireRed remote STT client accepts the response without a code-side
    provider key or direct Mistral URL.
  - 9Router logs and FireRed failure state contain no Mistral key or raw private
    transcript.
- **Validation**:
  - Authenticated catalog probe before and after the versioned adapter change.
  - One explicitly authorized synthetic `curl` request through 9Router using
    the same multipart fields FireRed sends.
  - `PYTHONPATH=src python tests/test_remote_stt.py` with Mistral timestamp and
    missing-segment fixtures.
  - Restart 9Router and repeat the catalog/STT probe to prove persistence.
- **Rollback**: Restore the recorded 9Router package/service and SQLite backup,
  remove the Mistral STT model from FireRed's active list, and keep deployment
  paused. Do not substitute a different STT provider automatically.

### Task 3.4: Align FireRed Model Configuration And Fail Closed

- **Location**: `config.toml`, `.env.kamal.example`, `.env.mvp.example`,
  `config/deploy.yml`, `src/open_storyline/config.py`, provider docs, and tests.
- **Description**: Make all runtime defaults and deployment examples express
  the approved policy exactly: `cx/gpt-5.6-sol`, `cx/gpt-5.5-image`, and
  `mistral/voxtral-mini-2602`. Keep provider credentials in 9Router and retain
  deterministic fail-closed behavior when any selected layer is unavailable.
- **Dependencies**: Tasks 3.1 through 3.3.
- **Acceptance criteria**:
  - Runtime and example configuration contain only the three approved model
    IDs for their respective layers.
  - No TTS or fallback provider is required by the remote MVP.
  - Catalog mismatch, authentication failure, rate limit, empty output, or
    missing STT segments causes a sanitized terminal provider error.
- **Validation**:
  - `PYTHONPATH=src python tests/test_kamal_config.py`
  - Focused LLM, image, and STT contract tests.
  - Search for stale runtime model IDs and scan the diff for secret patterns.
- **Rollback**: Restore the prior model lists for diagnosis only; deployment
  remains blocked until all three approved contracts pass again.

### Sprint 3 Gate

- [ ] `cx/gpt-5.6-sol`, `cx/gpt-5.5-image`, and
  `mistral/voxtral-mini-2602` are catalog- and contract-verified.
- [ ] No unapproved provider fallback is present in runtime configuration.
- [ ] OAuth/API-key metadata is reviewed without exposing credential values.
- [ ] The 9Router Mistral STT package/version and rollback point are recorded.
- [ ] Exactly one Sprint 3 repository commit is created with the proposed
  message; external 9Router source changes follow their own recorded revision.
- [ ] Sprint 4 has not started before this gate completes.

## Sprint 4: Automated Regression And Operational QA

**Goal**: Turn the discovery checks into a repeatable, redacted QA gate that
can be run before every personal-server deployment.

**Dependencies**: Sprint 3 gate and a working timestamped STT provider.

**Tracked scope**: New `scripts/qa_ninerouter.py`, focused tests under `tests/`,
`docs/mvp/api-keys.md`, `docs/mvp/architecture.md`, and a short operator
runbook under `docs/mvp/`.

**Commit**: `test: add redacted ninerouter qa gate`

**Demo/Validation**:

- One command reports pass/fail for auth, catalogs, text JSON, vision, image
  decode, STT timestamps, service health, and container-to-host reachability.
- Failure output includes provider/model ordinal, HTTP class, and sanitized
  reason only; it never includes endpoint keys, OAuth tokens, private prompts,
  email addresses, or raw provider bodies.
- Mock tests remain deterministic and do not replace the live preflight.

**Rollback point**: Disable the preflight gate or revert its script/tests; the
runtime application behavior remains unchanged.

### Task 4.1: Add Contract Tests For Provider-Specific Output

- **Location**: `tests/test_ninerouter.py`, `tests/test_remote_image.py`,
  `tests/test_remote_stt.py`, and new provider fixtures as needed.
- **Description**: Cover catalog mismatch, HTTP 401/403/429/5xx, invalid JSON,
  binary/base64 image responses, text-only STT rejection, timestamp
  normalization, single-provider fail-closed behavior, timeout, and secret
  redaction.
- **Dependencies**: Sprint 3 approved provider contracts.
- **Acceptance criteria**:
  - Tests encode the actual FireRed acceptance contract, not only a successful
    text response.
  - No test fixture contains a real token, OAuth payload, private transcript,
    or provider account identifier.
- **Validation**:
  - `PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py' -v`
    in the project environment.
  - Focused provider tests rerun raw for the first failure.
- **Rollback**: Revert only the new test/fixture commit; keep production config
  unchanged until the tests are restored.

### Task 4.2: Add The Live Preflight And Redaction Checks

- **Location**: `scripts/qa_ninerouter.py` and `docs/mvp/api-keys.md`.
- **Description**: Use shell-provided env values, synthetic media, bounded
  timeouts, and no persistent media output. Record only summary JSON suitable
  for a QA artifact. Include a negative auth probe and a model-catalog parity
  check before any authorized provider-generation call.
- **Dependencies**: Task 4.1.
- **Acceptance criteria**:
  - Image generation is skipped when no configured model is catalog-advertised.
  - STT generation is skipped when `mistral/voxtral-mini-2602` is absent or
    does not advertise the required transcription contract.
  - The preflight exits non-zero on a release-blocking modality failure.
- **Validation**:
  - Run locally and from a disposable FireRed-like container.
  - Inspect the summary with `jq`; scan output for sensitive patterns.
  - Verify synthetic artifacts are outside the repository and deleted after the
    run by the operator's documented cleanup step.
- **Rollback**: Remove the live gate from the deploy checklist while retaining
  the unit tests; do not weaken application fail-closed behavior.

### Task 4.3: Document The Codex And Mistral Incident Runbook

- **Location**: `docs/mvp/api-keys.md`, `docs/mvp/architecture.md`, new
  `docs/mvp/9router-vps-runbook.md`.
- **Description**: Document which provider owns each modality, how to reconnect
  Codex OAuth, how to verify or replace the Mistral connection, how to maintain
  or roll back the pinned Mistral STT adapter, how to interpret the Free-mode
  limits, how to rotate endpoint keys, how to inspect redacted systemd logs,
  and how to restore the 9Router DB.
- **Dependencies**: Tasks 4.1 and 4.2.
- **Acceptance criteria**:
  - A maintainer can diagnose auth, catalog, provider, timestamp, process, and
    network failures without reading raw credential data.
  - README navigation remains aligned if shared docs links change.
- **Validation**:
  - Link/path search with `rg`.
  - Manual dry run using a non-production/failing provider fixture.
- **Rollback**: Revert documentation only; keep any security remediation and
  provider credentials unchanged.

### Sprint 4 Gate

- [ ] Focused and complete deterministic tests pass in the project environment.
- [ ] Live preflight output is redacted and release-blocking failures are clear.
- [ ] Provider and restore runbooks are usable by a second operator.
- [ ] Exactly one Sprint 4 commit is created with the proposed message.
- [ ] The rollback point is recorded.
- [ ] Sprint 5 has not started before this gate completes.

## Sprint 5: Canary Deployment And End-To-End QA

**Goal**: Deploy the remote-only FireRed profile only after all gates pass and
prove one complete synthetic video workflow with rollback evidence.

**Dependencies**: Sprints 1-4 gates; explicit user authorization for deploy,
service restarts, firewall changes, and provider calls.

**Tracked scope**: `Dockerfile.remote`, `config/deploy.yml`, `bin/kamal-mvp`,
  deployment runbook, and release evidence. No full-agent container changes.

**Commit**: `release: gate remote mvp on ninerouter qa`

**Demo/Validation**:

- `docker build -f Dockerfile.remote .` completes without local inference
  packages or downloaded model archives.
- `kamal config` and the live preflight pass before deploy.
- `/up` and `/health` pass after deploy; Kamal reports the container healthy.
- An authenticated upload creates a durable job, reaches remote transcription,
  produces a validated clip plan, renders an MP4/subtitles, and registers
  artifacts under the persistent volume.
- Restart recovery, rate limits, artifact traversal protection, and sanitized
  failure state are verified.

**Rollback point**: Record the deployed image/version, previous healthy image,
  job-output volume state, and exact Kamal rollback command confirmed by the
  selected Kamal version. Do not roll back across incompatible job-state schema
  changes.

### Task 5.1: Build And Inspect The Remote Image

- **Location**: `Dockerfile.remote`, `requirements-remote.txt`,
  `tests/test_remote_profile.py`.
- **Description**: Build the remote-only image and inspect runtime user,
  healthcheck, exposed port, output volume, and dependency boundary. Do not run
  `download.sh` or add local inference packages.
- **Dependencies**: Sprint 4 gate.
- **Acceptance criteria**:
  - Image contains FFmpeg and remote HTTP dependencies only.
  - Healthcheck reaches `/health`.
  - No `.env`, provider key, or local model archive is copied into the image.
- **Validation**:
  - `docker build -f Dockerfile.remote .`
  - `PYTHONPATH=src python tests/test_remote_profile.py`
  - Inspect image history and filesystem for secret/model leakage.
- **Rollback**: Keep the previous image tag and do not publish the candidate.

### Task 5.2: Deploy A Single Canary And Exercise The API

- **Location**: `config/deploy.yml`, deployment runbook, VPS persistent output
  volume, and remote MVP API.
- **Description**: Deploy one canary using the validated env/model lists. Keep
  API authentication, upload limits, rate limits, and job persistence enabled.
  Use a synthetic or explicitly approved non-private video.
- **Dependencies**: Task 5.1 and all sprint gates.
- **Acceptance criteria**:
  - Kamal proxy health uses `/up`; application health reports remote-only
    inference and CPU FFmpeg rendering.
  - Job state survives a controlled app restart.
  - Provider attempts and failure reasons remain sanitized in `failure.json`.
  - Artifacts are downloadable only through registered job-scoped paths.
- **Validation**:
  - Authenticated `POST /api/mvp/jobs`, polling `GET /api/mvp/jobs/{id}`,
    artifact listing/download, and bundle download.
  - Remote `docker ps`, `docker logs`, `/up`, `/health`, and persistent-volume
    checks after the canary.
  - Run the complete deterministic suite and the live preflight again.
- **Rollback**: Stop accepting new canary jobs, preserve failed job state,
  restore the previous image/config, and verify `/up`/`/health` before reopening
  the endpoint. Keep the output volume for forensic review unless a separate
  approved cleanup is requested.

### Sprint 5 Gate

- [ ] Canary build, deploy, health, job, artifact, restart, and rollback checks
  are recorded.
- [ ] No provider token, private media, or raw transcript appears in evidence.
- [ ] Exactly one Sprint 5 commit is created with the proposed message.
- [ ] The deployed image and rollback point are recorded.
- [ ] Production/personal-server rollout remains paused if any modality gate is
  red.

## Testing Strategy

- **Unit**: Provider client parsing, catalog filtering, timestamp normalization,
  single-provider fail-closed behavior, invalid input, timeout, and secret
  redaction.
- **Integration**: Authenticated 9Router `/v1` endpoints, provider-specific
  response formats, image binary/base64 decoding, STT `verbose_json`, and
  9Router-to-FireRed endpoint-key behavior.
- **End-to-end/manual**: Synthetic video upload through job completion,
  subtitle/artifact validation, restart recovery, and dashboard/service restart.
- **Deployment**: Kamal version/config rendering, Docker build/profile boundary,
  proxy `/up`, app `/health`, persistent output volume, and rollback command.
- **Security/privacy**: `.env.kamal` mode `600`, root/admin ownership review,
  9Router DB modes, UFW scope, endpoint 401/200 checks, log redaction, and
  absence of tokens in Git/image layers/test artifacts.
- **Performance/reliability**: Record request latency/TTFT, provider retry and
  terminal failure counts, Codex account distribution, image byte limits,
  Mistral audio usage/limits, STT timeout, job queue recovery, and disk
  headroom.

## Risks And Gotchas

| Risk | Impact | Mitigation | Validation signal |
| --- | --- | --- | --- |
| Current 9Router omits Mistral from its STT catalog | FireRed cannot reach the approved STT model | Pin a supported release or maintained adapter that preserves Mistral segments | Catalog entry plus STT `200` with segments |
| Mistral Free-mode limit or availability changes | STT jobs fail closed | Monitor the organization limits page and stop deployment until the approved provider is restored | Redacted 429/availability canary and usage review |
| Codex OAuth expires or loses model entitlement | Text, vision, or image layer fails closed | Use the re-authentication runbook; do not substitute another provider automatically | One-by-one OAuth and exact-model probes |
| Codex image catalog changes | Image generation fails closed | Require `cx/gpt-5.5-image` catalog parity before deploy and pause on removal | Catalog and binary decode probe |
| 9Router launched from RDP terminal | Reboot/session loss | Systemd supervision and journal logs | Restart/reboot-equivalent test |
| Public 20128 exposure | Dashboard/API attack surface | UFW source restriction or SSH tunnel; endpoint key required | External 401/restricted-source check |
| Older Kamal executable | Deploy fails before SSH | Enforce `2.12.0` before deploy | `kamal config` pass |
| Public-IP hairpin from container | App cannot reach same-host 9Router | Use host gateway and test from a disposable container | Container `/api/health` probe |
| `.env.kamal` or SQLite permissions too broad | Credential disclosure | Owner-only modes and redacted audits | `stat`/secret scan |
| 9Router catalog changes | Stale model IDs fail closed | Catalog-driven preflight and documented replacement process | Catalog parity report |
| Low VPS disk headroom | Build/job failures | Monitor disk and output volume; retain/clean with approval | `df`, job-volume thresholds |

## Rollback Plan

- **Sprint 1**: Restore the prior version-check/config files; no remote state is
  changed by config validation.
- **Sprint 2**: No live rollback is expected because the process, user, port,
  permissions, and UFW rules remain unchanged. Preserve the root-only SQLite
  backup and discard only disposable restore-test copies.
- **Sprint 3**: Restore the recorded 9Router package/service and model lists,
  use the protected database backup if required, and pause deployment until
  the three approved contracts pass. Do not enable an alternative provider.
- **Sprint 4**: Disable the live preflight gate without weakening runtime
  fail-closed provider validation; keep deterministic tests and the runbook.
- **Sprint 5**: Roll back to the previous healthy image/config, preserve the
  persistent job volume, validate `/up`/`/health`, and document any jobs that
  were in flight. Do not perform destructive volume cleanup during rollback.

## Execution Order

1. Implement Sprint 1 only.
2. Run and record all Sprint 1 validation.
3. Create exactly one Sprint 1 commit and record its rollback point.
4. Start Sprint 2 only after the Sprint 1 gate passes.
5. Repeat the gate and one-commit rule for Sprints 2 through 5.
6. Stop only if Sprint 3 requires interactive Codex consent, the existing
   Mistral key is invalid/expired, or the approved model is unavailable.
7. Request explicit deployment authorization before any canary or remote
   service/firewall mutation.

## Completion Checklist

- [ ] Every sprint has passed its validation gate.
- [ ] Every sprint has exactly one sprint-specific commit.
- [ ] Kamal `2.12.0` config and network preflight pass.
- [ ] 9Router is supervised, backed up, permission-protected, and observable.
- [ ] `cx/gpt-5.6-sol`, `cx/gpt-5.5-image`, and
  `mistral/voxtral-mini-2602` are live and catalog/contract-matched.
- [ ] No runtime provider fallback is configured.
- [ ] Codex OAuth and Mistral API-key runbooks are current.
- [ ] Canary end-to-end job, restart recovery, artifact security, and rollback
  checks pass.
- [ ] Residual risks, provider quotas, and rollback instructions are recorded.
