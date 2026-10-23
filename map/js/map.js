// MapLibre map: base layers, place markers/clusters, fog of war, historical overlay, map gestures.
// Classic script (no modules): every file in map/js shares one global scope.
'use strict';

let map = null;
let mapAvailable = false;
let mapStyleReady = false; // true once the style JSON is parsed: layers/sources can be added
let currentBase = 'streets';

/** One style holding every base layer; only the active one is visible (see BASE_LAYERS). */
function buildStyle() {
  const sources = {}, layers = [];
  for (const [key, b] of Object.entries(BASE_LAYERS)) {
    sources[b.sourceId] = b.source;
    layers.push({ id: b.layerId, type: 'raster', source: b.sourceId, layout: { visibility: key === currentBase ? 'visible' : 'none' } });
  }
  return { version: 8, glyphs: GLYPHS_URL, sources, layers };
}

/** Create the map; `onReady` runs once layers can be added (or at once if there is no WebGL). */
function initMap(onReady) {
  try {
    map = new maplibregl.Map({ container:'map', style:buildStyle(), center:[state.userLng,state.userLat], zoom:13, maxZoom:19, minZoom:3, dragRotate:false });
    mapAvailable = true;
    if (state.lowPower && typeof map.setPixelRatio === 'function') map.setPixelRatio(1);
    map.addControl(new maplibregl.NavigationControl({showCompass:true,showZoom:true}), 'bottom-right');
    map.once('style.load', () => {
      mapStyleReady = true;
      applyBaseLayerState(); renderPlaces(); if (state.fogEnabled) renderFog();
      if (onReady) onReady();
    });
  } catch (e) {
    console.warn('Map initialization failed (WebGL or the map library is unavailable):', e.message);
    setHtml($('map'), raw('<div class="map-fallback"><div><div class="map-fallback-icon">🗺️</div><div>The map could not start (it needs WebGL and the map library).<br>Other features are still available.</div></div></div>'));
    map = new Proxy({}, { get: () => (...args) => map });
    if (onReady) onReady();
  }
  bindMapEvents();
}

// ── Map colours per UI theme (base-layer raster tweaks + overlay colours) ──
// Dark inverts the light OSM tiles (brightness min>max flips luminance, hue-rotate restores hues);
// sun boosts contrast/saturation so the map stays readable in direct light.
const MAP_THEMES = {
  light: {
    streets:   { 'raster-brightness-min': 0, 'raster-brightness-max': 1, 'raster-contrast': 0, 'raster-saturation': 0, 'raster-hue-rotate': 0 },
    satellite: { 'raster-brightness-min': 0, 'raster-brightness-max': 1, 'raster-contrast': 0, 'raster-saturation': 0, 'raster-hue-rotate': 0 },
    terrain:   { 'raster-brightness-min': 0, 'raster-brightness-max': 1, 'raster-contrast': 0, 'raster-saturation': 0, 'raster-hue-rotate': 0 },
    overlay: { fog: '#1b2540', fogOpacity: 0.42, stroke: '#ffffff', text: '#ffffff', halo: 'rgba(0,0,0,0.6)', radius: 1 },
  },
  dark: {
    streets:   { 'raster-brightness-min': 0.9, 'raster-brightness-max': 0.16, 'raster-contrast': 0.05, 'raster-saturation': -0.35, 'raster-hue-rotate': 180 },
    satellite: { 'raster-brightness-min': 0, 'raster-brightness-max': 0.8, 'raster-contrast': 0, 'raster-saturation': 0, 'raster-hue-rotate': 0 },
    terrain:   { 'raster-brightness-min': 0, 'raster-brightness-max': 0.6, 'raster-contrast': 0.1, 'raster-saturation': -0.2, 'raster-hue-rotate': 0 },
    overlay: { fog: '#0a0a1e', fogOpacity: 0.55, stroke: '#ffffff', text: '#ffffff', halo: 'rgba(0,0,0,0.6)', radius: 1 },
  },
  sun: {
    streets:   { 'raster-brightness-min': 0, 'raster-brightness-max': 0.95, 'raster-contrast': 0.4, 'raster-saturation': 0.25, 'raster-hue-rotate': 0 },
    satellite: { 'raster-brightness-min': 0, 'raster-brightness-max': 1, 'raster-contrast': 0.15, 'raster-saturation': 0.1, 'raster-hue-rotate': 0 },
    terrain:   { 'raster-brightness-min': 0, 'raster-brightness-max': 0.95, 'raster-contrast': 0.35, 'raster-saturation': 0.2, 'raster-hue-rotate': 0 },
    overlay: { fog: '#000000', fogOpacity: 0.38, stroke: '#000000', text: '#ffffff', halo: '#000000', radius: 1.2 },
  },
};
function mapTheme() { return MAP_THEMES[document.documentElement.dataset.theme] || MAP_THEMES.dark; }

