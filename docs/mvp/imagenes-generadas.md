# Imágenes remotas para el plan de video

Datos y precios revisados el 16 de julio de 2026. Los catálogos, cuotas y
condiciones de cada cuenta cambian; el endpoint de tu 9Router y el panel del
proveedor siempre tienen precedencia.

## Qué hace GPT‑5.6 Sol y qué hace el modelo de imagen

En esta arquitectura `cx/gpt-5.6-sol` analiza la intención, el material y el
plan narrativo; decide cuándo hacen falta imágenes y redacta `image_prompt`.
No se presupone que ese endpoint de chat entregue bytes de imagen. La imagen la
crea un modelo independiente que 9Router publique en `GET /v1/models/image`, a
través de `POST /v1/images/generations`.

OpenAI también documenta que un modelo principal compatible puede invocar una
herramienta de generación, pero esa herramienta usa un modelo GPT Image y tiene
su propio costo de salida. No convierte una suscripción de ChatGPT Plus en
crédito de API. Consulta la [guía oficial de Image API](https://developers.openai.com/api/docs/guides/image-generation),
la [presentación de GPT‑5.6](https://openai.com/index/gpt-5-6/) y la
[separación entre ChatGPT y API](https://help.openai.com/en/articles/8156019).

Regla operativa: sólo trata un ID como generador si aparece en
`/v1/models/image`. Que aparezca en el catálogo general o responda a
`/v1/chat/completions` no basta.

## “Original” no significa “sin copyright”

La generación reduce la dependencia de una fotografía de stock concreta, pero
no existe una garantía técnica de “copyright-free”. Una salida puede parecerse
a obras, marcas, personajes o personas existentes; además aplican los términos
del proveedor que 9Router haya elegido. Los [términos de OpenAI](https://openai.com/policies/terms-of-use/),
por ejemplo, asignan al usuario sus derechos sobre el output entre las partes,
pero advierten que el output puede no ser único y excluyen una garantía de no
infracción.

La implementación aplica cuatro controles:

1. añade al prompt una instrucción contra artistas nombrados, personajes,
   celebridades, logos, marcas, firmas, marcas de agua y texto;
2. guarda modelo, hash del prompt y SHA-256 de cada archivo en un manifiesto;
3. recuerda al agente y al usuario que hace falta revisión humana antes de
   publicar; y
4. si falla la cascada, elimina el lote parcial y no usa Pexels ni inferencia
   local de manera silenciosa.

Esto es trazabilidad y reducción de riesgo, no asesoría ni autorización legal.

## Opciones gratuitas o de costo incluido

9Router es software libre, pero no regala por sí mismo la inferencia de los
proveedores. “Gratis” normalmente significa cuota promocional o incluida en una
cuenta conectada.

| Ruta | Situación comprobable | Recomendación |
| --- | --- | --- |
| Antigravity/Gemini en 9Router | 9Router documenta generación nativa y usa `gemini/gemini-3-pro-image-preview` en su ejemplo. La gratuidad depende de la cuota de la cuenta conectada. | Primera candidata si aparece en tu catálogo y el panel muestra cuota disponible. |
| xAI/Grok en 9Router | El endpoint de texto-a-imagen funciona con `xai/grok-imagine-image`; no hay una asignación gratuita universal verificada. | Segundo candidato sólo si tu cuenta incluye cuota. |
| Hugging Face Inference Providers | Una cuenta Free recibe actualmente USD 0,10 mensuales; el modelo/ID debe aparecer en tu 9Router. | Útil para pruebas pequeñas, no para presupuestar una jornada diaria. |
| Gemini Developer API directo | Los modelos actuales `gemini-3.1-flash-image` y Lite no tienen Free Tier de salida de imagen; sus referencias de 1K son aproximadamente USD 0,067 y USD 0,0336 por imagen. | Alternativa económica de pago si la cuota incluida en 9Router no alcanza. |
| OpenAI GPT Image API | Generación separada y de pago; ChatGPT Plus no cubre el uso de API. | Buena calidad, pero no es la opción gratuita solicitada. |
| ComfyUI local | Requeriría un modelo local y normalmente GPU para tiempos razonables. | Excluido por la política remote-only de este fork. |

Fuentes: [skill oficial de imágenes de 9Router](https://github.com/decolua/9router/blob/master/skills/9router-image/SKILL.md),
[soporte Antigravity `kind:image`](https://github.com/decolua/9router/blob/master/CHANGELOG.md),
[endpoint Grok observado](https://github.com/decolua/9router/issues/1608),
[precios de Gemini](https://ai.google.dev/gemini-api/docs/pricing) y
[créditos de Hugging Face](https://huggingface.co/docs/inference-providers/pricing).

No hay, por tanto, un modelo de imagen remoto que podamos prometer como gratis,
estable y suficiente todos los días. La recomendación práctica es aprovechar
primero la cuota incluida que 9Router exponga, limitar el número de imágenes y
mantener una ruta económica de pago para cuando esa cuota se agote.

## Configuración en tres pasos

Descubre los IDs y consulta las capacidades de cada uno:

```bash
curl -fsS \
  -H "Authorization: Bearer $NINEROUTER_KEY" \
  "$NINEROUTER_URL/v1/models/image"

curl -fsS \
  -H "Authorization: Bearer $NINEROUTER_KEY" \
  "$NINEROUTER_URL/v1/models/info?id=gemini/gemini-3-pro-image-preview"
```

Configura una cascada que contenga sólo IDs devueltos por el primer comando:

```dotenv
OPENSTORYLINE_IMAGE_MODELS=gemini/gemini-3-pro-image-preview,xai/grok-imagine-image
OPENSTORYLINE_IMAGE_TIMEOUT=180
OPENSTORYLINE_IMAGE_MAX_BYTES=26214400
OPENSTORYLINE_IMAGE_SIZE=1024x1024
```

Después pide al agente usar el nodo `SearchMedia` con una intención equivalente
a:

```json
{
  "mode": "auto",
  "photo_source": "generated",
  "photo_number": 4,
  "video_number": 0,
  "orientation": "portrait",
  "search_keyword": "energía solar",
  "image_prompt": "Fotografía editorial de una comunidad instalando paneles solares al amanecer, composición dinámica, luz cálida, paleta azul y ámbar"
}
```

`video_number` debe ser `0` si no quieres configurar Pexels. Si pides uno o más
videos, éstos siguen viniendo de Pexels y su key continúa siendo obligatoria.
Los archivos generados conservan el mismo contrato `{"path": "..."}` que las
fotos de Pexels, por lo que los nodos posteriores pueden analizarlos y ubicarlos
en el timeline del flujo completo.
