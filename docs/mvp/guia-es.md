# Guía de producción del MVP remoto con Kamal

Este perfil no instala ni ejecuta modelos de IA locales. Texto, visión e
imágenes usan Codex OAuth mediante 9Router; STT usa Voxtral directamente con
Mistral y el stock opcional usa Pexels sin fallback. FFmpeg/FFprobe sólo procesan audio y video de forma determinista en
CPU. Si alguno de esos contratos falla, el trabajo guarda razones sanitizadas
en `failure.json`.

Kamal reemplaza el flujo manual con Docker Compose, pero usa Docker
internamente. `kamal setup` entra al VPS por SSH, instala Docker si falta,
construye la imagen con Python, FFmpeg y todas las dependencias del servicio,
arranca el proxy y verifica `/up`. No hace falta instalar Python ni FFmpeg a
mano en el VPS.

## 1. Prepara las credenciales remotas

1. Conserva activas las conexiones Codex OAuth de 9Router.
2. Crea una endpoint key de 9Router sólo para OpenStoryline.
3. Conserva una o más keys válidas de Mistral para `MISTRAL_API_KEYS`.
4. Deja Pexels apagado o conserva su key por separado si existe un rollout aprobado.

Las credenciales Codex quedan en 9Router. FireRed recibe la URL/key del endpoint
de 9Router y la key ring directa de Mistral como secretos Kamal independientes.
Revisa la [guía de keys](api-keys.md) y los [límites gratuitos
verificados](limites-gratis.md). Para imágenes revisa la
[guía de generación remota y derechos](imagenes-generadas.md); una cuota
incluida por un proveedor no equivale a gratuidad garantizada.

La rotación conserva el orden configurado. Un `429` respeta `Retry-After`, una
key inválida se desactiva para el proceso y un audio/contrato inválido no se
repite con las demás keys. El primer despliegue usa un solo contenedor porque
los cooldowns viven en memoria del proceso.

## 2. Prepara la máquina desde la que desplegarás

Necesitas Git, Docker en ejecución, acceso SSH por clave al VPS, Ruby con
`gem` y Kamal 2.12.0 instalado explícitamente. El wrapper rechaza otra versión
y no instala herramientas durante el despliegue. En Windows, ejecuta estos
pasos desde WSL2.

El VPS puede ser una instalación nueva de Ubuntu/Debian sin Python ni Docker.
Para el primer despliegue se recomienda SSH como `root`; abre el puerto SSH y
el puerto público elegido. Para dominio con HTTPS deben estar abiertos 80 y
443.

```bash
git clone https://github.com/loldlm1/FireRed-OpenStoryline.git
cd FireRed-OpenStoryline
cp .env.kamal.example .env.kamal
./bin/kamal-mvp auth hash-password
```

El último comando pide la contraseña dos veces sin mostrarla y devuelve sólo su
hash Argon2id. Copia ese hash a `OPENSTORYLINE_WEB_PASSWORD_HASH`. Genera además
valores distintos con `openssl rand -hex 32` para el pepper de seguridad y las
dos contraseñas de PostgreSQL. Edita como mínimo:

```dotenv
KAMAL_HOST=203.0.113.10
KAMAL_SSH_USER=root
KAMAL_DOMAIN=video.example.com
NINEROUTER_URL=https://tu-9router.example.com
NINEROUTER_KEY=clave-del-endpoint-de-9router
MISTRAL_API_KEYS=key-directa-de-mistral
PEXELS_API_KEY=
POSTGRES_PASSWORD=contraseña-aleatoria-del-administrador-de-postgres
OPENSTORYLINE_DATABASE_PASSWORD=otra-contraseña-aleatoria-de-la-aplicación
DATABASE_URL=postgresql+psycopg://openstoryline:contraseña-de-la-aplicación@openstoryline-mvp-db:5432/openstoryline
OPENSTORYLINE_WEB_PASSWORD_HASH='$argon2id$hash-generado'
OPENSTORYLINE_SECURITY_PEPPER=pepper-aleatorio-de-64-caracteres
OPENSTORYLINE_PUBLIC_ORIGIN=https://video.example.com
OPENSTORYLINE_ALLOW_INSECURE_HTTP=false
OPENSTORYLINE_PEXELS_ENABLED=false
OPENSTORYLINE_PEXELS_LICENSE_REVIEWED_AT=
```