function applyBaseLayerState() {
  if (!mapStyleReady) return;
  const theme = mapTheme();
  for (const [key, b] of Object.entries(BASE_LAYERS)) {
    if (!map.getLayer(b.layerId)) continue;
    map.setLayoutProperty(b.layerId, 'visibility', key === currentBase ? 'visible' : 'none');
    for (const [prop, val] of Object.entries(theme[key])) map.setPaintProperty(b.layerId, prop, val);
  }
}
/** Called by the theme switcher: recolour base layers and redraw overlays. */
function applyMapTheme() {
  if (!mapStyleReady) return;
  applyBaseLayerState(); renderPlaces(); if (state.fogEnabled) renderFog();
}
function setBaseLayer(key) {
  if (!BASE_LAYERS[key]) return;
  currentBase = key;
  $$('.layer-btn[data-layer]').forEach(b => {
    if (b.dataset.layer === 'historical') return;
    const on = b.dataset.layer === key;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', String(on));
  });
  applyBaseLayerState();
}

function isInFog(place) { return state.fogEnabled && !state.visitedPlaces.has(place.id) && !state.revealedIds.has(place.id); }
function beforePlaceLayers() { return map.getLayer('place-clusters') ? 'place-clusters' : undefined; }

// ── Place markers ──
function renderPlaces() {
  if (!mapStyleReady) return;
  ['place-clusters','cluster-count','unclustered-bg','unclustered-icon','place-silhouettes'].forEach(id => { if(map.getLayer(id)) map.removeLayer(id); });
  if (map.getSource('places')) map.removeSource('places');
  const ov = mapTheme().overlay;

  const features = state.filteredPlaces.map(p => {
    const cat = (p.categories&&p.categories[0])||'landmark';
    const def = catDef(cat);
    const inFog = isInFog(p);
    return {type:'Feature', properties:{id:p.id,name:p.name||'Unknown',icon:def.icon,color:def.color,category:cat,confidence:p.confidence||0.5,distance:p.distance_m||0,inFog,description:p.description||'',source:p.source||'osm'}, geometry:{type:'Point',coordinates:[p.coordinates.lng,p.coordinates.lat]}};
  });

  map.addSource('places',{type:'geojson',data:{type:'FeatureCollection',features},cluster:true,clusterMaxZoom:17,clusterRadius:50});
  map.addLayer({id:'place-clusters',type:'circle',source:'places',filter:['has','point_count'],paint:{'circle-color':['step',['get','point_count'],'#ff6b35',10,'#e74c3c',30,'#8e44ad'],'circle-radius':['step',['get','point_count'],22*ov.radius,10,28*ov.radius,30,34*ov.radius],'circle-opacity':1,'circle-stroke-width':3,'circle-stroke-color':ov.stroke}});
  map.addLayer({id:'cluster-count',type:'symbol',source:'places',filter:['has','point_count'],layout:{'text-field':'{point_count_abbreviated}','text-font':['Open Sans Regular','Arial Unicode MS Regular'],'text-size':15*ov.radius},paint:{'text-color':ov.text,'text-halo-color':ov.halo,'text-halo-width':1}});
  map.addLayer({id:'unclustered-bg',type:'circle',source:'places',filter:['!',['has','point_count']],paint:{'circle-color':['get','color'],'circle-radius':['case',['get','inFog'],10*ov.radius,14*ov.radius],'circle-opacity':1,'circle-stroke-width':3,'circle-stroke-color':['case',['get','inFog'],'rgba(255,255,255,0.3)',ov.stroke]}});
  map.addLayer({id:'unclustered-icon',type:'symbol',source:'places',filter:['all',['!',['has','point_count']],['!',['get','inFog']]],layout:{'text-field':['get','icon'],'text-size':22,'text-offset':[0,-1.5],'text-allow-overlap':true}});
  map.addLayer({id:'place-silhouettes',type:'symbol',source:'places',filter:['all',['!',['has','point_count']],['get','inFog']],layout:{'text-field':'❓','text-size':18,'text-offset':[0,-1.5],'text-allow-overlap':true},paint:{'text-opacity':0.5}});
}

