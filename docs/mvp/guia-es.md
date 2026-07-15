# Guía rápida del MVP remoto

Este perfil no instala ni ejecuta Whisper, Torch, TransNet, embeddings u otros
modelos locales. 9Router hace la inferencia remota; FFmpeg/FFprobe sólo procesan
audio y video de forma determinista en CPU.

Los niveles gratuitos de Groq y Hugging Face tienen límites, pueden cambiar y
no son un servicio ilimitado. La aplicación prueba los modelos en orden; si
todos fallan, el trabajo completo falla y conserva las razones en
`failure.json`.

## 1. Consigue las dos credenciales STT

1. Entra a <https://console.groq.com/keys>, crea una API key y guárdala.
2. Entra a <https://huggingface.co/settings/tokens>, crea un token fino con
   permiso para Inference Providers y guárdalo.
3. En 9Router abre **Providers**, agrega la key de Groq y el token de Hugging
   Face, y ejecuta la prueba de conexión de cada proveedor.

No pegues esas dos credenciales en este repositorio: quedan dentro de 9Router.

## 2. Crea la configuración local

```bash
cp .env.mvp.example .env.mvp
openssl rand -hex 32
```

Copia el resultado de `openssl` a `OPENSTORYLINE_WEB_TOKEN`. Luego edita sólo:

```dotenv
NINEROUTER_URL=http://host.docker.internal:20128
NINEROUTER_KEY=la-clave-del-endpoint-de-9router
OPENSTORYLINE_LLM_MODEL=cx/gpt-5.6-sol
OPENSTORYLINE_REASONING_EFFORT=medium
```

Si tu instalación de 9Router muestra otro identificador para GPT‑5.6 Sol,
utiliza exactamente el nombre que aparezca en su catálogo.

## 3. Comprueba 9Router

Desde el host, sustituye `host.docker.internal` por `127.0.0.1` si hace falta:

```bash
set -a
source .env.mvp
set +a

curl -fsS \
  -H "Authorization: Bearer $NINEROUTER_KEY" \
  "$NINEROUTER_URL/v1/models/stt"
```

La lista debe incluir al menos un modelo `groq/whisper-*` y uno
`huggingface/openai/whisper-*`.

## 4. Inicia el servicio

```bash
docker compose --env-file .env.mvp -f docker-compose.mvp.yml up --build
```

Abre <http://127.0.0.1:8000>, pega `OPENSTORYLINE_WEB_TOKEN`, sube el video y
escribe el prompt. La página muestra el progreso y permite descargar cada
artefacto o un ZIP completo.

Para exponerlo desde un VPS, mantén `MVP_BIND_IP=127.0.0.1` y publícalo detrás
de un reverse proxy HTTPS. No expongas el puerto sin TLS y sin el token.

## 5. Activa FFMPEGA, si lo deseas

1. Instala ComfyUI y, dentro de `ComfyUI/custom_nodes`, clona
   <https://github.com/AEmotionStudio/ComfyUI-FFMPEGA>.
2. Instala sus dependencias y reinicia ComfyUI en el puerto 8188.
3. En `.env.mvp` cambia:

```dotenv
OPENSTORYLINE_FFMPEGA_ENABLED=true
FFMPEGA_URL=http://host.docker.internal:8188
FFMPEGA_LOCAL_OUTPUT_ROOT=/app/outputs
FFMPEGA_REMOTE_OUTPUT_ROOT=/ruta/absoluta/FireRed-OpenStoryline/outputs
```

`FFMPEGA_REMOTE_OUTPUT_ROOT` es la ruta del mismo directorio `outputs` vista
desde el proceso de ComfyUI. Si ejecutas ambos servicios directamente en el
mismo host, deja ambas variables `*_OUTPUT_ROOT` vacías.

El adaptador usa el modo manual sin LLM de FFMPEGA, desactiva descargas de
modelos y sólo permite efectos FFmpeg de una lista blanca. Si FFMPEGA está
activado y falla, el trabajo falla: no se aplica un fallback silencioso.

## 6. Ejecuta sin Docker (opcional)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-remote.txt
set -a && source .env.mvp && set +a
uvicorn mvp_fastapi:app --host 127.0.0.1 --port 8000
```

Debes tener `ffmpeg` y `ffprobe` disponibles en `PATH`.