`NINEROUTER_URL` debe ser accesible desde el VPS. Si 9Router sólo escucha en
`127.0.0.1` de otra computadora, el servidor remoto no podrá conectarse.
El password sin hash no se guarda en `.env.kamal`, PostgreSQL, JavaScript,
headers, URLs ni logs. Conserva las comillas simples alrededor del hash: sus
caracteres `$` serían interpretados al cargar `.env.kamal` sin esas comillas.
Pexels permanece apagado y no necesita key para el despliegue base. Si se
aprueba activarlo, guarda la key sólo en `.env.kamal`, revisa manualmente la
documentación y licencia oficiales actuales, escribe la fecha `YYYY-MM-DD` en
`OPENSTORYLINE_PEXELS_LICENSE_REVIEWED_AT` y luego cambia
`OPENSTORYLINE_PEXELS_ENABLED=true`. El wrapper rechaza una fecha futura o con
más de 180 días; no realiza una búsqueda Pexels durante el deploy.

## 3. Elige IP:puerto o dominio

Para una prueba por IP, deja `KAMAL_DOMAIN` vacío. Puedes usar el puerto 80 o
uno personalizado:

```dotenv
KAMAL_DOMAIN=
KAMAL_HTTP_PORT=8080
OPENSTORYLINE_PUBLIC_ORIGIN=http://203.0.113.10:8080
OPENSTORYLINE_ALLOW_INSECURE_HTTP=true
```

La URL será `http://203.0.113.10:8080`. Este modo no cifra la contraseña durante
el transporte; este proyecto lo acepta únicamente para el uso personal privado
confirmado, dentro de una red confiable, VPN o acceso equivalente. No es una
recomendación para publicar HTTP en Internet. El wrapper exige el opt-in
explícito y no permite activar este modo por accidente cuando existe un dominio.
En este modo el contenedor publica el puerto directamente y no reinicia ni
reconfigura un `kamal-proxy` compartido que ya exista en el VPS. Los deploys y
rollbacks detienen sólo el contenedor web actual justo antes de arrancar el
nuevo, por lo que existe una ventana corta de mantenimiento en la aplicación;
el proceso de 9Router no se modifica.

Para producción con dominio, crea primero un registro DNS A/AAAA que apunte al
VPS y configura:

```dotenv
KAMAL_DOMAIN=video.example.com
KAMAL_HTTP_PORT=80
KAMAL_HTTPS_PORT=443
OPENSTORYLINE_PUBLIC_ORIGIN=https://video.example.com
OPENSTORYLINE_ALLOW_INSECURE_HTTP=false
```

Kamal-proxy solicitará y renovará el certificado de Let's Encrypt. La URL será
`https://video.example.com`. El HTTPS automático requiere un solo servidor y
los puertos 80/443. En un VPS con otro `kamal-proxy`, programa una ventana de
mantenimiento antes de activar este modo.

## 4. Verifica los proveedores y despliega

Antes del despliegue, carga el archivo y comprueba los catálogos Codex:

```bash
set -a
source .env.kamal
set +a

curl -fsS \
  -H "Authorization: Bearer $NINEROUTER_KEY" \
  "$NINEROUTER_URL/v1/models"

curl -fsS \
  -H "Authorization: Bearer $NINEROUTER_KEY" \
  "$NINEROUTER_URL/v1/models/image"
```

Confirma que aparece exactamente `cx/gpt-5.5-image`. Puedes consultar tamaños
y opciones con
`/v1/models/info?id=ID`. La aplicación usa `1024x1024` por compatibilidad; si
el modelo no lo acepta, cambia `OPENSTORYLINE_IMAGE_SIZE` por un valor anunciado
en ese endpoint.

Guarda fuera del repositorio un audio corto, sintético y no privado. Configura
su ruta absoluta en el `.env.kamal` ignorado:

```bash
MISTRAL_QA_STT_AUDIO=/ruta/absoluta/openstoryline-qa-speech.wav
NINEROUTER_QA_TIMEOUT=240
```

`./bin/kamal-mvp setup`, `deploy` y `redeploy` ejecutan gates separados: 9Router
para texto/visión/imagen y Mistral directo para STT. Cualquier fallo de catálogo,
autenticación, transporte, cuota o contrato detiene el comando antes de iniciar
Kamal. Los comandos de diagnóstico y `rollback` siguen disponibles cuando un
gate está rojo. No reinicies ni reconfigures el proceso manual de 9Router.
Si `OPENSTORYLINE_PEXELS_ENABLED=true`, el wrapper también exige la key y una
revisión vigente de licencia antes de esos gates, pero no consume cuota ni
descarga medios Pexels.

