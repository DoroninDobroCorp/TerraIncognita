// Small helpers: ids, storage migration, debounce/clamp, analytics, escHtml, geo math.
// Classic script (no modules): shares the global scope with the other map/js files.

function _uuid() { return 'xxxx-xxxx-xxxx'.replace(/x/g, () => (Math.random()*16|0).toString(16)); }

// ── User ID (persistent, auto-generated) ──
function getUserId() {
  let uid = localStorage.getItem('ti_user_id');
  if (!uid) { uid = 'explorer-' + _uuid().slice(0,8); localStorage.setItem('ti_user_id', uid); }
  return uid;
}
const USER_ID = getUserId();
function getActiveUserId() { return (document.getElementById('my-user-id')?.value?.trim()) || USER_ID; }

// ── localStorage migration ──
(function migrateStorage() {
  const ver = localStorage.getItem('ti_storage_version') || '0';
  if (ver < '1.1') {
    // Validate existing data
    try { JSON.parse(localStorage.getItem('ti_visited') || '[]'); } catch { localStorage.removeItem('ti_visited'); }
    try { JSON.parse(localStorage.getItem('ti_saved') || '[]'); } catch { localStorage.removeItem('ti_saved'); }
    try { const r = JSON.parse(localStorage.getItem('ti_routes') || '[]'); if (r.length > 100) localStorage.setItem('ti_routes', JSON.stringify(r.slice(-100))); } catch { localStorage.removeItem('ti_routes'); }
    localStorage.setItem('ti_storage_version', '1.1');
  }
})();

// ── Utilities ──
function debounce(fn, ms = 300) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
function clamp(v, min, max) { return Math.min(Math.max(v, min), max); }

// ── Simple analytics/event tracking ──
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

function escHtml(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}

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
