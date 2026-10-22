// MapLibre map, base layers, place markers, fog of war, historical overlay.
// Classic script (no modules): shares the global scope with the other map/js files.

let map;
let mapAvailable = false;
try {
  map = new maplibregl.Map({ container:'map', style:STYLES.dark, center:[state.userLng,state.userLat], zoom:13, maxZoom:19, minZoom:3, dragRotate:false });
  mapAvailable = true;
  map.addControl(new maplibregl.NavigationControl({showCompass:true,showZoom:true}), 'bottom-right');
} catch (e) {
  console.warn('Map initialization failed (WebGL unavailable):', e.message);
  document.getElementById('map').innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-secondary);text-align:center;padding:20px"><div><div style="font-size:48px;margin-bottom:12px">🗺️</div><div>Map requires WebGL support.<br>Other features are still available.</div></div></div>';
  map = new Proxy({}, { get: () => (...args) => map });
}

// Layer switcher
document.querySelectorAll('.layer-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const layer = btn.dataset.layer;
    if (layer === 'historical') return;
    document.querySelectorAll('.layer-btn').forEach(b => { if (b.dataset.layer !== 'historical') b.classList.remove('active'); });
    btn.classList.add('active');
    map.setStyle(STYLES[layer]);
    map.once('styledata', () => { renderPlaces(); if (state.fogEnabled) renderFog(); if (navTarget) { const src = map.getSource('nav-line'); if (!src) updateNavLine(); } });
  });
});

// Pin mode — click map to set location
state.pinMode = false;
document.getElementById('btn-pin').addEventListener('click', () => {
  state.pinMode = !state.pinMode;
  document.getElementById('btn-pin').classList.toggle('active', state.pinMode);
  document.getElementById('map').classList.toggle('pin-mode', state.pinMode);
  if (state.pinMode) toast('📌 Tap the map to set your location');
});
map.on('click', e => {
  if (!state.pinMode) return;
  state.userLat = e.lngLat.lat; state.userLng = e.lngLat.lng;
  updateUserMarker(); fetchPlaces();
  document.getElementById('explore-center').value = `${state.userLat.toFixed(4)}, ${state.userLng.toFixed(4)}`;
  document.getElementById('route-from').value = `${state.userLat.toFixed(4)}, ${state.userLng.toFixed(4)}`;
  toast(`📌 Location set: ${state.userLat.toFixed(4)}, ${state.userLng.toFixed(4)}`);
  state.pinMode = false;
  document.getElementById('btn-pin').classList.remove('active');
  document.getElementById('map').classList.remove('pin-mode');
});

// Fog toggle
document.getElementById('btn-fog').addEventListener('click', () => {
  state.fogEnabled = !state.fogEnabled;
  document.getElementById('btn-fog').classList.toggle('active',state.fogEnabled);
  document.getElementById('toggle-fog').classList.toggle('on',state.fogEnabled);
  document.getElementById('fog-stats').classList.toggle('visible',state.fogEnabled);
  renderFog(); renderPlaces();
});

function renderPlaces() {
  ['place-clusters','cluster-count','unclustered-bg','unclustered-icon','place-silhouettes'].forEach(id => { if(map.getLayer(id)) map.removeLayer(id); });
  if (map.getSource('places')) map.removeSource('places');

  const features = state.filteredPlaces.map(p => {
    const cat = (p.categories&&p.categories[0])||'landmark';
    const def = CATEGORIES[cat]||CATEGORIES.landmark;
    const inFog = state.fogEnabled && !state.visitedPlaces.has(p.id);
    return {type:'Feature', properties:{id:p.id,name:p.name||'Unknown',icon:def.icon,color:def.color,category:cat,confidence:p.confidence||0.5,distance:p.distance_m||0,inFog,description:p.description||'',source:p.source||'osm'}, geometry:{type:'Point',coordinates:[p.coordinates.lng,p.coordinates.lat]}};
  });

  map.addSource('places',{type:'geojson',data:{type:'FeatureCollection',features},cluster:true,clusterMaxZoom:17,clusterRadius:50});
  map.addLayer({id:'place-clusters',type:'circle',source:'places',filter:['has','point_count'],paint:{'circle-color':['step',['get','point_count'],'#ff6b35',10,'#e74c3c',30,'#8e44ad'],'circle-radius':['step',['get','point_count'],22,10,28,30,34],'circle-opacity':1,'circle-stroke-width':3,'circle-stroke-color':'rgba(255,255,255,0.7)'}});
  map.addLayer({id:'cluster-count',type:'symbol',source:'places',filter:['has','point_count'],layout:{'text-field':'{point_count_abbreviated}','text-font':['Open Sans Regular','Arial Unicode MS Regular'],'text-size':15},paint:{'text-color':'#fff','text-halo-color':'rgba(0,0,0,0.6)','text-halo-width':1}});
  map.addLayer({id:'unclustered-bg',type:'circle',source:'places',filter:['!',['has','point_count']],paint:{'circle-color':['get','color'],'circle-radius':['case',['get','inFog'],10,14],'circle-opacity':1,'circle-stroke-width':3,'circle-stroke-color':['case',['get','inFog'],'rgba(255,255,255,0.3)','rgba(255,255,255,0.8)']}});
  map.addLayer({id:'unclustered-icon',type:'symbol',source:'places',filter:['all',['!',['has','point_count']],['!',['get','inFog']]],layout:{'text-field':['get','icon'],'text-size':22,'text-offset':[0,-1.5],'text-allow-overlap':true}});
  map.addLayer({id:'place-silhouettes',type:'symbol',source:'places',filter:['all',['!',['has','point_count']],['get','inFog']],layout:{'text-field':'❓','text-size':18,'text-offset':[0,-1.5],'text-allow-overlap':true},paint:{'text-opacity':0.5}});
}

