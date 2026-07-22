const ACTIVITY_MESSAGES = Object.freeze({
  'activity.queue.waiting': 'La versión está esperando un turno de procesamiento.',
  'activity.queue.recovered': 'El trabajo se recuperó y volvió a la cola.',
  'activity.system.starting': 'Preparando el espacio seguro de edición.',
  'activity.analysis.extracting_audio': 'Separando el audio para entender la conversación.',
  'activity.analysis.audio_ready': 'El audio quedó listo para transcribir.',
  'activity.provider.transcribing': 'Transcribiendo el contenido hablado.',
  'activity.provider.transcription_ready': 'La transcripción está lista para el análisis.',
  'activity.analysis.detecting_scenes': 'Detectando cambios de escena y ritmo visual.',
  'activity.analysis.scenes_ready': 'El mapa de escenas quedó preparado.',
  'activity.analysis.sampling_frames': 'Seleccionando fotogramas representativos.',
  'activity.analysis.frames_ready': 'Los fotogramas clave están listos.',
  'activity.provider.understanding_video': 'Interpretando lo que ocurre en pantalla.',
  'activity.provider.video_understood': 'El contenido visual ya está comprendido.',
  'activity.provider.visual_understanding_skipped': 'El análisis visual no era necesario para este modo.',
  'activity.planning.selecting_clips': 'Buscando los momentos con más fuerza narrativa.',
  'activity.planning.clips_selected': 'Los mejores momentos quedaron seleccionados.',
  'activity.planning.designing_edit': 'Diseñando el ritmo, los cortes y la estructura.',
  'activity.planning.edit_ready': 'La dirección de edición está lista.',
  'activity.asset.resolving': 'Preparando recursos visuales que apoyan la historia.',
  'activity.asset.resolved': 'Los recursos visuales están preparados.',
  'activity.asset.not_requested': 'Esta versión no necesita recursos visuales adicionales.',
  'activity.asset.shadow_mode': 'Los recursos se evaluaron sin incorporarlos al resultado.',
  'activity.render.starting': 'Preparando el render de los clips.',
  'activity.render.rendering_clip': 'Renderizando un clip.',
  'activity.render.clip_completed': 'Un clip terminó de renderizarse.',
  'activity.planning.effects': 'Afinando efectos y continuidad entre cortes.',
  'activity.planning.effects_ready': 'Los efectos quedaron definidos.',
  'activity.planning.effects_skipped': 'Esta edición no necesita una capa adicional de efectos.',
  'activity.qa.checking_outputs': 'Revisando que cada salida sea reproducible y completa.',
  'activity.qa.completed': 'La revisión técnica terminó correctamente.',
  'activity.qa.skipped': 'La revisión adicional no era necesaria.',
  'activity.qa.unavailable': 'La revisión adicional no estuvo disponible; el resultado conserva sus comprobaciones básicas.',
  'activity.system.packaging': 'Registrando los archivos finales para descarga.',
  'activity.system.completed': 'La nueva versión está lista.',
  'activity.system.failed': 'La edición no pudo completarse.',
});

