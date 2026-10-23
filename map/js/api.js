// fetch wrapper (timeout/retry), API health polling, connectivity state and optional-feature probing.
// Classic script (no modules): every file in map/js shares one global scope.
'use strict';

const API_TIMEOUT = 10000;
const API_MAX_RETRIES = 2;

class ApiError extends Error {
  constructor(message, status = 0) { super(message); this.name = 'ApiError'; this.status = status; }
}

function sanitizePath(p) { return p.replace(/[^a-zA-Z0-9\-_\/?.=&%:,@+]/g, ''); }

/**
 * JSON request to our backend with timeout, retry (idempotent calls only) and timing.
 * Throws ApiError (message "HTTP <status>: <body>", .status set) or a plain Error on timeout.
 */
async function api(path, opts = {}) {
  const { retries = API_MAX_RETRIES, timeout = API_TIMEOUT, headers, body, ...init } = opts;
  const url = API + sanitizePath(path);
  const startTime = performance.now();
  for (let attempt = 0; attempt <= retries; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const res = await fetch(url, {
        ...init,
        headers: { 'Content-Type': 'application/json', ...headers },
        body: body ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });
      clearTimeout(timer);
      if (!res.ok) {
        const text = await res.text().catch(() => '');
        throw new ApiError(`HTTP ${res.status}${text ? ': ' + text.substring(0, 200) : ''}`, res.status);
      }
      const ct = res.headers.get('content-type') || '';
      if (!ct.includes('json')) throw new ApiError('Non-JSON response', res.status);
      const data = await res.json();
      analytics.track('api_call', { path, ms: Math.round(performance.now() - startTime), status: res.status, attempt });
      return data;
    } catch (e) {
      clearTimeout(timer);
      const isRetryable = e.name === 'AbortError' || e.message.includes('fetch') || e.message.includes('network') || e.message.includes('Failed');
      if (attempt < retries && isRetryable && !String(init.method || '').match(/POST|PUT|DELETE/i)) {
        await new Promise(r => setTimeout(r, Math.min(500 * (attempt + 1), 3000)));
        continue;
      }
      analytics.track('api_error', { path, ms: Math.round(performance.now() - startTime), error: e.message?.substring(0, 100), attempt });
      if (e.name === 'AbortError') { throw new Error('Request timeout — server may be busy'); }
      throw e;
    }
  }
}

// ── Connectivity ──
// device = browser's navigator.onLine; server = result of the last GET /health (null = unknown).
// `ok` means "the API should answer": a health result wins over the device flag, so a LAN/localhost
// server keeps working with no internet.
TI.net = {
  device: navigator.onLine,
  server: null,
  get ok() { return this.server === null ? this.device : this.server; },
};
function setNetState(patch) {
  const before = TI.net.ok;
  Object.assign(TI.net, patch);
  if (typeof applyNetState === 'function') applyNetState(before !== TI.net.ok);
}

// ── Check API health (adaptive interval, paused while the tab is hidden) ──
let healthInterval = 30000;
let healthOk = false;
let _healthTimer = null;
async function checkHealth() {
  clearTimeout(_healthTimer);
  _healthTimer = null;
  if (document.hidden) return; // resumed by the visibilitychange handler
  const status = $('nav-status');
  try {
    const h = await api('/health', {retries: 0, timeout: 5000});
    setHtml(status, html`🟢<span class="nav-tooltip">API OK · v${h.version || '?'}</span>`);
    status.setAttribute('aria-label', 'API connected');
    healthOk = true;
    healthInterval = 30000;
    setNetState({ server: true });
  } catch {
    setHtml(status, html`🔴<span class="nav-tooltip">API Offline — check connection</span>`);
    status.setAttribute('aria-label', 'API offline');
    healthOk = false;
    healthInterval = Math.min(healthInterval * 1.5, 30000);
    setNetState({ server: false });
  }
  _healthTimer = setTimeout(checkHealth, healthInterval);
}

// ── Optional backend features ──
// Some endpoints exist only on newer backends. `null` = unknown (optimistic), false = hide the UI.
const features = { enrich: null, daily: null, loop: null, collections: null, streak: null, ask: null, unusual: null };
const FEATURE_PATHS = {
  enrich: '/api/place/enrich', daily: '/api/daily', loop: '/api/route/loop',
  collections: '/api/collections', streak: '/api/streak', ask: '/api/place/ask',
};

function applyFeatureVisibility() {
  $$('[data-feature]').forEach(el => { el.hidden = features[el.dataset.feature] === false; });
  const ask = $('card-ask'); // lives in the place card, whose visibility also depends on fog
  if (ask && features.ask === false) ask.hidden = true;
}
/** Mark a feature unavailable when the server answers 404/405/501 for it. Returns true if it did. */
function featureUnavailable(name, err) {
  if (err && [404, 405, 501].includes(err.status)) {
    features[name] = false;
    applyFeatureVisibility();
    return true;
  }
  return false;
}
/**
 * One look at /openapi.json (~120 KB) tells us which optional endpoints this backend has. The derived flags are
 * remembered for an hour so we do not download it on every page load.
 */
const FEATURE_TTL_MS = 60 * 60 * 1000;
async function probeFeatures() {
  const cached = store.getJSON(LS.features, null);
  if (cached && cached.origin === API && Date.now() - cached.t < FEATURE_TTL_MS && cached.flags) {
    Object.assign(features, cached.flags);
    applyFeatureVisibility();
    return;
  }
  try {
    const res = await fetch(API + '/openapi.json', { cache: 'no-store' });
    if (!res.ok) throw new Error('no openapi');
    const spec = await res.json();
    const paths = spec.paths || {};
    for (const [name, p] of Object.entries(FEATURE_PATHS)) features[name] = !!paths[p];
    const sortPattern = spec.components?.schemas?.DiscoverRequest?.properties?.sort_by?.pattern;
    if (typeof sortPattern === 'string') features.unusual = sortPattern.includes('unusual');
    store.setJSON(LS.features, { t: Date.now(), origin: API, flags: { ...features } });
  } catch { /* docs disabled or offline: stay optimistic, 404s will hide the UI later */ }
  applyFeatureVisibility();
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden && _healthTimer === null) checkHealth();
});
