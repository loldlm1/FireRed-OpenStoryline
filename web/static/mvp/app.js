import { ActivityFeed } from './activity.js';
import { apiJson, download, setAuthExpiredHandler } from './api.js';
import { ResumableUpload } from './upload.js';
import {
  appendActivity,
  closeDialog,
  elements,
  errorMessage,
  formatBytes,
  formatElapsed,
  openDialog,
  renderConnection,
  renderJob,
  renderRecentVersions,
  renderSessions,
  renderSource,
  renderWorkspace,
  resetActivity,
  setComposerAvailability,
  showApp,
  showLoading,
  showLogin,
  showToast,
  showUpload,
  showUploadError,
  updateUploadProgress,
  updateUploadStage,
} from './views.js';

const state = {
  sessions: [],
  currentSession: null,
  source: { state: 'missing' },
  versions: [],
  currentJob: null,
  upload: null,
  activity: null,
};

function stopTransientWork() {
  state.activity?.stop();
  state.activity = null;
  state.upload?.pause();
  state.upload = null;
}

function setSessionUrl(sessionId) {
  const url = new URL(window.location.href);
  if (sessionId) url.searchParams.set('session', sessionId);
  else url.searchParams.delete('session');
  window.history.replaceState({}, '', url);
}

function preferredSessionFromUrl() {
  return new URL(window.location.href).searchParams.get('session') || '';
}

function activeRun(versions) {
  const runs = versions.flatMap((version) => version.attempts || []);
  return runs.find((run) => ['queued', 'running'].includes(run.state)) || null;
}

function latestRun(versions) {
  for (const version of versions) {
    if (version.attempts?.length) return version.attempts[0];
  }
  return null;
}

function updateSessionDeleteAvailability() {
  elements.sessionDelete.disabled = !state.currentSession || Boolean(activeRun(state.versions));
}

function refreshSessionViews() {
  renderSessions(state.sessions, state.currentSession?.id || '', selectSession);
  renderWorkspace(state.currentSession);
  renderSource(state.source, { activeUpload: Boolean(state.upload?.running) });
  renderRecentVersions(
    state.versions,
    state.currentJob?.id || '',
    (run) => selectRun(run.id, { startedAt: run.created_at }),
  );
  updateSessionDeleteAvailability();
}

async function checkAuth() {
  showLoading();
  try {
    const auth = await apiJson('/api/mvp/auth/session');
    if (!auth.authenticated) {
      showLogin();
      return;
    }
    showApp();
    await loadSessions(preferredSessionFromUrl());
  } catch (error) {
    showLogin(errorMessage(error));
  }
}

async function loadSessions(preferred = '') {
  elements.sessionError.textContent = '';
  const page = await apiJson('/api/mvp/sessions?limit=50');
  state.sessions = (page.items || []).filter((session) => session.workflow_version === 2);
  if (!state.sessions.length) {
    stopTransientWork();
    state.currentSession = null;
    state.source = { state: 'missing' };
    state.versions = [];
    state.currentJob = null;
    setSessionUrl('');
    refreshSessionViews();
    resetActivity();
    window.requestAnimationFrame(() => elements.emptyCreateSession.focus());
    return;
  }
  const selected = state.sessions.find((session) => session.id === preferred) || state.sessions[0];
  await selectSession(selected.id);
}

async function selectSession(sessionId) {
  if (!sessionId) return;
  stopTransientWork();
  elements.sessionError.textContent = '';
  elements.jobError.textContent = '';
  try {
    const [session, source, versions] = await Promise.all([
      apiJson(`/api/mvp/sessions/${sessionId}`),
      apiJson(`/api/mvp/sessions/${sessionId}/input-video`),
      apiJson(`/api/mvp/sessions/${sessionId}/prompt-versions?limit=20`),
    ]);
    state.currentSession = session;
    state.source = source;
    state.versions = versions.items || [];
    state.currentJob = null;
    setSessionUrl(session.id);
    refreshSessionViews();
    resetActivity();
    const run = activeRun(state.versions) || latestRun(state.versions);
    if (run) await selectRun(run.id, { startedAt: run.created_at });
    else if (source.state === 'ready') window.requestAnimationFrame(() => elements.prompt.focus());
    else if (source.state === 'missing' || source.state === 'failed') window.requestAnimationFrame(() => elements.video.focus());
  } catch (error) {
    elements.sessionError.textContent = errorMessage(error);
    if (error.code === 'SESSION_NOT_FOUND') await loadSessions();
  }
}

