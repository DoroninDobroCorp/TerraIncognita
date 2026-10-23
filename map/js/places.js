// Place discovery, filters (presets, chips, sliders, toggles) and the place card.
// Classic script (no modules): every file in map/js shares one global scope.
'use strict';

// ── Search + discovery ──
async function fetchChat(q) {
  showMapLoading(true);
  analytics.track('search', { query: q.substring(0, 50) });
  try {
    const data = await api('/api/chat', {method:'POST', timeout: 45000, body:{message:q, lat:state.userLat, lng:state.userLng, radius_km:state.radius}});
    if (data.places && data.places.length > 0) { state.places = data.places; applyFilters(); toast(`Found ${data.places.length} places`,'success'); }
  } catch(e) { toast('Search failed','error'); }
  showMapLoading(false);
}

function discoverBody() {
  return {
    lat:state.userLat, lng:state.userLng, radius_km:state.radius,
    categories:[...state.activeCategories], exclude_visited:state.hideVisited?[...state.visitedPlaces]:[],
    limit:200, sort_by: features.unusual === false ? 'confidence' : 'unusual' // newest backends rank by unusualness
  };
}

// ── Last-results snapshot: the one thing worth keeping on this device for offline starts ──
// Public place data only (no journal/visit data). Replaced on every successful discover call.
const SNAPSHOT_MAX_PLACES = 200;
function saveLastPlaces(places) {
  store.setJSON(LS.lastPlaces, { t: Date.now(), lat: state.userLat, lng: state.userLng, places: places.slice(0, SNAPSHOT_MAX_PLACES) });
}
function loadLastPlaces() {
  const s = store.getJSON(LS.lastPlaces, null);
  return s && Array.isArray(s.places) && s.places.length ? s : null;
}
/** Offline/API-down fallback: last snapshot if we have one, else the built-in demo places. */
function showFallbackPlaces() {
  const snap = loadLastPlaces();
  if (snap) {
    state.places = snap.places; applyFilters();
    toast(`📴 Showing places saved on ${dateStr(snap.t)}`, 'warning');
  } else {
    loadDemoData(); toast('API unavailable — showing demo data','warning');
  }
}

/** Sources whose "error" is routine and not worth a warning (Atlas Obscura blocks most servers). */
const OPTIONAL_SOURCES = new Set(['atlas']);

/** Newer backends report per-source outcomes ("ok" | "empty" | "partial" | "error" | "timeout"); say so when results are incomplete. */
function warnAboutSlowSources(status) {
  if (!status || typeof status !== 'object') return;
  const bad = Object.entries(status)
    .filter(([k, v]) => v === 'timeout' || v === 'partial' || (v === 'error' && !OPTIONAL_SOURCES.has(k)))
    .map(([k]) => SOURCE_NAMES[k] || k);
  if (bad.length) toast(`⚠️ ${bad.join(', ')} did not answer — results may be incomplete`, 'warning', 6000);
}

// Fetch places (guarded against concurrent calls)
let fetchBusy = false;
async function fetchPlaces() {
  if (fetchBusy) return;
  fetchBusy = true;
  showMapLoading(true);
  try {
    if (!TI.net.ok) throw new Error('offline');
    let data;
    try {
      data = await api('/api/discover', {method:'POST', timeout: 45000, body: discoverBody()});
    } catch (e) {
      if (e.status !== 422 || features.unusual === false || !String(e.message).includes('sort_by')) throw e;
      features.unusual = false; // older backend: sort_by "unusual" is not accepted, fall back for good
      data = await api('/api/discover', {method:'POST', timeout: 45000, body: discoverBody()});
    }
    state.places = data.places || []; applyFilters();
    saveLastPlaces(state.places);
    warnAboutSlowSources(data.source_status);
    loadDaily();
  } catch(e) { showFallbackPlaces(); }
  showMapLoading(false);
  fetchBusy = false;
}

