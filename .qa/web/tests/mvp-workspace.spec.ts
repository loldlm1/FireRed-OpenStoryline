import { test, expect, type Page, type Route } from '@playwright/test';

const sessionId = 'a'.repeat(32);
const uploadId = 'b'.repeat(32);
const promptVersionId = 'c'.repeat(32);
const jobId = 'd'.repeat(32);
const secondPromptVersionId = 'e'.repeat(32);
const secondJobId = 'f'.repeat(32);
const now = '2026-07-19T12:00:00+00:00';

type SourceState = {
  id: string | null;
  upload_id: string | null;
  editing_session_id: string;
  state: string;
  original_filename?: string;
  expected_size?: number;
  received_bytes: number;
  upload_offset: number;
  media_type?: string;
  completed_at?: string | null;
};

const missingSource = (): SourceState => ({
  id: null,
  upload_id: null,
  editing_session_id: sessionId,
  state: 'missing',
  received_bytes: 0,
  upload_offset: 0,
});

function session(source: SourceState) {
  return {
    id: sessionId,
    title: 'Entrevista editorial de julio',
    workflow_version: 2,
    capabilities: { retry_ux_enabled: true },
    input_video: source.state === 'missing' ? null : source,
    jobs: [],
    created_at: now,
    updated_at: now,
    deleted_at: null,
  };
}

function outcome(grade: 'enhanced' | 'with_limitations' | 'retryable_failure' | 'terminal_failure' = 'enhanced') {
  const limited = grade === 'with_limitations';
  const failed = grade === 'retryable_failure' || grade === 'terminal_failure';
  return {
    version: 'outcome_report.v2',
    registry_version: 'defect_registry.v1',
    grade,
    technical_status: grade === 'retryable_failure' ? 'blocked' : 'pass',
    output_count: failed ? 0 : 1,
    limitation_codes: limited ? ['ACTIVE_PICTURE_TOO_SMALL'] : [],
    fatal_error_codes: grade === 'retryable_failure' ? ['AUDIO_MISSING'] : [],
    limitations: limited ? [{
      code: 'ACTIVE_PICTURE_TOO_SMALL',
      stage: 'qa',
      requested: 'crop',
      executed: 'fit',
      description: 'Se preservó todo el contenido con un encuadre seguro.',
      retryable: true,
      recommended_retry_action: 'retry_defects',
    }] : [],
    fatal_errors: grade === 'retryable_failure' ? [{
      code: 'AUDIO_MISSING',
      stage: 'qa',
      retryable: true,
    }] : [],
    strict_qa: {
      decision: limited || failed ? 'block' : 'promote',
      blocker_codes: limited ? ['ACTIVE_PICTURE_TOO_SMALL'] : [],
    },
    delivery: {
      policy: limited ? 'technical_pass_guaranteed' : 'qa_enforced',
      decision: limited ? 'publish_with_limitations' : failed ? 'withhold_technical' : 'publish_enhanced',
      download_available: !failed,
    },
    repair: {
      report_version: limited ? 'repair_report.v1' : '',
      registry_version: 'defect_registry.v1',
      mode: limited ? 'enforce' : 'off',
      stages: limited ? [{ stage: 'plan_repair', status: 'rejected', checkpoint_reused: true }] : [],
      resolved_codes: limited ? ['CAPTION_WIDTH_EXCEEDED'] : [],
      remaining_codes: limited ? ['ACTIVE_PICTURE_TOO_SMALL'] : [],
      introduced_codes: [],
      fallback_applied_codes: limited ? ['VISUAL_REFRAME_FALLBACK'] : [],
      not_repairable_codes: [],
      defects: limited ? [{
        code: 'ACTIVE_PICTURE_TOO_SMALL',
        strategy: 'conditional_llm_or_fallback',
        eligible: true,
        repair_attempted: true,
        dispositions: ['remaining', 'fallback_applied'],
        stage_statuses: [{ stage: 'plan_repair', status: 'rejected', checkpoint_reused: true }],
        fallbacks: [{ requested: 'crop', executed: 'fit' }],
        presentation: {
          raw_code: 'ACTIVE_PICTURE_TOO_SMALL',
          retry_action: 'retry_defects',
          es: {
            title: 'La imagen activa es demasiado pequeña',
            description: 'El contenido visible quedó por debajo del umbral seguro.',
          },
        },
      }] : [],
    },
    retry: {
      supported: limited || grade === 'retryable_failure',
      quality_feedback_supported: limited || grade === 'retryable_failure',
      recommended_action: limited || grade === 'retryable_failure' ? 'retry_defects' : 'none',
      reused_stage_names: limited ? ['transcript', 'global_analysis'] : [],
      recomputed_stage_names: limited ? ['edit_plan', 'render'] : [],
      resolved_limitation_codes: limited ? ['CAPTION_WIDTH_EXCEEDED'] : [],
      remaining_limitation_codes: limited ? ['ACTIVE_PICTURE_TOO_SMALL'] : [],
      new_limitation_codes: [],
    },
  };
}