const ERROR_MESSAGES = Object.freeze({
  AUTH_UNAVAILABLE: 'El servicio de acceso no está disponible. Intenta de nuevo en un momento.',
  UNAUTHENTICATED: 'Tu sesión de acceso terminó. Vuelve a entrar para continuar.',
  INVALID_CREDENTIALS: 'La contraseña no es válida.',
  LOGIN_RATE_LIMITED: 'Hubo demasiados intentos fallidos. Espera antes de volver a intentarlo.',
  REQUEST_ORIGIN_INVALID: 'La solicitud no proviene de una dirección autorizada.',
  CSRF_VALIDATION_FAILED: 'La sesión cambió mientras trabajabas. Recarga la página e inténtalo de nuevo.',
  DATABASE_UNAVAILABLE: 'El almacenamiento está temporalmente fuera de servicio. Tu fuente e instrucciones siguen disponibles para reintentar.',
  SESSION_NOT_FOUND: 'Esta sesión ya no está disponible.',
  SESSION_ACTIVE_JOBS: 'Espera a que terminen los procesos activos antes de eliminar la sesión.',
  SESSION_SOURCE_NOT_FOUND: 'Esta sesión todavía no tiene un video fuente.',
  SESSION_SOURCE_UNAVAILABLE: 'El video fuente ya no está disponible.',
  SESSION_SOURCE_EXPIRED: 'El plazo de conservación del video fuente terminó.',
  SESSION_SOURCE_IMMUTABLE: 'El video de esta sesión ya quedó fijado y no puede reemplazarse.',
  SESSION_SOURCE_CHANGED: 'La identidad del video fuente no coincide. La edición se detuvo para proteger la sesión.',
  VIDEO_TYPE_UNSUPPORTED: 'Elige un video MP4, MOV, MKV, WebM, AVI o M4V.',
  UPLOAD_SIZE_INVALID: 'El archivo seleccionado está vacío o no informa un tamaño válido.',
  UPLOAD_TOO_LARGE: 'El video supera el límite de carga configurado.',
  UPLOAD_METADATA_CONFLICT: 'Selecciona exactamente el mismo archivo para continuar esta carga.',
  UPLOAD_OFFSET_MISMATCH: 'El servidor tiene un avance diferente. Ajustaremos la carga al punto confirmado.',
  UPLOAD_CHUNK_INVALID: 'Una parte del video no pudo cargarse. Puedes reintentar sin empezar desde cero.',
  UPLOAD_STATE_INVALID: 'Esta carga cambió de estado. Actualiza el avance antes de continuar.',
  SOURCE_UPLOAD_BUSY: 'El servidor está registrando otra parte del video. Reintentaremos en breve.',
  SOURCE_UPLOAD_NOT_FOUND: 'La carga incompleta ya no está disponible. Inicia una nueva carga.',
  SOURCE_UPLOAD_FAILED: 'La carga debe iniciarse de nuevo.',
  SOURCE_VALIDATION_UNAVAILABLE: 'No pudimos validar el video en este momento. Puedes reintentar.',
  SOURCE_VALIDATION_TIMEOUT: 'La validación tardó demasiado. Intenta completar la carga otra vez.',
  SOURCE_VIDEO_INVALID: 'El archivo terminó de subir, pero no contiene un video válido.',
  SOURCE_VALIDATION_STORAGE_FAILED: 'El servidor no pudo finalizar el video después de validarlo.',
  UPLOAD_CANCELLED: 'La carga se canceló. Puedes elegir un video de nuevo.',
  PROMPT_REQUIRED: 'Escribe las instrucciones para esta versión.',
  PROMPT_INVALID: 'Las instrucciones son demasiado largas o no tienen un formato válido.',
  JOB_QUEUE_FULL: 'La cola está ocupada. Conservamos tu video y tus instrucciones para que puedas intentarlo después.',
  JOB_NOT_FOUND: 'Esta ejecución ya no está disponible.',
  PROMPT_VERSION_NOT_FOUND: 'Esta versión de instrucciones ya no está disponible.',
  NINEROUTER_REQUEST_FAILED: 'El servicio de comprensión no respondió. Puedes volver a ejecutar esta versión.',
  NINEROUTER_RATE_LIMITED: 'El servicio de comprensión está ocupado. Intenta de nuevo más tarde.',
  MISTRAL_STT_REQUEST_FAILED: 'La transcripción remota no pudo completarse. Puedes reintentar esta versión.',
  MISTRAL_STT_RATE_LIMITED: 'El servicio de transcripción está ocupado. Intenta de nuevo más tarde.',
  PEXELS_SEARCH_FAILED: 'La búsqueda opcional de video de archivo falló. Puedes reintentar o desactivarla.',
  REMOTE_IMAGE_REQUEST_FAILED: 'No se pudo crear un recurso visual solicitado. Puedes reintentar esta versión.',
  CREATIVE_INTENT_CAPABILITY_UNAVAILABLE: 'La versión exige un recurso que no está habilitado en este despliegue.',
  EDIT_PLAN_INTENT_MISMATCH: 'La planificación no convirtió todos los requisitos obligatorios en operaciones editables.',
  EDIT_PLAN_REPAIR_EXHAUSTED: 'La planificación siguió siendo inválida después del único intento de reparación seguro.',
  EDIT_PLAN_VISUAL_COVERAGE_INSUFFICIENT: 'No hubo evidencia visual suficiente dentro del fragmento para ejecutar un recorte seguro.',
  REQUIRED_GENERATED_ASSET_COUNT_INVALID: 'Indica una cantidad positiva para las imágenes generadas obligatorias.',
  REQUIRED_STOCK_ASSET_COUNT_INVALID: 'Indica una cantidad positiva para los videos Pexels obligatorios.',
  ACTIVITY_STREAM_UNAVAILABLE: 'La conexión en vivo no está disponible. Seguiremos consultando el avance.',
});

