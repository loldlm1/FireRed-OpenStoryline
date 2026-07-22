import {
  activityMessage,
  activityMeta,
  defectDescription,
  defectTitle,
  deliveryDecisionLabel,
  errorMessage,
  qaDecisionLabel,
  repairDispositionLabel,
  stateLabel,
} from './messages.js';

const byId = (id) => document.getElementById(id);

export const elements = Object.freeze({
  loading: byId('loading-state'),
  loginView: byId('login-view'),
  appView: byId('app-view'),
  loginForm: byId('login-form'),
  password: byId('password'),
  loginError: byId('login-error'),
  loginSubmit: byId('login-submit'),
  logout: byId('logout'),
  sessionList: byId('session-list'),
  sessionSelect: byId('session-select'),
  sessionNew: byId('session-new'),
  emptyCreateSession: byId('empty-create-session'),
  sessionDialog: byId('session-dialog'),
  sessionForm: byId('session-form'),
  sessionTitle: byId('session-title'),
  sessionSubmit: byId('session-submit'),
  sessionFormError: byId('session-form-error'),
  sessionError: byId('session-error'),
  sessionDelete: byId('session-delete'),
  sessionDeleteDialog: byId('session-delete-dialog'),
  sessionDeleteCancel: byId('session-delete-cancel'),
  sessionDeleteConfirm: byId('session-delete-confirm'),
  sessionNotice: byId('session-notice'),
  headerSessionTitle: byId('header-session-title'),
  workspaceEmpty: byId('workspace-empty'),
  workspaceContent: byId('workspace-content'),
  workspaceMain: byId('workspace-main'),
  workspaceTitle: byId('workspace-title'),
  sessionSummary: byId('session-summary'),
  sessionSeal: byId('session-seal'),
  legacyWorkspace: byId('legacy-workspace'),
  legacyCreateSession: byId('legacy-create-session'),
  legacyHistory: byId('legacy-history'),
  modernWorkspace: byId('modern-workspace'),
  video: byId('video'),
  videoResume: byId('video-resume'),
  sourceState: byId('source-state'),
  sourceMissing: byId('source-missing'),
  sourceUploading: byId('source-uploading'),
  sourceResume: byId('source-resume'),
  sourceReady: byId('source-ready'),
  sourceVideo: byId('source-video'),
  sourceFilename: byId('source-filename'),
  sourceSize: byId('source-size'),
  resumeCopy: byId('resume-copy'),
  uploadFilename: byId('upload-filename'),
  uploadSize: byId('upload-size'),
  uploadPercent: byId('upload-percent'),
  uploadProgress: byId('upload-progress'),
  uploadStage: byId('upload-stage'),
  uploadDetail: byId('upload-detail'),
  uploadPause: byId('upload-pause'),
  uploadRetry: byId('upload-retry'),
  uploadCancel: byId('upload-cancel'),
  resumeCancel: byId('resume-cancel'),
  uploadError: byId('upload-error'),
  jobForm: byId('job-form'),
  prompt: byId('prompt'),
  promptCount: byId('prompt-count'),
  promptVersionLabel: byId('prompt-version-label'),
  maxClips: byId('max-clips'),
  editMode: byId('edit-mode'),
  assetPolicy: byId('asset-policy'),
  maxGeneratedAssets: byId('max-generated-assets'),
  stockPolicy: byId('stock-policy'),
  maxStockAssets: byId('max-stock-assets'),
  jobError: byId('job-error'),
  submit: byId('submit'),
  activityCard: byId('activity-card'),
  connectionState: byId('connection-state'),
  activityPercent: byId('activity-percent'),
  activityRing: document.querySelector('.activity-progress-ring'),
  status: byId('status'),
  elapsedTime: byId('elapsed-time'),
  progress: byId('progress'),
  activityList: byId('activity-list'),
  activityEmpty: byId('activity-empty'),
  activityRecovery: byId('activity-recovery'),
  activityRecoveryCopy: byId('activity-recovery-copy'),
  activityRetry: byId('activity-retry'),
  resultEmpty: byId('result-empty'),
  artifacts: byId('artifacts'),
  bundle: byId('bundle'),
  recentJobs: byId('recent-jobs'),
  recentEmpty: byId('recent-empty'),
  historyCount: byId('history-count'),
  historyLoadMore: byId('history-load-more'),
  comparisonToolbar: byId('comparison-toolbar'),
  comparisonCount: byId('comparison-count'),
  comparisonOpen: byId('comparison-open'),
  comparisonClear: byId('comparison-clear'),
  comparisonDialog: byId('comparison-dialog'),
  comparisonClose: byId('comparison-close'),
  comparisonContent: byId('comparison-content'),
  toastRegion: byId('toast-region'),
});

