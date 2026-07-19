import { activityMessage, activityMeta, errorMessage, stateLabel } from './messages.js';

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
    state.textContent = session.input_video?.state === 'ready' ? 'Fuente lista' : 'Fuente pendiente';
    copy.append(title, state);
    button.append(marker, copy);
    button.addEventListener('click', () => onSelect(session.id));
    elements.sessionList.append(button);
    elements.sessionSelect.append(new Option(session.title, session.id));
  }
  elements.sessionSelect.value = selectedId || '';
  elements.sessionSelect.disabled = sessions.length === 0;
}

export function renderWorkspace(session) {
  const hasSession = Boolean(session);
  elements.workspaceEmpty.hidden = hasSession;
  elements.workspaceContent.hidden = !hasSession;
  elements.sessionDelete.disabled = !hasSession;
  elements.headerSessionTitle.textContent = session?.title || 'Sin seleccionar';
  if (!hasSession) return;
  elements.workspaceTitle.textContent = session.title;
  elements.sessionSummary.textContent = 'La fuente permanece vinculada a esta sesión; cada nueva instrucción crea una versión auditable.';
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
    const link = document.createElement('a');
    link.className = 'button button-secondary';
    link.href = `/api/mvp/jobs/${jobId}/artifacts/${encodeURIComponent(artifact.name)}`;
    link.textContent = 'Descargar';
    row.append(copy, link);
    elements.artifacts.append(row);
  }
}

export function renderRecentVersions(versions, activeJobId, onSelect) {
  elements.recentJobs.replaceChildren();
  const runs = versions.flatMap((version) =>
    (version.attempts || []).map((attempt) => ({ ...attempt, version })),
  );
  elements.recentEmpty.hidden = runs.length > 0;
  for (const run of runs.slice(0, 8)) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'recent-job';
    if (run.id === activeJobId) button.setAttribute('aria-current', 'true');
    const copy = document.createElement('span');
    copy.className = 'recent-job-copy';
    const title = document.createElement('strong');
    title.textContent = `Versión ${run.version.version_number} · intento ${run.attempt_number}`;
    const prompt = document.createElement('span');
    prompt.textContent = run.version.prompt;
    copy.append(title, prompt);
    const status = document.createElement('span');
    status.className = 'run-state';
    status.textContent = stateLabel(run.state);
    button.append(copy, status);
    button.addEventListener('click', () => onSelect(run));
    elements.recentJobs.append(button);
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
