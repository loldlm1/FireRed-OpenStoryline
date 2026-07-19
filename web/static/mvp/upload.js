import { apiJson, parseXhrError, requestHeaders } from './api.js';

const CHUNK_BYTES = 8 * 1024 * 1024;
const MAX_TRANSIENT_RETRIES = 3;
const TRANSIENT_CODES = new Set([
  'DATABASE_UNAVAILABLE',
  'SOURCE_UPLOAD_BUSY',
  'UPLOAD_WRITE_FAILED',
  'JOB_STATE_UNAVAILABLE',
]);

const wait = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

function isTransient(error) {
  return error?.status === 0
    || error?.status === 408
    || error?.status === 425
    || error?.status === 429
    || error?.status >= 500
    || TRANSIENT_CODES.has(error?.code);
}

export class ResumableUpload {
  constructor(sessionId, callbacks = {}) {
    this.sessionId = sessionId;
    this.callbacks = callbacks;
    this.file = null;
    this.uploadId = '';
    this.offset = 0;
    this.maxPercent = 0;
    this.paused = false;
    this.cancelled = false;
    this.xhr = null;
    this.running = null;
  }

  start(file) {
    if (!(file instanceof File)) return Promise.reject(new TypeError('A File is required'));
    if (this.running) return this.running;
    this.file = file;
    this.paused = false;
    this.cancelled = false;
    this.callbacks.onFile?.(file);
    this.running = this.#initializeAndRun()
      .catch((error) => {
        if (!this.paused && !this.cancelled) this.callbacks.onError?.(error);
        throw error;
      })
      .finally(() => {
        this.running = null;
      });
    return this.running;
  }

  resume() {
    if (!this.file || this.running) return this.running || Promise.resolve();
    this.paused = false;
    this.cancelled = false;
    this.callbacks.onStage?.('uploading', { offset: this.offset });
    this.running = this.#runChunks()
      .catch((error) => {
        if (!this.paused && !this.cancelled) this.callbacks.onError?.(error);
        throw error;
      })
      .finally(() => {
        this.running = null;
      });
    return this.running;
  }

  pause() {
    if (!this.file || this.paused) return;
    this.paused = true;
    this.xhr?.abort();
    this.callbacks.onPaused?.({ offset: this.offset });
  }

  async cancel() {
    this.cancelled = true;
    this.paused = false;
    this.xhr?.abort();
    if (this.uploadId) {
      await apiJson(
        `/api/mvp/sessions/${this.sessionId}/input-video/uploads/${this.uploadId}`,
        { method: 'DELETE' },
      );
    }
    this.file = null;
    this.uploadId = '';
    this.offset = 0;
    this.maxPercent = 0;
    this.callbacks.onCancelled?.();
  }

  async #initializeAndRun() {
    this.callbacks.onStage?.('preparing');
    const source = await apiJson(
      `/api/mvp/sessions/${this.sessionId}/input-video/uploads`,
      {
        method: 'POST',
        body: {
          original_filename: this.file.name,
          expected_size: this.file.size,
          media_type: this.file.type || null,
        },
      },
    );
    this.uploadId = source.upload_id;
    this.offset = Number(source.upload_offset || source.received_bytes || 0);
    this.#reportProgress(this.offset);
    if (source.state === 'ready') {
      this.callbacks.onReady?.(source);
      return source;
    }
    return this.#runChunks();
  }

  async #runChunks() {
    this.callbacks.onStage?.('uploading', { offset: this.offset });
    while (this.offset < this.file.size) {
      if (this.paused || this.cancelled) return null;
      const start = this.offset;
      const chunk = this.file.slice(start, Math.min(start + CHUNK_BYTES, this.file.size));
      let attempt = 0;
      while (true) {
        if (this.paused || this.cancelled) return null;
        try {
          const state = await this.#sendChunk(chunk, start);
          this.offset = Number(state.upload_offset ?? state.received_bytes ?? start + chunk.size);
          this.#reportProgress(this.offset);
          break;
        } catch (error) {
          if (this.paused || this.cancelled || error?.name === 'AbortError') return null;
          if (error?.code === 'UPLOAD_OFFSET_MISMATCH') {
            this.offset = Number(error.details?.upload_offset || 0);
            this.#reportProgress(this.offset);
            this.callbacks.onOffsetAdjusted?.({ offset: this.offset });
            break;
          }
          attempt += 1;
          if (!isTransient(error) || attempt > MAX_TRANSIENT_RETRIES) throw error;
          this.callbacks.onRetry?.({ attempt, error });
          await wait(500 * (2 ** (attempt - 1)));
        }
      }
    }
    if (this.paused || this.cancelled) return null;
    this.callbacks.onStage?.('validating');
    const ready = await apiJson(
      `/api/mvp/sessions/${this.sessionId}/input-video/uploads/${this.uploadId}/complete`,
      { method: 'POST' },
    );
    this.offset = Number(ready.received_bytes || this.file.size);
    this.#reportProgress(this.offset);
    this.callbacks.onReady?.(ready);
    return ready;
  }

  #sendChunk(chunk, offset) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      this.xhr = xhr;
      xhr.open(
        'PATCH',
        `/api/mvp/sessions/${this.sessionId}/input-video/uploads/${this.uploadId}`,
      );
      xhr.withCredentials = true;
      const headers = requestHeaders('PATCH', {
        'Content-Type': 'application/offset+octet-stream',
        'Upload-Offset': String(offset),
      });
      headers.forEach((value, name) => xhr.setRequestHeader(name, value));
      xhr.upload.addEventListener('progress', (event) => {
        if (event.lengthComputable) this.#reportProgress(offset + event.loaded);
      });
      xhr.addEventListener('load', async () => {
        this.xhr = null;
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText || '{}'));
          } catch {
            reject(Object.assign(new Error('Invalid upload response'), { code: 'UPLOAD_RESPONSE_INVALID' }));
          }
          return;
        }
        reject(await parseXhrError(xhr));
      });
      xhr.addEventListener('error', async () => {
        this.xhr = null;
        reject(await parseXhrError(xhr));
      });
      xhr.addEventListener('abort', () => {
        this.xhr = null;
        reject(new DOMException('Upload paused', 'AbortError'));
      });
      xhr.send(chunk);
    });
  }

  #reportProgress(bytes) {
    const percent = this.file?.size ? Math.min(100, (Number(bytes) / this.file.size) * 100) : 0;
    this.maxPercent = Math.max(this.maxPercent, percent);
    this.callbacks.onProgress?.({
      bytes: Number(bytes),
      total: this.file?.size || 0,
      percent: this.maxPercent,
    });
  }
}
