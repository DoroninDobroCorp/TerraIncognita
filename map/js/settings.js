// Settings: colour theme (auto/light/dark/sun), low-power mode, and the settings dialog.
// Classic script (no modules): every file in map/js shares one global scope.
'use strict';

const THEMES = ['auto', 'light', 'dark', 'sun'];
const THEME_LABELS = { auto: 'Auto', light: 'Light', dark: 'Dark', sun: '☀️ Sun' };
let themePref = 'auto';

// ── Theme ──
// html[data-theme] always holds the RESOLVED theme (light | dark | sun) that css/app.css and the map use;
// html[data-theme-pref] holds what the user picked (auto follows the OS, incl. "increase contrast").
function systemTheme() {
  if (window.matchMedia('(prefers-contrast: more)').matches) return 'sun';
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}
function resolveTheme(pref) { return pref === 'auto' ? systemTheme() : pref; }

function applyTheme(pref, { persist = true } = {}) {
  themePref = THEMES.includes(pref) ? pref : 'auto';
  const theme = resolveTheme(themePref);
  const root = document.documentElement;
  root.dataset.theme = theme;
  root.dataset.themePref = themePref;
  if (persist) { if (themePref === 'auto') store.remove(LS.theme); else store.set(LS.theme, themePref); }
  // Browser chrome colour (Android address bar, iOS status bar in standalone mode)
  const color = getComputedStyle(root).getPropertyValue('--bg-secondary').trim();
  $$('meta[name="theme-color"]').forEach(m => { m.removeAttribute('media'); if (color) m.setAttribute('content', color); });
  applyMapTheme();
  refreshSettingsDialog();
}

function cycleTheme() {
  const order = ['light', 'dark', 'sun'];
  const next = order[(order.indexOf(resolveTheme(themePref)) + 1) % order.length];
  applyTheme(next);
  toast(`Theme: ${THEME_LABELS[next]}`, 'info', 1800);
}

// ── Low-power mode ──
// Coarser/cheaper GPS (see gpsOptions in nav.js), fewer GPS-driven redraws, no animations,
// 1x pixel ratio on the map.
function setLowPower(on, { persist = true } = {}) {
  state.lowPower = !!on;
  document.documentElement.classList.toggle('low-power', state.lowPower);
  if (persist) store.set(LS.lowPower, state.lowPower ? '1' : '0');
  if (mapAvailable && typeof map.setPixelRatio === 'function') map.setPixelRatio(state.lowPower ? 1 : (window.devicePixelRatio || 1));
  if (gpsTracking) restartGpsWatch();
  updateUserMarker();
  refreshSettingsDialog();
}

/** Offer low-power mode once per session when the battery runs low (Battery API: Chromium only). */
async function watchBattery() {
  if (!navigator.getBattery) return;
  try {
    const b = await navigator.getBattery();
    let hinted = false;
    const check = () => {
      if (hinted || state.lowPower || b.charging || b.level >= 0.2) return;
      hinted = true;
      toast('🔋 Battery below 20% — switch on Low-power mode?', 'info', 12000, { label: 'Turn on', action: 'low-power-on' });
    };
    b.addEventListener('levelchange', check);
    b.addEventListener('chargingchange', check);
    check();
  } catch { /* not available */ }
}

// ── Settings dialog ──
function switchHtml(action, on, labelId) {
  return html`<button type="button" class="toggle${on ? ' on' : ''}" role="switch" aria-checked="${String(on)}" aria-labelledby="${labelId}" data-action="${action}"></button>`;
}