export function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const power = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / (1024 ** power)).toFixed(power === 0 ? 0 : 1)} ${units[power]}`;
}

export function formatElapsed(milliseconds) {
  const seconds = Math.max(0, Math.floor(Number(milliseconds || 0) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  const clock = hours > 0
    ? [hours, minutes, remainder]
    : [minutes, remainder];
  return `${clock.map((value) => String(value).padStart(2, '0')).join(':')} transcurridos`;
}

export function showLoading() {
  elements.loading.hidden = false;
  elements.loginView.hidden = true;
  elements.appView.hidden = true;
}

export function showLogin(message = '') {
  elements.loading.hidden = true;
  elements.appView.hidden = true;
  elements.loginView.hidden = false;
  elements.loginError.textContent = message;
  window.requestAnimationFrame(() => elements.password.focus());
}

export function showApp() {
  elements.loading.hidden = true;
  elements.loginView.hidden = true;
  elements.appView.hidden = false;
  elements.password.value = '';
  elements.loginError.textContent = '';
}

export function renderSessions(sessions, selectedId, onSelect) {
  elements.sessionList.replaceChildren();
  elements.sessionSelect.replaceChildren();
  for (const session of sessions) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'session-item';
    button.dataset.sessionId = session.id;
    if (session.id === selectedId) button.setAttribute('aria-current', 'page');
    const marker = document.createElement('span');
    marker.className = 'session-item-marker';
    marker.setAttribute('aria-hidden', 'true');
    const copy = document.createElement('span');
    copy.className = 'session-item-copy';
    const title = document.createElement('strong');
    title.textContent = session.title;
    const state = document.createElement('span');
    state.textContent = session.workflow_version === 1
      ? 'Sesión anterior'
      : (session.input_video?.state === 'ready' ? 'Fuente lista' : 'Fuente pendiente');
    copy.append(title, state);
    button.append(marker, copy);
    button.addEventListener('click', () => onSelect(session.id));
    elements.sessionList.append(button);
    elements.sessionSelect.append(new Option(session.title, session.id));
  }
  elements.sessionSelect.value = selectedId || '';
  elements.sessionSelect.disabled = sessions.length === 0;
}

export function renderWorkspace(session, { favoriteLabel = '' } = {}) {
  const hasSession = Boolean(session);
  const legacy = hasSession && session.workflow_version === 1;
  elements.workspaceEmpty.hidden = hasSession;
  elements.workspaceContent.hidden = !hasSession;
  elements.legacyWorkspace.hidden = !legacy;
  elements.modernWorkspace.hidden = legacy;
  elements.sessionSeal.hidden = legacy;
  elements.sessionDelete.disabled = !hasSession;
  elements.headerSessionTitle.textContent = session?.title || 'Sin seleccionar';
  if (!hasSession) return;
  elements.workspaceTitle.textContent = session.title;
  if (legacy) {
    elements.sessionSummary.textContent = 'Historial de solo lectura del flujo anterior, donde cada ejecución tenía su propia carga.';
  } else if (favoriteLabel) {
    elements.sessionSummary.textContent = `${favoriteLabel} es tu elección favorita; la evidencia técnica permanece separada de esa decisión.`;
  } else {
    elements.sessionSummary.textContent = 'La fuente permanece vinculada a esta sesión; cada nueva instrucción crea una versión auditable.';
  }
}

function sourceStatusClass(state) {
  if (state === 'ready') return 'status-ready';
  if (['uploading', 'validating', 'pending'].includes(state)) return 'status-progress';
  if (['failed', 'expired', 'deleted'].includes(state)) return 'status-error';
  return 'status-neutral';
}

export function renderSource(source, { activeUpload = false } = {}) {
  const state = source?.state || 'missing';
  elements.sourceState.className = `status-pill ${sourceStatusClass(state)}`;
  elements.sourceState.textContent = stateLabel(state);
  elements.sourceMissing.hidden = state !== 'missing' && state !== 'failed';
  elements.sourceUploading.hidden = !activeUpload;
  elements.sourceResume.hidden = activeUpload || !['pending', 'uploading', 'validating'].includes(state);
  elements.sourceReady.hidden = state !== 'ready';
  elements.submit.disabled = state !== 'ready';
  if (['pending', 'uploading', 'validating'].includes(state) && !activeUpload) {
    const percent = source.expected_size
      ? Math.floor((Number(source.received_bytes || 0) / Number(source.expected_size)) * 100)
      : 0;
    elements.resumeCopy.textContent = `Vuelve a seleccionar ${source.original_filename || 'el mismo archivo'} para continuar desde ${percent}% confirmado por el servidor.`;
  }
  if (state === 'ready') {
    elements.sourceFilename.textContent = source.original_filename || 'Video fuente';
    elements.sourceSize.textContent = formatBytes(source.expected_size);
    const nextUrl = `/api/mvp/sessions/${source.editing_session_id}/input-video/content`;
    if (!elements.sourceVideo.src.endsWith(nextUrl)) elements.sourceVideo.src = nextUrl;
  } else {
    elements.sourceVideo.removeAttribute('src');
    elements.sourceVideo.load();
  }
}

export function showUpload(file, source = {}) {
  elements.sourceMissing.hidden = true;
  elements.sourceResume.hidden = true;
  elements.sourceReady.hidden = true;
  elements.sourceUploading.hidden = false;
  elements.uploadFilename.textContent = file.name;
  elements.uploadSize.textContent = formatBytes(file.size);
  elements.uploadError.textContent = '';
  elements.uploadRetry.hidden = true;
  elements.uploadPause.hidden = false;
  elements.uploadPause.textContent = 'Pausar';
  elements.uploadCancel.hidden = false;
  elements.sourceState.className = 'status-pill status-progress';
  elements.sourceState.textContent = stateLabel(source.state || 'uploading');
}

export function updateUploadProgress({ percent, bytes, total }) {
  const rounded = Math.min(100, Math.max(0, Math.floor(percent)));
  elements.uploadProgress.value = rounded;
  elements.uploadPercent.textContent = `${rounded}%`;
  elements.uploadDetail.textContent = `${formatBytes(bytes)} de ${formatBytes(total)}`;
}

export function updateUploadStage(stage, detail = {}) {
  const copy = {
    preparing: 'Negociando una carga segura con el servidor…',
    uploading: detail.offset > 0
      ? `Continuando desde ${formatBytes(detail.offset)} confirmados por el servidor…`
      : 'Enviando el video al servidor…',
    adjusted: `El servidor confirmó ${formatBytes(detail.offset)}. Retomamos la carga desde ese punto…`,
    validating: 'Carga completa. Validando formato, duración y reproducción…',
    paused: 'Carga pausada. El avance confirmado permanece en el servidor.',
    retrying: `La conexión se interrumpió. Reintento ${detail.attempt || 1} de 3…`,
    failed: 'La carga necesita tu atención.',
  };
  elements.uploadStage.textContent = copy[stage] || 'Enviando el video al servidor…';
  elements.uploadPause.textContent = stage === 'paused' ? 'Continuar' : 'Pausar';
  elements.uploadRetry.hidden = stage !== 'failed';
  elements.uploadPause.hidden = stage === 'failed' || stage === 'validating';
  elements.uploadCancel.hidden = stage === 'validating';
}

export function showUploadError(error) {
  elements.uploadError.textContent = errorMessage(error, 'La carga se interrumpió. Reintenta para continuar desde el avance confirmado.');
  updateUploadStage('failed');
}

export function resetActivity() {
  elements.activityList.replaceChildren();
  elements.activityEmpty.hidden = false;
  elements.activityRecovery.hidden = true;
  renderProgress(0);
  elements.status.textContent = 'Listo para recibir instrucciones';
  elements.elapsedTime.textContent = '00:00 transcurridos';
  renderConnection('idle');
}

export function renderConnection(state) {
  const labels = {
    idle: 'En espera',
    live: 'En vivo',
    reconnecting: 'Reconectando',
    polling: 'Consulta periódica',
    stale: 'Sin actualización',
    failed: 'Proceso fallido',
    complete: 'Completado',
  };
  elements.connectionState.className = `connection-state connection-${state}`;
  elements.connectionState.textContent = labels[state] || labels.idle;
  elements.activityRecovery.hidden = !['reconnecting', 'polling', 'stale'].includes(state);
  if (state === 'polling') {
    elements.activityRecoveryCopy.textContent = 'La conexión en vivo no está disponible. Seguimos consultando el avance de forma segura.';
  } else if (state === 'stale') {
    elements.activityRecoveryCopy.textContent = 'No recibimos una actualización reciente. Tu ejecución continúa en el servidor y puedes reconectar.';
  } else {
    elements.activityRecoveryCopy.textContent = 'La conexión en vivo se interrumpió. Estamos recuperando el avance.';
  }
}

export function renderProgress(value) {
  const progress = Math.min(1, Math.max(0, Number(value || 0)));
  const percent = Math.floor(progress * 100);
  elements.progress.value = progress;
  elements.activityPercent.textContent = `${percent}%`;
  elements.activityRing.style.setProperty('--progress', String(percent));
}

export function appendActivity(event) {
  elements.activityEmpty.hidden = true;
  const item = document.createElement('li');
  item.className = 'activity-item';
  item.dataset.sequence = String(event.sequence);
  item.dataset.status = event.status;
  const marker = document.createElement('span');
  marker.className = 'activity-item-marker';
  marker.setAttribute('aria-hidden', 'true');
  const copy = document.createElement('div');
  copy.className = 'activity-item-copy';
  const title = document.createElement('strong');
  title.textContent = activityMessage(event);
  const meta = document.createElement('span');
  meta.textContent = activityMeta(event) || stateLabel(event.status);
  copy.append(title, meta);
  const time = document.createElement('time');
  time.textContent = Number.isInteger(event.elapsed_ms) ? formatElapsed(event.elapsed_ms).replace(' transcurridos', '') : 'ahora';
  item.append(marker, copy, time);
  elements.activityList.append(item);
  while (elements.activityList.children.length > 120) {
    elements.activityList.firstElementChild?.remove();
  }
  item.scrollIntoView({ block: 'nearest' });
  elements.status.textContent = activityMessage(event);
  if (event.progress !== undefined) renderProgress(event.progress);
}

export function renderJob(job) {
  if (!job) return;
  renderProgress(job.progress);
  if (job.state === 'failed') {
    elements.status.textContent = errorMessage(job.error?.code, 'La edición no pudo completarse. Puedes crear otra versión con la misma fuente.');
  } else if (job.state === 'completed') {
    elements.status.textContent = 'La nueva versión está lista.';
  } else if (job.stage) {
    elements.status.textContent = `Procesando: ${stateLabel(job.state).toLowerCase()}.`;
  }
  renderArtifacts(job.id, job.artifacts || []);
}

export function renderArtifacts(jobId, artifacts) {
  const available = artifacts.filter((artifact) => artifact.availability === 'available');
  cleanupMedia(elements.artifacts);
  elements.artifacts.replaceChildren();
  elements.resultEmpty.hidden = available.length > 0;
  elements.bundle.hidden = available.length === 0;
  elements.bundle.dataset.jobId = available.length ? jobId : '';
  for (const artifact of available) {
    const row = document.createElement('div');
    row.className = 'artifact';
    const copy = document.createElement('div');
    copy.className = 'artifact-copy';
    const title = document.createElement('strong');
    title.textContent = artifact.name;
    const detail = document.createElement('span');
    detail.textContent = `${artifact.kind} · ${formatBytes(artifact.size)}`;
    copy.append(title, detail);
    const actions = document.createElement('div');
    actions.className = 'attempt-actions';
    if (isPreviewableVideo(artifact)) {
      const preview = document.createElement('button');
      preview.type = 'button';
      preview.className = 'button button-quiet';
      preview.textContent = 'Vista previa';
      preview.addEventListener('click', () => insertPreview(row, jobId, artifact, preview));
      actions.append(preview);
    }
    const link = document.createElement('a');
    link.className = 'button button-secondary';
    link.href = `/api/mvp/jobs/${jobId}/artifacts/${encodeURIComponent(artifact.name)}`;
    link.textContent = 'Descargar';
    actions.append(link);
    row.append(copy, actions);
    elements.artifacts.append(row);
  }
}

function dateLabel(value) {
  if (!value) return 'Fecha no disponible';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Fecha no disponible';
  return new Intl.DateTimeFormat('es', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

function durationLabel(run) {
  const started = Date.parse(run.started_at || run.created_at || '');
  const ended = Date.parse(run.completed_at || run.updated_at || '');
  if (!Number.isFinite(started) || !Number.isFinite(ended) || ended < started) {
    return ['queued', 'running'].includes(run.state) ? 'En proceso' : 'Duración no disponible';
  }
  return formatElapsed(ended - started).replace(' transcurridos', '');
}

function outcomePresentation(run) {
  const grade = run?.outcome?.grade;
  if (grade === 'enhanced') return { label: 'Mejorado', className: 'outcome-enhanced', symbol: '✓' };
  if (grade === 'with_limitations') {
    return { label: 'Completado con limitaciones', className: 'outcome-limited', symbol: '!' };
  }
  if (grade === 'retryable_failure') {
    return { label: 'Fallo reintentable', className: 'outcome-retryable', symbol: '↻' };
  }
  if (grade === 'terminal_failure') {
    return { label: 'Fallo no recuperable', className: 'outcome-failed', symbol: '×' };
  }
  return {
    label: stateLabel(run?.state),
    className: run?.state === 'failed' ? 'outcome-failed' : 'outcome-neutral',
    symbol: run?.state === 'failed' ? '×' : '•',
  };
}

function codeLabel(code) {
  return String(code || '').replaceAll('_', ' ').toLocaleLowerCase('es');
}

function stageNames(values = []) {
  return values.length ? values.map((value) => codeLabel(value)).join(', ') : 'ninguna';
}

function outcomeCodes(outcome, field, recordsField) {
  const explicit = outcome?.[field] || [];
  const records = (outcome?.[recordsField] || []).map((item) => item.code);
  return [...new Set([...explicit, ...records].filter(Boolean))];
}

function settingsLabels(settings = {}) {
  const labels = [];
  labels.push(settings.edit_mode === 'agentic' ? 'Edición agéntica' : 'Edición esencial');
  if (Number.isInteger(settings.max_clips)) labels.push(`Hasta ${settings.max_clips} clips`);
  if (settings.asset_policy === 'required') {
    labels.push(`${settings.max_generated_assets_per_clip || 0} imagen(es) obligatoria(s) por clip`);
  } else {
    labels.push(settings.asset_policy === 'auto' ? 'Imágenes opcionales hasta el máximo' : 'Sin imágenes generadas');
  }
  if (settings.stock_policy === 'required') {
    labels.push(`${settings.max_stock_assets_per_clip || 0} video(s) Pexels obligatorio(s) por clip`);
  } else if (settings.stock_policy === 'auto') {
    labels.push('Pexels opcional hasta el máximo');
  }
  return labels;
}

function isPreviewableVideo(artifact) {
  return artifact?.availability === 'available'
    && /\.(mp4|mov|m4v|webm)$/i.test(artifact.name || '');
}

function qaArtifacts(run) {
  const qaKinds = new Set(['render_qa', 'retention_rhythm_qa', 'creative_conformance']);
  return (run?.artifacts || []).filter((artifact) => qaKinds.has(artifact.kind));
}

function outputArtifacts(run) {
  return (run?.artifacts || []).filter((artifact) => isPreviewableVideo(artifact));
}

function createPreviewCard(jobId, artifact, { autoplay = false } = {}) {
  const card = document.createElement('div');
  card.className = 'preview-card';
  const video = document.createElement('video');
  video.controls = true;
  video.preload = 'metadata';
  video.playsInline = true;
  video.dataset.managedPreview = 'true';
  video.src = `/api/mvp/jobs/${jobId}/artifacts/${encodeURIComponent(artifact.name)}/preview`;
  if (autoplay) video.autoplay = true;
  const unavailable = document.createElement('p');
  unavailable.className = 'media-unavailable';
  unavailable.hidden = true;
  unavailable.textContent = 'La vista previa ya no está disponible. Conserva la evidencia visible o intenta la descarga mientras siga registrada.';
  video.addEventListener('error', () => {
    video.hidden = true;
    unavailable.hidden = false;
  }, { once: true });
  const title = document.createElement('strong');
  title.textContent = artifact.name;
  const meta = document.createElement('span');
  meta.textContent = `${artifact.kind} · ${formatBytes(artifact.size)}`;
  const download = document.createElement('a');
  download.className = 'button button-quiet';
  download.href = `/api/mvp/jobs/${jobId}/artifacts/${encodeURIComponent(artifact.name)}`;
  download.textContent = 'Descargar';
  card.append(video, unavailable, title, meta, download);
  return card;
}

function insertPreview(container, jobId, artifact, trigger) {
  const existing = container.querySelector('.preview-card');
  if (existing) {
    cleanupMedia(existing);
    existing.remove();
    trigger.textContent = 'Vista previa';
    return;
  }
  const preview = createPreviewCard(jobId, artifact);
  preview.style.gridColumn = '1 / -1';
  container.append(preview);
  trigger.textContent = 'Ocultar vista';
}

function createRunDetail(run) {
  const detail = document.createElement('div');
  detail.className = 'run-detail';
  const evidence = document.createElement('div');
  evidence.className = 'run-evidence';
  const outputs = outputArtifacts(run);
  const qa = qaArtifacts(run);
  for (const text of [
    `${outputs.length} salida${outputs.length === 1 ? '' : 's'}`,
    qa.length ? `${qa.length} evidencia${qa.length === 1 ? '' : 's'} de QA` : 'QA estructural no disponible',
    run.input?.sha256 ? `Fuente ${String(run.input.sha256).slice(0, 10)}…` : 'Identidad de fuente no disponible',
  ]) {
    const badge = document.createElement('span');
    badge.className = text.includes('QA') && qa.length ? 'qa-badge' : 'settings-chip';
    badge.textContent = text;
    evidence.append(badge);
  }
  detail.append(evidence);
  if (run.outcome) {
    const outcome = document.createElement('section');
    outcome.className = 'outcome-detail';
    const presentation = outcomePresentation(run);
    const heading = document.createElement('h4');
    heading.textContent = `${presentation.symbol} ${presentation.label}`;
    const retry = run.outcome.retry || {};
    const checkpointCopy = document.createElement('p');
    checkpointCopy.textContent = `Etapas reutilizadas: ${stageNames(retry.reused_stage_names)}. Recalculadas: ${stageNames(retry.recomputed_stage_names)}.`;
    const decisions = document.createElement('p');
    decisions.className = 'outcome-decisions';
    decisions.setAttribute('role', 'status');
    decisions.textContent = `QA estricta: ${qaDecisionLabel(run.outcome.strict_qa?.decision)}. Entrega: ${deliveryDecisionLabel(run.outcome.delivery?.decision)}.`;
    outcome.append(heading, decisions, checkpointCopy);
    const limitations = [
      ...(run.outcome.limitations || []),
      ...(run.outcome.fatal_errors || []),
    ];
    if (limitations.length) {
      const disclosure = document.createElement('details');
      disclosure.className = 'limitation-disclosure';
      const summary = document.createElement('summary');
      summary.textContent = `${limitations.length} hallazgo${limitations.length === 1 ? '' : 's'} verificable${limitations.length === 1 ? '' : 's'}`;
      const list = document.createElement('ul');
      for (const limitation of limitations) {
        const item = document.createElement('li');
        const title = document.createElement('strong');
        title.textContent = `${defectTitle(limitation)} · ${limitation.code}`;
        const copy = document.createElement('span');
        const execution = limitation.executed
          ? `Se ejecutó ${codeLabel(limitation.executed)}${limitation.requested ? ` en lugar de ${codeLabel(limitation.requested)}` : ''}.`
          : defectDescription(
            limitation,
            `Hallazgo en ${codeLabel(limitation.stage || 'qa')}.`,
          );
        copy.textContent = execution;
        item.append(title, copy);
        list.append(item);
      }
      disclosure.append(summary, list);
      outcome.append(disclosure);
    }
    const comparison = [
      retry.resolved_limitation_codes?.length
        ? `Resueltas: ${retry.resolved_limitation_codes.join(', ')}.`
        : null,
      retry.remaining_limitation_codes?.length
        ? `Persisten: ${retry.remaining_limitation_codes.join(', ')}.`
        : null,
      retry.new_limitation_codes?.length
        ? `Nuevas: ${retry.new_limitation_codes.join(', ')}.`
        : null,
    ].filter(Boolean).join(' ');
    if (comparison) {
      const comparisonCopy = document.createElement('p');
      comparisonCopy.className = 'outcome-comparison';
      comparisonCopy.textContent = comparison;
      outcome.append(comparisonCopy);
    }
    const repair = run.outcome.repair || {};
    const repairDefects = (repair.defects || []).filter((item) => item?.code);
    if (repairDefects.length) {
      const disclosure = document.createElement('details');
      disclosure.className = 'repair-disclosure';
      const summary = document.createElement('summary');
      summary.textContent = `Registro agéntico ${repair.registry_version || 'sin versión'} · ${repairDefects.length} defecto${repairDefects.length === 1 ? '' : 's'}`;
      const list = document.createElement('ul');
      for (const defect of repairDefects) {
        const item = document.createElement('li');
        const title = document.createElement('strong');
        const rawCode = defect.presentation?.raw_code || defect.code;
        title.textContent = `${defectTitle(defect)} · ${rawCode}`;
        const lifecycle = document.createElement('span');
        const attempt = defect.repair_attempted
          ? 'Reparación LLM intentada.'
          : defect.eligible
            ? 'Elegible, sin llamada ejecutada.'
            : 'No elegible para reparación LLM.';
        const states = (defect.dispositions || []).map(repairDispositionLabel).join(', ');
        lifecycle.textContent = `${attempt} Estado: ${states || 'sin disposición registrada'}. Estrategia: ${codeLabel(defect.strategy)}.`;
        item.append(title, lifecycle);
        for (const stage of defect.stage_statuses || []) {
          const stageCopy = document.createElement('span');
          stageCopy.textContent = `Etapa ${codeLabel(stage.stage)}: ${codeLabel(stage.status)}${stage.checkpoint_reused ? ' (checkpoint reutilizado)' : ''}.`;
          item.append(stageCopy);
        }
        for (const fallback of defect.fallbacks || []) {
          const fallbackCopy = document.createElement('span');
          fallbackCopy.textContent = `Fallback: ${codeLabel(fallback.executed || 'no ejecutado')}${fallback.requested ? ` en lugar de ${codeLabel(fallback.requested)}` : ''}.`;
          item.append(fallbackCopy);
        }
        const nextAction = document.createElement('span');
        nextAction.textContent = `Siguiente acción: ${codeLabel(defect.presentation?.retry_action || 'ninguna')}.`;
        item.append(nextAction);
        list.append(item);
      }
      const caveat = document.createElement('p');
      caveat.textContent = '“Resuelto” confirma que pasó las comprobaciones deterministas registradas; no garantiza calidad subjetiva ni viralidad.';
      disclosure.append(summary, list, caveat);
      outcome.append(disclosure);
    }
    detail.append(outcome);
  }
  if (outputs.length) {
    const previews = document.createElement('div');
    previews.className = 'preview-grid';
    for (const artifact of outputs) {
      const shell = document.createElement('div');
      shell.className = 'preview-card';
      const title = document.createElement('strong');
      title.textContent = artifact.name;
      const meta = document.createElement('span');
      meta.textContent = `${formatBytes(artifact.size)} · carga bajo demanda`;
      const trigger = document.createElement('button');
      trigger.type = 'button';
      trigger.className = 'button button-quiet';
      trigger.textContent = 'Cargar vista previa';
      trigger.addEventListener('click', () => {
        const video = shell.querySelector('video');
        if (video) {
          video.pause();
          video.removeAttribute('src');
          video.load();
          video.remove();
          trigger.textContent = 'Cargar vista previa';
          return;
        }
        shell.prepend(createPreviewCard(run.id, artifact).querySelector('video'));
        trigger.textContent = 'Ocultar vista previa';
      });
      shell.append(title, meta, trigger);
      previews.append(shell);
    }
    detail.append(previews);
  } else {
    const unavailable = document.createElement('p');
    unavailable.className = 'media-unavailable';
    unavailable.textContent = run.state === 'completed'
      ? 'Los medios ya no están disponibles. La instrucción, configuración y evidencia de auditoría permanecen consultables.'
      : 'Las salidas aparecerán cuando esta ejecución termine.';
    detail.append(unavailable);
  }
  return detail;
}

export function renderVersionHistory({
  versions,
  detailsByVersion,
  expandedRunIds,
  selectedRunIds,
  activeJobId,
  nextCursor,
  favoritePending,
  retryPendingIds,
  retryUxEnabled,
}, callbacks) {
  elements.recentJobs.replaceChildren();
  elements.recentEmpty.hidden = versions.length > 0;
  elements.historyCount.textContent = `${versions.length} versión${versions.length === 1 ? '' : 'es'}`;
  elements.historyLoadMore.hidden = !nextCursor;
  const selectedCount = selectedRunIds.size;
  elements.comparisonToolbar.hidden = selectedCount === 0;
  elements.comparisonCount.textContent = `${selectedCount} de 2 seleccionadas`;
  elements.comparisonOpen.disabled = selectedCount !== 2;

  for (const version of versions) {
    const detail = detailsByVersion.get(version.id);
    const detailedRuns = new Map((detail?.attempts || []).map((run) => [run.id, run]));
    const card = document.createElement('article');
    card.className = 'version-card';
    const header = document.createElement('header');
    header.className = 'version-card-header';
    const heading = document.createElement('div');
    heading.className = 'version-card-title';
    const title = document.createElement('strong');
    title.textContent = `Versión ${version.version_number}`;
    const time = document.createElement('time');
    time.dateTime = version.created_at || '';
    time.textContent = dateLabel(version.created_at);
    heading.append(title, time);
    const favorite = (version.attempts || []).find((run) => run.is_favorite);
    const stateBadge = document.createElement('span');
    stateBadge.className = favorite ? 'favorite-badge' : 'run-state';
    stateBadge.textContent = favorite ? '★ Tu favorita' : `${version.attempts?.length || 0} intento(s)`;
    header.append(heading, stateBadge);

    const prompt = document.createElement('p');
    prompt.className = 'version-prompt';
    prompt.textContent = version.prompt;
    prompt.setAttribute('aria-expanded', 'false');
    const chips = document.createElement('div');
    chips.className = 'settings-chips';
    for (const label of settingsLabels(version.settings)) {
      const chip = document.createElement('span');
      chip.className = 'settings-chip';
      chip.textContent = label;
      chips.append(chip);
    }
    const versionActions = document.createElement('div');
    versionActions.className = 'version-actions';
    if (version.prompt.length > 210) {
      const expandPrompt = document.createElement('button');
      expandPrompt.type = 'button';
      expandPrompt.className = 'button button-quiet';
      expandPrompt.textContent = 'Leer instrucción completa';
      expandPrompt.addEventListener('click', () => {
        const expanded = prompt.getAttribute('aria-expanded') === 'true';
        prompt.setAttribute('aria-expanded', String(!expanded));
        expandPrompt.textContent = expanded ? 'Leer instrucción completa' : 'Contraer instrucción';
      });
      versionActions.append(expandPrompt);
    }

    const attempts = document.createElement('ol');
    attempts.className = 'attempt-list';
    for (const summary of version.attempts || []) {
      const run = detailedRuns.get(summary.id) || summary;
      const row = document.createElement('li');
      row.className = 'attempt-row';
      if (run.id === activeJobId) row.setAttribute('aria-current', 'true');
      const summaryCopy = document.createElement('div');
      summaryCopy.className = 'attempt-summary';
      const runTitle = document.createElement('strong');
      const presentation = outcomePresentation(run);
      runTitle.textContent = `Intento ${run.attempt_number} · ${presentation.label}`;
      const outcomeBadge = document.createElement('span');
      outcomeBadge.className = `outcome-badge ${presentation.className}`;
      outcomeBadge.textContent = `${presentation.symbol} ${presentation.label}`;
      const meta = document.createElement('span');
      const outputs = run.artifacts ? outputArtifacts(run).length : null;
      meta.textContent = [
        durationLabel(run),
        outputs === null ? 'Detalles bajo demanda' : `${outputs} salida${outputs === 1 ? '' : 's'}`,
        run.error_code ? errorMessage(run.error_code) : null,
      ].filter(Boolean).join(' · ');
      summaryCopy.append(runTitle, outcomeBadge, meta);
      const actions = document.createElement('div');
      actions.className = 'attempt-actions';
      const open = document.createElement('button');
      open.type = 'button';
      open.className = 'button button-quiet';
      open.textContent = run.id === activeJobId ? 'Seleccionada' : 'Ver ejecución';
      open.addEventListener('click', () => callbacks.onSelect(run));
      actions.append(open);
      const evidence = document.createElement('button');
      evidence.type = 'button';
      evidence.className = 'button button-quiet';
      evidence.textContent = expandedRunIds.has(run.id) ? 'Ocultar detalles' : 'Ver salidas y QA';
      evidence.addEventListener('click', () => callbacks.onToggleDetail(version.id, run.id));
      actions.append(evidence);
      if (run.state === 'completed') {
        const compareLabel = document.createElement('label');
        compareLabel.className = 'compare-check';
        const compare = document.createElement('input');
        compare.type = 'checkbox';
        compare.checked = selectedRunIds.has(run.id);
        compare.disabled = !compare.checked && selectedRunIds.size >= 2;
        compare.addEventListener('change', () => callbacks.onCompare(version.id, run.id, compare.checked));
        compareLabel.append(compare, document.createTextNode('Comparar'));
        actions.append(compareLabel);
        const favoriteButton = document.createElement('button');
        favoriteButton.type = 'button';
        favoriteButton.className = run.is_favorite ? 'button button-secondary' : 'button button-quiet';
        favoriteButton.textContent = run.is_favorite ? '★ Quitar favorita' : '☆ Elegir favorita';
        favoriteButton.disabled = favoritePending;
        favoriteButton.addEventListener('click', () => callbacks.onFavorite(run));
        actions.append(favoriteButton);
      }
      if (
        retryUxEnabled
        && ['completed', 'failed'].includes(run.state)
        && run.outcome?.retry?.supported
      ) {
        const retryButton = document.createElement('button');
        retryButton.type = 'button';
        retryButton.className = 'button button-secondary';
        retryButton.textContent = retryPendingIds.has(run.id)
          ? 'Preparando reintento…'
          : 'Reintentar defectos';
        retryButton.disabled = retryPendingIds.has(run.id);
        retryButton.addEventListener('click', () => callbacks.onRetryDefects(version, run));
        const improveButton = document.createElement('button');
        improveButton.type = 'button';
        improveButton.className = 'button button-quiet';
        improveButton.textContent = 'Crear versión mejorada';
        improveButton.addEventListener('click', () => callbacks.onCreateImprovedVersion(version, run));
        actions.append(retryButton, improveButton);
      }
      row.append(summaryCopy, actions);
      if (expandedRunIds.has(run.id)) {
        row.append(run.artifacts ? createRunDetail(run) : loadingDetail());
      }
      attempts.append(row);
    }
    card.append(header, prompt, chips, versionActions, attempts);
    elements.recentJobs.append(card);
  }
}

function loadingDetail() {
  const detail = document.createElement('p');
  detail.className = 'media-unavailable';
  detail.textContent = 'Cargando salidas y evidencia técnica…';
  return detail;
}

export function renderLegacyHistory(jobs) {
  elements.legacyHistory.replaceChildren();
  if (!jobs.length) {
    const empty = document.createElement('p');
    empty.className = 'media-unavailable';
    empty.textContent = 'Esta sesión anterior no conserva ejecuciones visibles.';
    elements.legacyHistory.append(empty);
    return;
  }
  for (const job of jobs) {
    const card = document.createElement('article');
    card.className = 'version-card legacy-job';
    const copy = document.createElement('span');
    copy.className = 'recent-job-copy';
    const title = document.createElement('strong');
    title.textContent = job.prompt || `Ejecución ${job.id.slice(0, 8)}`;
    const meta = document.createElement('span');
    meta.textContent = `${stateLabel(job.state)} · ${dateLabel(job.created_at)}`;
    copy.append(title, meta);
    const state = document.createElement('span');
    state.className = 'run-state';
    state.textContent = stateLabel(job.state);
    const header = document.createElement('header');
    header.className = 'version-card-header';
    header.append(copy, state);
    card.append(header);
    const available = (job.artifacts || []).filter((artifact) => artifact.availability === 'available');
    if (available.length) {
      const actions = document.createElement('div');
      actions.className = 'attempt-actions';
      for (const artifact of available) {
        const link = document.createElement('a');
        link.className = 'button button-quiet';
        link.href = `/api/mvp/jobs/${job.id}/artifacts/${encodeURIComponent(artifact.name)}`;
        link.textContent = `Descargar ${artifact.name}`;
        actions.append(link);
      }
      card.append(actions);
    } else {
      const unavailable = document.createElement('p');
      unavailable.className = 'media-unavailable';
      unavailable.textContent = 'Los medios de esta ejecución ya no están disponibles; la instrucción y el estado permanecen visibles.';
      card.append(unavailable);
    }
    elements.legacyHistory.append(card);
  }
}

export function renderComparison(entries) {
  elements.comparisonContent.replaceChildren();
  for (const { version, run } of entries) {
    const column = document.createElement('article');
    column.className = 'comparison-column';
    const title = document.createElement('h3');
    title.textContent = `Versión ${version.version_number} · intento ${run.attempt_number}`;
    column.append(title);
    column.append(comparisonSection('Instrucción', version.prompt));
    column.append(comparisonSection('Configuración', settingsLabels(version.settings).join(' · ')));
    column.append(comparisonSection('Estado y tiempo', `${outcomePresentation(run).label} · ${durationLabel(run)}`));
    const retry = run.outcome?.retry || {};
    column.append(comparisonSection(
      'Limitaciones',
      outcomeCodes(run.outcome, 'limitation_codes', 'limitations').length
        ? outcomeCodes(run.outcome, 'limitation_codes', 'limitations').join(', ')
        : 'Sin limitaciones declaradas.',
    ));
    column.append(comparisonSection(
      'Cambio frente al intento anterior',
      [
        retry.resolved_limitation_codes?.length ? `Resueltas: ${retry.resolved_limitation_codes.join(', ')}` : null,
        retry.remaining_limitation_codes?.length ? `Persisten: ${retry.remaining_limitation_codes.join(', ')}` : null,
        retry.new_limitation_codes?.length ? `Nuevas: ${retry.new_limitation_codes.join(', ')}` : null,
      ].filter(Boolean).join(' · ') || 'Este intento no declara una comparación de defectos.',
    ));
    const outputs = outputArtifacts(run);
    const qa = qaArtifacts(run);
    column.append(comparisonSection(
      'Evidencia técnica',
      `${outputs.length} salida${outputs.length === 1 ? '' : 's'} · ${qa.length ? `${qa.length} documento(s) de QA` : 'QA estructural no disponible'}`,
    ));
    if (outputs.length) {
      const previews = document.createElement('div');
      previews.className = 'preview-grid';
      for (const artifact of outputs) previews.append(createPreviewCard(run.id, artifact));
      column.append(previews);
    } else {
      column.append(comparisonSection(
        'Medios',
        'Los medios no están disponibles; los metadatos y la evidencia de auditoría se conservan según su plazo.',
      ));
    }
    elements.comparisonContent.append(column);
  }
}

function comparisonSection(label, value) {
  const section = document.createElement('section');
  section.className = 'comparison-section';
  const title = document.createElement('span');
  title.textContent = label;
  const copy = document.createElement('p');
  copy.textContent = value;
  section.append(title, copy);
  return section;
}

export function cleanupMedia(root = document) {
  for (const video of root.querySelectorAll('video[data-managed-preview]')) {
    video.pause();
    video.removeAttribute('src');
    video.load();
  }
}

export function setComposerAvailability(sourceReady, busy = false) {
  elements.submit.disabled = !sourceReady || busy;
  elements.submit.querySelector('span').textContent = busy ? 'Creando versión…' : 'Crear nueva versión';
  elements.prompt.disabled = busy;
}

export function showToast(message, timeout = 4500) {
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.textContent = message;
  elements.toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), timeout);
}

export function openDialog(dialog, focusTarget) {
  if (!dialog.open) dialog.showModal();
  window.requestAnimationFrame(() => focusTarget?.focus());
}

export function closeDialog(dialog) {
  if (dialog.open) dialog.close();
}

export { errorMessage };
