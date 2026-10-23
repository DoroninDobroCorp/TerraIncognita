// Small helpers: DOM shortcuts, safe localStorage, SAFE HTML templating, analytics, geo math.
// Classic script (no modules): every file in map/js shares one global scope.
'use strict';

// ── DOM shortcuts ──
const $ = (id) => document.getElementById(id);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// ── ids ──
function _uuid() { return 'xxxx-xxxx-xxxx'.replace(/x/g, () => (Math.random()*16|0).toString(16)); }

// ── localStorage that never throws (private mode, blocked storage, quota) ──
const store = {
  get(key, def = null) { try { const v = localStorage.getItem(key); return v === null ? def : v; } catch { return def; } },
  set(key, val) { try { localStorage.setItem(key, val); return true; } catch { return false; } },
  remove(key) { try { localStorage.removeItem(key); } catch { /* ignore */ } },
  getJSON(key, def) { try { const v = localStorage.getItem(key); return v === null ? def : JSON.parse(v); } catch { return def; } },
  setJSON(key, val) { try { localStorage.setItem(key, JSON.stringify(val)); return true; } catch { return false; } },
};

// ── User ID (persistent, auto-generated) ──
function getUserId() {
  let uid = store.get(LS.userId);
  if (!uid) { uid = 'explorer-' + _uuid().slice(0,8); store.set(LS.userId, uid); }
  return uid;
}
const USER_ID = getUserId();
function getActiveUserId() { return ($('my-user-id')?.value?.trim()) || USER_ID; }

// ── localStorage migration ──
(function migrateStorage() {
  const ver = store.get(LS.storageVersion, '0');
  if (ver < '1.1') {
    // Validate existing data
    try { JSON.parse(store.get(LS.visited) || '[]'); } catch { store.remove(LS.visited); }
    try { JSON.parse(store.get(LS.saved) || '[]'); } catch { store.remove(LS.saved); }
    try { const r = JSON.parse(store.get(LS.routes) || '[]'); if (r.length > 100) store.setJSON(LS.routes, r.slice(-100)); } catch { store.remove(LS.routes); }
    store.set(LS.storageVersion, '1.1');
  }
})();

function loadPersistedState() {
  const asSet = (v) => new Set(Array.isArray(v) ? v.filter(x => typeof x === 'string') : []);
  state.visitedPlaces = asSet(store.getJSON(LS.visited, []));
  state.savedPlaces = asSet(store.getJSON(LS.saved, []));
}
function persistVisited() { store.setJSON(LS.visited, [...state.visitedPlaces]); }
function persistSaved() { store.setJSON(LS.saved, [...state.savedPlaces]); }

// ── Misc utilities ──
function debounce(fn, ms = 300) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
function clamp(v, min, max) { return Math.min(Math.max(v, min), max); }
/** Number or fallback (never NaN). */
function num(v, def = 0) { const n = Number(v); return Number.isFinite(n) ? n : def; }

// ── Simple analytics/event tracking (in-memory only, never sent anywhere) ──
const analytics = {
  sessionId: _uuid().slice(0,12),
  events: [],
  track(event, data = {}) {
    const entry = { event, data, ts: Date.now(), section: document.querySelector('.nav-btn.active')?.dataset?.section || 'unknown', sid: this.sessionId };
    this.events.push(entry);
    if (this.events.length > 500) this.events.shift();
  },
  getStats() {
    const bySection = {}, byEvent = {};
    this.events.forEach(e => { bySection[e.section] = (bySection[e.section]||0)+1; byEvent[e.event] = (byEvent[e.event]||0)+1; });
    return { total: this.events.length, bySection, byEvent, sessionId: this.sessionId, sessionStart: this.events[0]?.ts, lastEvent: this.events[this.events.length-1]?.ts, durationMs: this.events.length > 1 ? (this.events[this.events.length-1].ts - this.events[0].ts) : 0 };
  }
};

// ── Global error handler ──
window.addEventListener('unhandledrejection', e => {
  console.error('Unhandled promise rejection:', e.reason);
  toast('Something went wrong. Please try again.', 'error');
  analytics.track('error', { type: 'unhandled_rejection', message: String(e.reason) });
});
window.addEventListener('error', e => {
  console.error('Global error:', e.message);
  analytics.track('error', { type: 'js_error', message: e.message, file: e.filename, line: e.lineno });
});

