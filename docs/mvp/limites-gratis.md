# Capacidad gratuita de Mistral STT

Datos revisados el 16 de julio de 2026. La consola de la organización y los
encabezados de respuesta siempre tienen precedencia porque Mistral puede
cambiar límites o condiciones sin aviso.

## Modelo y ruta seleccionados

FireRed consume directamente el único modelo STT aprobado:

```text
POST https://api.mistral.ai/v1/audio/transcriptions
model=voxtral-mini-2602
```

La variable `MISTRAL_API_KEYS` acepta una o más keys ordenadas y Kamal la
entrega como secreto. 9Router no participa en STT y continúa atendiendo sólo
las capas Codex de texto, visión e imágenes.

## Límites observados del modo Free

La organización configurada mostró estas capacidades para
`voxtral-mini-2602`:

| Límite | Valor observado |
| --- | ---: |
| Solicitudes | 1 por segundo |
| Tokens | 50.000 por minuto |
| Audio | 3.600 segundos por minuto |

El panel mostró un guion para el valor mensual de audio. Ese guion no se trata
como una promesa de uso mensual ilimitado ni como SLA. Varias keys de la misma
organización pueden compartir los mismos límites y no garantizan más capacidad.

## Timestamps obligatorios

FireRed necesita segmentos con texto y valores finitos `start`/`end`; una
respuesta con sólo texto no sirve para subtítulos ni para los cortes. El cliente
solicita `timestamp_granularities=segment`. Mistral documenta que este parámetro
no puede combinarse actualmente con `language`, por lo que el servicio detecta
el idioma automáticamente.

## Política de fallo

No hay Groq, Hugging Face, Gemini, OpenRouter ni modelo local como fallback.
Si Mistral queda sin cuota, sin credencial o devuelve una respuesta sin
segmentos, el job termina con un error sanitizado. La rotación sólo puede
continuar con otra key configurada para el mismo endpoint y modelo.
