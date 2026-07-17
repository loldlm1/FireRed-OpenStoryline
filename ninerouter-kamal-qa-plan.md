# Plan: 9Router And Kamal VPS QA Readiness

**Generated**: 2026-07-16
**Updated**: 2026-07-17
**Status**: Repository execution is complete through Sprint 8. The remote MVP
is healthy on port `20129`; QA from the current Netherlands VPN still requires
an SSH tunnel or an off-VPN route because that path does not reach the VPS.
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

The revised provider policy remains intentionally small: Codex OAuth owns
text, vision, and image generation through 9Router; FireRed calls Mistral
Voxtral directly for speech-to-text. The only STT failover allowed is between
operator-supplied Mistral API keys serving the same fixed Voxtral model. There
is no cross-provider or cross-model fallback.

The remote MVP must remain remote-inference-only. FFmpeg remains the only local
media processing dependency. Mistral keys enter the container only through
Kamal secrets and are never written to this repository, FireRed job state,
artifacts, responses, or logs.

## Architecture Decision Update: Direct Mistral STT

The July 17 decision supersedes the earlier Sprint 3 requirement to expose
Mistral STT through 9Router. Sprints 1-5 remain as historical execution
evidence and must not be rewritten to imply that the blocked 9Router adapter
was activated.

```text
Text / vision / image  -> FireRed -> 9Router -> Codex OAuth
Speech-to-text         -> FireRed -> Mistral API directly
Media processing       -> FireRed -> local FFmpeg only
```

- FireRed will use the official Mistral transcription API and the exact model
  `voxtral-mini-2602`; the provider-prefixed 9Router ID
  `mistral/voxtral-mini-2602` is retired from active STT configuration.
- A separate STT microservice is intentionally not added. The existing remote
  MVP process owns the small provider adapter, validation, retries, and
  sanitized attempt metadata.
- `MISTRAL_API_KEYS` is the canonical secret and accepts one or more ordered,
  comma-separated keys. A single key is a valid one-element key ring.
- Key failover is for legitimate credential availability and independently
  scoped quota exhaustion. Multiple keys from the same organization may share
  RPM, RPD, token, or audio limits and therefore may all receive the same
  `429`. The implementation must not manufacture accounts, evade provider
  limits, or ignore Mistral terms.
- OpenRouter, Gemini, Groq, Hugging Face, and local ASR are not remote-MVP STT
  fallbacks. The original full-agent `local_asr` profile remains intact and is
  outside this migration.
- The version-pinned 9Router Mistral STT patch becomes obsolete and will be
  removed from active source/docs during Sprint 6, while historical records
  retain a concise superseded note.

## Scope

- **In scope**:
  - Make Kamal configuration parse and provide a network preflight that proves
    the deploy machine, VPS, Docker, and 9Router paths are reachable.
  - Protect and observe the existing manually launched 9Router process without
    changing its user, port, command, firewall, or supervisor.
  - Keep Codex text/vision and image generation reconciled with live 9Router
    catalogs while removing STT from the 9Router contract.
  - Add a direct Mistral Voxtral client with strict timestamp validation and an
    ordered, quota-aware API-key ring.
  - Remove obsolete Groq/Hugging Face remote-STT defaults, generic model
    cascade configuration, the unused 9Router Mistral patch, and stale
    runbook/env references without rewriting Git history.
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
  - Adding TTS, general audio understanding, or any text, vision, image, or
    cross-provider/cross-model STT fallback.
  - Building a standalone STT proxy or exposing a new public STT endpoint.
  - Deploying, rotating, revoking, or publishing credentials during planning.
  - Making OAuth account choices on behalf of the user when a browser login or
    provider-admin action is required.
- **Fixed decisions**:
  - Use only `cx/gpt-5.6-sol` through Codex OAuth for text planning, structured
    JSON, and frame/vision understanding.
  - Use only `cx/gpt-5.5-image` through Codex OAuth for image generation while
    that model remains in `/v1/models/image` and passes the binary image
    contract. Do not configure a second image model.
  - Use only direct Mistral `voxtral-mini-2602` for STT. FireRed must request
    segment timestamps and preserve non-empty `text` plus finite `start`/`end`
    segment fields accepted by the remote MVP.
  - Permit ordered failover only among values in `MISTRAL_API_KEYS`; never
    change the provider or model because one key is unavailable.
  - Keep Codex credentials inside 9Router. Provide Mistral keys to FireRed only
    through Kamal secrets, never clear env configuration or committed files.
  - HTTP is acceptable for this personal deployment, but the public 9Router
    port should be restricted where practical; the app container should use a
    host-local route when 9Router runs on the same VPS.
