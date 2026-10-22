// Journal: visits, trips, stats.
// Classic script (no modules): shares the global scope with the other map/js files.

// ═══════════════════════════════════════════════════════
//  JOURNAL
// ═══════════════════════════════════════════════════════
async function loadVisits() {
  const list = document.getElementById('visits-list');
  const filter = document.getElementById('visits-filter').value;
  list.innerHTML='<div class="skeleton skeleton-block"></div><div class="skeleton skeleton-block"></div><div class="skeleton skeleton-block"></div>';
  try {
    const q = filter ? `?status=${filter}&limit=50` : '?limit=50';
    const data = await api('/api/visits' + q);
    if (!data.visits || !data.visits.length) { list.innerHTML = '<div class="empty-state"><div class="empty-icon">📓</div><div class="empty-text">No visits yet. Check in at places on the map to start tracking!</div></div>'; return; }
    list.innerHTML = data.visits.map(v => `
      <div class="list-item" onclick="showVisitDetail('${escHtml(v.id)}')">
        <div class="item-icon">${v.status==='visited'?'✅':v.status==='want_to_visit'?'📌':'⏭️'}</div>
        <div class="item-body">
          <div class="item-title">${escHtml(v.place_name||v.place_id)}</div>
          <div class="item-sub">${new Date(v.visited_at||v.created_at).toLocaleDateString()} · ${v.duration_minutes?parseInt(v.duration_minutes)+' min':'—'}</div>
        </div>
        <span class="tag ${v.status==='visited'?'green':v.status==='want_to_visit'?'blue':'gray'}">${escHtml(v.status)}</span>
      </div>
    `).join('');
  } catch { list.innerHTML = '<div class="empty-state"><div class="empty-icon">📓</div><div class="empty-text">Could not load visits. Check that the API is running.</div><button class="btn btn-sm btn-secondary" style="margin-top:12px" onclick="loadVisits()">🔄 Retry</button></div>'; }
}

async function showVisitDetail(visitId) {
  try {
    const [visit, notesData] = await Promise.all([api(`/api/visits/${visitId}`), api(`/api/visits/${visitId}/notes`)]);
    const notes = notesData.notes || [];
    const html = `
      <div class="card" style="margin-bottom:14px">
        <div style="display:flex;justify-content:space-between;align-items:start">
          <div class="card-title">${escHtml(visit.place_name||visit.place_id)}</div>
          <button class="btn btn-danger btn-sm" onclick="deleteVisit('${visitId}')">🗑️</button>
        </div>
        <div class="card-meta">Status: ${visit.status} · ${visit.duration_minutes?visit.duration_minutes+' min':'—'}</div>
        <div class="card-meta">${new Date(visit.visited_at||visit.created_at).toLocaleString()}</div>
      </div>
      <h3 style="font-size:14px;margin-bottom:10px">📝 Notes (${notes.length})</h3>
      <div id="visit-notes">${notes.map(n=>`
        <div class="card" style="margin-bottom:8px">
          <div style="font-size:13px">${escHtml(n.text||'')}</div>
          ${n.rating?`<div style="margin-top:4px;font-size:11px;color:var(--text-secondary)">🎭 Atmosphere: ${n.rating.atmosphere||'—'} · ♿ Access: ${n.rating.accessibility||'—'} · 📸 Photo: ${n.rating.photogenic||'—'} · ✨ Unique: ${n.rating.uniqueness||'—'}</div>`:''}
          ${n.tags&&n.tags.length?`<div style="margin-top:4px">${n.tags.map(t=>`<span class="tag gray">${t}</span>`).join('')}</div>`:''}
        </div>
      `).join('')||'<div style="font-size:12px;color:var(--text-secondary)">No notes yet</div>'}</div>
      <div style="margin-top:14px">
        <div class="form-group"><label class="form-label">Add Note</label><textarea class="input" id="new-note-text" placeholder="Write about your experience..."></textarea></div>
        <div class="form-row">
          <div class="form-group"><label class="form-label">Atmosphere (0-5)</label><input type="number" class="input" id="note-atmosphere" min="0" max="5" placeholder="0-5"></div>
          <div class="form-group"><label class="form-label">Photogenic (0-5)</label><input type="number" class="input" id="note-photogenic" min="0" max="5" placeholder="0-5"></div>
        </div>
        <div class="form-row">
          <div class="form-group"><label class="form-label">Accessibility (0-5)</label><input type="number" class="input" id="note-accessibility" min="0" max="5" placeholder="0-5"></div>
          <div class="form-group"><label class="form-label">Uniqueness (0-5)</label><input type="number" class="input" id="note-uniqueness" min="0" max="5" placeholder="0-5"></div>
        </div>
        <div class="form-group"><label class="form-label">Tags (comma-separated)</label><input class="input" id="note-tags" placeholder="beautiful, eerie"></div>
        <button class="btn btn-primary btn-sm" onclick="addNote('${visitId}')" style="width:100%">Add Note</button>
      </div>
      <button class="btn btn-secondary btn-sm" onclick="loadVisits()" style="margin-top:12px">← Back to list</button>
    `;
    document.getElementById('visits-list').innerHTML = html;
  } catch(e) { toast('Error loading visit','error'); }
}

