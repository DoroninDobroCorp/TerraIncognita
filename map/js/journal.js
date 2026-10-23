// Journal: visits (+ notes), trips (+ export), exploration stats.
// Classic script (no modules): every file in map/js shares one global scope.
'use strict';

const dateStr = (v) => { const d = new Date(v); return isNaN(d) ? '—' : d.toLocaleDateString(); };
const dateTimeStr = (v) => { const d = new Date(v); return isNaN(d) ? '—' : d.toLocaleString(); };

// ── Visits ──
async function loadVisits() {
  const list = $('visits-list');
  const filter = $('visits-filter').value;
  showSkeleton(list);
  try {
    const q = filter ? `?status=${enc(filter)}&limit=50` : '?limit=50';
    const data = await api('/api/visits' + q);
    if (!data.visits || !data.visits.length) { setHtml(list, emptyStateHtml('📓', 'No visits yet. Check in at places on the map to start tracking!')); return; }
    setHtml(list, html`${data.visits.map(v => html`
      <div class="list-item" role="button" tabindex="0" data-action="visit-detail" data-id="${v.id}">
        <div class="item-icon">${v.status==='visited'?'✅':v.status==='want_to_visit'?'📌':'⏭️'}</div>
        <div class="item-body">
          <div class="item-title">${v.place_name||v.place_id}</div>
          <div class="item-sub">${dateStr(v.visited_at||v.created_at)} · ${v.duration_minutes?parseInt(v.duration_minutes)+' min':'—'}</div>
        </div>
        <span class="tag ${v.status==='visited'?'green':v.status==='want_to_visit'?'blue':'gray'}">${v.status}</span>
      </div>`)}`);
  } catch { setHtml(list, errorStateHtml('📓', 'Could not load visits. Check that the API is running.', 'load-visits')); }
}

async function showVisitDetail(visitId) {
  try {
    const [visit, notesData] = await Promise.all([api(`/api/visits/${enc(visitId)}`), api(`/api/visits/${enc(visitId)}/notes`)]);
    const notes = notesData.notes || [];
    setHtml($('visits-list'), html`
      <div class="card mb-14">
        <div class="row-between">
          <div class="card-title">${visit.place_name||visit.place_id}</div>
          <button class="btn btn-danger btn-sm" data-action="delete-visit" data-id="${visitId}" aria-label="Delete visit">🗑️</button>
        </div>
        <div class="card-meta">Status: ${visit.status} · ${visit.duration_minutes?visit.duration_minutes+' min':'—'}</div>
        <div class="card-meta">${dateTimeStr(visit.visited_at||visit.created_at)}</div>
      </div>
      <h3 class="subhead">📝 Notes (${notes.length})</h3>
      <div id="visit-notes">${notes.length ? notes.map(n => html`
        <div class="card mb-8">
          <div class="text-body">${n.text||''}</div>
          ${n.rating?html`<div class="text-small text-secondary mt-4">🎭 Atmosphere: ${n.rating.atmosphere||'—'} · ♿ Access: ${n.rating.accessibility||'—'} · 📸 Photo: ${n.rating.photogenic||'—'} · ✨ Unique: ${n.rating.uniqueness||'—'}</div>`:''}
          ${n.tags&&n.tags.length?html`<div class="mt-4">${n.tags.map(t => html`<span class="tag gray">${t}</span>`)}</div>`:''}
        </div>`) : html`<div class="text-small text-secondary">No notes yet</div>`}</div>
      <div class="mt-14">
        <div class="form-group"><label class="form-label" for="new-note-text">Add Note</label><textarea class="input" id="new-note-text" placeholder="Write about your experience..."></textarea></div>
        <div class="form-row">
          <div class="form-group"><label class="form-label" for="note-atmosphere">Atmosphere (0-5)</label><input type="number" class="input" id="note-atmosphere" min="0" max="5" placeholder="0-5"></div>
          <div class="form-group"><label class="form-label" for="note-photogenic">Photogenic (0-5)</label><input type="number" class="input" id="note-photogenic" min="0" max="5" placeholder="0-5"></div>
        </div>
        <div class="form-row">
          <div class="form-group"><label class="form-label" for="note-accessibility">Accessibility (0-5)</label><input type="number" class="input" id="note-accessibility" min="0" max="5" placeholder="0-5"></div>
          <div class="form-group"><label class="form-label" for="note-uniqueness">Uniqueness (0-5)</label><input type="number" class="input" id="note-uniqueness" min="0" max="5" placeholder="0-5"></div>
        </div>
        <div class="form-group"><label class="form-label" for="note-tags">Tags (comma-separated)</label><input class="input" id="note-tags" placeholder="beautiful, eerie"></div>
        <button class="btn btn-primary btn-sm w-100" data-action="add-note" data-id="${visitId}">Add Note</button>
      </div>
      <button class="btn btn-secondary btn-sm mt-12" data-action="load-visits">← Back to list</button>
    `);
  } catch(e) { toast('Error loading visit','error'); }
}