Antes de cambiar schemas estrictos, reparación, entrega de candidatos técnicos
o detalles de reintento, sigue el orden reversible de
[`agentic-defect-repair-rollout.md`](agentic-defect-repair-rollout.md) y ejecuta
`./bin/kamal-mvp rollout validate`. Ese validador es local: no llama proveedores
ni modifica el despliegue.

El primer rollout con PostgreSQL se prepara por etapas para que la base de
datos y la copia verificable existan antes de arrancar la nueva aplicación:

```bash
./bin/kamal-mvp server bootstrap
./bin/kamal-mvp registry setup
./bin/kamal-mvp accessory boot db
./bin/kamal-mvp build deliver
./bin/kamal-mvp db migrate
./bin/kamal-mvp db current
./bin/kamal-mvp db readiness
./bin/kamal-mvp db backup
./bin/kamal-mvp db restore-check
./bin/kamal-mvp deploy --skip-push
```

`build deliver` construye `Dockerfile.remote` y entrega la imagen candidata.
Las migraciones usan esa imagen exacta en la red privada, sin publicar el
puerto web. El hook `pre-deploy` repite la migración idempotente antes de
detener el contenedor actual; un rollback omite la migración hacia adelante.
El despliegue monta `/var/lib/openstoryline/outputs` y conserva el accesorio
PostgreSQL privado.
Antes del corte, el mismo hook crea o corrige de forma idempotente el dueño del
directorio de outputs para el UID/GID fijo `65532`; no cambia los directorios de
datos ni backups de PostgreSQL. La imagen web corre sin root. El hook
`post-deploy` exige que `/up` y `/health` respondan correctamente.
Las sesiones de autenticación, las sesiones de edición, los límites de login,
los trabajos y sus eventos sobreviven a redeploys en PostgreSQL. Los videos y
los snapshots `job.json` permanecen en el volumen de outputs; PostgreSQL es la
fuente autoritativa para el estado actual del trabajo.

Los siguientes cambios se publican con:

```bash
./bin/kamal-mvp deploy
```

Comandos útiles:

```bash
./bin/kamal-mvp details
./bin/kamal-mvp app logs
./bin/kamal-mvp rollback VERSION_EXPLICITA
```

El rollback ejecuta primero el contrato de readiness de la imagen objetivo
contra la revisión PostgreSQL actual. Si la imagen no reconoce el esquema, el
wrapper falla antes de pedirle a Kamal que la seleccione.

Si cambias `KAMAL_HTTP_PORT` en modo IP, ejecuta `./bin/kamal-mvp deploy` para
recrear el contenedor con la nueva publicación directa. Sólo el modo dominio
usa `./bin/kamal-mvp proxy reboot` al cambiar los puertos del proxy.

## 5. Entra con la contraseña y usa la aplicación

Abre la URL. La vista inicial muestra sólo el formulario de contraseña; el
formulario de video aparece después de autenticar. Crea una sesión de edición o
retoma una existente desde el selector. Cada sesión agrupa varios trabajos y la
URL conserva el identificador para restaurarla después de refrescar o volver a
iniciar sesión. La página permite ver trabajos recientes, progreso y descargar
cada artefacto o un ZIP. Cerrar sesión revoca la sesión de autenticación en
PostgreSQL y borra las cookies del navegador; no elimina la sesión de edición.

En edición agentiva hay dos controles separados. “Imágenes generadas” autoriza
únicamente `cx/gpt-5.5-image`; “Video de archivo” usa sólo Pexels. En ambos,
“cuando ayuden” define un máximo opcional y “cantidad exacta obligatoria” exige
ese número por clip. Las instrucciones explícitas como “exactamente una imagen
generada y un video Pexels” también se conservan como intención obligatoria al
repetir una versión antigua. El sistema falla antes de llamar al proveedor si
la capacidad requerida está apagada, y falla la planificación si el recurso no
queda unido a una operación visible. Desactivar capacidades sin requisitos
explícitos conserva encuadres, cortes, capas de fuente, texto, transiciones y
subtítulos sin llamadas de assets. No existe fallback entre Pexels, 9Router y
el video fuente.

