import { ActivityFeed } from './activity.js';
import { apiJson, download, setAuthExpiredHandler } from './api.js';
import { ResumableUpload } from './upload.js';
import {
  appendActivity,
  cleanupMedia,
  closeDialog,
  elements,
  errorMessage,
  formatBytes,
  formatElapsed,
  openDialog,
  renderComparison,
  renderConnection,
  renderJob,
  renderSessions,
  renderSource,
  renderVersionHistory,
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
  nextVersionCursor: null,
  detailsByVersion: new Map(),
  expandedRunIds: new Set(),
  selectedRunIds: new Set(),
  retryPendingIds: new Set(),
  favoritePending: false,
  currentJob: null,
  upload: null,
  activity: null,
  sessionEpoch: 0,
  runEpoch: 0,
  comparisonReturnFocus: null,
};

function stopTransientWork() {
  state.sessionEpoch += 1;
  state.runEpoch += 1;
  state.activity?.stop();
  state.activity = null;
  state.upload?.pause();
  state.upload = null;
  cleanupMedia(document);
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
  elements.sessionDelete.disabled = !state.currentSession
    || Boolean(activeRun(state.versions));
}

function resetHistoryState() {
  state.versions = [];
  state.nextVersionCursor = null;
  state.detailsByVersion = new Map();
  state.expandedRunIds = new Set();
  state.selectedRunIds = new Set();
  state.retryPendingIds = new Set();
  state.favoritePending = false;
}

function runEntry(runId) {
  for (const version of state.versions) {
    const detail = state.detailsByVersion.get(version.id);
    const run = (detail?.attempts || version.attempts || []).find((item) => item.id === runId);
    if (run) return { version: detail || version, run };
  }
  return null;
}

function favoriteRunId() {
  for (const version of state.versions) {
    const detail = state.detailsByVersion.get(version.id);
    const run = (detail?.attempts || version.attempts || []).find((item) => item.is_favorite);
    if (run) return run.id;
  }
  return null;
}

function favoriteLabel() {
  const favoriteId = favoriteRunId();
  const entry = favoriteId ? runEntry(favoriteId) : null;
  return entry
    ? `Versión ${entry.version.version_number}, intento ${entry.run.attempt_number}`
    : '';
}

function historyCallbacks() {
  return {
    onSelect: (run) => selectRun(run.id, { startedAt: run.created_at }),
    onToggleDetail: toggleRunDetail,
    onCompare: toggleComparisonSelection,
    onFavorite: toggleFavorite,
    onRerun: (version, run) => rerunVersion(version, run),
    onRetryDefects: (version, run) => rerunVersion(version, run, {
      useQualityFeedback: true,
    }),
    onCreateImprovedVersion: createImprovedVersion,
  };
}

function renderHistory() {
  if (!state.currentSession) return;
  cleanupMedia(elements.recentJobs);
  renderVersionHistory({
    versions: state.versions,
    detailsByVersion: state.detailsByVersion,
    expandedRunIds: state.expandedRunIds,
    selectedRunIds: state.selectedRunIds,
    activeJobId: state.currentJob?.id || '',
    nextCursor: state.nextVersionCursor,
    favoritePending: state.favoritePending,
    retryPendingIds: state.retryPendingIds,
    retryUxEnabled: Boolean(state.currentSession?.capabilities?.retry_ux_enabled),
  }, historyCallbacks());
  renderWorkspace(state.currentSession, { favoriteLabel: favoriteLabel() });
}

function mergeDetailedRun(job) {
  if (!job?.prompt_version_id) return;
  const version = state.versions.find((item) => item.id === job.prompt_version_id);
  if (!version) return;
  const detail = state.detailsByVersion.get(version.id) || { ...version };
  const attempts = [...(detail.attempts || [])];
  const index = attempts.findIndex((run) => run.id === job.id);
  const existing = index >= 0 ? attempts[index] : null;
  const retryCapability = existing?.outcome?.retry;
  const merged = retryCapability && job?.outcome
    ? {
        ...job,
        outcome: {
          ...job.outcome,
          retry: {
            ...(job.outcome.retry || {}),
            supported: retryCapability.supported,
            unavailable_reason: retryCapability.unavailable_reason || '',
            recommended_action: retryCapability.recommended_action,
          },
        },
      }
    : job;
  if (index >= 0) attempts[index] = merged;
  else attempts.unshift(merged);
  state.detailsByVersion.set(version.id, { ...detail, attempts });
  version.attempts = (version.attempts || []).map((run) => (
    run.id === job.id
      ? { ...run, ...merged, artifacts: undefined, input: undefined, request: undefined }
      : run
  ));
}