function run(state = 'running') {
  return {
    id: jobId,
    attempt_number: 1,
    state,
    stage: state === 'completed' ? 'completed' : 'remote_transcription',
    progress: state === 'completed' ? 1 : 0.28,
    is_favorite: false,
    error_code: null,
    outcome: state === 'completed' ? outcome() : null,
    created_at: now,
    completed_at: state === 'completed' ? now : null,
    media_expires_at: null,
  };
}

function fullJob(state = 'running') {
  return {
    ...run(state),
    editing_session_id: sessionId,
    prompt_version_id: promptVersionId,
    prompt: 'Encuentra los tres momentos con mayor claridad narrativa.',
    request: {},
    input: { source_kind: 'session_input_video' },
    error: null,
    artifacts: state === 'completed'
      ? [{
          name: 'short-01.mp4',
          kind: 'clip',
          size: 4096,
          availability: 'available',
          retention_expires_at: null,
          purged_at: null,
          purge_reason: null,
        }]
      : [],
    started_at: now,
    updated_at: now,
  };
}

function promptVersion(state = 'running') {
  return {
    id: promptVersionId,
    editing_session_id: sessionId,
    version_number: 1,
    prompt: 'Encuentra los tres momentos con mayor claridad narrativa.',
    settings: { settings_version: 1, edit_mode: 'agentic', max_clips: 8 },
    created_at: now,
    attempts: [run(state)],
  };
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

async function installWorkspaceApi(page: Page, options: {
  initialSource?: SourceState;
  streamMode?: 'success' | 'fallback';
} = {}) {
  let source = options.initialSource || missingSource();
  let versionCreated = false;
  let jobState = 'running';
  let streamRequests = 0;
  const chunkOffsets: number[] = [];

  await page.route('**/api/mvp/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (path === '/api/mvp/auth/session') return json(route, { authenticated: true });
    if (path === '/api/mvp/auth/logout') return json(route, { authenticated: false });
    if (path === '/api/mvp/sessions' && method === 'GET') {
      return json(route, { items: [session(source)], next_cursor: null });
    }
    if (path === `/api/mvp/sessions/${sessionId}` && method === 'GET') {
      return json(route, session(source));
    }
    if (path === `/api/mvp/sessions/${sessionId}/input-video` && method === 'GET') {
      return json(route, source);
    }
    if (path === `/api/mvp/sessions/${sessionId}/input-video/content`) {
      return route.fulfill({ status: 200, contentType: 'video/mp4', body: Buffer.from('mock-video') });
    }
    if (path === `/api/mvp/sessions/${sessionId}/input-video/uploads` && method === 'POST') {
      const payload = request.postDataJSON();
      if (source.state === 'uploading' && (
        payload.original_filename !== source.original_filename
        || payload.expected_size !== source.expected_size
      )) {
        return json(route, {
          detail: {
            code: 'UPLOAD_METADATA_CONFLICT',
            message: 'metadata conflict',
            details: { upload_offset: source.received_bytes },
          },
        }, 409);
      }
      source = {
        id: uploadId,
        upload_id: uploadId,
        editing_session_id: sessionId,
        state: 'uploading',
        original_filename: payload.original_filename,
        expected_size: payload.expected_size,
        received_bytes: source.state === 'uploading' ? source.received_bytes : 0,
        upload_offset: source.state === 'uploading' ? source.upload_offset : 0,
        media_type: payload.media_type || 'video/mp4',
      };
      return json(route, source, 201);
    }
    if (path === `/api/mvp/sessions/${sessionId}/input-video/uploads/${uploadId}` && method === 'PATCH') {
      const offset = Number(request.headers()['upload-offset'] || 0);
      chunkOffsets.push(offset);
      await new Promise((resolve) => setTimeout(resolve, 70));
      source.received_bytes = offset + request.postDataBuffer()!.byteLength;
      source.upload_offset = source.received_bytes;
      return json(route, source);
    }
    if (path === `/api/mvp/sessions/${sessionId}/input-video/uploads/${uploadId}/complete`) {
      source = {
        ...source,
        state: 'ready',
        received_bytes: source.expected_size || 0,
        upload_offset: source.expected_size || 0,
        completed_at: now,
      };
      return json(route, source);
    }
    if (path === `/api/mvp/sessions/${sessionId}/input-video/uploads/${uploadId}` && method === 'DELETE') {
      source = missingSource();
      return json(route, { ...source, failure_code: 'UPLOAD_CANCELLED' });
    }
    if (path === `/api/mvp/sessions/${sessionId}/prompt-versions` && method === 'GET') {
      return json(route, {
        items: versionCreated ? [promptVersion(jobState)] : [],
        next_cursor: null,
      });
    }
    if (path === `/api/mvp/sessions/${sessionId}/prompt-versions` && method === 'POST') {
      versionCreated = true;
      jobState = 'running';
      return json(route, { prompt_version: promptVersion(), run: run() }, 202);
    }
    if (path === `/api/mvp/jobs/${jobId}/events` && method === 'GET') {
      if (options.streamMode === 'fallback' && streamRequests > 3) {
        jobState = 'completed';
        return json(route, {
          items: [{
            schema_version: 1,
            sequence: 3,
            category: 'system',
            status: 'completed',
            message_key: 'activity.system.completed',
            progress: 1,
            clip_count: 1,
          }],
          next_cursor: null,
        });
      }
      return json(route, { items: [], next_cursor: null });
    }
    if (path === `/api/mvp/jobs/${jobId}/events/stream`) {
      streamRequests += 1;
      if (options.streamMode === 'fallback') {
        return route.fulfill({
          status: 200,
          contentType: 'text/event-stream',
          body: 'event: stream_error\ndata: {"code":"ACTIVITY_STREAM_UNAVAILABLE","retryable":true}\n\n',
        });
      }
      jobState = 'completed';
      const events = [
        {
          schema_version: 1,
          sequence: 1,
          category: 'provider',
          status: 'progress',
          message_key: 'activity.provider.transcribing',
          progress: 0.28,
          provider: 'Mistral',
          tool: 'Voxtral',
        },
        {
          schema_version: 1,
          sequence: 2,
          category: 'render',
          status: 'progress',
          message_key: 'activity.render.rendering_clip',
          progress: 0.82,
          current: 1,
          total: 1,
          tool: 'FFmpeg',
        },
        {
          schema_version: 1,
          sequence: 3,
          category: 'system',
          status: 'completed',
          message_key: 'activity.system.completed',
          progress: 1,
          clip_count: 1,
        },
      ];
      return route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: events.map((event) => (
          `id: ${event.sequence}\nevent: activity\ndata: ${JSON.stringify(event)}\n\n`
        )).join(''),
      });
    }
    if (path === `/api/mvp/jobs/${jobId}`) return json(route, fullJob(jobState));
    if (path === `/api/mvp/jobs/${jobId}/bundle`) {
      return route.fulfill({ status: 200, contentType: 'application/zip', body: Buffer.from('zip') });
    }
    return json(route, { detail: { code: 'MOCK_ROUTE_MISSING', message: `${method} ${path}` } }, 404);
  });

  return {
    chunkOffsets,
    get source() { return source; },
    get streamRequests() { return streamRequests; },
  };
}

