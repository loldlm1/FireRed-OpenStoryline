# Capacidad gratuita de Whisper remoto

Datos revisados el 15 de julio de 2026. Los proveedores pueden cambiarlos sin
aviso; la consola de tu organización y los encabezados HTTP siempre tienen
precedencia sobre esta guía.

## Groq Free Plan

La [tabla oficial de rate limits de Groq](https://console.groq.com/docs/rate-limits)
publica los mismos límites para ambos Whisper:

| Modelo Groq | RPM | RPD | ASH | ASD |
| --- | ---: | ---: | ---: | ---: |
| `whisper-large-v3-turbo` | 20 | 2.000 | 7.200 s (2 h) | 28.800 s (8 h) |
| `whisper-large-v3` | 20 | 2.000 | 7.200 s (2 h) | 28.800 s (8 h) |

RPM/RPD cuentan solicitudes. Para video, ASH/ASD —segundos de audio por hora y
por día— normalmente serán el límite real. Las cuotas se aplican a nivel de
organización y una petición puede ser rechazada al alcanzar cualquiera de
ellas. Comprueba el panel de límites de tu cuenta porque puede contener
excepciones.

La [documentación STT de Groq](https://console.groq.com/docs/speech-to-text)
limita el archivo directo gratuito a 25 MB. El MVP extrae MP3 mono, 16 kHz y
48 kbit/s: una fuente de 30 minutos produce aproximadamente 10,8 MB y cabe sin
trocearla. Un video mucho más largo puede superar 25 MB aunque el archivo de
video original no se envía a Groq.

### ¿Alcanza para una jornada?

Sí, para un uso individual moderado:

- 28.800 segundos equivalen teóricamente a 16 fuentes de 30 minutos por día
  para el modelo primario.
- 7.200 segundos/hora permiten hasta cuatro fuentes de 30 minutos en una hora.
- Crear muchos clips de 20 segundos desde una misma fuente no repite Whisper:
  la transcripción de los 30 minutos se hace una vez.
- 20 RPM y 2.000 RPD quedan muy por encima de ese volumen; ASH/ASD y el tiempo
  de render CPU serán los cuellos de botella.

Es una capacidad teórica, no un SLA. Los reintentos consumen cuota y no se debe
sumar automáticamente la capacidad de ambos modelos: el segundo sólo se prueba
cuando falla el primero y la cuenta/organización puede compartir restricciones.

## Hugging Face gratuito

Hugging Face no publica un RPM/RPD fijo y garantizado para cada modelo Whisper
ruteado. La disponibilidad y los 429 dependen del modelo, proveedor subyacente,
cuenta y carga. La [página oficial de precios](https://huggingface.co/docs/inference-providers/en/pricing)
indica actualmente:

| Cuenta | Crédito mensual de Inference Providers |
| --- | ---: |
| Free | USD 0,10, sujeto a cambios |
| PRO | USD 2,00 |

Al agotar el crédito gratuito hay que comprar crédito adicional. Si se usa una
provider key propia, el proveedor cobra directamente y no se aplica el crédito
de Hugging Face.

Por eso Hugging Face gratis sirve para verificar la integración o rescatar
algún trabajo, pero no debe presupuestarse como una jornada diaria. Además,
9Router debe mostrar los IDs exactos `huggingface/openai/whisper-*` en
`/v1/models/stt`; si no aparecen, elimínalos de la cascada o usa los nombres que
publique tu instalación.

## Orden recomendado

```dotenv
OPENSTORYLINE_STT_MODELS=groq/whisper-large-v3-turbo,groq/whisper-large-v3,huggingface/openai/whisper-large-v3,huggingface/openai/whisper-small
```

Groq Turbo cubre el trabajo normal; Groq Large prioriza exactitud cuando Turbo
falla; Hugging Face aporta separación de proveedor. No existe fallback local:
si todos fallan, el job termina con `STT_ALL_PROVIDERS_FAILED` y conserva la
razón de cada intento sin guardar las keys.
