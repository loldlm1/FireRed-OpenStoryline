# Provider credentials and model contracts

FireRed deliberately uses three independent provider boundaries:

| Layer | Exact model | Credential owner |
| --- | --- | --- |
| Text planning and vision | `cx/gpt-5.6-sol` | Codex OAuth in 9Router |
| Image generation | `cx/gpt-5.5-image` | Codex OAuth in 9Router |
| Speech-to-text | `voxtral-mini-2602` | Direct Mistral API key ring in FireRed |
| Optional stock photos/videos | Pexels API | Direct Pexels API key in FireRed |

There is no cross-provider or cross-model fallback. A missing catalog entry,
expired connection, rate limit, invalid response, or missing STT timestamp is a
release-blocking failure.

## 1. Configure 9Router for Codex layers

Keep the Codex OAuth connections active in 9Router and create one endpoint key
for FireRed. Do not paste OAuth tokens into this repository. The existing
9Router process remains on port `20128`; direct STT does not require a router
package change, adapter, restart, new model entry, or Mistral connection.

## 2. Configure direct Mistral STT

`MISTRAL_API_KEYS` is the only STT credential variable. It accepts one or more
ordered comma-separated keys. Values are trimmed, duplicates are collapsed,
and the runtime bounds the key count and provider attempts. Multiple keys may
still share one organization quota, so they are not assumed to add capacity.
HTTP `429` responses cool only the affected ordinal according to
`Retry-After`; `401`/`402`/`403` and entitlement `404` disable that ordinal for
the process. Bad media and invalid timestamp contracts stop immediately
instead of repeating the same request across every key. Initial deployment
uses one application container because cooldown state is process-local.

The endpoint and model are fixed in code:

```text
https://api.mistral.ai/v1/audio/transcriptions
voxtral-mini-2602
```

Configure the ignored `.env.kamal` file:

```dotenv
NINEROUTER_URL=http://host.docker.internal:20128
NINEROUTER_KEY=replace-with-your-9router-endpoint-key
OPENSTORYLINE_LLM_MODEL=cx/gpt-5.6-sol
OPENSTORYLINE_IMAGE_MODELS=cx/gpt-5.5-image
MISTRAL_API_KEYS=replace-with-your-mistral-api-key
MISTRAL_STT_TIMEOUT=180
```

Kamal delivers `NINEROUTER_KEY` and `MISTRAL_API_KEYS` as separate secret
environment variables. Never place resolved values in `config.toml`, committed
examples, provider QA output, container image layers, or job artifacts.

## 3. Configure optional Pexels stock sourcing

Pexels is disabled by default and is not a generated-image fallback. Keep its
credential only in ignored/local secret storage:

```dotenv
PEXELS_API_KEY=
OPENSTORYLINE_PEXELS_ENABLED=false
OPENSTORYLINE_MAX_STOCK_ASSETS_PER_CLIP=2
OPENSTORYLINE_PEXELS_LICENSE_REVIEWED_AT=
```

Before changing the flag to `true`, an operator must review the current
[Pexels API documentation](https://www.pexels.com/api/documentation/) and
[Pexels license](https://www.pexels.com/license/) in a browser, then record that
date as `YYYY-MM-DD`. The runtime rejects missing, future, or older-than-180-day
reviews. On 2026-07-18 both official pages remained Cloudflare-gated to the
automated documentation check, so this manual review is intentionally a release
prerequisite rather than a legal assumption embedded in code.

The adapter fixes the photo/video search endpoints, sends the key only in the
`Authorization` header, and applies a conservative application cap of 15 results
per search. It accepts only allowlisted Pexels HTTPS source/CDN URLs and
stores creator, source, selected-file, license, retrieval-time, and SHA-256
provenance. A search or download failure aborts the requested asset batch; it
does not call 9Router or substitute source media.

## 4. Run the redacted 9Router preflight

Load the private env file and validate only the Codex boundary:

```bash
set -a
source .env.kamal
set +a
python scripts/qa_ninerouter.py --strict-models --skip-ssh
```

For an explicitly authorized synthetic Codex canary, add `--live-inference`.
Image output remains in memory and the script prints only model, status,
category, byte count, and other sanitized metadata.

```bash
python scripts/qa_ninerouter.py \
  --strict-models \
  --live-inference \
  --timeout 240
```

The direct Mistral QA command is separate and uses a short synthetic,
non-private audio fixture. A skipped live call is not green deployment
evidence. `bin/kamal-mvp` requires both provider gates before `setup`, `deploy`,
or `redeploy`; read-only diagnostics and rollback remain available while a
gate is red.

## 5. Useful read-only 9Router checks

```bash
curl -fsS \
  -H "Authorization: Bearer $NINEROUTER_KEY" \
  "$NINEROUTER_URL/v1/models"

curl -fsS \
  -H "Authorization: Bearer $NINEROUTER_KEY" \
  "$NINEROUTER_URL/v1/models/image"
```

The exact Codex IDs must be present in their respective catalogs. STT is not
looked up under `/v1/models/stt`, and FireRed does not substitute OpenRouter,
Gemini, Groq, Hugging Face, or a local model.
