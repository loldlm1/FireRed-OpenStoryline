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

La política aprobada usa exclusivamente `cx/gpt-5.5-image` mediante las
sesiones Codex OAuth ya conectadas a 9Router. No se configura una key de
OpenAI, Gemini o xAI en FireRed y no existe un segundo modelo de imagen.

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
4. si falla el modelo aprobado, elimina el lote parcial y no usa Pexels ni inferencia
   local de manera silenciosa.

Esto es trazabilidad y reducción de riesgo, no asesoría ni autorización legal.

## Límite operativo

La disponibilidad depende de que 9Router siga publicando
`cx/gpt-5.5-image` y de que alguna sesión Codex OAuth activa pueda servirlo.
Si el catálogo cambia, la sesión expira o la generación falla, el despliegue y
el lote fallan cerrados. No se sustituye por otro proveedor.

## Configuración en tres pasos

Comprueba el catálogo y el modelo aprobado:

```bash
curl -fsS \
  -H "Authorization: Bearer $NINEROUTER_KEY" \
  "$NINEROUTER_URL/v1/models/image"

curl -fsS \
  -H "Authorization: Bearer $NINEROUTER_KEY" \
  "$NINEROUTER_URL/v1/models/info?id=cx/gpt-5.5-image"
```

Configura únicamente el ID aprobado:

```dotenv
OPENSTORYLINE_IMAGE_MODELS=cx/gpt-5.5-image
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
