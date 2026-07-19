import { test, expect, type Page, type Route } from '@playwright/test';

const sessionId = 'a'.repeat(32);
const uploadId = 'b'.repeat(32);
const promptVersionId = 'c'.repeat(32);
const jobId = 'd'.repeat(32);
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
    input_video: source.state === 'missing' ? null : source,
    jobs: [],
    created_at: now,
    updated_at: now,
    deleted_at: null,
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
    await expect(page.locator('#recent-jobs .recent-job')).toHaveCount(1);

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
    await expect(page.locator('#recent-jobs .recent-job')).toHaveCount(1);
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
});