type HistoryRun = ReturnType<typeof fullJob>;
type HistoryVersion = ReturnType<typeof promptVersion>;

function historyRun(id: string, versionId: string, attempt: number, options: {
  favorite?: boolean;
  available?: boolean;
  state?: string;
  grade?: 'enhanced' | 'with_limitations' | 'retryable_failure' | 'terminal_failure';
} = {}): HistoryRun {
  const state = options.state || 'completed';
  return {
    ...fullJob(state),
    id,
    prompt_version_id: versionId,
    attempt_number: attempt,
    is_favorite: Boolean(options.favorite),
    outcome: outcome(options.grade || (state === 'failed' ? 'retryable_failure' : 'enhanced')),
    artifacts: [{
      name: `${id.slice(0, 4)}-clip.mp4`,
      kind: 'clip',
      size: 8192,
      availability: options.available === false ? 'missing' : 'available',
      retention_expires_at: null,
      purged_at: options.available === false ? now : null,
      purge_reason: options.available === false ? 'retention_expired' : null,
    }, {
      name: `${id.slice(0, 4)}-qa.json`,
      kind: 'render_qa',
      size: 512,
      availability: 'available',
      retention_expires_at: null,
      purged_at: null,
      purge_reason: null,
    }],
    input: { source_kind: 'session_input_video', sha256: `${id}abc123` },
    started_at: now,
    updated_at: '2026-07-19T12:01:15+00:00',
    completed_at: state === 'completed' ? '2026-07-19T12:01:15+00:00' : null,
  };
}

function historyVersion(index: number, options: {
  id?: string;
  runId?: string;
  favorite?: boolean;
  available?: boolean;
  prompt?: string;
} = {}): HistoryVersion {
  const id = options.id || index.toString(16).padStart(32, '0');
  const runId = options.runId || (index + 100).toString(16).padStart(32, '0');
  const run = historyRun(runId, id, 1, options);
  return {
    ...promptVersion('completed'),
    id,
    editing_session_id: sessionId,
    version_number: index,
    prompt: options.prompt || `Dirección editorial para la versión ${index}.`,
    created_at: `2026-07-${String(Math.max(1, 20 - index)).padStart(2, '0')}T12:00:00+00:00`,
    attempts: [run],
  };
}

