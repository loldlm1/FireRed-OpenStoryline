let authExpiredHandler = null;

export class ApiError extends Error {
  constructor(response, payload = {}) {
    const detail = payload?.detail || payload || {};
    super(detail.message || `Solicitud rechazada (${response.status})`);
    this.name = 'ApiError';
    this.status = response.status;
    this.code = detail.code || 'REQUEST_FAILED';
    this.details = detail.details || {};
    this.retryAfter = Number(response.headers?.get?.('Retry-After') || 0);
  }
}

export function setAuthExpiredHandler(handler) {
  authExpiredHandler = typeof handler === 'function' ? handler : null;
}

export function readCookie(name) {
  const prefix = `${encodeURIComponent(name)}=`;
  const item = document.cookie
    .split('; ')
    .find((entry) => entry.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : '';
}

export function requestHeaders(method, headers = {}) {
  const result = new Headers(headers);
  const normalizedMethod = String(method || 'GET').toUpperCase();
  if (!['GET', 'HEAD', 'OPTIONS'].includes(normalizedMethod)) {
    const csrfToken = readCookie('openstoryline_csrf');
    if (csrfToken) result.set('X-CSRF-Token', csrfToken);
  }
  return result;
}

export async function api(url, options = {}) {
  const method = String(options.method || 'GET').toUpperCase();
  const response = await fetch(url, {
    ...options,
    method,
    headers: requestHeaders(method, options.headers),
    credentials: 'same-origin',
  });
  if (!response.ok) {
    let payload = {};
    try {
      payload = await response.json();
    } catch {
      // Some infrastructure errors have no JSON body.
    }
    const error = new ApiError(response, payload);
    if (response.status === 401 && !url.includes('/auth/login')) {
      authExpiredHandler?.(error);
    }
    throw error;
  }
  return response;
}

export async function apiJson(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body !== undefined && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }
  const response = await api(url, {
    ...options,
    headers,
    body: options.body !== undefined && !(options.body instanceof FormData)
      ? JSON.stringify(options.body)
      : options.body,
  });
  if (response.status === 204) return null;
  return response.json();
}

export async function download(url, filename) {
  const response = await api(url);
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

export async function parseXhrError(xhr) {
  let payload = {};
  try {
    payload = JSON.parse(xhr.responseText || '{}');
  } catch {
    // The ApiError fallback remains safe and user-facing.
  }
  const response = {
    status: xhr.status || 0,
    headers: { get: () => null },
  };
  const error = new ApiError(response, payload);
  if (xhr.status === 401) authExpiredHandler?.(error);
  return error;
}