// ── Fog of war ──
function renderFog() {
  if (!mapStyleReady) return;
  if(map.getLayer('fog-layer')) map.removeLayer('fog-layer');
  if(map.getSource('fog')) map.removeSource('fog');
  if(!state.fogEnabled) return;
  const ov = mapTheme().overlay;
  const visited = [...state.visitedPlaces].map(id => { const p = state.places.find(x=>x.id===id); return p?[p.coordinates.lng,p.coordinates.lat]:null; }).filter(Boolean);
  const world = [[-180,-85],[180,-85],[180,85],[-180,85],[-180,-85]];
  const holes = visited.map(([lng,lat]) => { const pts=[]; for(let i=0;i<=FOG_CIRCLE_SEGMENTS;i++){const a=(i/FOG_CIRCLE_SEGMENTS)*Math.PI*2; pts.push([lng+FOG_REVEAL_RADIUS_DEG/Math.cos(lat*Math.PI/180)*Math.cos(a),lat+FOG_REVEAL_RADIUS_DEG*Math.sin(a)]);} return pts; });
  map.addSource('fog',{type:'geojson',data:{type:'FeatureCollection',features:[{type:'Feature',geometry:{type:'Polygon',coordinates:[world,...holes]}}]}});
  map.addLayer({id:'fog-layer',type:'fill',source:'fog',paint:{'fill-color':ov.fog,'fill-opacity':ov.fogOpacity}}, beforePlaceLayers());
}
function updateFogStats() {
  const t=state.places.length, v=state.visitedPlaces.size, p=t>0?Math.round(v/t*100):0;
  $('fog-pct').textContent=p+'%'; $('fog-fill').style.width=p+'%';
}
function setFogEnabled(on) {
  state.fogEnabled = on;
  $('btn-fog').classList.toggle('active', on);
  $('btn-fog').setAttribute('aria-pressed', String(on));
  const tog = $('toggle-fog');
  tog.classList.toggle('on', on);
  tog.setAttribute('aria-checked', String(on));
  $('fog-stats').classList.toggle('visible', on);
  renderFog(); renderPlaces();
}

function showMapLoading(s) { $('loading-overlay').classList.toggle('visible', s); }

// ── Map gestures and marker interaction ──
function popupHtml(place, props) {
  const def = catDef(props.category);
  const fog = place ? isInFog(place) : false;
  const dist = Number(props.distance) || 0;
  return html`<div class="popup-title">${fog ? '❓ Mystery Place' : def.icon + ' ' + (place ? place.name : props.name)}</div><div class="popup-cat">${def.label}</div>${
    dist > 0 ? html`<div class="popup-dist">📏 ${(dist/1000).toFixed(1)} km</div>` : ''}<button type="button" class="popup-more" data-action="show-place" data-id="${props.id}">Show details →</button>`;
}

