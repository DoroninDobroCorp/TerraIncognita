// Shared UI: sections (sidebar), tab groups, toasts, skeletons, modal dialog, onboarding.
// Classic script (no modules): every file in map/js shares one global scope.
'use strict';

// ── Sections (sidebar navigation) ──
// Data loaders to run when a section is opened. Functions live in other files (resolved lazily).
const SECTION_HOOKS = {
  journal: () => loadVisits(),
  gamification: () => loadProfile(),
  community: () => loadCommunityPlaces(),
  offline: () => loadOfflineStatus(),
};

function showSection(sec, { runHooks = true } = {}) {
  if (!$('section-' + sec)) return;
  $$('.nav-btn[data-section]').forEach(b => {
    const on = b.dataset.section === sec;
    b.classList.toggle('active', on);
    if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  });
  $$('.section').forEach(s => s.classList.toggle('active', s.id === 'section-' + sec));
  if (sec === 'map' && typeof map !== 'undefined' && map) map.resize();
  if (runHooks && SECTION_HOOKS[sec]) SECTION_HOOKS[sec]();
}

function showOnMap(lat, lng) {
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return; // 0 is a valid coordinate
  showSection('map', { runHooks: false });
  map.resize(); map.flyTo({ center: [lng, lat], zoom: 15 });
}

// ── Tab groups ──
// Markup: <div role="tablist"> <button role="tab" data-action="tab" data-group="j" data-tab="visits">
// and panels <div id="jtab-visits" class="jtab" role="tabpanel">. Hooks: TI.tabHooks['j:stats'] = fn.
function selectTab(btn) {
  const group = btn.dataset.group, tab = btn.dataset.tab;
  const list = btn.closest('[role="tablist"]');
  $$('[role="tab"]', list).forEach(x => {
    const on = x === btn;
    x.classList.toggle('active', on);
    x.setAttribute('aria-selected', String(on));
    x.tabIndex = on ? 0 : -1;
  });
  $$('.' + group + 'tab').forEach(p => { p.hidden = true; });
  const panel = $(group + 'tab-' + tab);
  if (panel) panel.hidden = false;
  const hook = TI.tabHooks[group + ':' + tab];
  if (hook) hook();
}

// ── Toast notifications ──
function toast(msg, type = 'info', duration = 3500, action = null) {
  const c = $('toast-container');
  if (!c) return;
  // Limit max visible toasts to prevent layout overflow
  while (c.children.length >= 5) c.firstChild.remove();
  const t = document.createElement('div');
  t.className = 'toast ' + type;
  t.setAttribute('role', type === 'error' ? 'alert' : 'status');
  t.textContent = msg;
  if (action) { // {label, action}: a button that runs a registered action (e.g. "Reload")
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'toast-action'; b.textContent = action.label; b.dataset.action = action.action;
    t.appendChild(b);
  }
  c.appendChild(t);
  setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, duration);
}

// ── Skeleton loading helper ──
function skeletonHtml(count = 3) {
  return html`${Array.from({length: count}, () => raw('<div class="skeleton skeleton-block"></div>'))}`;
}
function showSkeleton(el, count = 3) { setHtml(el, skeletonHtml(count)); }

/** Standard "something went wrong" block with a retry button bound to a registered action. */
function errorStateHtml(icon, text, retryAction, retryData = {}) {
  const attrs = Object.entries(retryData).map(([k, v]) => html` data-${raw(k)}="${v}"`);
  return html`<div class="empty-state"><div class="empty-icon">${icon}</div><div class="empty-text">${text}</div>${
    retryAction ? html`<button class="btn btn-sm btn-secondary mt-12" data-action="${retryAction}"${attrs}>🔄 Retry</button>` : ''}</div>`;
}
function emptyStateHtml(icon, text) {
  return html`<div class="empty-state"><div class="empty-icon">${icon}</div><div class="empty-text">${text}</div></div>`;
}