async function addNote(visitId) {
  const text = $('new-note-text').value;
  if (!text.trim()) { toast('Write something first','warning'); return; }
  const rating = {};
  ['atmosphere','photogenic','accessibility','uniqueness'].forEach(k => { const v=parseInt($('note-'+k).value); if(v>=0&&v<=5) rating[k]=v; });
  const tags = $('note-tags').value.split(',').map(t=>t.trim()).filter(Boolean);
  try {
    await api(`/api/visits/${enc(visitId)}/notes`,{method:'POST',body:{text,rating:Object.keys(rating).length?rating:undefined,tags:tags.length?tags:undefined}});
    toast('Note added!','success'); showVisitDetail(visitId);
  } catch { toast('Failed to add note','error'); }
}

async function deleteVisit(id) {
  if(!confirm('Delete this visit?'))return;
  try{await api(`/api/visits/${enc(id)}`,{method:'DELETE'});toast('Visit deleted','success');loadVisits();}catch{toast('Delete failed','error');}
}

// ── Trips ──
async function loadTrips() {
  const list = $('trips-list');
  showSkeleton(list);
  try {
    const data = await api('/api/trips?limit=50');
    if (!data.trips||!data.trips.length) { setHtml(list, emptyStateHtml('✈️', 'No trips yet. Create one to group your visits!')); return; }
    setHtml(list, html`${data.trips.map(t => html`
      <div class="list-item" role="button" tabindex="0" data-action="trip-detail" data-id="${t.id}">
        <div class="item-icon">✈️</div>
        <div class="item-body">
          <div class="item-title">${t.name}</div>
          <div class="item-sub">${t.region||'—'} · ${t.start_date?utcDateStr(t.start_date):''} – ${t.end_date?utcDateStr(t.end_date):''}</div>
        </div>
      </div>`)}`);
  } catch { setHtml(list, errorStateHtml('✈️', 'Could not load trips.', 'load-trips')); }
}
function showCreateTrip() { $('create-trip-form').hidden = false; $('trip-name').focus(); }
function hideCreateTrip() { $('create-trip-form').hidden = true; }
async function createTrip() {
  const name=$('trip-name').value.trim(), region=$('trip-region').value.trim(), start=$('trip-start').value, end=$('trip-end').value;
  if(!name){toast('Please enter a trip name','warning');return;}
  if(name.length>100){toast('Trip name is too long (max 100 chars)','warning');return;}
  if(start && end && new Date(start) > new Date(end)){toast('Start date must be before end date','warning');return;}
  const btn=$('trip-create'); btn.disabled=true; btn.textContent='Creating...';
  try{ await api('/api/trips',{method:'POST',body:{name,region:region||undefined,start_date:start||undefined,end_date:end||undefined}});toast('Trip created!','success');analytics.track('trip_created');hideCreateTrip();loadTrips();}
  catch(e){toast('Failed to create trip: '+(apiDetail(e)||'check connection'),'error');}
  finally{btn.disabled=false;btn.textContent='Create';}
}
async function showTripDetail(id) {
  try {
    const [trip, summaryResp] = await Promise.all([api(`/api/trips/${enc(id)}`), api(`/api/trips/${enc(id)}/summary`).catch(()=>null)]);
    const summary = summaryResp ? (summaryResp.summary || summaryResp) : null;
    setHtml($('trips-list'), html`
      <div class="card mb-14">
        <div class="row-between"><div class="card-title">${trip.name}</div><button class="btn btn-danger btn-sm" data-action="delete-trip" data-id="${id}" aria-label="Delete trip">🗑️</button></div>
        <div class="card-meta">${trip.region||'—'} · ${trip.start_date?utcDateStr(trip.start_date):'—'} → ${trip.end_date?utcDateStr(trip.end_date):'—'}</div>
        ${summary?html`<div class="mt-10 text-small"><strong>${num(summary.total_places)}</strong> places · <strong>${num(summary.total_distance_km).toFixed(1)}</strong> km · <strong>${num(summary.total_hours).toFixed(1)}</strong> hours</div>`:''}
      </div>
      <div class="card-actions">
        <button class="btn btn-primary btn-sm" data-action="export-trip" data-id="${id}" data-fmt="markdown">📄 Markdown</button>
        <button class="btn btn-primary btn-sm" data-action="export-trip" data-id="${id}" data-fmt="json">📋 JSON</button>
        <button class="btn btn-secondary btn-sm" data-action="load-trips">← Back</button>
      </div>`);
  } catch { toast('Error loading trip','error'); }
}
async function deleteTrip(id){
  if(!confirm('Delete trip?'))return;
  try{await api(`/api/trips/${enc(id)}`,{method:'DELETE'});toast('Trip deleted','success');loadTrips();}catch{toast('Failed','error');}
}
async function exportTrip(id,fmt){
  try{
    const d=await api(`/api/trips/${enc(id)}/export`,{method:'POST',body:{format:fmt}});
    downloadText(d.filename||`trip.${fmt}`, d.content, 'text/plain');
    toast('Exported!','success');
  }catch{toast('Export failed','error');}
}