function settingsHtml() {
  const resolved = resolveTheme(themePref);
  const online = TI.net.ok;
  const iosHint = /iphone|ipad|ipod/i.test(navigator.userAgent) && !isStandalone();
  return html`
    <h3 id="dialog-title">⚙️ Settings</h3>
    <div class="setting-block">
      <div class="setting-title" id="lbl-theme">Colour theme</div>
      <div class="setting-sub">Auto follows your device. Sun is high-contrast for direct sunlight.</div>
      <div class="seg" role="radiogroup" aria-labelledby="lbl-theme">${THEMES.map(t => html`<button type="button" role="radio" aria-checked="${String(t === themePref)}" data-action="set-theme" data-theme-name="${t}">${THEME_LABELS[t]}</button>`)}</div>
      ${themePref === 'auto' ? html`<div class="setting-sub mt-4">Currently: ${THEME_LABELS[resolved]}</div>` : ''}
    </div>
    <div class="setting-row">
      <div><div class="setting-title" id="lbl-lowpower">🔋 Low-power mode</div><div class="setting-sub">Coarser GPS, fewer updates, no animations. Good for long hikes.</div></div>
      ${switchHtml('toggle-low-power', state.lowPower, 'lbl-lowpower')}
    </div>
    ${typeof voiceSettingsHtml === 'function' ? voiceSettingsHtml() : ''}
    <div class="setting-block">
      <div class="setting-title">📴 Offline map cache</div>
      <div class="setting-sub" id="settings-cache-info">Checking…</div>
      <button class="btn btn-secondary btn-sm mt-8" data-action="clear-offline-cache">Clear cached tiles</button>
    </div>
    ${canInstall() ? html`<div class="setting-block"><button class="btn btn-primary w-100" data-action="install-app">📲 Install Terra Incognita</button></div>` : ''}
    ${iosHint ? html`<div class="setting-sub mt-8">To install on iPhone/iPad: Share → “Add to Home Screen”.</div>` : ''}
    <div class="setting-sub mt-12">Terra Incognita v${APP_VERSION} · API ${online ? 'connected' : 'unreachable'}${isStandalone() ? ' · installed app' : ''}</div>
    <button class="btn btn-primary btn-sm w-100 mt-12" data-action="close-dialog">Done</button>
  `;
}

let _settingsOpen = false;
function openSettings() {
  _settingsOpen = true;
  openDialog(settingsHtml());
  fillCacheInfo();
}
async function fillCacheInfo() {
  const el = $('settings-cache-info');
  if (!el) return;
  const s = await offlineCacheStats();
  el.textContent = `${s.tiles} map tiles stored${s.usage != null ? ` · ${formatBytes(s.usage)} of browser storage in use` : ''}. Tiles you view are kept for offline use.`;
}
/** Re-render the dialog in place (keeps focus on the same control) when something changed under it. */
function refreshSettingsDialog() {
  if (!_settingsOpen) return;
  if (!dialogOpen() || !$('help-dialog').querySelector('[data-action="set-theme"]')) { _settingsOpen = false; return; }
  const focused = document.activeElement && document.activeElement.dataset ? { a: document.activeElement.dataset.action, t: document.activeElement.dataset.themeName } : null;
  setHtml($('help-dialog'), settingsHtml());
  if (focused && focused.a) {
    const sel = `[data-action="${focused.a}"]` + (focused.t ? `[data-theme-name="${focused.t}"]` : '');
    const el = $('help-dialog').querySelector(sel);
    if (el) el.focus();
  }
  fillCacheInfo();
}

registerActions({
  'open-settings': () => openSettings(),
  'set-theme': (el) => applyTheme(el.dataset.themeName),
  'cycle-theme': () => cycleTheme(),
  'toggle-low-power': () => setLowPower(!state.lowPower),
  'low-power-on': () => { setLowPower(true); toast('🔋 Low-power mode on', 'success'); },
  'clear-offline-cache': async () => { await clearOfflineCaches(); toast('Offline map cache cleared', 'success'); fillCacheInfo(); },
  'install-app': () => installApp(),
});

function initSettings() {
  onEscape(() => { _settingsOpen = false; return false; }); // let the dialog's own handler close it
  applyTheme(store.get(LS.theme, 'auto'), { persist: false });
  // "auto" follows the OS live (day/night switch, increase-contrast toggle)
  ['(prefers-color-scheme: light)', '(prefers-contrast: more)'].forEach(q => {
    window.matchMedia(q).addEventListener('change', () => { if (themePref === 'auto') applyTheme('auto', { persist: false }); });
  });
  setLowPower(store.get(LS.lowPower) === '1', { persist: false });
  watchBattery();
}