let _longPressTimer = null;
function bindMapEvents() {
  // Pin mode — click map to set location
  map.on('click', e => {
    if (!state.pinMode) return;
    setUserLocation(e.lngLat.lat, e.lngLat.lng, '📌 Location set');
    setPinMode(false);
  });
  // Long press (touch) / right click (mouse) to set location
  map.on('touchstart', e => {
    if (e.originalEvent.touches.length > 1) return;
    _longPressTimer = setTimeout(() => setUserLocation(e.lngLat.lat, e.lngLat.lng, '📍 Location set'), 600);
  });
  map.on('touchend', () => { clearTimeout(_longPressTimer); });
  map.on('touchmove', () => { clearTimeout(_longPressTimer); });
  map.on('contextmenu', e => {
    e.preventDefault();
    setUserLocation(e.lngLat.lat, e.lngLat.lng, '📍 Location set');
  });

  map.on('click','place-clusters', e => {
    if(state.pinMode) return;
    const f=map.queryRenderedFeatures(e.point,{layers:['place-clusters']});
    if(!f.length)return;
    map.getSource('places').getClusterExpansionZoom(f[0].properties.cluster_id,(err,z)=>{ if(!err)map.easeTo({center:f[0].geometry.coordinates,zoom:z+1}); });
  });
  map.on('click','unclustered-bg', e => {
    if(state.pinMode) return;
    const props=e.features[0].properties, c=e.features[0].geometry.coordinates;
    const place = state.places.find(p => p.id === props.id);
    new maplibregl.Popup({offset:15,maxWidth:'250px'}).setLngLat(c).setHTML(popupHtml(place, props).toString()).addTo(map);
  });
  ['unclustered-bg','place-clusters'].forEach(layer => {
    map.on('mouseenter',layer,()=>{map.getCanvas().style.cursor='pointer';});
    map.on('mouseleave',layer,()=>{map.getCanvas().style.cursor='';});
  });
}

function setPinMode(on) {
  state.pinMode = on;
  $('btn-pin').classList.toggle('active', on);
  $('btn-pin').setAttribute('aria-pressed', String(on));
  $('map').classList.toggle('pin-mode', on);
}

// ── Historical overlay ──
const historicalState = {layers:[],activeLayerId:null,opacity:0.5};
async function loadHistLayers() {
  try { const d=await api('/api/map/historical'); historicalState.layers=d.layers||DEFAULT_HIST; }
  catch { historicalState.layers=DEFAULT_HIST; }
  buildHistBtns();
}
function buildHistBtns() {
  setHtml($('hist-layers'), html`${historicalState.layers.map(l => html`<button type="button" class="hist-layer-btn${l.id===historicalState.activeLayerId?' active':''}" data-action="hist-layer" data-layer-id="${l.id}"><div>${l.name}</div><div class="hist-period">${l.period}</div></button>`)}`);
}
function toggleHistLayer(id) {
  if(historicalState.activeLayerId===id){removeHistOverlay();historicalState.activeLayerId=null;}
  else{historicalState.activeLayerId=id;applyHistOverlay(id);}
  $$('.hist-layer-btn').forEach(b=>b.classList.toggle('active',b.dataset.layerId===historicalState.activeLayerId));
}
function applyHistOverlay(id) {
  removeHistOverlay();
  const l=historicalState.layers.find(x=>x.id===id); if(!l)return;
  map.addSource('hist-overlay',{type:'raster',tiles:[l.url],tileSize:l.tile_size||256,minzoom:l.min_zoom||1,maxzoom:l.max_zoom||18});
  map.addLayer({id:'hist-overlay-layer',type:'raster',source:'hist-overlay',paint:{'raster-opacity':historicalState.opacity}}, beforePlaceLayers());
}
function removeHistOverlay() {
  if(map.getLayer('hist-overlay-layer'))map.removeLayer('hist-overlay-layer');
  if(map.getSource('hist-overlay'))map.removeSource('hist-overlay');
}

registerActions({
  'base-layer': (el) => setBaseLayer(el.dataset.layer),
  'toggle-history': (el, ev) => {
    ev.stopPropagation();
    const p = $('historical-panel');
    p.classList.toggle('visible');
    el.setAttribute('aria-expanded', String(p.classList.contains('visible')));
    if (p.classList.contains('visible') && !historicalState.layers.length) loadHistLayers();
  },
  'close-history': () => { $('historical-panel').classList.remove('visible'); $('btn-historical').setAttribute('aria-expanded', 'false'); },
  'hist-layer': (el) => toggleHistLayer(el.dataset.layerId),
  'toggle-pin': () => {
    setPinMode(!state.pinMode);
    if (state.pinMode) toast('📌 Tap the map to set your location');
  },
  'toggle-fog': () => setFogEnabled(!state.fogEnabled),
});
registerInput({
  'hist-opacity': (el) => {
    historicalState.opacity = parseInt(el.value) / 100;
    $('hist-opacity-value').textContent = el.value + '%';
    if (map.getLayer('hist-overlay-layer')) map.setPaintProperty('hist-overlay-layer', 'raster-opacity', historicalState.opacity);
  },
});