async function reloadVersions() {
  if (!state.currentSession) return;
  const page = await apiJson(
    `/api/mvp/sessions/${state.currentSession.id}/prompt-versions?limit=20`,
  );
  state.versions = page.items || [];
  renderRecentVersions(
    state.versions,
    state.currentJob?.id || '',
    (run) => selectRun(run.id, { startedAt: run.created_at }),
  );
  updateSessionDeleteAvailability();
}

async function selectRun(jobId, { startedAt, reset = true } = {}) {
  state.activity?.stop();
  state.activity = null;
  if (reset) resetActivity();
  try {
    const job = await apiJson(`/api/mvp/jobs/${jobId}`);
    state.currentJob = job;
    renderJob(job);
    renderRecentVersions(
      state.versions,
      job.id,
      (run) => selectRun(run.id, { startedAt: run.created_at }),
    );
    const feed = new ActivityFeed(job.id, {
      onConnection: (connection) => renderConnection(connection),
      onEvent: (event) => appendActivity(event),
      onElapsed: (milliseconds) => {
        elements.elapsedTime.textContent = formatElapsed(milliseconds);
      },
      onJob: (nextJob) => {
        state.currentJob = nextJob;
        renderJob(nextJob);
      },
      onTerminal: async () => {
        try {
          state.currentJob = await apiJson(`/api/mvp/jobs/${job.id}`);
          renderJob(state.currentJob);
          await reloadVersions();
        } catch (error) {
          elements.jobError.textContent = errorMessage(error);
        }
      },
    });
    state.activity = feed;
    await feed.start({ startedAt: startedAt || job.started_at || job.created_at });
  } catch (error) {
    elements.jobError.textContent = errorMessage(error);
  }
}

function createUploader(file) {
  if (!state.currentSession) return null;
  const uploader = new ResumableUpload(state.currentSession.id, {
    onFile: (selectedFile) => {
      showUpload(selectedFile, state.source);
      setComposerAvailability(false);
    },
    onStage: (stage, detail) => updateUploadStage(stage, detail),
    onProgress: updateUploadProgress,
    onPaused: () => updateUploadStage('paused'),
    onRetry: ({ attempt }) => updateUploadStage('retrying', { attempt }),
    onOffsetAdjusted: ({ offset }) => updateUploadStage('adjusted', { offset }),
    onError: showUploadError,
    onCancelled: async () => {
      state.upload = null;
      state.source = await apiJson(
        `/api/mvp/sessions/${state.currentSession.id}/input-video`,
      );
      renderSource(state.source);
      showToast('La carga se descartó. Puedes elegir un video de nuevo.');
    },
    onReady: async (source) => {
      state.source = source;
      state.upload = null;
      renderSource(source);
      setComposerAvailability(true);
      state.sessions = state.sessions.map((session) =>
        session.id === state.currentSession.id
          ? { ...session, input_video: source }
          : session,
      );
      renderSessions(state.sessions, state.currentSession.id, selectSession);
      showToast('Video validado. Ya puedes crear tantas versiones como necesites.');
      window.requestAnimationFrame(() => elements.prompt.focus());
    },
  });
  return uploader;
}

function beginUpload(file) {
  if (!file || !state.currentSession) return;
  state.upload?.pause();
  state.upload = createUploader(file);
  state.upload.start(file).catch(() => {});
}

async function discardIncompleteUpload() {
  if (!state.currentSession || !state.source?.upload_id) return;
  elements.resumeCancel.disabled = true;
  try {
    await apiJson(
      `/api/mvp/sessions/${state.currentSession.id}/input-video/uploads/${state.source.upload_id}`,
      { method: 'DELETE' },
    );
    state.source = await apiJson(
      `/api/mvp/sessions/${state.currentSession.id}/input-video`,
    );
    renderSource(state.source);
    showToast('La carga incompleta se descartó.');
  } catch (error) {
    elements.uploadError.textContent = errorMessage(error);
  } finally {
    elements.resumeCancel.disabled = false;
  }
}

