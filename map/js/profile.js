// Gamification: profile, achievements, leaderboard, fog stats.
// Classic script (no modules): shares the global scope with the other map/js files.

// ═══════════════════════════════════════════════════════
//  GAMIFICATION
// ═══════════════════════════════════════════════════════
async function loadProfile() {
  try {
    const [profile, achievements] = await Promise.all([api('/api/explorer/profile'), api('/api/achievements')]);
    document.getElementById('profile-title').textContent = profile.title || 'Explorer';
    document.getElementById('profile-xp').textContent = `${profile.total_xp||0} XP · Level ${profile.level||1}`;
    document.getElementById('profile-xp-bar').style.width = (profile.level_progress_percent||0)+'%';
    document.getElementById('ps-explored').textContent = (profile.fog_explored_km2||0).toFixed(1);
    document.getElementById('ps-achievements').textContent = profile.achievements_unlocked||0;
    document.getElementById('ps-visits').textContent = state.visitedPlaces.size;

    const grid = document.getElementById('achievements-grid');
    grid.innerHTML = (achievements.achievements||[]).map(a=>`
      <div class="achievement ${a.completed?'':'locked'}">
        <div class="ach-icon">${a.definition?.icon||'🏅'}</div>
        <div class="ach-name">${escHtml(a.definition?.name||'Achievement')}</div>
        <div class="ach-desc">${escHtml(a.definition?.description||'')}</div>
        <div class="progress-bar" style="margin:6px 0 4px"><div class="progress-fill" style="width:${a.progress_percent||0}%;background:${a.completed?'var(--success)':'var(--accent)'}"></div></div>
        <div class="ach-xp">+${a.definition?.xp_reward||0} XP</div>
      </div>`).join('') || '<div class="empty-state"><div class="empty-icon">🏅</div><div class="empty-text">No achievements loaded</div></div>';
  } catch(e) { console.error(e); document.getElementById('achievements-grid').innerHTML='<div class="empty-state"><div class="empty-icon">🏆</div><div class="empty-text">Could not load profile.</div><button class="btn btn-sm btn-secondary" style="margin-top:12px" onclick="loadProfile()">🔄 Retry</button></div>'; }
}

// Leaderboard tab
document.querySelectorAll('[data-gtab="leaderboard"]').forEach(t=>t.addEventListener('click', async ()=>{
  const list=document.getElementById('leaderboard-list');
  list.innerHTML='<div class="skeleton skeleton-block"></div><div class="skeleton skeleton-block"></div><div class="skeleton skeleton-block"></div>';
  try {
    const data = await api('/api/explorer/leaderboard');
    list.innerHTML = (data.entries||[]).map((e,i)=>`
      <div class="leaderboard-row">
        <div class="leaderboard-rank ${i===0?'gold':i===1?'silver':i===2?'bronze':''}">#${i+1}</div>
        <div class="leaderboard-name">${escHtml(e.username||e.user_id)}</div>
        <div class="leaderboard-xp">${parseInt(e.total_xp)||0} XP · L${parseInt(e.level)||0}</div>
      </div>`).join('') || '<div class="empty-state">No explorers yet</div>';
  } catch { list.innerHTML='<div class="empty-state">Could not load leaderboard<br><button class="btn btn-sm btn-secondary" style="margin-top:12px" onclick="loadLeaderboard()">🔄 Retry</button></div>'; }
}));

// Fog stats tab
document.querySelectorAll('[data-gtab="fog"]').forEach(t=>t.addEventListener('click', async ()=>{
  const el=document.getElementById('fog-region-stats');
  el.innerHTML='<div class="skeleton skeleton-block"></div><div class="skeleton skeleton-block"></div><div class="skeleton skeleton-block"></div>';
  try {
    const data = await api('/api/fog/status');
    el.innerHTML = `
      <div class="card" style="margin-bottom:14px">
        <div class="card-title">🌫️ Total Explored</div>
        <div style="font-size:24px;font-weight:700;color:var(--accent);margin-top:8px">${(data.total_explored_area_km2||0).toFixed(2)} km²</div>
        <div style="font-size:12px;color:var(--text-secondary)">${data.total_explored_cells||0} cells</div>
      </div>
      ${(data.regions||[]).map(r=>`
        <div class="card" style="margin-bottom:8px">
          <div style="display:flex;justify-content:space-between"><span>${escHtml(r.region_name)}</span><span style="color:var(--accent);font-weight:600">${(r.coverage_percent||0).toFixed(1)}%</span></div>
          <div class="progress-bar" style="margin-top:6px"><div class="progress-fill" style="width:${r.coverage_percent||0}%"></div></div>
        </div>`).join('')}
    `;
  } catch { el.innerHTML='<div class="empty-state">Could not load fog stats<br><button class="btn btn-sm btn-secondary" style="margin-top:12px" onclick="loadFogStats()">🔄 Retry</button></div>'; }
}));
