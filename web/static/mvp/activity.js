import { apiJson } from './api.js';

const TERMINAL_STATES = new Set(['completed', 'failed', 'cancelled']);
const TERMINAL_MESSAGES = new Set(['activity.system.completed', 'activity.system.failed']);

export class ActivityFeed {
  constructor(jobId, callbacks = {}) {
    this.jobId = jobId;
    this.callbacks = callbacks;
    this.lastSequence = 0;
    this.events = new Map();
    this.eventSource = null;
    this.reconnectAttempts = 0;
    this.reconnectTimer = 0;
    this.pollTimer = 0;
    this.elapsedTimer = 0;
    this.pollDelay = 1800;
    this.startedAt = Date.now();
    this.stopped = true;
  }

  async start({ startedAt } = {}) {
    this.stop();
    this.stopped = false;
    this.startedAt = startedAt ? Date.parse(startedAt) || Date.now() : Date.now();
    this.#startElapsedClock();
    this.callbacks.onConnection?.('reconnecting');
    try {
      await this.#replay();
      if (!this.stopped) this.#openStream();
    } catch (error) {
      if (!this.stopped) this.#startPolling(error);
    }
  }

  stop() {
    this.stopped = true;
    this.eventSource?.close();
    this.eventSource = null;
    window.clearTimeout(this.reconnectTimer);
    window.clearTimeout(this.pollTimer);
    window.clearInterval(this.elapsedTimer);
    this.reconnectTimer = 0;
    this.pollTimer = 0;
    this.elapsedTimer = 0;
  }

  retryNow() {
    if (this.stopped) return;
    this.eventSource?.close();
    window.clearTimeout(this.reconnectTimer);
    window.clearTimeout(this.pollTimer);
    this.reconnectAttempts = 0;
    this.pollDelay = 1800;
    this.callbacks.onConnection?.('reconnecting');
    this.#replay()
      .then(() => {
        if (!this.stopped) this.#openStream();
      })
      .catch((error) => this.#startPolling(error));
  }

  async #replay() {
    const page = await apiJson(
      `/api/mvp/jobs/${this.jobId}/events?after=${this.lastSequence}&limit=100`,
    );
    for (const event of page.items || []) this.#accept(event);
    const job = await apiJson(`/api/mvp/jobs/${this.jobId}`);
    this.callbacks.onJob?.(job);
    if (TERMINAL_STATES.has(job.state)) this.#finish(job.state);
  }

  #openStream() {
    if (this.stopped) return;
    this.eventSource?.close();
    const source = new EventSource(
      `/api/mvp/jobs/${this.jobId}/events/stream?after=${this.lastSequence}`,
      { withCredentials: true },
    );
    this.eventSource = source;
    source.addEventListener('open', () => {
      this.pollDelay = 1800;
      this.callbacks.onConnection?.('live');
    });
    source.addEventListener('activity', (message) => {
      try {
        const event = JSON.parse(message.data);
        this.reconnectAttempts = 0;
        this.#accept(event);
        if (TERMINAL_MESSAGES.has(event.message_key)) {
          this.#finish(event.status === 'failed' ? 'failed' : 'completed');
        }
      } catch {
        this.#scheduleReconnect();
      }
    });
    source.addEventListener('stream_error', () => this.#scheduleReconnect());
    source.addEventListener('error', () => this.#scheduleReconnect());
  }

  #scheduleReconnect() {
    if (this.stopped) return;
    this.eventSource?.close();
    this.eventSource = null;
    this.reconnectAttempts += 1;
    if (this.reconnectAttempts > 3) {
      this.#startPolling();
      return;
    }
    this.callbacks.onConnection?.('reconnecting', { attempt: this.reconnectAttempts });
    window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = window.setTimeout(async () => {
      try {
        await this.#replay();
        if (!this.stopped) this.#openStream();
      } catch (error) {
        if (!this.stopped) this.#scheduleReconnect(error);
      }
    }, Math.min(700 * (2 ** (this.reconnectAttempts - 1)), 5000));
  }

  #startPolling(error) {
    if (this.stopped) return;
    this.eventSource?.close();
    this.eventSource = null;
    this.callbacks.onConnection?.('polling', { error });
    window.clearTimeout(this.pollTimer);
    const poll = async () => {
      if (this.stopped) return;
      try {
        await this.#replay();
        this.pollDelay = 1800;
      } catch (pollError) {
        this.pollDelay = Math.min(this.pollDelay * 1.7, 8000);
        this.callbacks.onConnection?.('stale', { error: pollError });
      }
      if (!this.stopped) this.pollTimer = window.setTimeout(poll, this.pollDelay);
    };
    this.pollTimer = window.setTimeout(poll, 250);
  }

  #accept(event) {
    const sequence = Number(event?.sequence || 0);
    if (!Number.isInteger(sequence) || sequence <= this.lastSequence || this.events.has(sequence)) return;
    this.lastSequence = sequence;
    this.events.set(sequence, event);
    while (this.events.size > 256) {
      this.events.delete(this.events.keys().next().value);
    }
    this.callbacks.onEvent?.(event);
  }

  #finish(state) {
    if (this.stopped) return;
    this.callbacks.onConnection?.(state === 'completed' ? 'complete' : 'failed');
    this.callbacks.onTerminal?.(state);
    this.stop();
  }

  #startElapsedClock() {
    const update = () => this.callbacks.onElapsed?.(Math.max(0, Date.now() - this.startedAt));
    update();
    this.elapsedTimer = window.setInterval(update, 1000);
  }
}
