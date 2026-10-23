// Offline tab (backend-managed): tile regions, cached places, sync queue, storage status.
// This is the SERVER's offline feature; the browser-side PWA cache lives in pwa.js + sw.js.
// Classic script (no modules): every file in map/js shares one global scope.
'use strict';

async function loadOfflineStatus() {
  try {
    const [status,connectivity] = await Promise.all([api('/api/offline/status').catch(()=>({regions_count:0,cached_places_count:0,pending_sync_count:0})), api('/api/offline/connectivity').catch(()=>({is_online:true}))]);
    const badge = $('offline-status-badge');
    badge.textContent = connectivity.is_online?'Online':'Offline';
    badge.style.background = connectivity.is_online?'var(--success)':'var(--danger)';
    setHtml($('offline-storage-info'), html`
      <div>Regions: ${status.regions_count||0} · Cached places: ${status.cached_places_count||0}</div>
      <div>Pending sync: ${status.pending_sync_count||0}</div>
      <div>Disk: ${formatBytes(status.total_disk_usage_bytes||0)}</div>
    `);
    loadTileRegions();
  } catch { $('offline-storage-info').textContent='Could not load status'; }
}

async function loadTileRegions() {
  const list=$('tile-regions-list');
  try {
    const data=await api('/api/offline/tiles/regions');
    const regions = data.regions || [];
    setHtml(list, regions.length ? html`${regions.map(r => html`
      <div class="card mb-8">
        <div class="row-between align-start"><div class="card-title">${r.name}</div><button class="btn btn-danger btn-sm" data-action="delete-region" data-id="${r.region_id}" aria-label="Delete region">🗑️</button></div>
        <div class="card-meta">${r.tile_count||0} tiles · ${formatBytes(r.disk_usage_bytes||0)}</div>
        ${r.status!=='ready'&&r.status!=='completed'?html`<div class="progress-bar mt-6"><div class="progress-fill" style="width:${clamp(num(r.progress_percent),0,100)}%"></div></div>`:''}
      </div>`)}` : html`<div class="text-secondary">No downloaded regions</div>`);
  } catch { /* leave the previous list in place */ }
}

async function downloadTiles() {
  const name=$('dl-region-name').value.trim();
  if(!name){toast('Enter region name','warning');return;}
  const bbox={min_lat:parseFloat($('dl-min-lat').value),min_lng:parseFloat($('dl-min-lng').value),max_lat:parseFloat($('dl-max-lat').value),max_lng:parseFloat($('dl-max-lng').value)};
  if (Object.values(bbox).some(v => !Number.isFinite(v))) { toast('Enter all four bounding-box coordinates','warning'); return; }
  const min_zoom=parseInt($('dl-min-zoom').value), max_zoom=parseInt($('dl-max-zoom').value);
  try {
    const data = await api('/api/offline/tiles/download',{method:'POST',body:{name,bbox,tile_source:'osm',min_zoom,max_zoom}});
    toast(`Download started: ~${data.estimated_tile_count} tiles (~${data.estimated_size_mb?.toFixed(1)} MB)`,'success');
    loadTileRegions();
  } catch { toast('Download failed','error'); }
}
async function deleteRegion(id){
  try{await api(`/api/offline/tiles/regions/${enc(id)}`,{method:'DELETE'});toast('Region deleted','success');loadTileRegions();}catch{toast('Failed','error');}
}

async function loadCachedPlaces() {
  const list = $('cached-places-list');
  showSkeleton(list, 2);
  try {
    const data = await api('/api/offline/places');
    const items = data.places || [];
    setHtml(list, items.length ? html`${items.map(c => html`<div class="list-item"><div class="item-icon">📍</div><div class="item-body"><div class="item-title">${c.place?.name||c.place?.id}</div><div class="item-sub">cached ${dateStr(c.cached_at)}${c.has_llm_description?' · with AI description':''}</div></div></div>`)}` : html`<div class="text-secondary">No cached places</div>`);
  } catch { setHtml(list, errorStateHtml('📍', 'Could not load cached places', 'load-cached-places')); }
}
async function cachePlaces() {
  if (!mapAvailable) { toast('The map is not available — cannot tell which area to cache','warning'); return; }
  const b = map.getBounds(), sw = b.getSouthWest().wrap(), ne = b.getNorthEast().wrap();
  if (sw.lng > ne.lng) { toast('Zoom in: the view crosses the date line','warning'); return; }
  try {
    // discovery may take up to its server-side deadline (25 s), longer for cold sources
    const data = await api('/api/offline/places/cache',{method:'POST',timeout:60000,body:{bbox:{min_lat:sw.lat,min_lng:sw.lng,max_lat:ne.lat,max_lng:ne.lng},include_descriptions:true}});
    if (data.warning) toast(`⚠️ Cached ${data.cached_count} places. ${data.warning}`, 'warning', 8000);
    else toast(`Cached ${data.cached_count} places!`,'success');
  } catch { toast('Cache failed','error'); }
  finally { loadCachedPlaces(); } // also after a timeout: the server may have finished anyway
}
async function clearCachedPlaces(){
  try{await api('/api/offline/places',{method:'DELETE'});toast('Cache cleared','success');loadCachedPlaces();}catch{toast('Failed','error');}
}

async function loadSyncQueue() {
  const list = $('sync-queue-list');
  showSkeleton(list, 2);
  try {
    const data = await api('/api/offline/sync/queue');
    const items = data.items || [];
    setHtml(list, items.length ? html`${items.map(i => html`<div class="list-item"><div class="item-icon">${i.synced?'✅':'⏳'}</div><div class="item-body"><div class="item-title">${i.operation} ${i.resource_type}</div><div class="item-sub">${dateStr(i.created_at)}${i.last_error?' · '+i.last_error:''}</div></div></div>`)}` : html`<div class="text-secondary">Sync queue is empty</div>`);
  } catch { setHtml(list, errorStateHtml('⏳', 'Could not load the sync queue', 'load-sync-queue')); }
}
async function syncNow(){
  try{const d=await api('/api/offline/sync',{method:'POST',body:{resolve_conflicts:'local_wins'}});toast(`Synced: ${d.synced_count} OK, ${d.failed_count} failed`,'success');loadSyncQueue();}catch{toast('Sync failed','error');}
}
async function clearSynced(){
  try{await api('/api/offline/sync/queue/clear-synced',{method:'POST'});toast('Cleared synced items','success');loadSyncQueue();}catch{toast('Failed','error');}
}

TI.tabHooks['o:places'] = () => loadCachedPlaces();
TI.tabHooks['o:sync'] = () => loadSyncQueue();

registerActions({
  'download-tiles': () => downloadTiles(),
  'delete-region': (el) => deleteRegion(el.dataset.id),
  'cache-places': () => cachePlaces(),
  'clear-cached-places': () => clearCachedPlaces(),
  'load-cached-places': () => loadCachedPlaces(),
  'sync-now': () => syncNow(),
  'clear-synced': () => clearSynced(),
  'load-sync-queue': () => loadSyncQueue(),
});
