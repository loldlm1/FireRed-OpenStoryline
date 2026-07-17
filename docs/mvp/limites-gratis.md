# Capacidad gratuita de Mistral STT

Datos revisados el 16 de julio de 2026. La consola de la organización y los
encabezados de respuesta siempre tienen precedencia porque Mistral puede
cambiar límites o condiciones sin aviso.

## Modelo seleccionado

El único modelo STT aprobado es:

```text
mistral/voxtral-mini-2602
```

FireRed lo consume mediante `/v1/audio/transcriptions` de 9Router. La key de
Mistral permanece dentro de 9Router; FireRed sólo conoce la URL y endpoint key
del router.

## Límites observados del modo Free

La organización configurada mostró estas capacidades para
`voxtral-mini-2602`:

| Límite | Valor observado |
| --- | ---: |
| Solicitudes | 1 por segundo |
| Tokens | 50.000 por minuto |
| Audio | 3.600 segundos por minuto |

El panel mostró un guion para el valor mensual de audio. Ese guion no se trata
como una promesa de uso mensual ilimitado, ni como SLA. Un HTTP 429 o un cambio
de catálogo debe detener el trabajo con un error sanitizado.

## Timestamps obligatorios

FireRed necesita segmentos con texto y valores finitos `start`/`end`; una
respuesta con sólo texto no sirve para subtítulos ni para los cortes. El
adaptador de 9Router solicita:

```text
timestamp_granularities=segment
```

Mistral documenta que este parámetro no puede combinarse actualmente con
`language`. Por eso el adaptador omite el idioma explícito y deja que el
servicio lo detecte.

## Política de fallo

No hay Groq, Hugging Face, Gemini, OpenRouter ni modelo local como fallback.
Si Mistral está sin cuota, sin credencial, fuera del catálogo o devuelve una
respuesta sin segmentos, el job termina con `STT_ALL_PROVIDERS_FAILED` y
conserva sólo la razón sanitizada del intento.