La intención creativa `creative_intent.v2` también reconoce, sin depender de
acentos, requisitos explícitos en español o inglés para un título de apertura,
una cantidad exacta de 2, 3 o 4 —o una secuencia acotada de 2 a 4—
reencuadres/zooms y transiciones breves y discretas. Cada requisito debe quedar
unido a capas o segmentos ejecutables con conteos y tiempos válidos. El planner
y la reparación comparten un template ejecutable que conserva esas operaciones
después del intento LLM, sin cambiar el video fuente, la selección temporal ni
el número de salidas. Las transiciones discretas seleccionan un fundido
ejecutable de un estilo compatible del catálogo creativo, sin emitir un ID de
catálogo vacío o inventado. Si un reencuadre obligatorio conserva poca evidencia
temporal después de sus intentos LLM acotados, el segmento usa un reencuadre
central sin objetivo inventado en vez de convertirse silenciosamente en un fit
estático; la QA semántica del resultado sigue siendo autoritativa. Si aun después del intento y del baseline seguro no puede
cumplirse, la QA estricta lo conserva como limitación creativa y la entrega
técnica nunca lo presenta como resultado mejorado.

El análisis visual ahora tiene dos escalas. Las muestras globales ayudan a
elegir el fragmento, pero cada fragmento seleccionado recibe además muestras
propias cerca del inicio, final, cuartiles, centro y cambios de escena. Un
recorte automático sólo puede usar regiones o tracks observados dentro de esa
ventana. Si la cobertura no alcanza, el sistema repite una vez el análisis y la
planificación con más frames; si todavía falla, termina antes de buscar assets o
renderizar. `clip_visual_coverage.json` conserva sólo IDs, timestamps, métricas
y códigos de bloqueo. Un fallback a `fit`/letterbox debe estar autorizado de
forma explícita; el motor ya no convierte silenciosamente un recorte en una
imagen horizontal pequeña dentro del lienzo vertical. `fit` conserva el primer
plano completo sobre una copia atenuada y desenfocada del mismo video que llena
el lienzo; `letterbox` conserva relleno sólido sólo cuando se pide de forma
explícita. Si ese relleno deja muy poca imagen activa, el defecto recibe primero
un intento LLM y su fallback determinista cambia únicamente ese segmento a
`fit` antes del dry-run final.

En dominio/HTTPS, la sesión usa una cookie opaca `HttpOnly`, `Secure` y
`SameSite`, junto con un token CSRF separado para operaciones que modifican
estado. El opt-in HTTP de desarrollo no puede usar `Secure`. En ningún modo hay
una clave reutilizable en el DOM, URL, `localStorage` o `sessionStorage`.

Los clientes antiguos que enviaban `X-API-Key` o `Authorization: Bearer` dejan
de ser compatibles de forma intencional. Un cliente automatizado debe iniciar
sesión en `/api/mvp/auth/login`, conservar cookies, enviar el origen configurado
y presentar `X-CSRF-Token` en cada request que modifique estado. No pases la
contraseña por argumentos del shell ni la guardes en scripts.

Sólo los intentos fallidos de contraseña consumen límites persistentes:

| Ámbito | RPM | RPD | Para qué sirve |
| --- | ---: | ---: | --- |
| Intentos inválidos por cliente | 10 | 100 | Frenar fuerza bruta local |
| Intentos inválidos globales | 120 | 5.000 | Frenar abuso distribuido |

Un exceso devuelve HTTP 429 con `Retry-After`; una contraseña incorrecta
devuelve un 401 genérico. Logins correctos, consultas autenticadas, descargas y
nuevos trabajos no consumen una cuota RPM/RPD. Ajusta los cuatro valores
`OPENSTORYLINE_LOGIN_*` en `.env.kamal` si hace falta.

`OPENSTORYLINE_MAX_ACTIVE_JOBS` limita la cantidad total de uploads y trabajos
pendientes o activos para proteger la capacidad del VPS. Es backpressure
operacional, no una cuota por usuario ni una ventana RPM/RPD. Un exceso devuelve
`JOB_QUEUE_FULL`; espera a que termine un trabajo antes de reintentar.

Los trabajos nuevos se crean en
`POST /api/mvp/sessions/{session_id}/jobs`. El antiguo
`POST /api/mvp/jobs` devuelve `SESSION_REQUIRED` de forma intencional. Las rutas
de consulta, artefactos y ZIP continúan usando el identificador del trabajo.