- **Assumptions**:
  - The 9Router service and FireRed remote MVP will run on the same VPS.
  - At least one valid Mistral key is available to the deploy environment. If
    more than one key is supplied, the operator confirms whether their quota
    scopes are independent; same-organization keys are not assumed to add
    capacity.
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
  - [9Router image skill](https://github.com/decolua/9router/blob/master/skills/9router-image/SKILL.md)
  - [9Router provider registry](https://github.com/decolua/9router/tree/master/open-sse/providers/registry)
  - [Kamal configuration reference](https://github.com/basecamp/kamal/blob/main/lib/kamal/configuration/docs/configuration.yml)
  - [Mistral offline transcription](https://github.com/mistralai/platform-docs-public/blob/main/public/studio-api/audio/speech_to_text/offline_transcription.md)
  - [Mistral Free-mode API keys](https://github.com/mistralai/platform-docs-public/blob/main/public/getting-started/quickstarts/studio/activate-and-generate-api-key.md)
  - [Mistral usage and limits](https://github.com/mistralai/platform-docs-public/blob/main/src/content/en/docs/admin/billing-usage/usage-limits/page.mdx)

## Prerequisites

- The provider policy is already approved: `cx/gpt-5.6-sol` for text/vision,
  `cx/gpt-5.5-image` for image generation, and
  direct `voxtral-mini-2602` for STT, with key-only Mistral failover and no
  provider/model fallbacks.
- A root-only backup destination exists for the 9Router SQLite database before
  permission or supervision changes.
- The deploy machine has Docker and can select/install Kamal `2.12.0`.
- Codex provider tokens remain in 9Router. Mistral values are supplied through
  the deploy machine's secret environment and Kamal secret references; values
  are never pasted into this plan, Git, command output, `.env` templates, or
  job artifacts.
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
- Direct Mistral `voxtral-mini-2602` returned HTTP `200`, a non-empty
  transcript, and valid segment timestamps with the existing synthetic audio
  fixture. The current 9Router STT route instead returns HTTP `400` with a
  provider-level unsupported-STT error for Mistral.
- 9Router Gemini transcription can return text without segments, and its
  OpenRouter adapter rejects STT and did not deliver audio through the tested
  chat route. Neither satisfies FireRed's timestamp contract.
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
| Direct Mistral STT client and redacted QA | Yes | Supply at least one valid key through the deploy secret environment |
| Mistral key-ring ordering and quota-scope declaration | Yes after key names/count are available; values remain unreadable in output | Confirm whether multiple keys share an organization/quota scope |
| 9Router Mistral STT adapter or restart | No longer needed | No 9Router mutation is required for direct STT |
| Add Mistral values to committed or clear env files | Never | Export `MISTRAL_API_KEYS` privately so Kamal can resolve its secret reference |
| Canary deploy and live provider smoke | Yes after all gates | Explicit deploy/provider-call authorization |

FireRed will receive `MISTRAL_API_KEYS` as a container secret because STT now
bypasses 9Router. The value must not live in `.env.kamal.example`,
`.env.mvp.example`, `config.toml`, the image, job state, or logs. The existing
9Router endpoint key remains required only for Codex text, vision, and image
generation.

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
changes require a separate maintenance window and the Sprint 2 backup; they
are not allowed while this process supplies Codex inference.

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

### Task 3.3: Expose Mistral Voxtral Through 9Router STT (Historical, Superseded)

This task records the original Sprint 3 decision and its offline patch boundary.
It is not an active implementation target; Sprint 6 replaces it with direct
Mistral STT and removes the unused patch.

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
  - The pinned offline adapter publishes only the selected Mistral model for
    FireRed; live `/v1/models/stt` activation remains blocked until the
    maintenance window.
  - A synthetic audio request returns HTTP `200`, non-empty text, and finite
    segments with `end > start`.
  - The FireRed remote STT client accepts the response without a code-side
    provider key or direct Mistral URL.
  - 9Router logs and FireRed failure state contain no Mistral key or raw private
    transcript.
- **Validation**:
  - Authenticated catalog probe records the current pre-activation mismatch.
  - Apply the versioned patch to a clean upstream checkout and run its focused
    multipart/timestamp test without changing the installed package.
  - `PYTHONPATH=src python tests/test_remote_stt.py` with Mistral timestamp and
    missing-segment fixtures.
  - After a separately approved maintenance window, restart 9Router and repeat
    the catalog/STT probe to prove persistence.
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
  `mistral/voxtral-mini-2602` are all live catalog- and contract-verified;
  Mistral remains the only red item because the running router lacks its STT
  adapter.
- [x] No unapproved provider fallback is present in runtime configuration.
- [x] OAuth/API-key metadata is reviewed without exposing credential values.
- [x] The pinned 9Router `0.5.35` Mistral STT patch and rollback boundary are
  recorded without touching the live package/process.
- [ ] Exactly one Sprint 3 repository commit is created with the proposed
  message; external 9Router source changes follow their own recorded revision.
- [ ] Sprint 4 has not started before this gate completes.

## Sprint 4: Automated Regression And Operational QA

**Goal**: Turn the discovery checks into a repeatable, redacted QA gate that
can be run before every personal-server deployment.

**Dependencies**: Sprint 3 repository gate. The live gate may remain red while
the QA tooling is implemented, but no deployment can proceed without a
working timestamped STT provider.

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

- [x] Focused and complete deterministic tests pass in the project environment.
- [x] Live preflight output is redacted and release-blocking failures are clear.
- [x] Provider and restore runbooks are usable by a second operator.
- [x] Exactly one Sprint 4 commit is created with the proposed message.
- [x] The rollback point is the QA script/tests/runbook commit; runtime provider
  behavior remains unchanged.
- [x] Sprint 5 has not started before this gate completes.

## Sprint 5: Canary Deployment And End-To-End QA

**Goal**: Deploy the remote-only FireRed profile only after all gates pass and
prove one complete synthetic video workflow with rollback evidence.

**Dependencies**: Sprints 1-4 repository gates plus a green live preflight.
The canary deploy remains blocked while Mistral STT is absent; no 9Router
restart, package change, firewall change, or workaround is permitted.

**Tracked scope**: `Dockerfile.remote`, `config/deploy.yml`, `bin/kamal-mvp`,
  deployment runbook, and release evidence. No full-agent container changes.

**Commit**: `release: gate remote mvp on ninerouter qa`

**Demo/Validation**:

- `docker build -f Dockerfile.remote .` completes without local inference
  packages or downloaded model archives.
- `kamal config` passes and the wrapper's mandatory live preflight is green
  before deploy.
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
- **Recorded Sprint 5 repository evidence**: the candidate image builds as
  `openstoryline-mvp:sprint5-local` (229 MB, 798 KB context), its filesystem
  contains no env/Kamal files or local model packages, and disposable local
  `/health` plus `/up` checks pass. The image uses the default root user so the
  existing persistent volume contract is not changed without a separate UID
  and ownership migration.
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

- [x] Local canary image build, filesystem inspection, and disposable health
  checks are recorded.
- [ ] VPS canary deploy, health, job, artifact, restart, and rollback checks
  are recorded; deployment is blocked until Mistral STT is catalog-advertised.
- [x] No provider token, private media, or raw transcript appears in evidence.
- [x] Exactly one Sprint 5 commit is created with the proposed message.
- [ ] The deployed image and rollback point are recorded.
- [x] Production/personal-server rollout remains paused if any modality gate is
  red.

The final live preflight is red only for `catalog:stt=catalog_mismatch`; text,
vision, image, endpoint-key, SSH, and remote Docker checks pass. STT inference
is skipped because the exact configured model is absent from `/v1/models/stt`.
No Kamal deploy, 9Router restart, package mutation, port change, user change,
or firewall change is performed in this sprint.

## Sprint 6: Direct Mistral Boundary And Legacy STT Cleanup

**Goal**: Replace the blocked 9Router STT route with one direct, timestamped
Mistral Voxtral integration and remove obsolete remote-MVP model/provider
configuration without changing the original full-agent local ASR profile.

**Dependencies**: Sprint 5 repository checkpoint and the approved July 17
architecture decision. No 9Router restart or package change is required.

**Tracked scope**: `AGENTS.md`, `docs/agent-engineering.md`,
`docs/mvp/architecture.md`, `src/open_storyline/utils/remote_stt.py`,
`src/open_storyline/nodes/core_nodes/remote_asr.py`,
`src/open_storyline/config.py`, `config.toml`, `.env.mvp.example`,
`.env.kamal.example`, `.kamal/secrets.example`, `config/deploy.yml`,
`bin/kamal-mvp`, provider docs/tests, and removal of
`patches/9router/0.5.35-mistral-stt.patch` after all active references are
updated.

**Commit**: `refactor: route remote stt directly to mistral`

**Demo/Validation**:

- FireRed sends the synthetic fixture directly to
  `https://api.mistral.ai/v1/audio/transcriptions` with
  `model=voxtral-mini-2602` and segment timestamps enabled.
- The response contains non-empty text and finite, increasing segment bounds
  accepted by the existing clip-planning/rendering contract.
- 9Router remains the only path for Codex text, vision, and image generation;
  its `/v1/models/stt` catalog is no longer a release dependency.
- No Groq, Hugging Face, Gemini, OpenRouter, provider-prefixed Mistral model ID,
  or generic STT model cascade remains in active remote-MVP configuration.

**Rollback point**: Revert the Sprint 6 commit and keep deployment paused. The
previous 9Router STT path was already red, so rollback restores the known
blocked state rather than claiming a working transcription path.

### Task 6.1: Retire Obsolete Remote-STT Models And Adapter Artifacts

- **Location**: `AGENTS.md`, architecture guidance, STT defaults/config, env
  examples, deployment gate, tests, provider docs, and
  `patches/9router/0.5.35-mistral-stt.patch`.
- **Description**: Remove the early Groq/Hugging Face model-cascade assumptions,
  the active `OPENSTORYLINE_STT_MODELS` contract, provider-prefixed Mistral STT
  ID, 9Router STT catalog checks, and the now-unused adapter patch. Preserve
  concise historical notes where they explain completed commits; do not rewrite
  Git history. Do not remove or modify the full-agent `local_asr` FunASR node.
- **Dependencies**: None beyond the architecture decision.
- **Acceptance criteria**:
  - Active runtime code, templates, and current operating docs contain no
    Groq/Hugging Face/OpenRouter/Gemini STT fallback ID.
  - Repository instructions explicitly permit direct Mistral STT while keeping
    text, vision, and image generation on 9Router; no implementation step must
    contradict the old all-inference-through-9Router invariant.
  - The remote MVP has one model constant: `voxtral-mini-2602`.
  - `remote_asr` remains a stable public node/schema name even if its internal
    client is renamed from a generic cascade to a Mistral-specific client.
  - Historical documentation labels superseded behavior rather than deleting
    useful audit context.
- **Validation**:
  - Focused `rg` searches for retired model IDs, `OPENSTORYLINE_STT_MODELS`,
    `/v1/models/stt`, and the removed patch path.
  - Full-agent node/schema discovery tests confirm `local_asr` and `remote_asr`
    public contracts remain available.
- **Rollback**: Restore the removed files/references from the Sprint 6 parent
  commit and keep release blocked.

### Task 6.2: Define The Direct Mistral Secret And Configuration Contract

- **Location**: `src/open_storyline/config.py`, `config.toml`,
  `.env.mvp.example`, `.env.kamal.example`, `.kamal/secrets.example`,
  `config/deploy.yml`, and `bin/kamal-mvp`.
- **Description**: Introduce `MISTRAL_API_KEYS` as the only STT credential
  source. It accepts one or more ordered comma-separated values; whitespace is
  trimmed, duplicates are rejected or collapsed deterministically, empty and
  placeholder values fail startup, and the key-ring size is bounded. Keep the
  official Mistral base URL fixed in production. Use
  `MISTRAL_STT_TIMEOUT` for the bounded provider timeout and keep the model
  fixed rather than user-selectable.
- **Dependencies**: Task 6.1.
- **Acceptance criteria**:
  - One key and multiple keys use the same canonical secret variable.
  - The secret is listed under Kamal secret env, not `env.clear`, and examples
    contain only a shell reference or safe placeholder—not a credential.
  - `NINEROUTER_KEY` remains required only by Codex-backed layers.
  - Configuration errors report the variable name and safe counts only; no key
    prefix, suffix, hash, or value appears.
- **Validation**:
  - Config/Kamal tests for absent, empty, one-key, duplicate, oversized, and
    multi-key inputs.
  - Rendered Kamal config is inspected with secret values unavailable to the
    test process.
  - Image history/build context scans confirm no Mistral key enters a layer.
- **Rollback**: Restore the previous secret/config references and pause release.

### Task 6.3: Implement The Single-Key Direct Voxtral Contract

- **Location**: `src/open_storyline/utils/remote_stt.py`, remote ASR/MVP
  pipeline consumers, and focused STT tests.
- **Description**: Replace the 9Router multipart request with the official
  direct Mistral request. Send the fixed model, the compressed audio file,
  `timestamp_granularities=["segment"]`, and no diarization unless a later
  product requirement approves it. Preserve normalized millisecond timestamps,
  sanitized attempt records, bounded timeout, and fail-closed behavior.
- **Dependencies**: Task 6.2. Sprint 7 expands this working one-key path into
  quota-aware multi-key failover.
- **Acceptance criteria**:
  - A valid single key returns the same `STTResult` contract consumed by the
    planner and renderer.
  - Invalid JSON, empty text, missing segments, non-finite times, or `end <=
    start` fails closed without persisting provider output.
  - Audio is uploaded once on a successful attempt and file handles are closed
    between attempts.
- **Validation**:
  - `PYTHONPATH=src python tests/test_remote_stt.py`
  - Pipeline tests proving transcript segments still feed clip planning and
    subtitles.
  - One explicitly authorized synthetic direct-Mistral canary.
- **Rollback**: Revert the direct client; do not silently invoke local ASR or a
  second provider.

### Sprint 6 Gate

- [x] Obsolete remote-STT model lists, 9Router STT routing, and the adapter
  patch are absent from active source/config/docs.
- [x] Single-key direct Voxtral returns validated timestamped segments.
- [x] The full local-agent ASR profile and remote-MVP public contracts remain
  separate and intact.
- [x] Secret/config scans show no Mistral credential value.
- [x] Exactly one Sprint 6 commit is created with the proposed message.
- [x] Sprint 7 does not start before the Sprint 6 gate passes.

## Sprint 7: Quota-Aware Multi-Key Mistral Failover

**Goal**: Allow one or more legitimate Mistral API keys while preventing retry
storms, secret leakage, duplicate billing, and false assumptions about shared
organization quotas.

**Dependencies**: Sprint 6 direct single-key contract.

**Tracked scope**: Mistral client/key-ring state, typed attempt metadata,
focused tests, operator docs, and optional process-local metrics. No database
schema, provider dashboard, or public API change is required.

**Commit**: `feat: add quota-aware mistral key failover`

**Demo/Validation**:

- One configured key behaves exactly like Sprint 6.
- In deterministic tests, key 1 returns `429` with `Retry-After`; key 2 returns
  timestamped `200`; the request succeeds and records only safe key ordinal and
  status category.
- If all keys are exhausted, FireRed returns one sanitized terminal rate-limit
  error and does not spin, sleep unboundedly, or retry the same key immediately.
- Invalid media/request errors stop without wasting requests against every key.

**Rollback point**: Revert to the Sprint 6 single-key client and deploy with one
known-good secret while multi-key behavior is corrected.

### Task 7.1: Parse And Protect The Ordered Key Ring

- **Location**: Direct Mistral client configuration and tests.
- **Description**: Parse `MISTRAL_API_KEYS` into a bounded ordered ring. Use
  ordinal labels such as `key_1` only inside sanitized attempt metadata. Never
  log key values, partial values, hashes, authorization headers, provider error
  bodies containing credentials, or the complete key count on public health
  endpoints.
- **Dependencies**: Sprint 6 configuration contract.
- **Acceptance criteria**:
  - Input order is stable and defines failover priority.
  - Empty entries are ignored safely; duplicate values do not create duplicate
    attempts; a fully empty result fails startup.
  - The parser has a small documented maximum to bound accidental secret-list
    expansion and retry cost.
  - Restart clears process-local cooldown state but not credentials; a renewed
    `429` re-establishes cooldown without a loop.
- **Validation**:
  - Unit tests for whitespace, duplicates, one/many keys, maximum size, and
    redaction against representative Mistral-style tokens.
- **Rollback**: Configure one key and revert the key-ring parser.

### Task 7.2: Classify Failover, Retry, And Cooldown Behavior

- **Location**: Mistral request loop, typed error categories, and metrics.
- **Description**: Apply the following bounded policy. Honor valid
  `Retry-After` values and skip cooled keys. Serialize selector/cooldown updates
  inside each process so concurrent jobs do not race the same known-limited
  credential. The initial Kamal canary remains one application container;
  horizontal scale requires a later shared limiter because cooldown state is
  process-local.

| Result | Action |
| --- | --- |
| `200` with valid text and segments | Return immediately |
| `401`, `402`, `403`, or key-specific entitlement `404` | Disable/cool that key and try the next key |
| `429` | Honor `Retry-After`, cool the key, then try the next eligible key |
| Transport error, `408`, or `5xx` | At most one bounded retry, then try the next key |
| `400`, `413`, or `422` input/schema/media error | Stop; do not repeat the bad request across keys |
| `200` with invalid/missing timestamp contract | Stop with contract error; another key cannot repair the same model contract |

- **Dependencies**: Task 7.1.
- **Acceptance criteria**:
  - Total attempts are bounded by key count plus the explicitly allowed single
    retry for transient failures.
  - Same-organization keys receiving organization-wide `429` terminate cleanly
    after one pass; they are not treated as independent capacity.
  - Attempt metadata contains model, safe key ordinal, status/category, and
    sanitized reason only.
  - No fallback changes provider, model, endpoint, timestamp requirements, or
    local-inference policy.
- **Validation**:
  - Deterministic transport tests for every table row, malformed/missing
    `Retry-After`, all-keys-limited, mixed invalid/valid keys, and concurrent
    selection.
  - A test asserts that invalid media generates exactly one provider attempt.
- **Rollback**: Revert to the one-key Sprint 6 behavior and retain terminal
  failure rather than broadening fallback conditions.

### Task 7.3: Add Safe Observability And Operator Guidance

- **Location**: failure metadata, logs, health details, and Mistral runbook.
- **Description**: Record aggregate outcomes needed for QA—success, latency,
  failover count, terminal category, and safe key ordinal—without transcripts,
  audio, provider response bodies, or credentials. Document that adding API
  keys from the same organization may not increase RPM/RPD and that account
  creation or key rotation must comply with provider terms.
- **Dependencies**: Task 7.2.
- **Acceptance criteria**:
  - Operators can distinguish invalid key, shared quota exhaustion, provider
    outage, bad input, and response-contract failure.
  - Public `/health` does not disclose keys, ordinals, quota ownership, or live
    provider responses.
  - Job failure artifacts remain sanitized and bounded.
- **Validation**:
  - Secret-pattern scans across logs, job state, failure fixtures, and test
    output.
  - Runbook dry run using fake keys and deterministic provider responses.
- **Rollback**: Remove optional metrics fields while preserving safe terminal
  errors and redaction.

### Sprint 7 Gate

- [x] One-key and multi-key behavior pass deterministic tests.
- [x] `429`, invalid-key, transport, and all-keys-exhausted paths are bounded
  and honor cooldown policy.
- [x] Bad input and invalid timestamp contracts do not fan out across keys.
- [x] No provider/model fallback or secret leakage is present.
- [x] Exactly one Sprint 7 commit is created with the proposed message.
- [x] Sprint 8 does not start before the Sprint 7 gate passes.

## Sprint 8: Split Provider QA And Resume The VPS Canary

**Goal**: Replace the obsolete all-9Router release gate with two explicit
provider gates—9Router for Codex layers and direct Mistral for STT—then resume
the synthetic VPS canary without modifying the running 9Router service.

**Dependencies**: Sprints 6 and 7 plus explicit authorization for live
provider canaries and Kamal deployment.

**Tracked scope**: `scripts/qa_ninerouter.py`, a direct-Mistral QA command,
`bin/kamal-mvp`, Kamal env/secrets, release docs, tests, and canary evidence.

**Commit**: `release: gate direct mistral stt`

**Demo/Validation**:

- The 9Router gate verifies endpoint authentication, `cx/gpt-5.6-sol` text and
  vision, and `cx/gpt-5.5-image`; it no longer requests `/v1/models/stt`.
- The direct-Mistral gate verifies secret parsing and one timestamped synthetic
  transcription through the real key ring without printing a key or transcript.
- An explicitly authorized one-by-one diagnostic can verify every configured
  key with the same non-private fixture, sequentially and within the documented
  provider rate limit.
- A Kamal canary completes upload, direct STT, Codex planning/vision, FFmpeg
  rendering, artifact download, restart recovery, and rollback checks.

**Rollback point**: Roll back the application image/config to Sprint 7 or the
previous paused image, retain the output volume, and keep 9Router untouched.

### Task 8.1: Split The Release Preflight By Provider Boundary

- **Location**: QA scripts, `bin/kamal-mvp`, tests, and runbooks.
- **Description**: Remove Mistral/STT checks from the 9Router catalog gate and
  add a redacted direct-Mistral gate. Both must pass before `setup`, `deploy`,
  or `redeploy`; read-only diagnostics and rollback remain available when
  either is red.
- **Dependencies**: Sprint 7 gate.
- **Acceptance criteria**:
  - The 9Router QA script never receives Mistral keys.
  - The Mistral QA path never receives the 9Router endpoint key unless the
    wrapper process already has it for the separate Codex check.
  - QA output contains status/category, latency, model, segment count, and safe
    attempt count only—not transcript or credentials.
  - A skipped live call is not treated as green deployment evidence.
- **Validation**:
  - Deterministic QA-script tests for success, missing secrets, `429`, invalid
    segments, redaction, and split exit codes.
  - `bash -n bin/kamal-mvp` and Kamal config tests.
- **Rollback**: Revert the split gate and leave deploy disabled rather than
  restoring the known-red 9Router STT requirement.

### Task 8.2: Validate Secret Delivery And Every Configured Key

- **Location**: Deploy-machine environment, `.kamal/secrets`, rendered Kamal
  config, candidate container, and direct-Mistral QA command.
- **Description**: Confirm the secret reference resolves into the container
  while remaining absent from rendered clear env, image history, Docker build
  context, logs, and repository files. With explicit provider-call approval,
  test each configured key sequentially using the synthetic fixture and record
  only safe ordinal/status results.
- **Dependencies**: Task 8.1 and user-supplied secret values.
- **Acceptance criteria**:
  - At least one key passes timestamped Voxtral transcription.
  - Keys that are invalid or share exhausted quota are clearly classified and
    can be removed/reordered without code changes.
  - No one-by-one check exceeds the documented one-request-per-second limit.
- **Validation**:
  - Secret scans of Git diff, build context, image history, container inspect
    output, and sanitized QA artifacts.
  - Redacted per-key canary summary reviewed by the operator.
- **Rollback**: Remove the secret from the candidate deployment and keep the
  previous application image running/paused.

### Task 8.3: Deploy And Observe One End-To-End Canary

- **Location**: Kamal/VPS application container, persistent job volume, health
  endpoints, and remote MVP API.
- **Description**: Deploy one candidate only after both provider gates are
  green. Use synthetic media, preserve existing 9Router user/port/process, and
  observe direct Mistral failover metadata plus Codex and rendering stages.
- **Dependencies**: Task 8.2 and explicit deployment authorization.
- **Acceptance criteria**:
  - The job completes with timestamped transcript, validated clip plan,
    rendered MP4/subtitles, and registered artifacts.
  - A controlled application restart preserves job/output state; 9Router is not
    restarted or reconfigured.
  - Rollback restores `/up` and `/health` without deleting persistent outputs.
- **Validation**:
  - Full deterministic suite, Docker build/profile inspection, both live
    provider gates, authenticated API workflow, restart recovery, and Kamal
    rollback rehearsal.
- **Rollback**: Stop new jobs, preserve evidence, restore the previous image and
  secret/config set, and verify health before reopening the endpoint.

### Sprint 8 Gate

- [x] 9Router Codex and direct-Mistral gates are independently green.
- [x] Every configured Mistral key has a redacted validation result and at
  least one key passes the full timestamp contract.
- [x] The end-to-end canary, restart recovery, artifact security, and rollback
  checks pass without changing 9Router.
- [x] No secret, transcript, or private media appears in evidence.
- [x] Exactly one Sprint 8 commit is created with the proposed message.
- [x] The personal-server rollout opens only after this gate passes.

Execution evidence is intentionally redacted. The live gates returned HTTP
`200` for `cx/gpt-5.6-sol` text and vision, decodable bytes for
`cx/gpt-5.5-image`, and timestamped `voxtral-mini-2602` STT through the single
configured key ordinal. A 30-second synthetic video completed one validated
18-second clip with transcript, video, subtitles, manifest, bundle download,
and traversal rejection. The job survived an application restart and a
rollback to a retained candidate, then the latest candidate was restored.
Direct-port deploys use a stop-first hook so only the FireRed web container has
a short maintenance window; the existing 9Router process and port `20128`
remain unchanged. UFW and the host listener are open on `20129`; the VPS can
reach its public endpoint, while the current VPN route must be bypassed or
corrected for browser QA.

## Testing Strategy

- **Unit**: Direct Mistral request/response parsing, key-ring normalization,
  timestamp normalization, failover classification, cooldowns, invalid input,
  timeout, concurrency, and secret redaction.
- **Integration**: Authenticated 9Router `/v1` endpoints for Codex text/vision
  and image binary/base64 decoding; direct Mistral `verbose_json` transcription
  with timestamp segments; and 9Router-to-FireRed endpoint-key behavior.
- **End-to-end/manual**: Synthetic video upload through job completion,
  direct-Mistral subtitle/artifact validation, restart recovery, and a
  no-restart 9Router observation check.
- **Deployment**: Kamal version/config rendering, Docker build/profile boundary,
  proxy `/up`, app `/health`, persistent output volume, and rollback command.
- **Security/privacy**: `.env.kamal` mode `600`, root/admin ownership review,
  9Router DB modes, UFW scope, endpoint 401/200 checks, Mistral secret delivery,
  log redaction, and absence of tokens in Git/image layers/test artifacts.
- **Performance/reliability**: Record request latency/TTFT, provider retry and
  terminal failure counts, safe key-ordinal failover counts, Codex account
  distribution, image byte limits, Mistral audio usage/limits, STT timeout,
  job queue recovery, and disk headroom.

## Risks And Gotchas

| Risk | Impact | Mitigation | Validation signal |
| --- | --- | --- | --- |
| Direct Mistral outage or model/API change | STT jobs fail closed | Pin the fixed model contract, keep a bounded key ring, and monitor the direct canary; do not switch providers implicitly | Redacted direct-Mistral canary and contract test |
| Mistral keys share one organization quota | Multiple keys do not increase RPM/RPD/audio capacity | Record quota ownership, honor `Retry-After`, and treat an all-key `429` as terminal | Per-key status summary plus organization-limit review |
| Key-ring retry storm | Duplicate inference, extra cost, or provider throttling | One bounded transient retry, per-key cooldown, process-local serialization, and a maximum key count | Attempt-count and cooldown tests |
| Unauthorized/expired key in the ring | Some requests fail before a healthy key is tried | Disable that ordinal for the process and continue; alert without exposing the value | Redacted 401/403 failover test |
| Direct Mistral secret delivery error | Container cannot transcribe | Validate Kamal secret references and fail startup with a variable-name-only error | Rendered-config and container secret scan |
| Codex OAuth expires or loses model entitlement | Text, vision, or image layer fails closed | Use the re-authentication runbook; do not substitute another provider automatically | One-by-one OAuth and exact-model probes |
| Codex image catalog changes | Image generation fails closed | Require `cx/gpt-5.5-image` catalog parity before deploy and pause on removal | Catalog and binary decode probe |
| 9Router launched from RDP terminal | Reboot/session loss | Systemd supervision and journal logs | Restart/reboot-equivalent test |
| Public 20128 exposure | Dashboard/API attack surface | UFW source restriction or SSH tunnel; endpoint key required | External 401/restricted-source check |
| Older Kamal executable | Deploy fails before SSH | Enforce `2.12.0` before deploy | `kamal config` pass |
| Public-IP hairpin from container | App cannot reach same-host 9Router | Use host gateway and test from a disposable container | Container `/api/health` probe |
| `.env.kamal` or SQLite permissions too broad | Credential disclosure | Owner-only modes and redacted audits | `stat`/secret scan |
| 9Router catalog changes | Codex text/vision/image layers fail closed | Catalog-driven preflight for only those three 9Router contracts | Catalog parity report |
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
- **Sprint 6**: Revert the direct-Mistral boundary and restore the previous
  paused configuration; do not reactivate the known-red 9Router STT patch.
- **Sprint 7**: Disable multi-key rotation and deploy one known-good Mistral
  secret using the Sprint 6 client. Preserve sanitized failure evidence.
- **Sprint 8**: Roll back the application image, Kamal secret/config set, and
  QA gate to the previous healthy checkpoint; keep the manually launched
  9Router process, user, port, and database untouched.

## Execution Order

1. Treat Sprints 1-5 and their commits as completed historical checkpoints; do
   not rerun or rewrite them merely to change the STT architecture.
2. Implement Sprint 6 and prove one-key direct Mistral transcription.
3. Implement Sprint 7 and prove deterministic multi-key failover without
   cross-provider/model fallback or quota-bypass behavior.
4. Implement Sprint 8, then request explicit live-provider and deployment
   authorization before the canary. Never restart or mutate the existing
   9Router process as part of this STT migration.

## Completion Checklist

- [ ] Every sprint has passed its validation gate.
- [ ] Every sprint has exactly one sprint-specific commit.
- [ ] Kamal `2.12.0` config and network preflight pass.
- [ ] 9Router is supervised, backed up, permission-protected, and observable.
- [ ] `cx/gpt-5.6-sol`, `cx/gpt-5.5-image`, and
  direct `voxtral-mini-2602` are live and contract-matched through their
  respective provider boundaries.
- [ ] Only ordered Mistral key failover is configured; no cross-provider or
  cross-model runtime fallback is present.
- [ ] Obsolete remote-STT model defaults, `OPENSTORYLINE_STT_MODELS`, and the
  unused 9Router Mistral patch are absent from active source/config/docs.
- [ ] `MISTRAL_API_KEYS` is delivered only as a Kamal secret and all configured
  keys have redacted validation evidence.
- [ ] Codex OAuth, direct-Mistral secret, quota-scope, and key-rotation
  runbooks are current.
- [ ] Canary end-to-end job, restart recovery, artifact security, and rollback
  checks pass.
- [ ] Residual risks, provider quotas, and rollback instructions are recorded.
