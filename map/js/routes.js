// Route builder: point-to-point, circular walk, saved routes, GPX/KML export, route story, map drawing.
// Classic script (no modules): every file in map/js shares one global scope.
'use strict';

const EXPLORE_CATS = ['abandoned','ruins','nature_hidden','viewpoint','underground','military'];

/** Category chips used by the circular-walk and loop forms (all selected by default). */
function categoryChipsHtml(keys = EXPLORE_CATS) {
  return html`${keys.filter(k => CATEGORIES[k]).map(k => { const d = catDef(k); return html`<div class="chip active" data-action="toggle-simple-chip" data-cat="${k}" role="checkbox" aria-checked="true" tabindex="0"><span class="chip-icon">${d.icon}</span>${d.label}</div>`; })}`;
}
function buildExploreCats() { setHtml($('explore-cats'), categoryChipsHtml()); setHtml($('loop-cats'), categoryChipsHtml()); }
function selectedCats(containerId) { return $$(`#${containerId} .chip.active`).map(c => c.dataset.cat); }

// ── Build / explore ──
async function buildRoute() {
  const from=parseCoords($('route-from').value);
  const to=parseCoords($('route-to').value);
  if(!from||!to){toast('Enter valid coordinates as "lat, lng" (e.g., 42.441, 18.771)','warning');return;}
  const btn = $('route-build-btn'); btn.disabled=true;
  toast('Building route...','info');
  analytics.track('route_build', { mode: $('route-mode').value });
  try {
    const data = await api('/api/route',{method:'POST',timeout:60000,body:{origin:from,destination:to,max_duration_hours:clamp(parseFloat($('route-time').value)||2,0.5,24),transport_mode:$('route-mode').value}});
    if (setCurrentRoute(data.route || data, { summary: data.summary, links: data.navigation_links })) toast('Route built!','success');
  } catch(e) { toast('Route building failed: '+(apiDetail(e)||'check connection'),'error'); } finally { btn.disabled=false; }
}

async function exploreRoute() {
  const center = parseCoords($('explore-center').value);
  if(!center){toast('Enter valid center point as "lat, lng" (e.g., 42.441, 18.771)','warning');return;}
  const btn = $('route-explore-btn'); btn.disabled=true;
  toast('Generating circular walk...','info');
  analytics.track('route_explore');
  try {
    const data = await api('/api/route/explore',{method:'POST',timeout:45000,body:{origin:center,max_duration_hours:clamp(parseFloat($('explore-time').value)||2,0.5,8),categories:selectedCats('explore-cats'),transport_mode:'walking'}});
    if (setCurrentRoute(data.route || data, { summary: data.summary, links: data.navigation_links })) toast('Circular walk generated!','success');
  } catch(e) { toast('Generation failed: '+(apiDetail(e)||'check connection'),'error'); } finally { btn.disabled=false; }
}

// ── Loop route (POST /api/route/loop): same RouteResponse as the other builders ──
function loopMode() { const b = $('loop-mode').querySelector('[aria-checked="true"]'); return b ? b.dataset.mode : 'walking'; }
function setLoopMode(mode) {
  $$('#loop-mode [role="radio"]').forEach(b => b.setAttribute('aria-checked', String(b.dataset.mode === mode)));
}
async function buildLoop() {
  const center = parseCoords($('loop-center').value);
  if (!center) { toast('Enter a valid start point as "lat, lng" (or tap 📍 on the map first)','warning'); return; }
  const btn = $('route-loop-btn'); btn.disabled = true;
  toast('Building your loop...','info');
  analytics.track('route_loop', { mode: loopMode() });
  try {
    const data = await api('/api/route/loop', { method: 'POST', retries: 0, timeout: 90000, body: {
      lat: center.lat, lng: center.lng,
      duration_hours: clamp(parseFloat($('loop-hours').value) || 2, 0.5, 12),
      mode: loopMode(),
      categories: selectedCats('loop-cats'),
      max_stops: clamp(parseInt($('loop-stops').value) || 5, 2, 8),
    } });
    if (setCurrentRoute(data.route || data, { label: '🔁 Loop route', summary: data.summary, links: data.navigation_links })) toast('Loop ready!','success');
  } catch (e) {
    if (featureUnavailable('loop', e)) { toast('This server cannot build loop routes yet','warning'); selectTab($('tab-r-build')); }
    else toast('Loop failed: ' + (apiDetail(e) || 'check connection'), 'error');
  } finally { btn.disabled = false; }
}