Para rotar la contraseña, genera un hash nuevo, reemplaza
`OPENSTORYLINE_WEB_PASSWORD_HASH` en el archivo ignorado y despliega/reinicia.
La rotación se trata como un cierre global de sesiones: verifica que una sesión
anterior quede rechazada y que el login nuevo funcione. Conserva el antiguo
secreto web sólo fuera de la configuración activa y únicamente durante la
ventana de rollback al release anterior.

## 6. Audita trabajos y calidad estructural

PostgreSQL conserva el historial autoritativo: eventos ordenados, snapshots
versionados de `job.json`, todos los JSON/SRT registrados dentro del límite,
hashes, disponibilidad del medio y revisiones. No guarda bytes de video, audio,
frames ni ZIP. Para revisar un trabajo desde otra sesión de agente:

```bash
./bin/kamal-mvp audit list --since 24h --limit 50 --format json
./bin/kamal-mvp audit outcomes --since 24h --limit 5000 --format json
./bin/kamal-mvp audit show JOB_ID --limit 200 --format json
./bin/kamal-mvp audit events JOB_ID --limit 200 --format json
./bin/kamal-mvp audit documents JOB_ID --limit 200 --format ndjson
./bin/kamal-mvp audit verify JOB_ID --format json
```

`audit outcomes` resume la tasa de salida reproducible, el tamaño de muestra,
el intervalo de confianza del 95%, estados, limitaciones, éxito de reintentos,
reutilización de checkpoints y tiempo hasta una salida reproducible. No afirma
el SLO de 99% hasta que `claim_ready` sea verdadero. `audit verify` usa FFprobe
y reglas deterministas sobre duración, streams,
cantidad de salidas y orden de subtítulos. El veredicto sólo confirma estructura;
no evalúa creatividad, narrativa ni calidad visual. Para trabajos importados,
ejecuta primero `audit backfill --dry-run` y luego `audit backfill --apply` en
lotes acotados.

Las notas privadas de una revisión se entregan con `--input archivo.json` o
`--input -` por stdin, nunca como argumento directo. `./bin/kamal-mvp app logs`
sirve para diagnóstico reciente y correlación; sus logs rotan y no sustituyen
el historial PostgreSQL. Los logs no incluyen prompts, transcripciones, SRT,
bodies de proveedores, cookies ni secretos.

Los videos de entrada, clips y ZIP se conservan siete días después de terminar
el trabajo. Al eliminar una sesión de edición desde la web, sus videos se
eliminan de forma irreversible y la sesión desaparece de la vista normal; los
prompts, planes, JSON/SRT, eventos, controles de calidad y revisiones siguen
consultables durante 30 días por la CLI de auditoría.

La retención automática comienza desactivada. Primero revisa los comandos de
sólo lectura:

```bash
./bin/kamal-mvp retention status --format json
./bin/kamal-mvp retention preview --limit 100 --format json
```

`retention run` también hace preview salvo que pases `--apply`. Los holds de
auditoría se crean o eliminan sólo por CLI, con el motivo por stdin/archivo; un
hold conserva la evidencia de PostgreSQL después del día 30, pero nunca conserva
los videos:

```bash
printf '%s' '{"reason":"revisión manual de calidad"}' | \
  ./bin/kamal-mvp audit hold SESSION_ID --set --input - --format json
./bin/kamal-mvp audit hold SESSION_ID --clear --format json
./bin/kamal-mvp retention run --apply --limit 100 --format json
```

Para el primer corte: verifica el accesorio PostgreSQL, aplica migraciones,
crea y prueba el dump, despliega con retención desactivada, importa los trabajos
legados con dry-run/aplicación/idempotencia, completa el backfill de auditoría y
revisa el preview de retención dos veces. Activa
`OPENSTORYLINE_RETENTION_ENABLED=true` sólo con aprobación explícita. Si debes
detener la limpieza, vuelve a `false` antes de considerar un rollback. El dump
restaura metadatos y texto, no medios ya eliminados.

## 7. Rollout agentivo y Pexels

El release seguro mantiene estos valores iniciales:

