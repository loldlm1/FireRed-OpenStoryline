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
el transporte; úsalo sólo en una red privada, VPN o prueba controlada. El
wrapper exige el opt-in explícito y no permite activar este modo por accidente
cuando existe un dominio.
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

El primer rollout con PostgreSQL se prepara por etapas para que la base de
datos y la copia verificable existan antes de arrancar la nueva aplicación:

```bash
./bin/kamal-mvp server bootstrap
./bin/kamal-mvp registry setup
./bin/kamal-mvp accessory boot db
./bin/kamal-mvp build deliver
./bin/kamal-mvp db migrate
./bin/kamal-mvp db current
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
./bin/kamal-mvp rollback
```

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
únicamente `cx/gpt-5.5-image` y sólo cuando el plan detecta un vacío visual.
“Stock externo · Pexels” comienza en `No usar Pexels`; al activarlo permite
fotos o videos de stock dentro del límite indicado. Desactivar ambos conserva
encuadres, cortes, capas de fuente, texto, transiciones y subtítulos inteligentes
sin llamadas a proveedores de assets. No existe fallback entre Pexels, 9Router
y el video fuente.

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
./bin/kamal-mvp audit show JOB_ID --limit 200 --format json
./bin/kamal-mvp audit events JOB_ID --limit 200 --format json
./bin/kamal-mvp audit documents JOB_ID --limit 200 --format ndjson
./bin/kamal-mvp audit verify JOB_ID --format json
```

`audit verify` usa FFprobe y reglas deterministas sobre duración, streams,
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
OPENSTORYLINE_SEMANTIC_QA_ENABLED=false
```

Primero compara planes y evidencia sin cambiar el renderer. Luego autoriza un
canary privado con fuente sintética, seguido por `Sesion prueba 1` y al menos dos
nichos no relacionados; esos medios y reportes nunca entran a Git. Activa en
orden: render agentivo source-only, imágenes generadas y, finalmente, Pexels.
Verifica `/up`, `/health`, recuperación de cola, descargas, auditoría, retención,
visibilidad del objetivo, sincronía, latencia y errores de proveedor después de
cada cambio.

Sin autorización para desplegar o llamar proveedores, todos los flags permanecen
apagados. El rollback normal no requiere restaurar PostgreSQL: vuelve la UI a
legacy, fija `OPENSTORYLINE_AGENTIC_EDITING_MODE=off`, desactiva assets/QA
semántica y ejecuta `./bin/kamal-mvp rollback` al release previo. Restaura la base
sólo ante una migración incompatible revisada por separado; esta entrega no añade
migraciones.

## 8. Activa ComfyUI-FFMPEGA, si lo deseas

El despliegue base ya incluye todos los componentes obligatorios. FFMPEGA es un
servicio opcional separado: instala ComfyUI y
<https://github.com/AEmotionStudio/ComfyUI-FFMPEGA> en el mismo VPS, hazlo
escuchar en el puerto 8188 y dale acceso de lectura/escritura a
`/var/lib/openstoryline/outputs`.

Después cambia:

```dotenv
OPENSTORYLINE_FFMPEGA_ENABLED=true
FFMPEGA_URL=http://host.docker.internal:8188
FFMPEGA_REMOTE_OUTPUT_ROOT=/var/lib/openstoryline/outputs
```

Y ejecuta `./bin/kamal-mvp deploy`. La configuración Kamal crea el alias
`host.docker.internal` dentro del contenedor. El adaptador sólo permite una
lista blanca de efectos FFmpeg deterministas, usa el modo manual sin LLM,
prohíbe descargas de modelos y falla todo el trabajo si FFMPEGA falla.

En un VPS sin GPU esta ruta determinista puede correr en CPU. Los efectos que
requieran modelos de ComfyUI quedan fuera de este MVP remoto-only.

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