// ── Stats ──
async function loadStats() {
  const c=$('stats-content');
  showSkeleton(c);
  try {
    const [resp,heatmap] = await Promise.all([api('/api/stats'), api('/api/stats/heatmap').catch(()=>({points:[]}))]);
    const stats = resp.stats || resp;  // GET /api/stats wraps the numbers in {"stats": {...}}
    const streak = stats.streak;
    const cats = stats.by_category || stats.top_categories || [];
    const visited = stats.total_visited ?? stats.total_visits ?? 0;
    setHtml(c, html`
      <div class="card mb-14">
        <div class="card-title">📊 Exploration Statistics</div>
        <div class="stat-grid">
          <div><div class="stat-value">${num(visited)}</div><div class="stat-label">Places Visited</div></div>
          <div><div class="stat-value">${num(stats.total_distance_km).toFixed(1)}</div><div class="stat-label">km Traveled</div></div>
          <div><div class="stat-value">${num(stats.total_hours).toFixed(1)}</div><div class="stat-label">Hours Exploring</div></div>
          <div><div class="stat-value">${num(stats.total_want_to_visit)}</div><div class="stat-label">Want to Visit</div></div>
        </div>
      </div>
      ${streak?html`<div class="card mb-14"><div class="card-title">🔥 Streak</div><div class="stat-value mt-4">${num(streak.current_streak)} days</div><div class="stat-label">Longest: ${num(streak.longest_streak)} days</div></div>`:''}
      ${cats.length?html`<div class="card"><div class="card-title">🏷️ Top Categories</div>${cats.map(c => html`<div class="row-between mt-6"><span class="text-small">${(CATEGORIES[c.category]||{icon:'📍'}).icon} ${c.category}</span><span class="text-small text-accent bold">${num(c.count)}</span></div>`)}</div>`:''}
      <div class="card mt-14"><div class="card-title">🗺️ Activity Heatmap</div><div class="text-small text-secondary mt-4">${heatmap.points?.length||0} data points</div>${heatmap.points?.length ? html`<button class="btn btn-secondary btn-sm mt-8" data-action="show-heatmap">🔥 Show on map</button>` : ''}</div>
    `);
  } catch { setHtml(c, errorStateHtml('📊', 'Could not load stats.', 'load-stats')); }
}

TI.tabHooks['j:stats'] = () => loadStats();

registerActions({
  'load-visits': () => loadVisits(),
  'visit-detail': (el) => showVisitDetail(el.dataset.id),
  'delete-visit': (el) => deleteVisit(el.dataset.id),
  'add-note': (el) => addNote(el.dataset.id),
  'load-trips': () => loadTrips(),
  'show-create-trip': () => showCreateTrip(),
  'hide-create-trip': () => hideCreateTrip(),
  'create-trip': () => createTrip(),
  'trip-detail': (el) => showTripDetail(el.dataset.id),
  'delete-trip': (el) => deleteTrip(el.dataset.id),
  'export-trip': (el) => exportTrip(el.dataset.id, el.dataset.fmt),
  'load-stats': () => loadStats(),
});
registerChange({ 'load-visits': () => loadVisits() });