function renderFog() {
  if(map.getLayer('fog-layer')) map.removeLayer('fog-layer');
  if(map.getSource('fog')) map.removeSource('fog');
  if(!state.fogEnabled) return;
  const visited = [...state.visitedPlaces].map(id => { const p = state.places.find(x=>x.id===id); return p?[p.coordinates.lng,p.coordinates.lat]:null; }).filter(Boolean);
  const world = [[-180,-85],[180,-85],[180,85],[-180,85],[-180,-85]];
  const holes = visited.map(([lng,lat]) => { const pts=[]; for(let i=0;i<=FOG_CIRCLE_SEGMENTS;i++){const a=(i/FOG_CIRCLE_SEGMENTS)*Math.PI*2; pts.push([lng+FOG_REVEAL_RADIUS_DEG/Math.cos(lat*Math.PI/180)*Math.cos(a),lat+FOG_REVEAL_RADIUS_DEG*Math.sin(a)]);} return pts; });
  map.addSource('fog',{type:'geojson',data:{type:'FeatureCollection',features:[{type:'Feature',geometry:{type:'Polygon',coordinates:[world,...holes]}}]}});
  map.addLayer({id:'fog-layer',type:'fill',source:'fog',paint:{'fill-color':'#0a0a1e','fill-opacity':0.55}},'place-clusters');
}
function updateFogStats() { const t=state.places.length,v=state.visitedPlaces.size,p=t>0?Math.round(v/t*100):0; document.getElementById('fog-pct').textContent=p+'%'; document.getElementById('fog-fill').style.width=p+'%'; }

// Map interactions — long press to set location
let _longPressTimer = null;
map.on('touchstart', e => {
  if (e.originalEvent.touches.length > 1) return;
  _longPressTimer = setTimeout(() => {
    const {lng, lat} = e.lngLat;
    state.userLat = lat; state.userLng = lng;
    updateUserMarker(); fetchPlaces();
    document.getElementById('explore-center').value = `${lat.toFixed(4)}, ${lng.toFixed(4)}`;
    document.getElementById('route-from').value = `${lat.toFixed(4)}, ${lng.toFixed(4)}`;
    toast(`📍 Location set: ${lat.toFixed(4)}, ${lng.toFixed(4)}`);
  }, 600);
});
map.on('touchend', () => { clearTimeout(_longPressTimer); });
map.on('touchmove', () => { clearTimeout(_longPressTimer); });
map.on('contextmenu', e => {
  e.preventDefault();
  const {lng, lat} = e.lngLat;
  state.userLat = lat; state.userLng = lng;
  updateUserMarker(); fetchPlaces();
  document.getElementById('explore-center').value = `${lat.toFixed(4)}, ${lng.toFixed(4)}`;
  document.getElementById('route-from').value = `${lat.toFixed(4)}, ${lng.toFixed(4)}`;
  toast(`📍 Location set: ${lat.toFixed(4)}, ${lng.toFixed(4)}`);
});

