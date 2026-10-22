// fetch wrapper (timeout/retry) and API health polling.
// Classic script (no modules): shares the global scope with the other map/js files.

// ── API helper with timeout + sanitization + retry + timing ──
const API_TIMEOUT = 10000;
const API_MAX_RETRIES = 2;
function sanitizePath(p) { return p.replace(/[^a-zA-Z0-9\-_\/?.=&%:,@+]/g, ''); }
async function api(path, opts = {}) {
  const url = API + sanitizePath(path);
  const retries = opts.retries ?? API_MAX_RETRIES;
  const startTime = performance.now();
  for (let attempt = 0; attempt <= retries; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), opts.timeout || API_TIMEOUT);
    try {
      const res = await fetch(url, {
        headers: { 'Content-Type': 'application/json', ...opts.headers },
        ...opts,
        body: opts.body ? JSON.stringify(opts.body) : undefined,
        signal: controller.signal,
      });
      clearTimeout(timer);
      if (!res.ok) {
        const text = await res.text().catch(() => '');
        throw new Error(`HTTP ${res.status}${text ? ': ' + text.substring(0, 200) : ''}`);
      }
      const ct = res.headers.get('content-type') || '';
      if (!ct.includes('json')) throw new Error('Non-JSON response');
      const data = await res.json();
      analytics.track('api_call', { path, ms: Math.round(performance.now() - startTime), status: res.status, attempt });
      return data;
    } catch (e) {
      clearTimeout(timer);
      const isRetryable = e.name === 'AbortError' || e.message.includes('fetch') || e.message.includes('network') || e.message.includes('Failed');
      if (attempt < retries && isRetryable && !opts.method?.match(/POST|PUT|DELETE/i)) {
        await new Promise(r => setTimeout(r, Math.min(500 * (attempt + 1), 3000)));
        continue;
      }
      analytics.track('api_error', { path, ms: Math.round(performance.now() - startTime), error: e.message?.substring(0, 100), attempt });
      if (e.name === 'AbortError') { throw new Error('Request timeout — server may be busy'); }
      throw e;
    }
  }
}

// ── Check API health (adaptive interval) ──
let healthInterval = 30000;
let healthOk = false;
async function checkHealth() {
  try {
    const h = await api('/health', {retries: 0, timeout: 5000});
    document.getElementById('nav-status').innerHTML = '🟢<span class="nav-tooltip">API OK · v' + escHtml(h.version || '?') + '</span>';
    healthOk = true;
    healthInterval = 30000;
  } catch {
    document.getElementById('nav-status').innerHTML = '🔴<span class="nav-tooltip">API Offline — check connection</span>';
    healthOk = false;
    healthInterval = Math.min(healthInterval * 1.5, 120000);
  }
  setTimeout(checkHealth, healthInterval);
}
