// Life heatmap: every place you checked in at, from GET /api/stats/heatmap -> {points:[{lat,lng,intensity}]}.
// Toggle with the 🔥 map button (or "Show on map" in Journal > Stats).
// Classic script (no modules): every file in map/js shares one global scope.
'use strict';

let heatmapOn = false;
let heatmapBusy = false;

function removeHeatmap() {
  if (!mapStyleReady) return;
  if (map.getLayer('life-heat-layer')) map.removeLayer('life-heat-layer');
  if (map.getSource('life-heat')) map.removeSource('life-heat');
}

function drawHeatmap(points) {
  if (!mapStyleReady) return;
  removeHeatmap();
  const features = points
    .filter(p => Number.isFinite(Number(p.lat)) && Number.isFinite(Number(p.lng)))
    .map(p => ({ type: 'Feature', properties: { w: clamp(num(p.intensity, 1), 0, 1) }, geometry: { type: 'Point', coordinates: [Number(p.lng), Number(p.lat)] } }));
  map.addSource('life-heat', { type: 'geojson', data: { type: 'FeatureCollection', features } });
  map.addLayer({ id: 'life-heat-layer', type: 'heatmap', source: 'life-heat', paint: {
    'heatmap-weight': ['get', 'w'],
    'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 0, 0.8, 15, 2.4],
    'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 0, 8, 10, 26, 16, 60],
    'heatmap-opacity': 0.85,
    'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'],
      0, 'rgba(255,214,102,0)', 0.2, 'rgba(255,214,102,0.6)', 0.5, 'rgba(255,140,0,0.8)', 0.8, 'rgba(230,57,70,0.92)', 1, 'rgba(150,0,70,1)'],
  } }, beforePlaceLayers());
  return features.length;
}

function setHeatmapButton(on) {
  const b = $('btn-heatmap');
  b.classList.toggle('active', on);
  b.setAttribute('aria-pressed', String(on));
}

async function setHeatmap(on) {
  if (heatmapBusy) return;
  if (!on) { heatmapOn = false; removeHeatmap(); setHeatmapButton(false); return; }
  heatmapBusy = true;
  try {
    const data = await api('/api/stats/heatmap');
    const n = drawHeatmap(data.points || []);
    heatmapOn = true;
    setHeatmapButton(true);
    if (!n) toast('No visits yet — check in at places and your life map will glow here', 'info');
  } catch { toast('Could not load your heatmap', 'error'); heatmapOn = false; setHeatmapButton(false); }
  heatmapBusy = false;
}

/** After a check-in: redraw if the layer is showing. */
async function refreshHeatmap() {
  if (!heatmapOn) return;
  try { const data = await api('/api/stats/heatmap'); drawHeatmap(data.points || []); } catch { /* keep the old picture */ }
}

registerActions({
  'toggle-heatmap': () => setHeatmap(!heatmapOn),
  'show-heatmap': () => { showSection('map', { runHooks: false }); setHeatmap(true); },
});