const STATE_LABELS = Object.freeze({
  queued: 'En cola',
  running: 'Procesando',
  completed: 'Completado',
  failed: 'Falló',
  cancelled: 'Cancelado',
  missing: 'Pendiente',
  pending: 'Pendiente',
  uploading: 'Subiendo',
  validating: 'Validando',
  ready: 'Fuente lista',
  expired: 'Expirado',
  deleted: 'Eliminado',
});

const REPAIR_DISPOSITION_LABELS = Object.freeze({
  resolved: 'resuelto por validación determinista',
  remaining: 'permanece',
  new: 'nuevo después de la reparación',
  fallback_applied: 'fallback ejecutado',
  not_repairable: 'no reparable en esta etapa',
});

const QA_DECISION_LABELS = Object.freeze({
  promote: 'aprobada',
  block: 'bloqueada',
  observe: 'observada con hallazgos',
  unknown: 'no disponible',
});

const DELIVERY_DECISION_LABELS = Object.freeze({
  publish_enhanced: 'publicada sin limitaciones',
  publish_with_limitations: 'publicada con limitaciones',
  withhold_strict: 'retenida por la política estricta',
  withhold_technical: 'retenida por un bloqueo técnico',
  unknown: 'no disponible',
});

export function activityMessage(event) {
  let message = ACTIVITY_MESSAGES[event?.message_key] || 'El proceso de edición avanzó a una nueva etapa.';
  if (Number.isInteger(event?.current) && Number.isInteger(event?.total)) {
    message += ` ${event.current} de ${event.total}.`;
  } else if (Number.isInteger(event?.selected_clips)) {
    message += ` ${event.selected_clips} momentos seleccionados.`;
  } else if (Number.isInteger(event?.clip_count)) {
    message += ` ${event.clip_count} clips preparados.`;
  }
  return message;
}

export function errorMessage(error, fallback = 'No pudimos completar la solicitud. Intenta de nuevo.') {
  const code = typeof error === 'string' ? error : error?.code;
  const registryDescription = typeof error === 'object'
    ? error?.presentation?.es?.description
    : null;
  if (registryDescription) return registryDescription;
  return ERROR_MESSAGES[code] || fallback;
}

export function defectTitle(defect) {
  return defect?.presentation?.es?.title || 'Hallazgo verificable';
}

export function defectDescription(defect, fallback = '') {
  return defect?.presentation?.es?.description || defect?.description || fallback;
}

export function repairDispositionLabel(value) {
  return REPAIR_DISPOSITION_LABELS[value] || String(value || '').replaceAll('_', ' ');
}

export function qaDecisionLabel(value) {
  return QA_DECISION_LABELS[value] || QA_DECISION_LABELS.unknown;
}

export function deliveryDecisionLabel(value) {
  return DELIVERY_DECISION_LABELS[value] || DELIVERY_DECISION_LABELS.unknown;
}

export function stateLabel(state) {
  return STATE_LABELS[state] || 'En proceso';
}

export function activityMeta(event) {
  const parts = [];
  if (event?.provider) parts.push(event.provider);
  if (event?.tool) parts.push(event.tool);
  if (Number.isInteger(event?.attempt_number)) parts.push(`intento ${event.attempt_number}`);
  if (event?.status === 'skipped') parts.push('omitido');
  if (event?.retryable) parts.push('se puede reintentar');
  return parts.join(' · ');
}
