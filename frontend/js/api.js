// Thin API client: JWT auth with one automatic refresh, store header, friendly errors.
const BASE = window.APP_CONFIG.API_BASE_URL;

export const Session = {
  get access() { return localStorage.getItem('ipace_access'); },
  get refresh() { return localStorage.getItem('ipace_refresh'); },
  setTokens(access, refresh) {
    localStorage.setItem('ipace_access', access);
    if (refresh) localStorage.setItem('ipace_refresh', refresh);
  },
  get storeId() { return localStorage.getItem('ipace_store_id'); },
  set storeId(id) { id ? localStorage.setItem('ipace_store_id', id) : localStorage.removeItem('ipace_store_id'); },
  clear() {
    localStorage.removeItem('ipace_access');
    localStorage.removeItem('ipace_refresh');
    localStorage.removeItem('ipace_store_id');
  },
};

export class ApiError extends Error {
  constructor(status, data) {
    super(formatErrors(data));
    this.status = status;
    this.data = data;
  }
}

function humanize(key) {
  return key.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

// DRF errors come as {detail}, {field: [msgs]}, a list of strings, or nested objects.
export function formatErrors(data) {
  if (!data) return 'Something went wrong. Please try again.';
  if (typeof data === 'string') return data.length > 300 ? 'Something went wrong. Please try again.' : data;
  if (Array.isArray(data)) return data.map(formatErrors).join(' ');
  if (data.detail) return formatErrors(data.detail);
  const parts = Object.entries(data).map(([key, value]) => {
    const text = formatErrors(value);
    return key === 'non_field_errors' ? text : `${humanize(key)}: ${text}`;
  });
  return parts.join(' ') || 'Something went wrong. Please try again.';
}

function buildUrl(path, params) {
  const url = new URL(BASE + path, window.location.href);
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, value);
    });
  }
  return url.toString();
}

let refreshing = null;
async function refreshAccess() {
  if (!Session.refresh) throw new ApiError(401, 'Please sign in again.');
  const res = await fetch(buildUrl('/auth/refresh/'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh: Session.refresh }),
  });
  if (!res.ok) throw new ApiError(401, 'Your session has expired. Please sign in again.');
  const data = await res.json();
  Session.setTokens(data.access, data.refresh);
}

async function send(path, { method = 'GET', params, body, form, auth = true, scoped = true } = {}, retry = true) {
  const headers = {};
  if (!form && body !== undefined) headers['Content-Type'] = 'application/json';
  if (auth && Session.access) headers.Authorization = `Bearer ${Session.access}`;
  if (scoped && Session.storeId) headers['X-Store-ID'] = Session.storeId;

  let res;
  try {
    res = await fetch(buildUrl(path, params), {
      method,
      headers,
      body: form ? form : (body === undefined ? undefined : JSON.stringify(body)),
    });
  } catch (e) {
    throw new ApiError(0, 'Cannot reach the server. Check your connection and try again.');
  }

  if (res.status === 401 && auth && retry && Session.refresh) {
    try {
      refreshing = refreshing || refreshAccess().finally(() => { refreshing = null; });
      await refreshing;
    } catch (e) {
      window.dispatchEvent(new CustomEvent('session-expired'));
      throw e;
    }
    return send(path, { method, params, body, form, auth, scoped }, false);
  }
  return res;
}

async function json(path, options) {
  const res = await send(path, options);
  if (res.status === 204) return null;
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
  if (!res.ok) {
    if (res.status === 401) window.dispatchEvent(new CustomEvent('session-expired'));
    throw new ApiError(res.status, data);
  }
  return data;
}

async function download(path, params, fallbackName) {
  const res = await send(path, { params });
  if (!res.ok) {
    let data = null;
    try { data = await res.json(); } catch (e) { /* not json */ }
    throw new ApiError(res.status, data);
  }
  const blob = await res.blob();
  const disposition = res.headers.get('Content-Disposition') || '';
  const match = /filename="?([^";]+)"?/i.exec(disposition);
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = match ? match[1] : fallbackName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 2000);
}

export const Api = {
  get: (path, params) => json(path, { params }),
  post: (path, body) => json(path, { method: 'POST', body: body === undefined ? {} : body }),
  patch: (path, body) => json(path, { method: 'PATCH', body }),
  put: (path, body) => json(path, { method: 'PUT', body }),
  del: (path) => json(path, { method: 'DELETE' }),
  upload: (path, form) => json(path, { method: 'POST', form }),
  download,

  // Every page of a list endpoint (up to a sane cap) — for dropdowns and pickers.
  async all(path, params = {}) {
    let page = 1;
    const out = [];
    while (page <= 25) {
      const data = await json(path, { params: { ...params, page, page_size: 200 } });
      if (Array.isArray(data)) return data;
      out.push(...data.results);
      if (!data.next) break;
      page += 1;
    }
    return out;
  },

  // Auth (not store-scoped, no bearer token needed for login/register)
  async login(username, password) {
    const data = await json('/auth/login/', { method: 'POST', body: { username, password }, auth: false, scoped: false });
    Session.setTokens(data.access, data.refresh);
    return data;
  },
  async register(payload) {
    const data = await json('/auth/register/', { method: 'POST', body: payload, auth: false, scoped: false });
    Session.setTokens(data.access, data.refresh);
    return data;
  },
  async logout() {
    try {
      if (Session.refresh) await json('/auth/logout/', { method: 'POST', body: { refresh: Session.refresh }, scoped: false });
    } catch (e) { /* ignore — we are signing out anyway */ }
    Session.clear();
  },
  me: () => json('/auth/me/', { scoped: false }),
  updateProfile: (payload) => json('/auth/me/', { method: 'PATCH', body: payload, scoped: false }),
  changePassword: (current_password, new_password) =>
    json('/auth/change-password/', { method: 'POST', body: { current_password, new_password }, scoped: false }),
  listStores: () => json('/stores/', { scoped: false }),
  createStore: (payload) => json('/stores/', { method: 'POST', body: payload, scoped: false }),
  updateStore: (id, payload) => json(`/stores/${id}/`, { method: 'PATCH', body: payload, scoped: false }),
};
