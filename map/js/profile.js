// Gamification tab: explorer profile, achievements, leaderboard, fog statistics.
// Classic script (no modules): every file in map/js shares one global scope.
'use strict';

/** Backend levels are enum strings ("scout"); older shapes used numbers. */
function levelLabel(level) {
  if (typeof level === 'number') return 'Level ' + level;
  const s = String(level || 'novice');
  return s.charAt(0).toUpperCase() + s.slice(1);
}

async function loadProfile() {
  try {
    const [pr, achievements] = await Promise.all([api('/api/explorer/profile'), api('/api/achievements')]);
    const profile = pr.profile || pr;  // GET /api/explorer/profile wraps the data in {"profile": {...}}
    $('profile-title').textContent = profile.title || 'Explorer';
    $('profile-xp').textContent = `${profile.total_xp||0} XP · ${levelLabel(profile.level)}`;
    $('profile-xp-bar').style.width = clamp(num(profile.level_progress_percent), 0, 100)+'%';
    $('ps-explored').textContent = num(profile.fog_explored_km2).toFixed(1);
    $('ps-achievements').textContent = profile.achievements_unlocked||0;
    $('ps-visits').textContent = state.visitedPlaces.size;

    const list = achievements.achievements || [];
    setHtml($('achievements-grid'), list.length ? html`${list.map(a => html`
      <div class="achievement ${a.completed?'':'locked'}">
        <div class="ach-icon">${a.definition?.icon||'🏅'}</div>
        <div class="ach-name">${a.definition?.name||'Achievement'}</div>
        <div class="ach-desc">${a.definition?.description||''}</div>
        <div class="progress-bar progress-bar-sm"><div class="progress-fill${a.completed?' done':''}" style="width:${clamp(num(a.progress_percent),0,100)}%"></div></div>
        <div class="ach-xp">+${a.definition?.xp_reward||0} XP</div>
      </div>`)}` : emptyStateHtml('🏅', 'No achievements loaded'));
  } catch(e) {
    console.error(e);
    setHtml($('achievements-grid'), errorStateHtml('🏆', 'Could not load profile.', 'load-profile'));
  }
}

async function loadLeaderboard() {
  const list=$('leaderboard-list');
  showSkeleton(list);
  try {
    const data = await api('/api/explorer/leaderboard');
    const entries = data.entries || [];
    setHtml(list, entries.length ? html`${entries.map((e,i) => html`
      <div class="leaderboard-row">
        <div class="leaderboard-rank ${i===0?'gold':i===1?'silver':i===2?'bronze':''}">#${i+1}</div>
        <div class="leaderboard-name">${e.username||e.user_id}</div>
        <div class="leaderboard-xp">${parseInt(e.total_xp)||0} XP · ${levelLabel(e.level)}</div>
      </div>`)}` : html`<div class="empty-state">No explorers yet</div>`);
  } catch { setHtml(list, errorStateHtml('🏆', 'Could not load leaderboard', 'load-leaderboard')); }
}

async function loadFogStats() {
  const el=$('fog-region-stats');
  showSkeleton(el);
  try {
    const resp = await api('/api/fog/status');
    const data = resp.state || resp;  // wrapped in {"state": {...}}
    setHtml(el, html`
      <div class="card mb-14">
        <div class="card-title">🌫️ Total Explored</div>
        <div class="stat-value stat-value-lg mt-8">${num(data.total_explored_area_km2).toFixed(2)} km²</div>
        <div class="text-small text-secondary">${data.total_explored_cells||0} cells</div>
      </div>
      ${(data.regions||[]).map(r => html`
        <div class="card mb-8">
          <div class="row-between"><span>${r.region_name}</span><span class="text-accent bold">${num(r.coverage_percent).toFixed(1)}%</span></div>
          <div class="progress-bar mt-6"><div class="progress-fill" style="width:${clamp(num(r.coverage_percent),0,100)}%"></div></div>
        </div>`)}
    `);
  } catch { setHtml(el, errorStateHtml('🌫️', 'Could not load fog stats', 'load-fog-stats')); }
}

TI.tabHooks['g:leaderboard'] = () => loadLeaderboard();
TI.tabHooks['g:fog'] = () => loadFogStats();

registerActions({
  'load-profile': () => loadProfile(),
  'load-leaderboard': () => loadLeaderboard(),
  'load-fog-stats': () => loadFogStats(),
});