function refreshSessionViews() {
  renderSessions(state.sessions, state.currentSession?.id || '', selectSession);
  renderWorkspace(state.currentSession, { favoriteLabel: favoriteLabel() });
  renderSource(state.source, { activeUpload: Boolean(state.upload?.running) });
  renderHistory();
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
  state.sessions = page.items || [];
  if (!state.sessions.length) {
    stopTransientWork();
    state.currentSession = null;
    state.source = { state: 'missing' };
    resetHistoryState();
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
  const epoch = state.sessionEpoch;
  resetHistoryState();
  elements.sessionError.textContent = '';
  elements.jobError.textContent = '';
  try {
    const session = await apiJson(`/api/mvp/sessions/${sessionId}?job_limit=50`);
    if (epoch !== state.sessionEpoch) return;
    state.currentSession = session;
    state.currentJob = null;
    setSessionUrl(session.id);
    const [source, versions] = await Promise.all([
      apiJson(`/api/mvp/sessions/${sessionId}/input-video`),
      apiJson(`/api/mvp/sessions/${sessionId}/prompt-versions?limit=20`),
    ]);
    if (epoch !== state.sessionEpoch) return;
    state.source = source;
    state.versions = versions.items || [];
    state.nextVersionCursor = versions.next_cursor || null;
    refreshSessionViews();
    resetActivity();
    const run = activeRun(state.versions) || latestRun(state.versions);
    if (run) await selectRun(run.id, { startedAt: run.created_at });
    else if (source.state === 'ready') window.requestAnimationFrame(() => elements.prompt.focus());
    else if (source.state === 'missing' || source.state === 'failed') window.requestAnimationFrame(() => elements.video.focus());
  } catch (error) {
    if (epoch !== state.sessionEpoch) return;
    elements.sessionError.textContent = errorMessage(error);
    if (error.code === 'SESSION_NOT_FOUND') await loadSessions();
  }
}

async function reloadVersions() {
  if (!state.currentSession) return;
  const sessionId = state.currentSession.id;
  const epoch = state.sessionEpoch;
  const page = await apiJson(
    `/api/mvp/sessions/${sessionId}/prompt-versions?limit=20`,
  );
  if (epoch !== state.sessionEpoch || state.currentSession?.id !== sessionId) return;
  state.versions = page.items || [];
  state.nextVersionCursor = page.next_cursor || null;
  renderHistory();
  updateSessionDeleteAvailability();
}

async function loadMoreVersions() {
  if (!state.currentSession || !state.nextVersionCursor) return;
  const sessionId = state.currentSession.id;
  const epoch = state.sessionEpoch;
  const cursor = state.nextVersionCursor;
  elements.historyLoadMore.disabled = true;
  elements.historyLoadMore.textContent = 'Cargando…';
  try {
    const page = await apiJson(
      `/api/mvp/sessions/${sessionId}/prompt-versions?limit=20&cursor=${encodeURIComponent(cursor)}`,
    );
    if (epoch !== state.sessionEpoch || state.currentSession?.id !== sessionId) return;
    const known = new Set(state.versions.map((version) => version.id));
    state.versions.push(...(page.items || []).filter((version) => !known.has(version.id)));
    state.nextVersionCursor = page.next_cursor || null;
    renderHistory();
  } catch (error) {
    showToast(errorMessage(error, 'No pudimos cargar las versiones anteriores.'));
  } finally {
    elements.historyLoadMore.disabled = false;
    elements.historyLoadMore.textContent = 'Cargar versiones anteriores';
  }
}

async function loadVersionDetail(versionId) {
  if (state.detailsByVersion.has(versionId)) return state.detailsByVersion.get(versionId);
  const sessionId = state.currentSession?.id;
  const epoch = state.sessionEpoch;
  const detail = await apiJson(`/api/mvp/prompt-versions/${versionId}`);
  if (
    epoch !== state.sessionEpoch
    || state.currentSession?.id !== sessionId
    || detail.editing_session_id !== sessionId
  ) return null;
  state.detailsByVersion.set(versionId, detail);
  return detail;
}

async function toggleRunDetail(versionId, runId) {
  if (state.expandedRunIds.has(runId)) {
    state.expandedRunIds.delete(runId);
    renderHistory();
    return;
  }
  state.expandedRunIds.add(runId);
  renderHistory();
  try {
    await loadVersionDetail(versionId);
  } catch (error) {
    state.expandedRunIds.delete(runId);
    showToast(errorMessage(error, 'No pudimos cargar las salidas de esta ejecución.'));
  }
  renderHistory();
}

function toggleComparisonSelection(_versionId, runId, selected) {
  if (selected) {
    if (state.selectedRunIds.size >= 2) return;
    state.selectedRunIds.add(runId);
  } else {
    state.selectedRunIds.delete(runId);
  }
  renderHistory();
}

async function openComparison() {
  if (state.selectedRunIds.size !== 2) return;
  state.comparisonReturnFocus = document.activeElement;
  elements.comparisonOpen.disabled = true;
  try {
    const selections = [...state.selectedRunIds].map((runId) => runEntry(runId));
    await Promise.all(
      selections.filter(Boolean).map((entry) => loadVersionDetail(entry.version.id)),
    );
    const entries = [...state.selectedRunIds].map((runId) => runEntry(runId)).filter(Boolean);
    if (entries.length !== 2) throw new Error('comparison selection is no longer available');
    cleanupMedia(elements.comparisonContent);
    renderComparison(entries);
    openDialog(elements.comparisonDialog, elements.comparisonClose);
  } catch (error) {
    showToast(errorMessage(error, 'No pudimos preparar esta comparación.'));
  } finally {
    elements.comparisonOpen.disabled = state.selectedRunIds.size !== 2;
  }
}

function setFavoriteState(runId) {
  for (const version of state.versions) {
    version.attempts = (version.attempts || []).map((run) => ({
      ...run,
      is_favorite: run.id === runId,
    }));
    const detail = state.detailsByVersion.get(version.id);
    if (detail) {
      state.detailsByVersion.set(version.id, {
        ...detail,
        attempts: (detail.attempts || []).map((run) => ({
          ...run,
          is_favorite: run.id === runId,
        })),
      });
    }
  }
}

async function toggleFavorite(run) {
  if (!state.currentSession || state.favoritePending || run.state !== 'completed') return;
  const sessionId = state.currentSession.id;
  const epoch = state.sessionEpoch;
  const previousFavorite = favoriteRunId();
  const clearing = run.is_favorite;
  state.favoritePending = true;
  setFavoriteState(clearing ? null : run.id);
  renderHistory();
  try {
    await apiJson(`/api/mvp/sessions/${sessionId}/favorite-run`, clearing
      ? { method: 'DELETE' }
      : { method: 'PUT', body: { run_id: run.id } });
    if (epoch !== state.sessionEpoch || state.currentSession?.id !== sessionId) return;
    showToast(clearing
      ? 'La sesión ya no tiene una versión favorita.'
      : 'Marcaste esta ejecución como tu favorita.');
  } catch (error) {
    if (epoch !== state.sessionEpoch || state.currentSession?.id !== sessionId) return;
    setFavoriteState(previousFavorite);
    showToast(errorMessage(error, 'No pudimos guardar tu favorita; restauramos la selección anterior.'));
  } finally {
    if (epoch !== state.sessionEpoch || state.currentSession?.id !== sessionId) return;
    state.favoritePending = false;
    renderHistory();
  }
}

async function rerunVersion(version, run, { useQualityFeedback = false } = {}) {
  if (
    !state.currentSession?.capabilities?.retry_ux_enabled
    || state.retryPendingIds.has(run.id)
    || !run.outcome?.retry?.supported
    || (useQualityFeedback && !run.outcome?.retry?.quality_feedback_supported)
  ) return;
  const sessionId = state.currentSession.id;
  const epoch = state.sessionEpoch;
  state.retryPendingIds.add(run.id);
  renderHistory();
  try {
    let detail = state.detailsByVersion.get(version.id) || null;
    if (useQualityFeedback) {
      detail = await loadVersionDetail(version.id);
      const prior = (detail?.attempts || []).find((item) => item.id === run.id) || run;
      if (!prior.outcome?.retry?.quality_feedback_supported) {
        showToast('La evidencia objetiva de este intento ya no está disponible para reparar.');
        return;
      }
    }
    const nextRun = await apiJson(
      `/api/mvp/prompt-versions/${version.id}/runs`,
      {
        method: 'POST',
        body: useQualityFeedback
          ? { prior_attempt_id: run.id, use_quality_feedback: true }
          : { use_quality_feedback: false },
      },
    );
    if (epoch !== state.sessionEpoch || state.currentSession?.id !== sessionId) return;
    version.attempts = [
      nextRun,
      ...(version.attempts || []).filter((item) => item.id !== nextRun.id),
    ];
    if (detail) {
      state.detailsByVersion.set(version.id, {
        ...detail,
        attempts: [
          nextRun,
          ...(detail.attempts || []).filter((item) => item.id !== nextRun.id),
        ],
      });
    }
    await selectRun(nextRun.id, { startedAt: nextRun.created_at });
    showToast(useQualityFeedback
      ? `Intento ${nextRun.attempt_number} creado con la evidencia objetiva anterior.`
      : `Intento ${nextRun.attempt_number} creado con la misma instrucción y fuente.`);
  } catch (error) {
    if (epoch === state.sessionEpoch && state.currentSession?.id === sessionId) {
      showToast(errorMessage(
        error,
        useQualityFeedback
          ? 'No pudimos preparar la reparación con evidencia.'
          : 'No pudimos volver a ejecutar esta versión.',
      ));
    }
  } finally {
    state.retryPendingIds.delete(run.id);
    if (epoch === state.sessionEpoch && state.currentSession?.id === sessionId) renderHistory();
  }
}

function createImprovedVersion(version, run) {
  if (!state.currentSession?.capabilities?.retry_ux_enabled) return;
  const issues = [
    ...(run.outcome?.limitation_codes || []),
    ...(run.outcome?.fatal_error_codes || []),
    ...(run.outcome?.limitations || []).map((item) => item.code),
    ...(run.outcome?.fatal_errors || []).map((item) => item.code),
  ].filter(Boolean);
  const uniqueIssues = [...new Set(issues)].slice(0, 12);
  const guidance = uniqueIssues.length
    ? `\n\nCrea una versión mejorada que conserve la intención original y resuelva estos defectos verificados:\n${uniqueIssues.map((code) => `- ${code}`).join('\n')}`
    : '\n\nCrea una versión mejorada que conserve la intención original y corrija los defectos de la ejecución anterior.';
  elements.prompt.value = `${version.prompt}${guidance}`.slice(0, 12000);
  elements.promptCount.textContent = `${elements.prompt.value.length.toLocaleString('es')} / 12.000`;
  const settings = version.settings || {};
  elements.maxClips.value = String(settings.max_clips || 8);
  elements.assetPolicy.value = settings.asset_policy || 'auto';
  elements.maxGeneratedAssets.value = String(settings.max_generated_assets_per_clip ?? 2);
  elements.stockPolicy.value = settings.stock_policy || 'off';
  elements.maxStockAssets.value = String(settings.max_stock_assets_per_clip ?? 0);
  syncAdvancedSettings();
  elements.jobForm.scrollIntoView({ behavior: 'smooth', block: 'start' });
  window.requestAnimationFrame(() => elements.prompt.focus());
  showToast('Preparamos una nueva versión editable con los defectos verificados.');
}

async function selectRun(jobId, { startedAt, reset = true } = {}) {
  const runEpoch = ++state.runEpoch;
  state.activity?.stop();
  state.activity = null;
  if (reset) resetActivity();
  try {
    const job = await apiJson(`/api/mvp/jobs/${jobId}`);
    if (runEpoch !== state.runEpoch) return;
    state.currentJob = job;
    mergeDetailedRun(job);
    renderJob(job);
    renderHistory();
    const feed = new ActivityFeed(job.id, {
      onConnection: (connection) => {
        if (runEpoch === state.runEpoch) renderConnection(connection);
      },
      onEvent: (event) => {
        if (runEpoch === state.runEpoch) appendActivity(event);
      },
      onElapsed: (milliseconds) => {
        if (runEpoch === state.runEpoch) {
          elements.elapsedTime.textContent = formatElapsed(milliseconds);
        }
      },
      onJob: (nextJob) => {
        if (runEpoch !== state.runEpoch) return;
        state.currentJob = nextJob;
        mergeDetailedRun(nextJob);
        renderJob(nextJob);
      },
      onTerminal: async () => {
        if (runEpoch !== state.runEpoch) return;
        try {
          const terminalJob = await apiJson(`/api/mvp/jobs/${job.id}`);
          if (runEpoch !== state.runEpoch) return;
          state.currentJob = terminalJob;
          mergeDetailedRun(state.currentJob);
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
    if (runEpoch !== state.runEpoch) return;
    elements.jobError.textContent = errorMessage(error);
  }
}

function createUploader(file) {
  if (!state.currentSession) return null;
  const sessionId = state.currentSession.id;
  const uploader = new ResumableUpload(sessionId, {
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
      if (state.currentSession?.id !== sessionId) return;
      state.upload = null;
      state.source = await apiJson(
        `/api/mvp/sessions/${sessionId}/input-video`,
      );
      if (state.currentSession?.id !== sessionId) return;
      renderSource(state.source);
      showToast('La carga se descartó. Puedes elegir un video de nuevo.');
    },
    onReady: async (source) => {
      if (state.currentSession?.id !== sessionId) return;
      state.source = source;
      state.upload = null;
      renderSource(source);
      setComposerAvailability(true);
      state.sessions = state.sessions.map((session) =>
        session.id === sessionId
          ? { ...session, input_video: source }
          : session,
      );
      renderSessions(state.sessions, sessionId, selectSession);
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
  elements.maxGeneratedAssets.disabled = elements.assetPolicy.value === 'off';
  elements.maxStockAssets.disabled = elements.stockPolicy.value === 'off';
  if (elements.assetPolicy.value === 'auto' && elements.maxGeneratedAssets.value === '0') {
    elements.maxGeneratedAssets.value = '2';
  }
  if (elements.assetPolicy.value === 'required' && elements.maxGeneratedAssets.value === '0') {
    elements.maxGeneratedAssets.value = '1';
  }
  if (elements.stockPolicy.value === 'required' && elements.maxStockAssets.value === '0') {
    elements.maxStockAssets.value = '1';
  }
}

function promptPayload() {
  return {
    prompt: elements.prompt.value,
    max_clips: Number(elements.maxClips.value),
    asset_policy: elements.assetPolicy.value,
    max_generated_assets_per_clip: elements.assetPolicy.value !== 'off'
      ? Number(elements.maxGeneratedAssets.value)
      : 0,
    stock_policy: elements.stockPolicy.value,
    max_stock_assets_per_clip: elements.stockPolicy.value !== 'off'
      ? Number(elements.maxStockAssets.value)
      : 0,
    stock_asset_kind: 'video',
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
    if (!result.prompt_version.attempts?.length) {
      result.prompt_version.attempts = [result.run];
    }
    renderHistory();
    await selectRun(result.run.id, { startedAt: result.run.created_at });
    showToast(`Versión ${result.prompt_version.version_number} enviada a edición.`);
  } catch (error) {
    elements.jobError.textContent = errorMessage(error);
  } finally {
    setComposerAvailability(state.source.state === 'ready');
  }
});

elements.activityRetry.addEventListener('click', () => state.activity?.retryNow());
elements.historyLoadMore.addEventListener('click', loadMoreVersions);
elements.comparisonOpen.addEventListener('click', openComparison);
elements.comparisonClear.addEventListener('click', () => {
  state.selectedRunIds.clear();
  renderHistory();
});
elements.comparisonClose.addEventListener('click', () => closeDialog(elements.comparisonDialog));
elements.comparisonDialog.addEventListener('close', () => {
  cleanupMedia(elements.comparisonContent);
  elements.comparisonContent.replaceChildren();
  const target = state.comparisonReturnFocus;
  state.comparisonReturnFocus = null;
  window.requestAnimationFrame(() => target?.focus?.());
});

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
