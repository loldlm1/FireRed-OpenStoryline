# API keys: five-minute setup

The MVP needs one 9Router endpoint key plus credentials for at least two remote
STT providers. Free allocations are provider-controlled, rate-limited, and can
change; they are not an unlimited service guarantee.

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

Copy `.env.mvp.example` to `.env.mvp` when that file is introduced in Sprint 8.
The effective values are:

```dotenv
NINEROUTER_URL=http://127.0.0.1:20128
NINEROUTER_KEY=replace-with-your-9router-endpoint-key
OPENSTORYLINE_LLM_MODEL=cx/gpt-5.6-sol
OPENSTORYLINE_REASONING_EFFORT=medium
OPENSTORYLINE_STT_MODELS=groq/whisper-large-v3-turbo,groq/whisper-large-v3,huggingface/openai/whisper-large-v3,huggingface/openai/whisper-small
```

`NINEROUTER_KEY` authenticates this application to your router. Groq and
Hugging Face credentials remain stored inside 9Router.

## 4. Verify discovery

```bash
curl -fsS \
  -H "Authorization: Bearer $NINEROUTER_KEY" \
  "$NINEROUTER_URL/v1/models/stt"
```

Confirm that at least one `groq/whisper-*` model and one
`huggingface/openai/whisper-*` model appear.

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
your local `.env.mvp`; never paste them into `config.toml` or commit them.