function loadDemoData() {
  state.places = [
    {id:'demo-1',source:'osm',name:'Заброшенный бункер',description:'Старый военный бункер времён WWII.',categories:['abandoned','military'],coordinates:{lat:42.4450,lng:18.7650},confidence:0.85,distance_m:350},
    {id:'demo-2',source:'osm',name:'Подземный тоннель',description:'Скрытый каменный тоннель к морю.',categories:['underground'],coordinates:{lat:42.4380,lng:18.7730},confidence:0.7,distance_m:520},
    {id:'demo-3',source:'atlas',name:'Заброшенный хлебозавод',description:'Промышленное здание с граффити.',categories:['abandoned','industrial'],coordinates:{lat:42.4470,lng:18.7800},confidence:0.6,distance_m:820},
    {id:'demo-4',source:'wiki',name:'Крепость Святого Иоанна',description:'Средневековая крепость с видом на залив.',categories:['landmark','ruins'],coordinates:{lat:42.4290,lng:18.7710},confidence:0.95,distance_m:1200},
    {id:'demo-5',source:'osm',name:'Скрытая бухта',description:'Пляж за скалами.',categories:['nature_hidden','water'],coordinates:{lat:42.4340,lng:18.7550},confidence:0.75,distance_m:1500},
    {id:'demo-6',source:'osm',name:'Смотровая площадка',description:'Панорамный вид на бухту.',categories:['viewpoint'],coordinates:{lat:42.4500,lng:18.7580},confidence:0.9,distance_m:2300},
  ];
  applyFilters();
}

function applyFilters() {
  state.filteredPlaces = state.places.filter(p => {
    const cats = p.categories || [];
    if (cats.length && !cats.some(c => state.activeCategories.has(c))) return false;
    if ((p.confidence||0) < state.minConfidence/100) return false;
    if (state.hideVisited && state.visitedPlaces.has(p.id)) return false;
    return true;
  });
  $('place-count').textContent = `${state.filteredPlaces.length} places`;
  renderPlaces();
  if (state.fogEnabled) updateFogStats();
}

// ── Place card ──
const SOURCE_NAMES = {osm:'OpenStreetMap',atlas:'Atlas Obscura',wiki:'Wikidata',community:'Community',deep_research:'AI research'};

function showPlaceCard(placeId) {
  const place = state.places.find(p=>p.id===placeId); if(!place)return;
  state.selectedPlace = place;
  const cats = place.categories||[];
  setHtml($('card-categories'), html`${cats.map(c => { const d = catDef(c); return html`<span class="card-category" style="background:${d.color};color:${contrastText(d.color)}">${d.icon} ${d.label}</span>`; })}`);
  const inFog = isInFog(place);
  $('card-name').textContent = inFog?'❓ Mystery Place':(place.name||'Unknown');
  $('card-meta').textContent = `📡 ${SOURCE_NAMES[place.source]||place.source}${place.distance_m?' · 📏 '+(place.distance_m/1000).toFixed(1)+' km':''}`;
  $('card-desc').textContent = inFog?'Visit this place to reveal its secrets...':place.description||'No description';
  const conf=place.confidence||0.5, fill=$('card-conf-fill');
  fill.style.width=(conf*100)+'%'; fill.style.background=conf>0.7?'#2ecc71':conf>0.4?'#f39c12':'#e74c3c';
  updateSaveButton();
  renderPlaceExtras(place, inFog);
  $('place-card').classList.add('visible');
}
function updateSaveButton() {
  const p = state.selectedPlace;
  $('card-save').textContent = p && state.savedPlaces.has(p.id) ? '★ Saved' : '⭐ Save';
}
function closePlaceCard() {
  $('place-card').classList.remove('visible');
  state.selectedPlace = null;
}

async function showPlaceStory() {
  if(!state.selectedPlace) return;
  const p = state.selectedPlace;
  toast('Generating story...','info');
  try {
    const data = await api('/api/story',{method:'POST',timeout:45000,body:{place_id:p.id,place_name:p.name,lat:p.coordinates.lat,lng:p.coordinates.lng,place_categories:p.categories,language:'ru'}});
    setHtml($('card-desc'), html`<strong>📖 AI Story:</strong><br>${data.story}`);
    toast('Story generated!','success');
  } catch { toast('Story generation failed','error'); }
}

async function checkInSelectedPlace() {
  if(!state.selectedPlace) return;
  const p = state.selectedPlace;
  analytics.track('checkin', { place_id: p.id, category: p.categories?.[0] });
  const markVisited = () => { state.visitedPlaces.add(p.id); persistVisited(); renderFog(); renderPlaces(); };
  try {
    await api('/api/visits',{method:'POST',body:{place_id:p.id,lat:p.coordinates.lat,lng:p.coordinates.lng,place_name:p.name,status:'visited'}});
    markVisited(); toast('✅ Checked in at '+p.name,'success');
    refreshHeatmap(); loadStreak();
  } catch { toast('Check-in failed — saved locally','warning'); markVisited(); }
}

