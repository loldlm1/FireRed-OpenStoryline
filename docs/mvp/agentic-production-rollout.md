# Agentic Production Rollout

This record covers the first production rollout of the general-purpose
agentic editor merged in pull request 6. Private source media, transcripts,
prompts, session names, provider keys, and artifact contents were not inspected
or copied into Git history.

## Fixed boundary

- Release date: 2026-07-18.
- Production image: `d7f3c24775e73182d5ac3d7d9db25799cf1ac34e`.
- Previous rollback image: `9e98f58`.
- Generated images: disabled.
- Pexels: disabled pending a separate manual license review.
- Semantic model QA: disabled.
- Database migration delta: none.
- PostgreSQL remains authoritative; database restore is not part of the normal
  code or feature-flag rollback.

## Sprint 9: release readiness and shadow deployment

Status: completed.

- Confirmed pull request 6 was merged into the writable fork's `main` branch.
- Confirmed no active production jobs before deployment.
- Created an atomic production PostgreSQL backup and restored it into an
  isolated verification database at schema revision `20260717_0001`.
- Passed 228 deterministic tests with 46 expected environment-gated skips.
- Passed config parsing, shell syntax, Kamal rendering, remote image build, and
  mandatory redacted 9Router and direct-Mistral release gates.
- Deployed `OPENSTORYLINE_AGENTIC_EDITING_MODE=shadow` with generated assets,
  Pexels, and semantic QA disabled.
- Verified `/`, `/up`, `/health`, container health, schema head, queue state,
  retention CLI, retained rollback image, and zero recent application error
  lines. Shadow mode preserved the legacy renderer for new jobs.

## Sprint 10: source-only render activation

Status: completed.

- Changed only `OPENSTORYLINE_AGENTIC_EDITING_MODE` from `shadow` to `render`.
- Redeployed the same production image after the mandatory redacted provider
  gates passed again.
- Verified the live container reports `render`, while generated assets, Pexels,
  and semantic QA all report `false`.
- Verified `/`, `/up`, and `/health` return 200, unauthenticated session access
  returns 401, the agentic UI option is present, the queue has no active jobs,
  and recent application logs contain no error lines.

An authenticated automated production job was intentionally not created because
the deploy environment has no separate plaintext QA password. Creative-output
acceptance therefore remains a private operator task rather than a deployment
gate.

## Sprint 11: private manual comparison

Status: ready for operator QA.

1. Open production and create a new editing session.
2. Choose `Edición agentiva · beta` under `Modo de edición`.
3. Choose `Nunca generar imágenes`; leave Pexels at `No usar Pexels`.
4. Upload the same private source video used by the earlier audited session and
   provide the intended editing context in the prompt.
5. Compare transcript timing, selected moments, subject visibility, portrait
   composition, hooks, source cutaways, effects, transitions, subtitles, audio
   sync, render time, and download behavior.
6. Share only redacted observations and job/artifact identifiers needed for a
   follow-up investigation.

## Rollback

For a plan or render-quality failure, set
`OPENSTORYLINE_AGENTIC_EDITING_MODE=off` and redeploy; legacy jobs remain
available. For a release-level failure, also run
`./bin/kamal-mvp rollback 9e98f58` after checking that no job is actively
executing. Restore PostgreSQL only for separately verified data corruption.