```dotenv
OPENSTORYLINE_AGENTIC_EDITING_MODE=shadow
OPENSTORYLINE_GENERATED_ASSETS_ENABLED=false
OPENSTORYLINE_PEXELS_ENABLED=false
OPENSTORYLINE_RENDER_QUALITY_PROFILE=high
OPENSTORYLINE_RENDER_FPS_CAP=60
OPENSTORYLINE_RENDER_PROMOTION_MODE=report
OPENSTORYLINE_COMPLETION_POLICY=strict
OPENSTORYLINE_LIMITED_OUTPUT_PROMOTION_ENABLED=false
OPENSTORYLINE_DELIVERY_POLICY=qa_enforced
OPENSTORYLINE_RETRY_UX_ENABLED=false
OPENSTORYLINE_CHECKPOINTS_ENABLED=false
OPENSTORYLINE_BASELINE_FALLBACKS_ENABLED=false
OPENSTORYLINE_CREATIVE_CATALOG_PLANNING_ENABLED=false
OPENSTORYLINE_SEMANTIC_QA_ENABLED=false
```

Primero compara planes y evidencia sin cambiar el renderer. La reparación
agentiva sigue una secuencia acotada: una tanda primaria con todos los defectos
autoritativos, revalidación determinista y, como máximo, una tanda de
contingencia sólo si aparece un defecto autoritativo nuevo. Un candidato que
introduce defectos se descarta y no consume esa contingencia. Un proveedor que
falla después de iniciar la llamada cuenta como intento; sólo entonces el motor
puede aplicar un fallback local por segmento. El compositor debe pasar su
dry-run final antes de invocar FFmpeg. Luego autoriza un
canary privado con fuente sintética, seguido por una sesión de producción
autorizada y al menos dos nichos no relacionados; sus identificadores, medios y
reportes nunca entran a Git. Activa en
orden: render agentivo source-only, imágenes generadas y, finalmente, Pexels.
Verifica `/up`, `/health`, recuperación de cola, descargas, auditoría, retención,
visibilidad del objetivo, sincronía, latencia y errores de proveedor después de
cada cambio. `report` conserva la finalización mientras calibra los bloqueadores;
`enforce` se activa sólo para el canary aprobado. Conserva `qa_enforced` durante
la comparación inicial. Después,
`OPENSTORYLINE_DELIVERY_POLICY=technical_pass_guaranteed` permite descargar
salidas técnicamente válidas con limitaciones creativas declaradas, sin cambiar
el veredicto bloqueado de la QA estricta; estructura, codec, audio, duración o
evidencia técnica inválida siguen bloqueando. La combinación histórica
`baseline_guaranteed` más promoción limitada se conserva sólo como
compatibilidad. Activa
`OPENSTORYLINE_RETRY_UX_ENABLED=true` por separado para mostrar reintento de
defectos y prellenado de una versión mejorada. En producción, `render` exige
`OPENSTORYLINE_LLM_DEFECT_REPAIR_MODE=enforce`, schemas estrictos verificados,
`OPENSTORYLINE_BASELINE_FALLBACKS_ENABLED=true` y esta UX activa; el validador
rechaza combinaciones parciales. “Volver a ejecutar” reutiliza la versión y
fuente retenidas sin exigir evidencia de calidad. “Reparar con evidencia” sólo
aparece cuando existe evidencia objetiva y una fuente todavía disponible.

La capa de fiabilidad también se activa por etapas: primero checkpoints,
después fallbacks deterministas y por último planificación con catálogo. Tras
cada cambio, reinicia con la misma imagen, ejecuta un intento de la misma
versión inmutable y revisa `audit outcomes`, `audit defects`, `audit show` y
`audit verify`. Activa `technical_pass_guaranteed` sólo cuando el artefacto,
los subtítulos, la evidencia de frames y la decisión de promoción pasen; activa
la UX de reintento en un reinicio separado. Este orden mantiene un kill switch
independiente para catálogo, promoción, checkpoints y UI.

Sin autorización para desplegar o llamar proveedores, todos los flags permanecen
apagados. El rollback normal no requiere restaurar PostgreSQL: vuelve la UI a
legacy, fija `OPENSTORYLINE_AGENTIC_EDITING_MODE=off`, desactiva assets/QA
semántica, catálogo, promoción limitada, UX de reintento y lectura de
checkpoints; luego usa `OPENSTORYLINE_DELIVERY_POLICY=qa_enforced`,
`OPENSTORYLINE_COMPLETION_POLICY=strict`,
`OPENSTORYLINE_RENDER_PROMOTION_MODE=off` y el perfil `legacy`, y
ejecuta `./bin/kamal-mvp rollback VERSION_EXPLICITA` al release previo. Restaura la base
sólo ante una migración incompatible revisada por separado; esta entrega no añade
migraciones.