registerActions({
  'show-place': (el) => showPlaceCard(el.dataset.id),
  'close-card': () => closePlaceCard(),
  'card-navigate': () => { if (!state.selectedPlace) return; startNavigation(state.selectedPlace); $('place-card').classList.remove('visible'); },
  'card-save': () => {
    if (!state.selectedPlace) return;
    const id = state.selectedPlace.id;
    if (state.savedPlaces.has(id)) state.savedPlaces.delete(id); else state.savedPlaces.add(id);
    persistSaved(); updateSaveButton();
  },
  'card-story': () => showPlaceStory(),
  'card-checkin': () => checkInSelectedPlace(),
  refresh: () => {
    const c = mapCenter();
    state.userLat = c.lat; state.userLng = c.lng;
    fetchPlaces();
  },
});

// ── Filter panel ──
function rebuildCategoryChips() {
  setHtml($('category-chips'), html`${Object.entries(CATEGORIES).map(([k]) => {
    const d = catDef(k), on = state.activeCategories.has(k);
    return html`<div class="chip${on ? ' active' : ''}" data-action="toggle-chip" data-cat="${k}" role="checkbox" aria-checked="${String(on)}" tabindex="0"><span class="chip-icon">${d.icon}</span>${d.label}</div>`;
  })}`);
}
function toggleChip(ch) {
  const k = ch.dataset.cat;
  const on = !ch.classList.contains('active');
  ch.classList.toggle('active', on);
  ch.setAttribute('aria-checked', String(on));
  if (on) state.activeCategories.add(k); else state.activeCategories.delete(k);
  $$('.preset-btn').forEach(b => { b.classList.remove('active'); b.setAttribute('aria-pressed', 'false'); });
  applyFilters();
}
function applyPreset(name) {
  if (!PRESETS[name]) return;
  $$('.preset-btn').forEach(b => { const on = b === document.querySelector(`.preset-btn[data-preset="${name}"]`); b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on)); });
  state.activeCategories = new Set(PRESETS[name]);
  $$('#category-chips .chip').forEach(c => { const on = state.activeCategories.has(c.dataset.cat); c.classList.toggle('active', on); c.setAttribute('aria-checked', String(on)); });
  applyFilters();
}
function setSwitch(el, on) { el.classList.toggle('on', on); el.setAttribute('aria-checked', String(on)); }

const _refetchSoon = debounce(() => fetchPlaces(), 400);
const _applyConfidence = debounce((v) => {
  state.minConfidence = v;
  $('confidence-value').textContent = v === 0 ? 'All' : '≥ ' + v + '%';
  applyFilters();
}, 150);

registerActions({
  'toggle-filters': () => { const p = $('filter-panel'); const open = p.classList.toggle('open'); $('filter-handle').setAttribute('aria-expanded', String(open)); },
  preset: (el) => applyPreset(el.dataset.preset),
  'toggle-chip': (el) => toggleChip(el),
  'toggle-hide-visited': (el) => { const on = !el.classList.contains('on'); setSwitch(el, on); state.hideVisited = on; applyFilters(); },
  'toggle-fog-setting': (el) => setFogEnabled(!el.classList.contains('on')),
});
registerInput({
  radius: (el) => { state.radius = parseFloat(el.value); $('radius-value').textContent = state.radius.toFixed(1) + ' km'; },
  confidence: (el) => _applyConfidence(parseInt(el.value)),
});
registerChange({ radius: () => _refetchSoon() });
registerEnter({ search: (el) => { const q = el.value.trim(); if (q) fetchChat(q); } });

function initPlaces() {
  rebuildCategoryChips();
  // Swipe the filter sheet up/down
  const fp = $('filter-panel');
  let touchStartY = 0;
  fp.addEventListener('touchstart', e => { touchStartY = e.touches[0].clientY; }, { passive: true });
  fp.addEventListener('touchend', e => {
    const d = e.changedTouches[0].clientY - touchStartY;
    if (d > 50) fp.classList.remove('open'); else if (d < -50) fp.classList.add('open');
    $('filter-handle').setAttribute('aria-expanded', String(fp.classList.contains('open')));
  }, { passive: true });
}
