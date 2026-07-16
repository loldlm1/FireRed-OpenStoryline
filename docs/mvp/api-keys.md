# API keys: five-minute setup

The MVP needs one 9Router endpoint key plus credentials for at least two remote
STT providers. Free allocations are provider-controlled, rate-limited, and can
change; they are not an unlimited service guarantee. See the [verified RPM/RPD
and audio capacity](limites-gratis.md).

## 1. Create a free Groq key

1. Open <https://console.groq.com/keys> and sign in.
2. Select **Create API Key** and copy it once.
3. In 9Router, open **Providers > Groq**, add the API key, and run its connection
   test.

Groq supplies the first two models in the default cascade:

```text
groq/whisper-large-v3-turbo
groq/whisper-large-v3
```

## 2. Create a Hugging Face token

1. Open <https://huggingface.co/settings/tokens> and sign in.
2. Create a fine-grained token with permission to call Inference Providers.
3. In 9Router, open **Providers > Hugging Face**, add the token, and test it.

Hugging Face supplies the cross-provider fallbacks:

```text
huggingface/openai/whisper-large-v3
huggingface/openai/whisper-small
```

## 3. Configure the application

For production, copy `.env.kamal.example` to `.env.kamal`. The effective values
include:

```dotenv
KAMAL_HOST=203.0.113.10
NINEROUTER_URL=https://your-9router.example.com
NINEROUTER_KEY=replace-with-your-9router-endpoint-key
OPENSTORYLINE_WEB_TOKEN=replace-with-a-long-random-token
OPENSTORYLINE_LLM_MODEL=cx/gpt-5.6-sol
OPENSTORYLINE_REASONING_EFFORT=medium
OPENSTORYLINE_STT_MODELS=groq/whisper-large-v3-turbo,groq/whisper-large-v3,huggingface/openai/whisper-large-v3,huggingface/openai/whisper-small
```

`NINEROUTER_KEY` authenticates this application to your router. Groq and
Hugging Face credentials remain stored inside 9Router.

Image generation uses the same endpoint key. Add an image-capable account in
9Router (for example the Antigravity/Gemini route documented by 9Router) and
configure only models that its image catalog actually exposes. Provider keys
and subscription sessions remain inside 9Router.

## 4. Verify discovery

Run the redacted connectivity preflight before making provider inference calls:

```bash
set -a
source .env.kamal
set +a
python scripts/qa_ninerouter.py
```

The command checks public health, missing/invalid/valid endpoint-key behavior,
the text/image/STT catalogs, SSH, and remote Docker. It reports model counts and
configured IDs but never prints provider or endpoint credentials. Add
`--strict-models` only after the configured model policy has been reconciled
with the live catalogs.

```bash
curl -fsS \
  -H "Authorization: Bearer $NINEROUTER_KEY" \
  "$NINEROUTER_URL/v1/models/stt"
```

Confirm that at least one `groq/whisper-*` model and one
`huggingface/openai/whisper-*` model appear.

Discover image models separately:

```bash
curl -fsS \
  -H "Authorization: Bearer $NINEROUTER_KEY" \
  "$NINEROUTER_URL/v1/models/image"
```

Chat and image catalogs are different. Do not put `cx/gpt-5.6-sol` in
`OPENSTORYLINE_IMAGE_MODELS` unless that exact ID is returned by the image
catalog.

## 5. Verify transcription

```bash
curl -fsS -X POST \
  -H "Authorization: Bearer $NINEROUTER_KEY" \
  -F "model=groq/whisper-large-v3-turbo" \
  -F "language=es" \
  -F "response_format=verbose_json" \
  -F "file=@sample.mp3" \
  "$NINEROUTER_URL/v1/audio/transcriptions"
```

A successful response contains `text`. Keep the real keys only in 9Router and
your local `.env.kamal`; never paste them into `config.toml` or commit them.