async function installHistoryApi(page: Page, options: {
  legacy?: boolean;
  versions?: HistoryVersion[];
} = {}) {
  const ready: SourceState = {
    id: uploadId,
    upload_id: uploadId,
    editing_session_id: sessionId,
    state: 'ready',
    original_filename: 'fuente-reutilizable.mp4',
    expected_size: 42_000_000,
    received_bytes: 42_000_000,
    upload_offset: 42_000_000,
    media_type: 'video/mp4',
    completed_at: now,
  };
  const versions = options.versions || [
    historyVersion(2, { id: secondPromptVersionId, runId: secondJobId }),
    historyVersion(1, {
      id: promptVersionId,
      runId: jobId,
      prompt: 'Una instrucción deliberadamente larga. '.repeat(18),
    }),
  ];
  let favoriteId = versions.flatMap((version) => version.attempts).find((run) => run.is_favorite)?.id || null;
  let failNextFavorite = false;
  let lastRetryPayload: Record<string, unknown> | null = null;

  const syncFavorites = () => {
    for (const version of versions) {
      for (const run of version.attempts) run.is_favorite = run.id === favoriteId;
    }
  };
  const detailFor = (versionId: string) => versions.find((version) => version.id === versionId);
  const jobFor = (runId: string) => versions.flatMap((version) => version.attempts).find((run) => run.id === runId);

  await page.route('**/api/mvp/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (path === '/api/mvp/auth/session') return json(route, { authenticated: true });
    if (path === '/api/mvp/sessions' && method === 'GET') {
      const item = {
        ...session(ready),
        workflow_version: options.legacy ? 1 : 2,
        input_video: options.legacy ? null : ready,
      };
      return json(route, { items: [item], next_cursor: null });
    }
    if (path === `/api/mvp/sessions/${sessionId}` && method === 'GET') {
      if (options.legacy) {
        const legacyJob = historyRun(jobId, '', 1);
        legacyJob.prompt_version_id = null;
        legacyJob.attempt_number = null as unknown as number;
        legacyJob.prompt = 'Corte histórico de una carga anterior.';
        return json(route, {
          ...session(missingSource()),
          workflow_version: 1,
          jobs: [legacyJob],
          next_job_cursor: null,
        });
      }
      return json(route, { ...session(ready), jobs: [], next_job_cursor: null });
    }
    if (path === `/api/mvp/sessions/${sessionId}/input-video`) return json(route, ready);
    if (path === `/api/mvp/sessions/${sessionId}/input-video/content`) {
      return route.fulfill({ status: 200, contentType: 'video/mp4', body: Buffer.from('source-video') });
    }
    if (path === `/api/mvp/sessions/${sessionId}/prompt-versions` && method === 'GET') {
      syncFavorites();
      const cursor = url.searchParams.get('cursor');
      const items = cursor === 'older' ? versions.slice(20) : versions.slice(0, 20);
      return json(route, {
        items,
        next_cursor: !cursor && versions.length > 20 ? 'older' : null,
      });
    }
    const versionMatch = path.match(/^\/api\/mvp\/prompt-versions\/([a-f0-9]{32})$/);
    if (versionMatch) {
      syncFavorites();
      const version = detailFor(versionMatch[1]);
      return version ? json(route, version) : json(route, { detail: { code: 'PROMPT_VERSION_NOT_FOUND' } }, 404);
    }
    const rerunMatch = path.match(/^\/api\/mvp\/prompt-versions\/([a-f0-9]{32})\/runs$/);
    if (rerunMatch && method === 'POST') {
      const version = detailFor(rerunMatch[1]);
      if (!version) return json(route, { detail: { code: 'PROMPT_VERSION_NOT_FOUND' } }, 404);
      lastRetryPayload = request.postDataJSON();
      const next = historyRun('9'.repeat(32), version.id, version.attempts.length + 1, {
        state: 'queued',
        grade: 'enhanced',
      });
      next.outcome = null;
      next.artifacts = [];
      version.attempts.unshift(next);
      return json(route, next, 202);
    }
    const jobMatch = path.match(/^\/api\/mvp\/jobs\/([a-f0-9]{32})$/);
    if (jobMatch) {
      syncFavorites();
      const job = jobFor(jobMatch[1]);
      return job ? json(route, job) : json(route, { detail: { code: 'JOB_NOT_FOUND' } }, 404);
    }
    if (path.match(/^\/api\/mvp\/jobs\/[a-f0-9]{32}\/events$/)) {
      return json(route, { items: [], next_cursor: null });
    }
    if (path === `/api/mvp/sessions/${sessionId}/favorite-run` && method === 'PUT') {
      if (failNextFavorite) {
        failNextFavorite = false;
        return json(route, { detail: { code: 'DATABASE_UNAVAILABLE' } }, 503);
      }
      favoriteId = request.postDataJSON().run_id;
      syncFavorites();
      return json(route, { editing_session_id: sessionId, favorite_run_id: favoriteId, selection_source: 'human' });
    }
    if (path === `/api/mvp/sessions/${sessionId}/favorite-run` && method === 'DELETE') {
      if (failNextFavorite) {
        failNextFavorite = false;
        return json(route, { detail: { code: 'DATABASE_UNAVAILABLE' } }, 503);
      }
      favoriteId = null;
      syncFavorites();
      return json(route, { editing_session_id: sessionId, favorite_run_id: null, selection_source: 'human' });
    }
    const previewMatch = path.match(/^\/api\/mvp\/jobs\/([a-f0-9]{32})\/artifacts\/([^/]+)\/preview$/);
    if (previewMatch) {
      const artifact = jobFor(previewMatch[1])?.artifacts.find((item) => item.name === decodeURIComponent(previewMatch[2]));
      if (!artifact || artifact.availability !== 'available') {
        return json(route, { detail: { code: 'ARTIFACT_NOT_AVAILABLE' } }, 404);
      }
      return route.fulfill({
        status: request.headers().range ? 206 : 200,
        contentType: 'video/mp4',
        headers: request.headers().range ? { 'Content-Range': 'bytes 0-9/10', 'Accept-Ranges': 'bytes' } : {},
        body: Buffer.from('mock-video'),
      });
    }
    if (path.match(/^\/api\/mvp\/jobs\/[a-f0-9]{32}\/artifacts\/[^/]+$/)) {
      return route.fulfill({ status: 200, contentType: 'application/octet-stream', body: Buffer.from('artifact') });
    }
    return json(route, { detail: { code: 'MOCK_ROUTE_MISSING', message: `${method} ${path}` } }, 404);
  });

  return {
    failNextFavorite() { failNextFavorite = true; },
    get favoriteId() { return favoriteId; },
    get lastRetryPayload() { return lastRetryPayload; },
  };
}