async function addNote(visitId) {
  const text = document.getElementById('new-note-text').value;
  if (!text.trim()) { toast('Write something first','warning'); return; }
  const rating = {};
  ['atmosphere','photogenic','accessibility','uniqueness'].forEach(k => { const v=parseInt(document.getElementById('note-'+k).value); if(v>=0&&v<=5) rating[k]=v; });
  const tags = document.getElementById('note-tags').value.split(',').map(t=>t.trim()).filter(Boolean);
  try {
    await api(`/api/visits/${visitId}/notes`,{method:'POST',body:{text,rating:Object.keys(rating).length?rating:undefined,tags:tags.length?tags:undefined}});
    toast('Note added!','success'); showVisitDetail(visitId);
  } catch { toast('Failed to add note','error'); }
}

async function deleteVisit(id) { if(!confirm('Delete this visit?'))return; try{await api(`/api/visits/${id}`,{method:'DELETE'});toast('Visit deleted','success');loadVisits();}catch{toast('Delete failed','error');} }

// Trips
async function loadTrips() {
  const list = document.getElementById('trips-list');
  list.innerHTML='<div class="skeleton skeleton-block"></div><div class="skeleton skeleton-block"></div><div class="skeleton skeleton-block"></div>';
  try {
    const data = await api('/api/trips?limit=50');
    if (!data.trips||!data.trips.length) { list.innerHTML = '<div class="empty-state"><div class="empty-icon">✈️</div><div class="empty-text">No trips yet. Create one to group your visits!</div></div>'; return; }
    list.innerHTML = data.trips.map(t=>`
      <div class="list-item" onclick="showTripDetail('${escHtml(t.id)}')">
        <div class="item-icon">✈️</div>
        <div class="item-body">
          <div class="item-title">${escHtml(t.name)}</div>
          <div class="item-sub">${escHtml(t.region||'—')} · ${t.start_date?new Date(t.start_date).toLocaleDateString():''} – ${t.end_date?new Date(t.end_date).toLocaleDateString():''}</div>
        </div>
      </div>`).join('');
  } catch { list.innerHTML = '<div class="empty-state"><div class="empty-icon">✈️</div><div class="empty-text">Could not load trips.</div><button class="btn btn-sm btn-secondary" style="margin-top:12px" onclick="loadTrips()">🔄 Retry</button></div>'; }
}
function showCreateTrip(){document.getElementById('create-trip-form').style.display='block';}
function hideCreateTrip(){document.getElementById('create-trip-form').style.display='none';}
async function createTrip() {
  const name=document.getElementById('trip-name').value.trim(), region=document.getElementById('trip-region').value.trim(), start=document.getElementById('trip-start').value, end=document.getElementById('trip-end').value;
  if(!name){toast('Please enter a trip name','warning');return;}
  if(name.length>100){toast('Trip name is too long (max 100 chars)','warning');return;}
  if(start && end && new Date(start) > new Date(end)){toast('Start date must be before end date','warning');return;}
  const btn=document.querySelector('#create-trip-form .btn-primary'); btn.disabled=true; btn.textContent='Creating...';
  try{ await api('/api/trips',{method:'POST',body:{name,region:region||undefined,start_date:start||undefined,end_date:end||undefined}});toast('Trip created!','success');analytics.track('trip_created');hideCreateTrip();loadTrips();}catch(e){toast('Failed to create trip: '+(e.message||'check connection'),'error');}finally{btn.disabled=false;btn.textContent='Create';}
}
async function showTripDetail(id) {
  try {
    const [trip, summary] = await Promise.all([api(`/api/trips/${id}`), api(`/api/trips/${id}/summary`).catch(()=>null)]);
    document.getElementById('trips-list').innerHTML = `
      <div class="card" style="margin-bottom:14px">
        <div style="display:flex;justify-content:space-between;align-items:start"><div class="card-title">${escHtml(trip.name)}</div><button class="btn btn-danger btn-sm" onclick="deleteTrip('${id}')">🗑️</button></div>
        <div class="card-meta">${trip.region||'—'} · ${trip.start_date||'—'} → ${trip.end_date||'—'}</div>
        ${summary?`<div style="margin-top:10px;font-size:12px"><strong>${summary.total_places||0}</strong> places · <strong>${(summary.total_distance_km||0).toFixed(1)}</strong> km · <strong>${(summary.total_hours||0).toFixed(1)}</strong> hours</div>`:''}
      </div>
      <div class="card-actions">
        <button class="btn btn-primary btn-sm" onclick="exportTrip('${id}','markdown')">📄 Markdown</button>
        <button class="btn btn-primary btn-sm" onclick="exportTrip('${id}','json')">📋 JSON</button>
        <button class="btn btn-secondary btn-sm" onclick="loadTrips()">← Back</button>
      </div>`;
  } catch { toast('Error loading trip','error'); }
}
async function deleteTrip(id){if(!confirm('Delete trip?'))return;try{await api(`/api/trips/${id}`,{method:'DELETE'});toast('Trip deleted','success');loadTrips();}catch{toast('Failed','error');}}
async function exportTrip(id,fmt){try{const d=await api(`/api/trips/${id}/export`,{method:'POST',body:{format:fmt}});const blob=new Blob([d.content],{type:'text/plain'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=d.filename||`trip.${fmt}`;a.click();URL.revokeObjectURL(url);toast('Exported!','success');}catch{toast('Export failed','error');}}

// Stats
async function loadStats() {
  const c=document.getElementById('stats-content');
  c.innerHTML='<div class="skeleton skeleton-block"></div><div class="skeleton skeleton-block"></div><div class="skeleton skeleton-block"></div>';
  try {
    const [stats,heatmap] = await Promise.all([api('/api/stats'), api('/api/stats/heatmap').catch(()=>({points:[]}))]);
    c.innerHTML = `
      <div class="card" style="margin-bottom:14px">
        <div class="card-title">📊 Exploration Statistics</div>
        <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-top:12px">
          <div><div style="font-size:20px;font-weight:700;color:var(--accent)">${stats.total_visits||0}</div><div style="font-size:11px;color:var(--text-secondary)">Total Visits</div></div>
          <div><div style="font-size:20px;font-weight:700;color:var(--accent)">${(stats.total_distance_km||0).toFixed(1)}</div><div style="font-size:11px;color:var(--text-secondary)">km Traveled</div></div>
          <div><div style="font-size:20px;font-weight:700;color:var(--accent)">${(stats.total_hours||0).toFixed(1)}</div><div style="font-size:11px;color:var(--text-secondary)">Hours Exploring</div></div>
          <div><div style="font-size:20px;font-weight:700;color:var(--accent)">${stats.unique_categories||0}</div><div style="font-size:11px;color:var(--text-secondary)">Categories</div></div>
        </div>
      </div>
      ${stats.streak?`<div class="card" style="margin-bottom:14px"><div class="card-title">🔥 Streak</div><div style="font-size:20px;font-weight:700;color:var(--accent);margin-top:4px">${stats.streak.current_streak||0} days</div><div style="font-size:11px;color:var(--text-secondary)">Longest: ${stats.streak.longest_streak||0} days</div></div>`:''}
      ${stats.top_categories?`<div class="card"><div class="card-title">🏷️ Top Categories</div>${(stats.top_categories||[]).map(c=>`<div style="display:flex;justify-content:space-between;margin-top:6px"><span style="font-size:12px">${(CATEGORIES[c.category]||{icon:'📍'}).icon} ${c.category}</span><span style="font-size:12px;color:var(--accent);font-weight:600">${c.count}</span></div>`).join('')}</div>`:''}
      <div class="card" style="margin-top:14px"><div class="card-title">🗺️ Activity Heatmap</div><div style="font-size:12px;color:var(--text-secondary);margin-top:4px">${heatmap.points?.length||0} data points</div></div>
    `;
  } catch { c.innerHTML = '<div class="empty-state"><div class="empty-icon">📊</div><div class="empty-text">Could not load stats.</div><button class="btn btn-sm btn-secondary" style="margin-top:12px" onclick="loadStats()">🔄 Retry</button></div>'; }
}

// Journal tab changes also trigger stats load
document.querySelectorAll('[data-jtab="stats"]').forEach(t=>t.addEventListener('click',loadStats));