// ── Result card (shared by every route builder) ──
/** Make `route` the current one: show it in the panel and draw it on the map. */
/** Show and draw `route`; false (after saying why) when there is nothing to show. */
function setCurrentRoute(route, opts = {}) {
  state.currentRoute = route;
  const ok = showRouteResult(route, opts);
  if (ok) drawRouteOnMap(route);
  return ok;
}

/** "Open in Google Maps / OsmAnd" links the backend builds for every route (navigation_links). */
const NAV_LINK_LABELS = { google_maps: '🧭 Google Maps', osmand: '📲 OsmAnd', geo: '📍 Maps app' };
function navLinkUrl(u) {
  const s = String(u || '');
  if (/^(geo|osmand):/i.test(s)) return /^[\w:/?&=.,%+\-@#!]*$/.test(s) ? s : '';   // custom schemes open a mapping app; never script
  return safeUrl(s);
}
function renderNavLinks(links) {
  const box = $('route-links');
  const items = Object.entries(NAV_LINK_LABELS).map(([k, label]) => ({ label, url: navLinkUrl(links && links[k]) })).filter(i => i.url);
  box.hidden = items.length === 0;
  setHtml(box, html`${items.map(i => html`<a class="btn btn-secondary btn-sm" href="${i.url}" target="_blank" rel="noopener noreferrer" data-native>${i.label}</a>`)}`);
}

function showRouteResult(route, { label = null, summary = null, links = null } = {}) {
  if (route && Array.isArray(route.waypoints) && !route.total_distance_m) { toast('No interesting places found for this route — try more time or another area','warning'); return false; }
  if (!route || !route.total_distance_m) { toast('Invalid route data','error'); return false; }
  $('route-result').hidden = false;
  $('route-title').textContent = label || (route.is_circular?'🔄 Circular Walk':'🛤️ Route');
  const modeIcon = { walking: '🚶', cycling: '🚲', driving: '🚗' }[route.transport_mode] || '';
  $('route-meta').textContent = `${modeIcon ? modeIcon + ' ' : ''}${((route.total_distance_m||0)/1000).toFixed(1)} km · ${Math.round((route.total_duration_s||0)/60)} min · ${route.places_count||route.waypoints?.length||0} places`;
  const sum = $('route-summary');
  sum.textContent = typeof summary === 'string' ? summary : '';
  sum.hidden = !sum.textContent;
  renderNavLinks(links);
  const wps = route.waypoints||[];
  setHtml($('route-waypoints'), wps.length ? html`${wps.map((w,i) => html`
    <div class="list-item list-item-compact">
      <div class="step-badge">${i+1}</div>
      <div class="item-body"><div class="item-title">${w.place?.name||'Waypoint '+(i+1)}</div><div class="item-sub">${w.detour_distance_m?'Detour: '+(w.detour_distance_m/1000).toFixed(1)+' km':''}</div></div>
    </div>`)}` : html`<div class="text-small text-secondary">No waypoints in route</div>`);
  $('route-story').hidden = true;
  return true;
}

/** [lng,lat] path of a route: segment geometry when present, else straight legs between waypoints. */
function routePath(route) {
  const pts = [];
  for (const s of route.segments || []) {
    const g = (s.geometry && s.geometry.length) ? s.geometry : [s.from_point, s.to_point];
    for (const c of g) if (c && Number.isFinite(c.lat) && Number.isFinite(c.lng)) pts.push([c.lng, c.lat]);
  }
  if (pts.length < 2) {
    for (const w of route.waypoints || []) { const c = w.place?.coordinates; if (c) pts.push([c.lng, c.lat]); }
  }
  return pts;
}

function clearRouteFromMap() {
  if (!mapStyleReady) return;
  ['route-stops-layer', 'route-line-layer'].forEach(id => { if (map.getLayer(id)) map.removeLayer(id); });
  if (map.getSource('route')) map.removeSource('route');
}

function drawRouteOnMap(route, { fit = false } = {}) {
  if (!mapStyleReady) return;
  clearRouteFromMap();
  const path = routePath(route);
  if (path.length < 2) return;
  const stops = (route.waypoints || []).filter(w => w.place?.coordinates).map((w, i) => ({
    type: 'Feature', properties: { n: i + 1 },
    geometry: { type: 'Point', coordinates: [w.place.coordinates.lng, w.place.coordinates.lat] },
  }));
  map.addSource('route', { type: 'geojson', data: { type: 'FeatureCollection', features: [
    { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: path } }, ...stops,
  ] } });
  const before = beforePlaceLayers();
  map.addLayer({ id: 'route-line-layer', type: 'line', source: 'route', filter: ['==', '$type', 'LineString'],
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': '#1e88e5', 'line-width': 5, 'line-opacity': 0.9 } }, before);
  map.addLayer({ id: 'route-stops-layer', type: 'circle', source: 'route', filter: ['==', '$type', 'Point'],
    paint: { 'circle-radius': 7, 'circle-color': '#ffffff', 'circle-stroke-color': '#1e88e5', 'circle-stroke-width': 3 } }, before);
  if (fit) {
    const b = path.reduce((acc, c) => acc.extend(c), new maplibregl.LngLatBounds(path[0], path[0]));
    map.fitBounds(b, { padding: 60, maxZoom: 16 });
  }
}

function showRouteOnMap() {
  if (!state.currentRoute) return;
  showSection('map', { runHooks: false });
  drawRouteOnMap(state.currentRoute, { fit: true });
}

async function exportRoute(fmt) {
  if(!state.currentRoute){toast('No route to export','warning');return;}
  try {
    const data = await api('/api/route/export',{method:'POST',body:{route:state.currentRoute,format:fmt,include_descriptions:true}});
    downloadText(data.filename||`route.${fmt}`, data.content, fmt === 'gpx' ? 'application/gpx+xml' : 'application/vnd.google-earth.kml+xml');
    toast(`Exported as ${fmt.toUpperCase()}!`,'success');
  } catch { toast('Export failed','error'); }
}

async function getRouteStory() {
  if(!state.currentRoute){return;}
  const places = (state.currentRoute.waypoints||[]).filter(w=>w.place).map(w=>({place_id:w.place.id,place_name:w.place.name,lat:w.place.coordinates?.lat,lng:w.place.coordinates?.lng,place_categories:w.place.categories}));
  if (places.length < 2) { toast('A story needs at least 2 places on the route','warning'); return; }
  toast('Generating route story...','info');
  try {
    const data = await api('/api/story/route',{method:'POST',timeout:45000,body:{places,language:'ru'}});
    $('route-story').hidden = false;
    $('route-story-text').textContent = data.story;
    toast('Story ready!','success');
  } catch (e) { toast(friendlyError(e, 'Story failed'), 'error'); }
}

// ── Saved routes (localStorage) ──
function saveRouteLocal() {
  if(!state.currentRoute)return;
  const saved = store.getJSON(LS.routes, []);
  saved.push({...state.currentRoute, saved_at:new Date().toISOString()});
  if (store.setJSON(LS.routes, saved.slice(-100))) toast('Route saved locally!','success');
  else toast('Could not save — browser storage is full or blocked','error');
}
function renderSavedRoutes() {
  const saved = store.getJSON(LS.routes, []);
  const list = $('saved-routes-list');
  if (!saved.length) { setHtml(list, emptyStateHtml('💾', 'No saved routes yet. Build a route and press 💾 Save.')); return; }
  setHtml(list, html`${saved.map((r, i) => ({ r, i })).reverse().map(({ r, i }) => html`
    <div class="list-item">
      <div class="item-icon">${r.is_circular?'🔄':'🛤️'}</div>
      <div class="item-body"><div class="item-title">${((r.total_distance_m||0)/1000).toFixed(1)} km · ${Math.round((r.total_duration_s||0)/60)} min</div><div class="item-sub">${r.saved_at?dateStr(r.saved_at):''} · ${(r.waypoints||[]).length} stops</div></div>
      <button class="btn btn-secondary btn-sm" data-action="open-saved-route" data-index="${i}">Open</button>
      <button class="btn btn-danger btn-sm" data-action="delete-saved-route" data-index="${i}" aria-label="Delete saved route">🗑️</button>
    </div>`)}`);
}
function openSavedRoute(i) {
  const r = store.getJSON(LS.routes, [])[i];
  if (!r) return;
  setCurrentRoute(r);
}
function deleteSavedRoute(i) {
  const saved = store.getJSON(LS.routes, []);
  saved.splice(i, 1);
  store.setJSON(LS.routes, saved);
  renderSavedRoutes();
}

TI.tabHooks['r:saved'] = () => renderSavedRoutes();

registerActions({
  'route-build': () => buildRoute(),
  'route-explore': () => exploreRoute(),
  'route-loop': () => buildLoop(),
  'loop-mode': (el) => setLoopMode(el.dataset.mode),
  'route-export': (el) => exportRoute(el.dataset.fmt),
  'route-story': () => getRouteStory(),
  'route-save': () => saveRouteLocal(),
  'route-show-map': () => showRouteOnMap(),
  'toggle-simple-chip': (el) => { const on = !el.classList.contains('active'); el.classList.toggle('active', on); el.setAttribute('aria-checked', String(on)); },
  'open-saved-route': (el) => openSavedRoute(parseInt(el.dataset.index)),
  'delete-saved-route': (el) => deleteSavedRoute(parseInt(el.dataset.index)),
});