// ── Modal dialog (help, onboarding, settings) ──
let _dialogPrevFocus = null;
function openDialog(contentHtml, { labelledBy = 'dialog-title' } = {}) {
  _dialogPrevFocus = document.activeElement;
  const d = $('help-dialog');
  setHtml(d, contentHtml);
  d.setAttribute('role', 'dialog');
  d.setAttribute('aria-modal', 'true');
  d.setAttribute('aria-labelledby', labelledBy);
  $('help-overlay').classList.add('visible');
  setTimeout(() => { const b = d.querySelector('button, [href], input, select'); if (b) b.focus(); }, 50);
}
function closeDialog() {
  const o = $('help-overlay');
  if (!o.classList.contains('visible')) return false;
  o.classList.remove('visible');
  if (_dialogPrevFocus && _dialogPrevFocus.focus) { _dialogPrevFocus.focus(); }
  _dialogPrevFocus = null;
  return true;
}
function dialogOpen() { const o = $('help-overlay'); return !!o && o.classList.contains('visible'); }

/** Keep Tab inside the open dialog. */
TI.trapFocus = function (ev) {
  if (!dialogOpen()) return;
  const f = $$('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])', $('help-dialog'));
  if (!f.length) return;
  const first = f[0], last = f[f.length - 1];
  if (!$('help-dialog').contains(document.activeElement)) { ev.preventDefault(); first.focus(); }
  else if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
  else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
};

// ── Onboarding (first visit) ──
function showOnboarding() {
  if (store.get(LS.onboarded)) return;
  if (window._userInteracted) return;
  store.set(LS.onboarded, '1');
  analytics.track('onboarding_shown');
  openDialog(html`
    <h3 id="dialog-title">👋 Welcome to Terra Incognita!</h3>
    <p>Your personal explorer's companion for discovering hidden and unusual places.</p>
    <ul class="help-list">
      <li><strong>🗺️ Map</strong> — explore hidden places near you with category filters</li>
      <li><strong>🤖 AI Chat</strong> — ask the AI to find places matching your mood</li>
      <li><strong>📓 Journal</strong> — track visits, write notes, group into trips</li>
      <li><strong>🛤️ Routes</strong> — build optimized routes with GPX/KML export</li>
      <li><strong>🏆 Gamification</strong> — earn XP, unlock achievements, climb the leaderboard</li>
      <li><strong>👥 Community</strong> — submit places, write reviews, follow explorers</li>
      <li><strong>📴 Offline</strong> — download maps for areas without internet</li>
    </ul>
    <p class="text-small text-secondary">Press <strong>?</strong> in any section for contextual help.</p>
    <button class="btn btn-primary w-100 mt-12" data-action="close-onboarding">Start Exploring! 🚀</button>
  `);
}

registerActions({
  'show-section': (el) => { analytics.track('navigate', { section: el.dataset.section }); showSection(el.dataset.section); },
  tab: (el) => selectTab(el),
  'close-dialog': () => { closeDialog(); },
  'close-onboarding': () => { closeDialog(); analytics.track('onboarding_closed'); },
  'dialog-backdrop': (el, ev) => { if (ev.target === el) closeDialog(); },
});

function initUi() {
  onEscape(() => closeDialog());
  // Cancel onboarding if the user interacts before the timeout
  window._userInteracted = false;
  document.addEventListener('click', () => { window._userInteracted = true; }, { once: true });
  document.addEventListener('keydown', () => { window._userInteracted = true; }, { once: true });
  setTimeout(showOnboarding, 1500);
  // Populate user ID input with the auto-generated ID
  const uidInput = $('my-user-id');
  if (uidInput && !uidInput.value) uidInput.value = USER_ID;
  // The id is the one stored in this browser (getUserId); a custom one is kept for next time, so that
  // authorship and karma do not split across a generated id and the one the person chose.
  if (uidInput) uidInput.addEventListener('change', () => {
    const id = getActiveUserId();
    if (id !== USER_ID) store.set(LS.userId, id);
  });
}