map.on('click','place-clusters', e => { if(state.pinMode) return; const f=map.queryRenderedFeatures(e.point,{layers:['place-clusters']}); if(!f.length)return; map.getSource('places').getClusterExpansionZoom(f[0].properties.cluster_id,(err,z)=>{ if(!err)map.easeTo({center:f[0].geometry.coordinates,zoom:z+1}); }); });
map.on('click','unclustered-bg', e => {
  if(state.pinMode) return;
  const p=e.features[0].properties, c=e.features[0].geometry.coordinates, def=CATEGORIES[p.category]||CATEGORIES.landmark;
  new maplibregl.Popup({offset:15,maxWidth:'250px'}).setLngLat(c).setHTML(`<div style="font-weight:700">${p.inFog==='true'?'❓ Mystery Place':def.icon+' '+p.name}</div><div style="font-size:11px;color:var(--accent)">${def.label}</div>${p.distance>0?'<div style="font-size:11px;color:var(--text-secondary)">📏 '+(p.distance/1000).toFixed(1)+' km</div>':''}<div style="margin-top:6px;color:var(--accent);font-size:11px;cursor:pointer;text-decoration:underline" onclick="showPlaceCard('${p.id}')">Show details →</div>`).addTo(map);
});
map.on('mouseenter','unclustered-bg',()=>{map.getCanvas().style.cursor='pointer';});
map.on('mouseleave','unclustered-bg',()=>{map.getCanvas().style.cursor='';});
map.on('mouseenter','place-clusters',()=>{map.getCanvas().style.cursor='pointer';});
map.on('mouseleave','place-clusters',()=>{map.getCanvas().style.cursor='';});

// Historical overlay
let historicalState = {layers:[],activeLayerId:null,opacity:0.5};
const DEFAULT_HIST = [
  {id:'ohm',name:'OpenHistoricalMap',period:'Multi-era',year_start:1000,year_end:2000,url:'https://www.openhistoricalmap.org/render/{z}/{x}/{y}.png',tile_size:256,min_zoom:1,max_zoom:18},
  {id:'esri',name:'Esri Historic Topo',period:'Early 20th C.',year_start:1890,year_end:1940,url:'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}',tile_size:256,min_zoom:1,max_zoom:16},
];
async function loadHistLayers() { try { const d=await api('/api/map/historical'); historicalState.layers=d.layers||DEFAULT_HIST; } catch { historicalState.layers=DEFAULT_HIST; } buildHistBtns(); }
function buildHistBtns() { const c=document.getElementById('hist-layers');c.innerHTML='';historicalState.layers.forEach(l=>{const b=document.createElement('button');b.className='hist-layer-btn';b.dataset.layerId=l.id;b.innerHTML=`<div>${l.name}</div><div style="font-size:10px;opacity:0.7">${l.period}</div>`;b.addEventListener('click',()=>toggleHistLayer(l.id));c.appendChild(b);}); }
function toggleHistLayer(id) { if(historicalState.activeLayerId===id){removeHistOverlay();historicalState.activeLayerId=null;}else{historicalState.activeLayerId=id;applyHistOverlay(id);} document.querySelectorAll('.hist-layer-btn').forEach(b=>b.classList.toggle('active',b.dataset.layerId===historicalState.activeLayerId)); }
function applyHistOverlay(id) { removeHistOverlay();const l=historicalState.layers.find(x=>x.id===id);if(!l)return; map.addSource('hist-overlay',{type:'raster',tiles:[l.url],tileSize:l.tile_size||256,minzoom:l.min_zoom||1,maxzoom:l.max_zoom||18}); map.addLayer({id:'hist-overlay-layer',type:'raster',source:'hist-overlay',paint:{'raster-opacity':historicalState.opacity}},'place-clusters'); }
function removeHistOverlay() { if(map.getLayer('hist-overlay-layer'))map.removeLayer('hist-overlay-layer');if(map.getSource('hist-overlay'))map.removeSource('hist-overlay'); }
document.getElementById('btn-historical').addEventListener('click',e=>{e.stopPropagation();const p=document.getElementById('historical-panel');p.classList.toggle('visible');if(p.classList.contains('visible')&&!historicalState.layers.length)loadHistLayers();});
document.getElementById('hist-close').addEventListener('click',()=>document.getElementById('historical-panel').classList.remove('visible'));
document.getElementById('hist-opacity').addEventListener('input',function(){historicalState.opacity=parseInt(this.value)/100;document.getElementById('hist-opacity-value').textContent=this.value+'%';if(map.getLayer('hist-overlay-layer'))map.setPaintProperty('hist-overlay-layer','raster-opacity',historicalState.opacity);});

function showMapLoading(s){document.getElementById('loading-overlay').classList.toggle('visible',s);}
