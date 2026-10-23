// PWA glue: service worker registration/update notice, offline banner + disabled network-only actions,
// install prompt, offline-cache helpers. The caching rules themselves live in map/sw.js.
// Classic script (no modules): every file in map/js shares one global scope.
'use strict';

// ── Connectivity UI ──
// TI.net (api.js) tracks navigator.onLine AND the last /health result; this paints it.
function applyNetState(changed) {
  const ok = TI.net.ok;
  document.body.classList.toggle('is-offline', !ok);
  const banner = $('offline-banner');
  if (banner) {
    banner.hidden = ok;
    $('offline-banner-text').textContent = TI.net.device
      ? "Can't reach the Terra Incognita server — retrying. Saved places and cached map tiles still work."
      : "You're offline — saved places and cached map tiles still work. Search, AI chat, routes and sync need a connection.";
  }
  // Network-only controls: marked in markup with data-needs-network (templates too, via the CSS class
  // above and the guard in actions.js).
  $$('[data-needs-network]').forEach(el => {
    if (ok) el.removeAttribute('aria-disabled'); else el.setAttribute('aria-disabled', 'true');
  });
  if (typeof mapAvailable !== 'undefined' && mapAvailable) map.resize(); // the banner changes the map's height
  if (changed) toast(ok ? '🟢 Back online' : '📴 You are offline', ok ? 'success' : 'warning');
}

// ── Service worker ──
async function disableServiceWorker() {
  try {
    const regs = await navigator.serviceWorker.getRegistrations();
    await Promise.all(regs.map(r => r.unregister()));
    const names = await caches.keys();
    await Promise.all(names.filter(n => n.startsWith('ti-')).map(n => caches.delete(n)));
    toast('Service worker and offline caches removed (?nosw)', 'info');
  } catch (e) { console.warn('disableServiceWorker failed', e); }
}

async function registerServiceWorker() {
  if (!('serviceWorker' in navigator) || !window.isSecureContext) return; // needs https or localhost
  if (new URLSearchParams(window.location.search).has('nosw')) { await disableServiceWorker(); return; }
  const hadController = !!navigator.serviceWorker.controller;
  try {
    await navigator.serviceWorker.register('sw.js'); // scope defaults to /map/
  } catch (e) { console.warn('Service worker registration failed:', e); return; }
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    // First install also fires this (clients.claim); only announce real updates.
    if (hadController) toast('A new version of the app is ready.', 'info', 20000, { label: 'Reload', action: 'reload-app' });
  });
}

// ── Offline cache helpers (used by the settings dialog) ──
async function offlineCacheStats() {
  const out = { tiles: 0, usage: null, quota: null };
  try {
    if ('caches' in window) out.tiles = (await (await caches.open('ti-tiles-v1')).keys()).length;
    if (navigator.storage && navigator.storage.estimate) { const e = await navigator.storage.estimate(); out.usage = e.usage; out.quota = e.quota; }
  } catch { /* stats are best effort */ }
  return out;
}
async function clearOfflineCaches() {
  if (!('caches' in window)) return;
  const names = await caches.keys();
  await Promise.all(names.filter(n => n.startsWith('ti-tiles') || n.startsWith('ti-api')).map(n => caches.delete(n)));
}

// ── Install prompt ──
let _installEvent = null;
function canInstall() { return !!_installEvent; }
async function installApp() {
  if (!_installEvent) return;
  _installEvent.prompt();
  await _installEvent.userChoice.catch(() => null);
  _installEvent = null;
  if (typeof refreshSettingsDialog === 'function') refreshSettingsDialog();
}
function isStandalone() {
  return window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
}

registerActions({
  'reload-app': () => window.location.reload(),
});

function initPwa() {
  window.addEventListener('online', () => { setNetState({ device: true }); checkHealth(); });
  window.addEventListener('offline', () => { setNetState({ device: false }); checkHealth(); });
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    _installEvent = e;
    if (typeof refreshSettingsDialog === 'function') refreshSettingsDialog();
  });
  window.addEventListener('appinstalled', () => { _installEvent = null; toast('App installed', 'success'); });
  applyNetState(false);
  // Register after the page settled so the precache download does not compete with start-up.
  if (document.readyState === 'complete') registerServiceWorker();
  else window.addEventListener('load', registerServiceWorker, { once: true });
}