test.describe('reusable video workspace', () => {
  test('uploads once with monotonic percentage, creates a version, and streams activity', async ({ page }) => {
    const mock = await installWorkspaceApi(page);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.addInitScript(() => {
      (window as Window & { uploadPercentages?: number[] }).uploadPercentages = [];
      document.addEventListener('DOMContentLoaded', () => {
        const value = document.querySelector('#upload-percent');
        if (!value) return;
        new MutationObserver(() => {
          const numeric = Number(value.textContent?.replace('%', '') || 0);
          (window as Window & { uploadPercentages: number[] }).uploadPercentages.push(numeric);
        }).observe(value, { childList: true, subtree: true, characterData: true });
      });
    });

    await page.goto(`/?session=${sessionId}`);
    await expect(page.locator('#app-view')).toBeVisible();
    await expect(page.locator('#workspace-title')).toHaveText('Entrevista editorial de julio');
    await expect(page.locator('#submit')).toBeDisabled();

    await page.locator('#video').setInputFiles({
      name: 'entrevista.mp4',
      mimeType: 'video/mp4',
      buffer: Buffer.alloc(9 * 1024 * 1024, 7),
    });
    await expect(page.locator('#upload-percent')).toHaveText('100%', { timeout: 10_000 });
    await expect(page.locator('#source-state')).toHaveText('Fuente lista');
    await expect(page.locator('#source-ready')).toBeVisible();
    await expect(page.locator('#source-missing')).toBeHidden();
    await expect(page.locator('label[for="video"]')).toBeHidden();
    await expect(page.locator('#submit')).toBeEnabled();
    expect(mock.chunkOffsets).toEqual([0, 8 * 1024 * 1024]);

    const percentages = await page.evaluate(() => (
      (window as Window & { uploadPercentages: number[] }).uploadPercentages
    ));
    expect(percentages.length).toBeGreaterThan(0);
    expect(percentages.every((value, index) => index === 0 || value >= percentages[index - 1])).toBe(true);

    await page.locator('#prompt').fill('Encuentra los tres momentos con mayor claridad narrativa.');
    await page.locator('#submit').click();
    await expect(page.locator('#activity-list .activity-item')).toHaveCount(3, { timeout: 10_000 });
    await expect(page.locator('#connection-state')).toHaveText('Completado');
    await expect(page.locator('#status')).toHaveText('La nueva versión está lista.');
    await expect(page.locator('#activity-percent')).toHaveText('100%');
    await expect(page.locator('#activity-list')).toContainText('Transcribiendo el contenido hablado');
    await expect(page.locator('#activity-list')).toContainText('Renderizando un clip');
    await expect(page.locator('#artifacts')).toContainText('short-01.mp4');
    await expect(page.locator('#recent-jobs .version-card')).toHaveCount(1);

    expect(await page.evaluate(() => ({
      local: Object.keys(localStorage),
      session: Object.keys(sessionStorage),
    }))).toEqual({ local: [], session: [] });
    const transitionDuration = await page.locator('#submit').evaluate((node) => (
      Number.parseFloat(getComputedStyle(node).transitionDuration)
    ));
    expect(transitionDuration).toBeLessThanOrEqual(0.001);

    await page.reload();
    await expect(page.locator('#workspace-title')).toHaveText('Entrevista editorial de julio');
    await expect(page.locator('#source-state')).toHaveText('Fuente lista');
    await expect(page.locator('#recent-jobs .version-card')).toHaveCount(1);
  });

  test('requires matching metadata to resume from the server offset and can discard a partial upload', async ({ page }) => {
    const partial: SourceState = {
      id: uploadId,
      upload_id: uploadId,
      editing_session_id: sessionId,
      state: 'uploading',
      original_filename: 'continuar.mp4',
      expected_size: 12,
      received_bytes: 4,
      upload_offset: 4,
      media_type: 'video/mp4',
    };
    const mock = await installWorkspaceApi(page, { initialSource: partial });
    await page.goto(`/?session=${sessionId}`);

    await expect(page.locator('#source-resume')).toBeVisible();
    await expect(page.locator('#resume-copy')).toContainText('33% confirmado');
    await page.locator('#video-resume').setInputFiles({
      name: 'otro.mp4',
      mimeType: 'video/mp4',
      buffer: Buffer.alloc(12, 1),
    });
    await expect(page.locator('#upload-error')).toContainText('exactamente el mismo archivo');

    await page.locator('#video-resume').setInputFiles({
      name: 'continuar.mp4',
      mimeType: 'video/mp4',
      buffer: Buffer.alloc(12, 1),
    });
    await expect(page.locator('#source-state')).toHaveText('Fuente lista');
    expect(mock.chunkOffsets).toEqual([4]);

    await page.reload();
    await expect(page.locator('#source-ready')).toBeVisible();
    expect(await page.evaluate(() => ({
      local: Object.keys(localStorage),
      session: Object.keys(sessionStorage),
    }))).toEqual({ local: [], session: [] });
  });

  test('cancels an incomplete upload and falls back to polling after stream failures', async ({ page }) => {
    const partial: SourceState = {
      id: uploadId,
      upload_id: uploadId,
      editing_session_id: sessionId,
      state: 'uploading',
      original_filename: 'parcial.mp4',
      expected_size: 20,
      received_bytes: 5,
      upload_offset: 5,
      media_type: 'video/mp4',
    };
    const mock = await installWorkspaceApi(page, { initialSource: partial, streamMode: 'fallback' });
    await page.goto(`/?session=${sessionId}`);
    await page.locator('#resume-cancel').click();
    await expect(page.locator('#source-missing')).toBeVisible();
    await expect(page.locator('#source-state')).toHaveText('Pendiente');

    await page.evaluate(() => {
      const state = document.querySelector('#connection-state');
      (window as Window & { connectionStates?: string[] }).connectionStates = [];
      if (state) {
        new MutationObserver(() => {
          (window as Window & { connectionStates: string[] }).connectionStates.push(state.textContent || '');
        }).observe(state, { childList: true, subtree: true, characterData: true });
      }
    });
    await page.locator('#video').setInputFiles({
      name: 'nuevo.mp4',
      mimeType: 'video/mp4',
      buffer: Buffer.alloc(12, 2),
    });
    await expect(page.locator('#source-state')).toHaveText('Fuente lista');
    await page.locator('#prompt').fill('Crea un corte breve para probar la recuperación de actividad.');
    await page.locator('#submit').click();
    await expect(page.locator('#connection-state')).toHaveText('Completado', { timeout: 15_000 });
    expect(mock.streamRequests).toBeGreaterThan(3);
    expect(await page.evaluate(() => (
      (window as Window & { connectionStates: string[] }).connectionStates
    ))).toContain('Consulta periódica');

    await page.setViewportSize({ width: 720, height: 900 });
    await expect(page.locator('#session-new')).toBeVisible();
    await page.locator('#session-new').focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('#session-dialog')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('#session-dialog')).toBeHidden();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(720);
  });

  test('paginates long history, lazily previews outputs, and compares exactly two runs with focus return', async ({ page }) => {
    const versions = Array.from({ length: 22 }, (_, offset) => historyVersion(22 - offset, {
      prompt: offset === 0 ? 'Una instrucción muy extensa. '.repeat(30) : undefined,
      available: offset !== 1,
    }));
    await installHistoryApi(page, { versions });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(`/?session=${sessionId}`);

    await expect(page.locator('#recent-jobs .version-card')).toHaveCount(20);
    await expect(page.locator('#history-load-more')).toBeVisible();
    await expect(page.locator('#recent-jobs video[data-managed-preview]')).toHaveCount(0);
    await page.locator('#history-load-more').click();
    await expect(page.locator('#recent-jobs .version-card')).toHaveCount(22);
    await expect(page.locator('#history-load-more')).toBeHidden();

    const firstCard = page.locator('#recent-jobs .version-card').first();
    await firstCard.getByRole('button', { name: 'Leer instrucción completa' }).click();
    await expect(firstCard.locator('.version-prompt')).toHaveAttribute('aria-expanded', 'true');
    await firstCard.getByRole('button', { name: 'Ver salidas y QA' }).click();
    await expect(firstCard.locator('.run-detail')).toBeVisible();
    await expect(firstCard.locator('video[data-managed-preview]')).toHaveCount(0);
    await firstCard.getByRole('button', { name: 'Cargar vista previa' }).click();
    await expect(firstCard.locator('video[preload="metadata"]')).toHaveCount(1);

    const compareChecks = page.locator('#recent-jobs .compare-check input');
    await compareChecks.nth(0).check();
    await compareChecks.nth(1).check();
    await expect(compareChecks.nth(2)).toBeDisabled();
    await expect(page.locator('#comparison-count')).toHaveText('2 de 2 seleccionadas');
    await page.locator('#comparison-open').focus();
    await page.locator('#comparison-open').click();
    await expect(page.locator('#comparison-dialog')).toBeVisible();
    await expect(page.locator('#comparison-content .comparison-column')).toHaveCount(2);
    await expect(page.locator('#comparison-dialog')).toContainText('OpenStoryline no asigna una puntuación');
    await page.locator('#comparison-close').click();
    await expect(page.locator('#comparison-dialog')).toBeHidden();
    await expect(page.locator('#comparison-open')).toBeFocused();
  });

  test('explains limited outcomes, prefills an improved version, and retries with prior evidence', async ({ page }) => {
    const limited = historyVersion(2, {
      id: secondPromptVersionId,
      runId: secondJobId,
    });
    limited.attempts[0] = historyRun(secondJobId, secondPromptVersionId, 1, {
      grade: 'with_limitations',
    });
    const enhanced = historyVersion(1, { id: promptVersionId, runId: jobId });
    const mock = await installHistoryApi(page, { versions: [limited, enhanced] });
    await page.goto(`/?session=${sessionId}`);

    const card = page.locator('#recent-jobs .version-card').first();
    await expect(card.locator('.outcome-badge')).toHaveText('! Completado con limitaciones');
    await card.getByRole('button', { name: 'Ver salidas y QA' }).click();
    await expect(card.locator('.outcome-detail')).toContainText('ACTIVE_PICTURE_TOO_SMALL');
    await expect(card.locator('.outcome-detail')).toContainText('Etapas reutilizadas');
    await expect(card.locator('.outcome-detail')).toContainText('QA estricta: bloqueada');
    await expect(card.locator('.outcome-detail')).toContainText('Entrega: publicada con limitaciones');
    await card.locator('.limitation-disclosure summary').click();
    await expect(card.locator('.limitation-disclosure')).toContainText('Se ejecutó fit en lugar de crop');
    await card.locator('.repair-disclosure summary').click();
    await expect(card.locator('.repair-disclosure')).toContainText('La imagen activa es demasiado pequeña');
    await expect(card.locator('.repair-disclosure')).toContainText('Reparación LLM intentada');
    await expect(card.locator('.repair-disclosure')).toContainText('checkpoint reutilizado');
    await expect(card.locator('.repair-disclosure')).toContainText('no garantiza calidad subjetiva');

    await card.getByRole('button', { name: 'Crear versión mejorada' }).click();
    await expect(page.locator('#prompt')).toHaveValue(/ACTIVE_PICTURE_TOO_SMALL/);
    await expect(page.locator('#prompt')).toBeFocused();

    const compareChecks = page.locator('#recent-jobs .compare-check input');
    await compareChecks.nth(0).check();
    await compareChecks.nth(1).check();
    await page.locator('#comparison-open').click();
    await expect(page.locator('#comparison-dialog')).toContainText('Resueltas: CAPTION_WIDTH_EXCEEDED');
    await page.keyboard.press('Escape');

    await card.getByRole('button', { name: 'Reintentar defectos' }).click();
    await expect(card.locator('.attempt-row')).toHaveCount(2);
    expect(mock.lastRetryPayload).toEqual({
      prior_attempt_id: secondJobId,
      use_quality_feedback: true,
    });
  });

  test('persists a human favorite, clears it, and rolls optimistic state back after failure', async ({ page }) => {
    const mock = await installHistoryApi(page);
    await page.goto(`/?session=${sessionId}`);
    const cards = page.locator('#recent-jobs .version-card');
    const firstFavorite = cards.nth(0).getByRole('button', { name: 'Elegir favorita' });

    await firstFavorite.click();
    await expect(cards.nth(0)).toContainText('Tu favorita');
    await expect(page.locator('#session-summary')).toContainText('es tu elección favorita');
    expect(mock.favoriteId).toBe(secondJobId);

    await page.reload();
    await expect(page.locator('#recent-jobs .version-card').nth(0)).toContainText('Tu favorita');
    await page.locator('#recent-jobs .version-card').nth(0).getByRole('button', { name: 'Quitar favorita' }).click();
    await expect(page.locator('#recent-jobs')).not.toContainText('Tu favorita');
    expect(mock.favoriteId).toBeNull();

    mock.failNextFavorite();
    await page.locator('#recent-jobs .version-card').nth(1).getByRole('button', { name: 'Elegir favorita' }).click();
    await expect(page.locator('#recent-jobs')).not.toContainText('Tu favorita');
    await expect(page.locator('#toast-region')).toContainText('temporalmente fuera de servicio');
    expect(mock.favoriteId).toBeNull();
  });

  test('shows missing media guidance and keeps legacy sessions read-only', async ({ page }) => {
    await installHistoryApi(page, {
      versions: [historyVersion(1, { id: promptVersionId, runId: jobId, available: false })],
    });
    await page.goto(`/?session=${sessionId}`);
    const card = page.locator('#recent-jobs .version-card').first();
    await card.getByRole('button', { name: 'Ver salidas y QA' }).click();
    await expect(card).toContainText('Los medios ya no están disponibles');

    await page.unroute('**/api/mvp/**');
    await installHistoryApi(page, { legacy: true });
    await page.reload();
    await expect(page.locator('#legacy-workspace')).toBeVisible();
    await expect(page.locator('#modern-workspace')).toBeHidden();
    await expect(page.locator('#legacy-history')).toContainText('Corte histórico de una carga anterior');
    await expect(page.locator('#legacy-history')).toContainText('Descargar');
    await page.locator('#legacy-create-session').click();
    await expect(page.locator('#session-dialog')).toBeVisible();
    await expect(page.locator('#session-title')).toBeFocused();
  });

  test('390px mobile keeps the source, composer, and activity in a usable reading order', async ({ page }) => {
    const ready: SourceState = {
      id: uploadId,
      upload_id: uploadId,
      editing_session_id: sessionId,
      state: 'ready',
      original_filename: 'una-entrevista-con-un-nombre-muy-largo-para-probar-el-ajuste.mp4',
      expected_size: 42_000_000,
      received_bytes: 42_000_000,
      upload_offset: 42_000_000,
      media_type: 'video/mp4',
      completed_at: now,
    };
    await installWorkspaceApi(page, { initialSource: ready });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/?session=${sessionId}`);

    await expect(page.locator('#source-card')).toBeVisible();
    await expect(page.locator('#job-form')).toBeVisible();
    await expect(page.locator('#activity-card')).toBeVisible();
    const order = await page.evaluate(() => [
      document.querySelector('#source-card')!.getBoundingClientRect().top,
      document.querySelector('#job-form')!.getBoundingClientRect().top,
      document.querySelector('#activity-card')!.getBoundingClientRect().top,
    ]);
    expect(order[0]).toBeLessThan(order[1]);
    expect(order[1]).toBeLessThan(order[2]);

    await page.locator('#prompt').fill('Una instrucción extensa '.repeat(120));
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    for (const selector of ['#session-new', '#logout', '#submit']) {
      const box = await page.locator(selector).boundingBox();
      expect(box).toBeTruthy();
      expect(box!.height).toBeGreaterThanOrEqual(44);
    }
    expect(await page.evaluate(() => ({
      local: Object.keys(localStorage),
      session: Object.keys(sessionStorage),
    }))).toEqual({ local: [], session: [] });
  });

  test('390px mobile stacks comparison columns without horizontal overflow', async ({ page }) => {
    await installHistoryApi(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/?session=${sessionId}`);
    const compareChecks = page.locator('#recent-jobs .compare-check input');
    await compareChecks.nth(0).check();
    await compareChecks.nth(1).check();
    await page.locator('#comparison-open').click();
    const columns = page.locator('#comparison-content .comparison-column');
    await expect(columns).toHaveCount(2);
    const boxes = await columns.evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect()));
    expect(boxes[1].top).toBeGreaterThan(boxes[0].bottom - 1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await page.keyboard.press('Escape');
    await expect(page.locator('#comparison-dialog')).toBeHidden();
  });
});
