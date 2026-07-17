# 9Router compatibility patches

`0.5.35-mistral-stt.patch` targets upstream 9Router commit
`bc252ea80298d4879dc6b3c69585af1610d2c76f` (`v0.5.35`). It adds the
`mistral/voxtral-mini-2602` STT catalog entry and adapts FireRed's
OpenAI-compatible multipart request to Mistral segment timestamps.

The patch is an offline build input, not a live-package hotfix. Do not apply it
to the globally installed package or restart the current manual 9Router
process while it is serving Codex inference. Activation requires a separately
approved maintenance window, a source build, the existing database backup,
and a tested rollback to the unmodified package.

Validate against a clean checkout of the pinned revision:

```bash
git apply --check /path/to/FireRed-OpenStoryline/patches/9router/0.5.35-mistral-stt.patch
git apply /path/to/FireRed-OpenStoryline/patches/9router/0.5.35-mistral-stt.patch
node tests/unit/mistral-stt-adapter.test.mjs
node tests/__baseline__/verify-providers.mjs
npm run build
```

Mistral currently documents that `timestamp_granularities` cannot be combined
with an explicit `language`. The adapter always requests `segment` timestamps
and intentionally omits FireRed's `language` and `response_format` fields.