function syncAdvancedSettings() {
  const agentic = elements.editMode.value === 'agentic';
  elements.assetPolicy.disabled = !agentic;
  elements.stockPolicy.disabled = !agentic;
  elements.maxGeneratedAssets.disabled = !agentic || elements.assetPolicy.value === 'off';
  elements.maxStockAssets.disabled = !agentic || elements.stockPolicy.value === 'off';
  if (!agentic) {
    elements.maxGeneratedAssets.value = '0';
    elements.maxStockAssets.value = '0';
  } else if (elements.assetPolicy.value === 'auto' && elements.maxGeneratedAssets.value === '0') {
    elements.maxGeneratedAssets.value = '2';
  }
}

function promptPayload() {
  const agentic = elements.editMode.value === 'agentic';
  return {
    prompt: elements.prompt.value,
    max_clips: Number(elements.maxClips.value),
    edit_mode: elements.editMode.value,
    asset_policy: agentic ? elements.assetPolicy.value : 'off',
    max_generated_assets_per_clip: agentic && elements.assetPolicy.value === 'auto'
      ? Number(elements.maxGeneratedAssets.value)
      : 0,
    stock_policy: agentic ? elements.stockPolicy.value : 'off',
    max_stock_assets_per_clip: agentic && elements.stockPolicy.value === 'auto'
      ? Number(elements.maxStockAssets.value)
      : 0,
  };
}

elements.loginForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  elements.loginError.textContent = '';
  elements.loginSubmit.disabled = true;
  elements.loginForm.setAttribute('aria-busy', 'true');
  try {
    await apiJson('/api/mvp/auth/login', {
      method: 'POST',
      body: { password: elements.password.value },
    });
    showApp();
    await loadSessions(preferredSessionFromUrl());
  } catch (error) {
    const wait = error.retryAfter || 60;
    elements.loginError.textContent = error.code === 'LOGIN_RATE_LIMITED'
      ? `Demasiados intentos fallidos. Intenta de nuevo en ${wait} segundos.`
      : errorMessage(error, 'No pudimos iniciar la sesión. Intenta de nuevo.');
    elements.password.select();
  } finally {
    elements.loginSubmit.disabled = false;
    elements.loginForm.removeAttribute('aria-busy');
  }
});

elements.logout.addEventListener('click', async () => {
  elements.logout.disabled = true;
  try {
    await apiJson('/api/mvp/auth/logout', { method: 'POST' });
  } catch {
    // The browser still returns to the locked state if the server session already expired.
  } finally {
    stopTransientWork();
    elements.logout.disabled = false;
    state.currentSession = null;
    state.currentJob = null;
    showLogin();
  }
});

function openSessionDialog() {
  elements.sessionFormError.textContent = '';
  openDialog(elements.sessionDialog, elements.sessionTitle);
}

elements.sessionNew.addEventListener('click', openSessionDialog);
elements.emptyCreateSession.addEventListener('click', openSessionDialog);

for (const closeButton of document.querySelectorAll('[data-close-dialog]')) {
  closeButton.addEventListener('click', () => closeDialog(byDialogId(closeButton.dataset.closeDialog)));
}

function byDialogId(id) {
  return document.getElementById(id);
}

elements.sessionForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  elements.sessionFormError.textContent = '';
  elements.sessionSubmit.disabled = true;
  try {
    const created = await apiJson('/api/mvp/sessions', {
      method: 'POST',
      body: { title: elements.sessionTitle.value },
    });
    elements.sessionTitle.value = '';
    closeDialog(elements.sessionDialog);
    await loadSessions(created.id);
    showToast('Sesión creada. Esta será la única fuente de video de este espacio.');
  } catch (error) {
    elements.sessionFormError.textContent = errorMessage(error);
  } finally {
    elements.sessionSubmit.disabled = false;
  }
});

elements.sessionDelete.addEventListener('click', () => {
  elements.sessionNotice.textContent = '';
  openDialog(elements.sessionDeleteDialog, elements.sessionDeleteConfirm);
});

elements.sessionDeleteCancel.addEventListener('click', () => closeDialog(elements.sessionDeleteDialog));