## 8. Activa el servicio FFMPEGA determinista, si lo deseas

El despliegue base ya incluye todos los componentes obligatorios. FFMPEGA corre
como un sidecar opcional, privado y separado de la imagen web. El builder fija
el código de <https://github.com/AEmotionStudio/ComfyUI-FFMPEGA> al commit
`0cfe2db05df104f95c98cc45e11f129fa5ef5193`, instala sólo FFmpeg y el contrato
Python necesario, y no instala Torch, Whisper, modelos ni la interfaz completa
de ComfyUI. Primero construye, entrega y verifica el servicio:

```bash
./bin/kamal-mvp ffmpega deploy
./bin/kamal-mvp ffmpega readiness
```

El contenedor corre sin root, sin puertos públicos, con filesystem raíz de sólo
lectura, límites de CPU/memoria/procesos y acceso de lectura/escritura únicamente
a `KAMAL_OUTPUTS_DIR`. Después cambia:

```dotenv
OPENSTORYLINE_FFMPEGA_ENABLED=true
FFMPEGA_URL=http://openstoryline-mvp-ffmpega:8188
FFMPEGA_REMOTE_OUTPUT_ROOT=/var/lib/openstoryline/outputs
```

Y ejecuta `./bin/kamal-mvp deploy`. El release falla antes de Kamal cuando el
sidecar no está saludable, el commit no coincide o las rutas compartidas no son
exactas. El adaptador y el servicio validan la misma lista blanca tipada, usan
modo manual sin LLM y prohíben descargas de modelos. Si planificación,
ejecución, descubrimiento o validación de FFMPEGA falla, el trabajo conserva el
primer render nativo reproducible y registra la limitación exacta. El preflight
determinista permite hasta 180 segundos para que un render vertical de alta
resolución no sea rechazado por el límite anterior de 30 segundos antes de la
ejecución, que conserva su propio límite separado.

En un VPS sin GPU esta ruta corre en CPU. Los efectos que requieran modelos de
ComfyUI quedan fuera del MVP remoto-only. Para rollback usa, en orden:

```bash
# Primero desactiva OPENSTORYLINE_FFMPEGA_ENABLED y redeploya la aplicación.
./bin/kamal-mvp ffmpega rollback
# Si sólo necesitas detener el sidecar ya desactivado:
./bin/kamal-mvp ffmpega stop
```

## 9. Diagnóstico rápido

```bash
curl -fsS https://video.example.com/up
./bin/kamal-mvp app logs --lines 200
./bin/kamal-mvp proxy logs --lines 200
```

- `STT_ALL_PROVIDERS_FAILED`: revisa `failure.json`, la key ring directa de
  Mistral y la cuota de la organización.
- `LOGIN_RATE_LIMITED`: espera el valor de `Retry-After` o ajusta los límites
  de intentos fallidos.
- `AUTH_UNAVAILABLE`: revisa la conexión, la migración y el estado del accesorio
  PostgreSQL sin imprimir la URL ni los secretos.
- Timeout al subir: confirma el puerto/firewall y espacio en disco; el proxy y
  la aplicación aceptan hasta `OPENSTORYLINE_MAX_UPLOAD_BYTES`.
- `IMAGE_DISCOVERY_FAILED`: actualiza 9Router (la generación nativa requiere un
  catálogo de imágenes) y revisa la endpoint key.
- `IMAGE_MODELS_UNAVAILABLE`: `cx/gpt-5.5-image` no aparece en el catálogo;
  pausa el despliegue y corrige la conexión Codex OAuth.
- `IMAGE_ALL_PROVIDERS_FAILED`: el modelo Codex seleccionado falló; el lote
  parcial se elimina y no se sustituye silenciosamente con Pexels o un modelo
  local.
- `PEXELS_LICENSE_REVIEW_REQUIRED`: Pexels está activado sin una revisión manual
  vigente; revisa las páginas oficiales y actualiza sólo la fecha, no la key en
  logs o artefactos.
- `PEXELS_SEARCH_FAILED` o `PEXELS_DOWNLOAD_FAILED`: revisa cuota, red y contrato;
  el lote parcial se elimina y no se sustituye con 9Router ni el video fuente.
