// Offline tab: tile regions, cached places, sync queue.
// Classic script (no modules): shares the global scope with the other map/js files.

// ═══════════════════════════════════════════════════════
//  OFFLINE
// ═══════════════════════════════════════════════════════
async function loadOfflineStatus() {
  try {
    const [status,connectivity] = await Promise.all([api('/api/offline/status').catch(()=>({regions_count:0,cached_places_count:0,pending_sync_count:0})), api('/api/offline/connectivity').catch(()=>({is_online:true}))]);
    document.getElementById('offline-status-badge').textContent = connectivity.is_online?'Online':'Offline';
    document.getElementById('offline-status-badge').style.background = connectivity.is_online?'var(--success)':'var(--danger)';
    document.getElementById('offline-storage-info').innerHTML = `
      <div>Regions: ${status.regions_count||0} · Cached places: ${status.cached_places_count||0}</div>
      <div>Pending sync: ${status.pending_sync_count||0}</div>
      <div>Disk: ${formatBytes(status.total_disk_usage_bytes||0)}</div>
    `;
    loadTileRegions();
  } catch { document.getElementById('offline-storage-info').textContent='Could not load status'; }
}

async function loadTileRegions() {
  const list=document.getElementById('tile-regions-list');
  try {
    const data=await api('/api/offline/tiles/regions');
    list.innerHTML=(data.regions||[]).map(r=>`
      <div class="card" style="margin-bottom:8px">
        <div style="display:flex;justify-content:space-between;align-items:start"><div class="card-title">${escHtml(r.name)}</div><button class="btn btn-danger btn-sm" onclick="deleteRegion('${r.region_id}')">🗑️</button></div>
        <div class="card-meta">${r.tile_count||0} tiles · ${formatBytes(r.disk_usage_bytes||0)}</div>
        ${r.status!=='completed'?`<div class="progress-bar" style="margin-top:6px"><div class="progress-fill" style="width:${r.progress_percent||0}%"></div></div>`:''}
      </div>`).join('')||'<div style="color:var(--text-secondary)">No downloaded regions</div>';
  } catch {}
}

async function downloadTiles() {
  const name=document.getElementById('dl-region-name').value;
  if(!name){toast('Enter region name','warning');return;}
  const bbox={min_lat:parseFloat(document.getElementById('dl-min-lat').value),min_lng:parseFloat(document.getElementById('dl-min-lng').value),max_lat:parseFloat(document.getElementById('dl-max-lat').value),max_lng:parseFloat(document.getElementById('dl-max-lng').value)};
  const min_zoom=parseInt(document.getElementById('dl-min-zoom').value), max_zoom=parseInt(document.getElementById('dl-max-zoom').value);
  try {
    const data = await api('/api/offline/tiles/download',{method:'POST',body:{name,bbox,tile_source:'osm',min_zoom,max_zoom}});
    toast(`Download started: ~${data.estimated_tile_count} tiles (~${data.estimated_size_mb?.toFixed(1)} MB)`,'success');
    loadTileRegions();
  } catch { toast('Download failed','error'); }
}
async function deleteRegion(id){try{await api(`/api/offline/tiles/regions/${id}`,{method:'DELETE'});toast('Region deleted','success');loadTileRegions();}catch{toast('Failed','error');}}

async function cachePlaces() {
  const b=map.getBounds();
  try {
    const data = await api('/api/offline/places/cache',{method:'POST',body:{bbox:{min_lat:b.getSouth(),min_lng:b.getWest(),max_lat:b.getNorth(),max_lng:b.getEast()},include_descriptions:true}});
    toast(`Cached ${data.cached_count} places!`,'success');
  } catch { toast('Cache failed','error'); }
}
async function clearCachedPlaces(){try{await api('/api/offline/places',{method:'DELETE'});toast('Cache cleared','success');}catch{toast('Failed','error');}}

async function syncNow(){try{const d=await api('/api/offline/sync',{method:'POST',body:{resolve_conflicts:'local_wins'}});toast(`Synced: ${d.synced_count} OK, ${d.failed_count} failed`,'success');}catch{toast('Sync failed','error');}}
async function clearSynced(){try{await api('/api/offline/sync/queue/clear-synced',{method:'POST'});toast('Cleared synced items','success');}catch{toast('Failed','error');}}