elements.sessionDeleteConfirm.addEventListener('click', async () => {
  if (!state.currentSession) return;
  elements.sessionDeleteConfirm.disabled = true;
  try {
    const deletedTitle = state.currentSession.title;
    await apiJson(`/api/mvp/sessions/${state.currentSession.id}`, { method: 'DELETE' });
    closeDialog(elements.sessionDeleteDialog);
    showToast(`“${deletedTitle}” se eliminó junto con sus medios disponibles.`);
    await loadSessions();
  } catch (error) {
    elements.sessionNotice.textContent = errorMessage(error);
  } finally {
    elements.sessionDeleteConfirm.disabled = false;
  }
});

elements.video.addEventListener('change', () => {
  const [file] = elements.video.files || [];
  if (file) beginUpload(file);
  elements.video.value = '';
});

elements.videoResume.addEventListener('change', () => {
  const [file] = elements.videoResume.files || [];
  if (file) beginUpload(file);
  elements.videoResume.value = '';
});

elements.uploadPause.addEventListener('click', () => {
  if (!state.upload) return;
  if (state.upload.paused) state.upload.resume().catch(() => {});
  else state.upload.pause();
});

elements.uploadRetry.addEventListener('click', () => {
  if (!state.upload?.file) return;
  state.upload.start(state.upload.file).catch(() => {});
});

elements.uploadCancel.addEventListener('click', () => {
  state.upload?.cancel().catch((error) => showUploadError(error));
});

elements.resumeCancel.addEventListener('click', discardIncompleteUpload);

elements.prompt.addEventListener('input', () => {
  elements.promptCount.textContent = `${elements.prompt.value.length.toLocaleString('es')} / 12.000`;
});

elements.editMode.addEventListener('change', syncAdvancedSettings);
elements.assetPolicy.addEventListener('change', syncAdvancedSettings);
elements.stockPolicy.addEventListener('change', () => {
  if (elements.stockPolicy.value === 'auto' && elements.maxStockAssets.value === '0') {
    elements.maxStockAssets.value = '1';
  }
  syncAdvancedSettings();
});

elements.jobForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  elements.jobError.textContent = '';
  if (!state.currentSession || state.source.state !== 'ready') {
    elements.jobError.textContent = 'Espera a que el video fuente quede validado antes de crear una versión.';
    return;
  }
  if (!elements.prompt.value.trim()) {
    elements.jobError.textContent = 'Escribe las instrucciones para esta versión.';
    elements.prompt.focus();
    return;
  }
  setComposerAvailability(true, true);
  try {
    const result = await apiJson(
      `/api/mvp/sessions/${state.currentSession.id}/prompt-versions`,
      { method: 'POST', body: promptPayload() },
    );
    state.currentJob = result.run;
    state.versions = [
      result.prompt_version,
      ...state.versions.filter((version) => version.id !== result.prompt_version.id),
    ];
    elements.promptVersionLabel.textContent = `Versión ${result.prompt_version.version_number}`;
    renderRecentVersions(
      state.versions,
      result.run.id,
      (run) => selectRun(run.id, { startedAt: run.created_at }),
    );
    await selectRun(result.run.id, { startedAt: result.run.created_at });
    showToast(`Versión ${result.prompt_version.version_number} enviada a edición.`);
  } catch (error) {
    elements.jobError.textContent = errorMessage(error);
  } finally {
    setComposerAvailability(state.source.state === 'ready');
  }
});

elements.activityRetry.addEventListener('click', () => state.activity?.retryNow());

elements.bundle.addEventListener('click', async () => {
  const jobId = elements.bundle.dataset.jobId;
  if (!jobId) return;
  elements.bundle.disabled = true;
  try {
    await download(`/api/mvp/jobs/${jobId}/bundle`, `${jobId}-artifacts.zip`);
  } catch (error) {
    elements.jobError.textContent = errorMessage(error);
  } finally {
    elements.bundle.disabled = false;
  }
});

window.addEventListener('popstate', () => loadSessions(preferredSessionFromUrl()).catch(() => {}));

setAuthExpiredHandler((error) => {
  stopTransientWork();
  showLogin(errorMessage(error));
});

syncAdvancedSettings();
resetActivity();
checkAuth();
