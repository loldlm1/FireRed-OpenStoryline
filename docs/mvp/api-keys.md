# 9Router credentials and model contracts

FireRed uses one endpoint key for 9Router. Provider credentials stay in
9Router and never enter FireRed environment files, job state, logs, or Git.
The approved runtime policy is deliberately single-provider:

| Layer | Exact model | Credential owner |
| --- | --- | --- |
| Text planning and vision | `cx/gpt-5.6-sol` | Codex OAuth in 9Router |
| Image generation | `cx/gpt-5.5-image` | Codex OAuth in 9Router |
| Speech-to-text | `mistral/voxtral-mini-2602` | Mistral API key in 9Router |

There are no runtime fallbacks. A missing catalog entry, expired connection,
rate limit, invalid response, or missing STT timestamps is a release-blocking
failure.

## 1. Connect the approved providers in 9Router

1. Add or keep the Codex OAuth connections in 9Router and verify that the
   connections are active. Do not paste OAuth tokens into this repository.
2. Add the Mistral API key in 9Router under the Mistral provider. Keep the key
   in the router database only.
3. Create one 9Router endpoint key for FireRed.

The current published 9Router `0.5.35` catalog includes Mistral chat models but
does not expose Mistral under `/v1/models/stt`. The repository contains an
offline, version-pinned adapter patch under
`patches/9router/0.5.35-mistral-stt.patch`; activating it requires a separate
maintenance window because it cannot be loaded into the running Node process.

## 2. Configure FireRed

Copy `.env.kamal.example` to `.env.kamal`, set the endpoint URL/key and web
token, then keep the model values exactly as follows:

```dotenv
NINEROUTER_URL=http://host.docker.internal:20128
NINEROUTER_KEY=replace-with-your-9router-endpoint-key
OPENSTORYLINE_LLM_MODEL=cx/gpt-5.6-sol
OPENSTORYLINE_IMAGE_MODELS=cx/gpt-5.5-image
OPENSTORYLINE_STT_MODELS=mistral/voxtral-mini-2602
```

`NINEROUTER_KEY` is the only provider credential FireRed needs. The endpoint
key is stored through Kamal secrets and must not be placed in `config.toml` or
committed files.

## 3. Run the redacted preflight

Load the private env file in the shell and run the catalog/auth checks before
any provider generation call:

```bash
set -a
source .env.kamal
set +a
python scripts/qa_ninerouter.py --strict-models --skip-ssh
```

The preflight reports only status classes, model IDs, counts, and sanitized
reasons. It performs negative endpoint-key probes and never prints keys,
OAuth payloads, prompts, transcripts, or raw provider bodies.

For an explicitly authorized synthetic provider canary, add
`--live-inference` and provide a short non-private speech fixture with
`--stt-audio /path/to/sample.wav`. Image output is held in memory and is not
written to the repository.

```bash
python scripts/qa_ninerouter.py \
  --strict-models \
  --live-inference \
  --stt-audio /tmp/openstoryline-qa-speech.wav \
  --timeout 240
```

To prove the VPS container-to-host route, select an existing disposable image
that contains `curl` or `wget`. The preflight uses `--pull=never`, does not
restart 9Router, and removes the probe container when it exits:

```bash
NINEROUTER_PROBE_IMAGE=your-existing-python-or-curl-image \
python scripts/qa_ninerouter.py \
  --strict-models \
  --container-host-probe
```

Important categories are `auth`, `catalog_mismatch`, `rate_limited`,
`upstream`, `contract_invalid`, `missing_fixture`, and `transport`. A skipped
provider call is not itself green evidence; the corresponding strict catalog
check remains release-blocking.

## 4. Useful read-only checks

```bash
curl -fsS \
  -H "Authorization: Bearer $NINEROUTER_KEY" \
  "$NINEROUTER_URL/v1/models"

curl -fsS \
  -H "Authorization: Bearer $NINEROUTER_KEY" \
  "$NINEROUTER_URL/v1/models/image"

curl -fsS \
  -H "Authorization: Bearer $NINEROUTER_KEY" \
  "$NINEROUTER_URL/v1/models/stt"
```

The exact IDs must be present in their respective catalogs. Do not substitute
an OpenRouter, Gemini, Groq, Hugging Face, or local model when a selected
contract is unavailable.