// ═══════════════════════════════════════════════════════
//  SAFE HTML
//  Every dynamic value that ends up in markup MUST go through html`...` (escapes each
//  interpolation unless it is already SafeHtml) or escHtml(). Assign the result with
//  setHtml(el, safe) / appendHtml(el, safe). Raw `.innerHTML =` is banned outside this
//  block — backend/tests/test_map_assets.py enforces it.
// ═══════════════════════════════════════════════════════
function escHtml(s) {
  return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

class SafeHtml {
  constructor(s) { this.s = String(s); }
  toString() { return this.s; }
}
/** Mark a trusted, static string as safe markup (never pass user/API/LLM text). */
function raw(s) { return new SafeHtml(s); }
function _toHtml(v) {
  if (v instanceof SafeHtml) return v.s;
  if (Array.isArray(v)) return v.map(_toHtml).join('');
  if (v == null || v === false) return '';
  return escHtml(v);
}
/** Tagged template: html`<b>${userText}</b>` escapes `userText`; nested html`` and arrays are kept. */
function html(strings, ...vals) {
  let out = strings[0];
  for (let i = 0; i < vals.length; i++) out += _toHtml(vals[i]) + strings[i + 1];
  return new SafeHtml(out);
}
function _requireSafe(v) {
  if (!(v instanceof SafeHtml)) throw new TypeError('setHtml() needs html`...`/raw() output, got ' + typeof v);
  return v.s;
}
function setHtml(el, safe) { el.innerHTML = _requireSafe(safe); }
function appendHtml(el, safe, where = 'beforeend') { el.insertAdjacentHTML(where, _requireSafe(safe)); }

/** Only http(s) URLs (resolved against the page) — blocks javascript:/data: links from API text. */
function safeUrl(u) {
  try {
    const x = new URL(String(u), window.location.href);
    return (x.protocol === 'https:' || x.protocol === 'http:') ? x.href : '';
  } catch { return ''; }
}
/** A CSS colour we are willing to put in a style attribute. */
function safeColor(c, fallback = '#888888') {
  const s = String(c || '').trim();
  return /^#[0-9a-f]{3,8}$/i.test(s) || /^(rgb|hsl)a?\([\d\s.,%/]+\)$/i.test(s) ? s : fallback;
}
/** Black or white, whichever reads better on `bg` (#rgb / #rrggbb); white for anything else. */
function contrastText(bg) {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(bg || '').trim());
  if (!m) return '#ffffff';
  let h = m[1];
  if (h.length === 3) h = [...h].map(c => c + c).join('');
  const [r, g, b] = [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16) / 255)
    .map(c => c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.179 ? '#000000' : '#ffffff';
}
/** Category definition for an id, never undefined and with safe colour. */
function catDef(id) {
  const d = CATEGORIES[id] || CATEGORIES.landmark || { icon:'📍', label:String(id || 'Place'), color:'#888888' };
  return { icon: d.icon || '📍', label: d.label || String(id), color: safeColor(d.color), group: d.group };
}

// ── Geo / formatting ──
function haversineJS(lat1, lng1, lat2, lng2) {
  const R = 6371000, toRad = d => d * Math.PI / 180;
  const dLat = toRad(lat2 - lat1), dLng = toRad(lng2 - lng1);
  const a = Math.sin(dLat/2)**2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng/2)**2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
}
function formatBytes(b){if(!b||b<=0)return'0 B';const k=1024,s=['B','KB','MB','GB'];const i=Math.min(Math.floor(Math.log(b)/Math.log(k)),s.length-1);return(b/Math.pow(k,i)).toFixed(1)+' '+s[i];}
function parseCoords(str) {
  const parts = (str||'').split(',').map(s => parseFloat(s.trim()));
  if (parts.length < 2 || isNaN(parts[0]) || isNaN(parts[1])) return null;
  if (parts[0] < -90 || parts[0] > 90 || parts[1] < -180 || parts[1] > 180) return null;
  return { lat: parts[0], lng: parts[1] };
}
function fmtCoords(lat, lng) { return `${num(lat).toFixed(4)}, ${num(lng).toFixed(4)}`; }

/** Two-letter UI/content language for backend calls that take `lang`. */
function uiLang() {
  const l = String(navigator.language || 'en').slice(0, 2).toLowerCase();
  return /^[a-z]{2}$/.test(l) ? l : 'en';
}

/** Offer a text file to the user (GPX/KML/Markdown exports). */
function downloadText(filename, content, mime = 'text/plain') {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
