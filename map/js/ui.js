// Sidebar navigation, tab groups, toasts, skeletons, onboarding.
// Classic script (no modules): shares the global scope with the other map/js files.

// ── Navigation ──
document.querySelectorAll('.nav-btn[data-section]').forEach(btn => {
  btn.addEventListener('click', () => {
    const sec = btn.dataset.section;
    analytics.track('navigate', { section: sec });
    document.querySelectorAll('.nav-btn[data-section]').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    document.getElementById('section-' + sec).classList.add('active');
    if (sec === 'map' && typeof map !== 'undefined' && map) map.resize();
    if (sec === 'journal') loadVisits();
    if (sec === 'gamification') loadProfile();
    if (sec === 'community') loadCommunityPlaces();
    if (sec === 'offline') loadOfflineStatus();
  });
});

// ── Tab systems ──
function setupTabs(parent, prefix) {
  document.querySelectorAll(parent + ' .tab').forEach(t => {
    t.addEventListener('click', () => {
      document.querySelectorAll(parent + ' .tab').forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      const attr = t.dataset[prefix + 'tab'] || t.dataset.jtab || t.dataset.rtab || t.dataset.gtab || t.dataset.ctab || t.dataset.otab;
      document.querySelectorAll('.' + prefix + 'tab').forEach(p => p.style.display = 'none');
      const el = document.getElementById(prefix + 'tab-' + attr);
      if (el) el.style.display = 'block';
    });
  });
}
setupTabs('#section-journal .panel-header', 'j');
setupTabs('#route-tabs', 'r');
setupTabs('#gam-tabs', 'g');
setupTabs('#comm-tabs', 'c');
setupTabs('#off-tabs', 'o');

// ── Toast notifications ──
function toast(msg, type = 'info', duration = 3500) {
  const c = document.getElementById('toast-container');
  // Limit max visible toasts to prevent layout overflow
  while (c.children.length >= 5) c.firstChild.remove();
  const t = document.createElement('div');
  t.className = 'toast ' + type;
  t.textContent = msg;
  c.appendChild(t);
  setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, duration);
}

// ── Skeleton loading helper ──
function showSkeleton(el, count = 3) {
  el.innerHTML = Array.from({length: count}, () => '<div class="skeleton skeleton-block"></div>').join('');
}

// ── Onboarding (first visit) ──
function showOnboarding() {
  if (localStorage.getItem('ti_onboarded')) return;
  if (window._userInteracted) return;
  localStorage.setItem('ti_onboarded', '1');
  analytics.track('onboarding_shown');
  const overlay = document.getElementById('help-overlay');
  const dialog = document.getElementById('help-dialog');
  dialog.innerHTML = `
    <h3>👋 Welcome to Terra Incognita!</h3>
    <p>Your personal explorer's companion for discovering hidden and unusual places.</p>
    <ul style="margin:12px 0;padding-left:20px">
      <li><strong>🗺️ Map</strong> — explore hidden places near you with category filters</li>
      <li><strong>🤖 AI Chat</strong> — ask the AI to find places matching your mood</li>
      <li><strong>📓 Journal</strong> — track visits, write notes, group into trips</li>
      <li><strong>🛤️ Routes</strong> — build optimized routes with GPX/KML export</li>
      <li><strong>🏆 Gamification</strong> — earn XP, unlock achievements, climb the leaderboard</li>
      <li><strong>👥 Community</strong> — submit places, write reviews, follow explorers</li>
      <li><strong>📴 Offline</strong> — download maps for areas without internet</li>
    </ul>
    <p style="font-size:12px;color:var(--text-secondary)">Press <strong>?</strong> in any section for contextual help.</p>
    <button class="btn btn-primary" style="margin-top:12px;width:100%" onclick="closeHelp();analytics.track('onboarding_closed')">Start Exploring! 🚀</button>
  `;
  overlay.classList.add('visible');
}
// Cancel onboarding if user interacts before timeout
window._userInteracted = false;
document.addEventListener('click', () => { window._userInteracted = true; }, { once: true });
document.addEventListener('keydown', () => { window._userInteracted = true; }, { once: true });
setTimeout(showOnboarding, 1500);
// Populate user ID input with auto-generated ID
const uidInput = document.getElementById('my-user-id');
if (uidInput && !uidInput.value) uidInput.value = USER_ID;

function showOnMap(lat,lng){
  if(!lat||!lng)return;
  document.querySelectorAll('.nav-btn[data-section]').forEach(b=>b.classList.remove('active'));
  document.querySelector('.nav-btn[data-section="map"]').classList.add('active');
  document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));
  document.getElementById('section-map').classList.add('active');
  map.resize(); map.flyTo({center:[lng,lat],zoom:15});
}
