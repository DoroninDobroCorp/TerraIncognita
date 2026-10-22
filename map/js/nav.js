// GPS tracking, user marker and turn-by-turn style navigation to a place.
// Classic script (no modules): shares the global scope with the other map/js files.

let userMarker = null;
let gpsWatchId = null;
let gpsTracking = false;
let navLine = null;
let navTarget = null;

// Geolocation
document.getElementById('btn-locate').addEventListener('click', () => {
  if (gpsTracking) {
    stopGpsTracking();
    toast('📍 GPS tracking off');
    return;
  }
  if (!navigator.geolocation) {
    const c = map.getCenter();
    state.userLat = c.lat; state.userLng = c.lng;
    updateUserMarker(); fetchPlaces();
    document.getElementById('explore-center').value = `${c.lat.toFixed(4)}, ${c.lng.toFixed(4)}`;
    document.getElementById('route-from').value = `${c.lat.toFixed(4)}, ${c.lng.toFixed(4)}`;
    toast('📍 Location set to map center (GPS not available)');
    return;
  }
  // First: get current position quickly, then start tracking
  toast('📍 Getting location...', 'info');
  navigator.geolocation.getCurrentPosition(pos => {
    state.userLat = pos.coords.latitude; state.userLng = pos.coords.longitude;
    map.flyTo({center:[state.userLng,state.userLat],zoom:14});
    updateUserMarker(); fetchPlaces();
    document.getElementById('explore-center').value = `${state.userLat.toFixed(4)}, ${state.userLng.toFixed(4)}`;
    document.getElementById('route-from').value = `${state.userLat.toFixed(4)}, ${state.userLng.toFixed(4)}`;
    startGpsTracking();
  }, (err) => {
    console.warn('Geolocation error:', err.code, err.message);
    const c = map.getCenter();
    state.userLat = c.lat; state.userLng = c.lng;
    updateUserMarker(); fetchPlaces();
    document.getElementById('explore-center').value = `${c.lat.toFixed(4)}, ${c.lng.toFixed(4)}`;
    document.getElementById('route-from').value = `${c.lat.toFixed(4)}, ${c.lng.toFixed(4)}`;
    if (err.code === 1) toast('📍 Location access denied — check browser permissions', 'error');
    else toast('📍 GPS unavailable — tap 📌 to set location manually');
  }, {enableHighAccuracy:true,timeout:15000});
});

function updateUserMarker() {
  if (userMarker) userMarker.remove();
  const el = document.createElement('div');
  el.className = 'user-marker-tracking' + (gpsTracking ? ' active' : '');
  userMarker = new maplibregl.Marker({element:el}).setLngLat([state.userLng,state.userLat]).addTo(map);
  updateNavLine();
}

function startGpsTracking() {
  if (gpsWatchId !== null) return;
  if (!navigator.geolocation) { toast('GPS not available','error'); return; }
  gpsTracking = true;
  document.getElementById('btn-locate').classList.add('active');
  toast('📍 GPS tracking on', 'success');
  gpsWatchId = navigator.geolocation.watchPosition(pos => {
    state.userLat = pos.coords.latitude;
    state.userLng = pos.coords.longitude;
    updateUserMarker();
    document.getElementById('explore-center').value = `${state.userLat.toFixed(4)}, ${state.userLng.toFixed(4)}`;
    document.getElementById('route-from').value = `${state.userLat.toFixed(4)}, ${state.userLng.toFixed(4)}`;
  }, err => {
    console.warn('GPS error:', err.code, err.message);
    if (err.code === 1) { toast('📍 Location access denied — check browser permissions', 'error'); stopGpsTracking(); }
    else if (err.code === 3) { /* timeout — keep trying */ }
    else { toast('📍 GPS error: ' + err.message, 'error'); }
  }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 3000 });
}

function stopGpsTracking() {
  if (gpsWatchId !== null) { navigator.geolocation.clearWatch(gpsWatchId); gpsWatchId = null; }
  gpsTracking = false;
  document.getElementById('btn-locate').classList.remove('active');
  updateUserMarker();
}

let navRouteCoords = null; // cached route geometry from OSRM
let navRouteDist = null;   // route distance in meters
let navRouteDur = null;    // route duration in seconds
let navRouteLoading = false;
let navLastRouteFrom = null; // to avoid re-fetching on every GPS tick

async function fetchRoute(fromLng, fromLat, toLng, toLat) {
  const url = `https://router.project-osrm.org/route/v1/foot/${fromLng},${fromLat};${toLng},${toLat}?overview=full&geometries=geojson&steps=true`;
  try {
    const resp = await fetch(url);
    const data = await resp.json();
    if (data.code === 'Ok' && data.routes.length > 0) {
      return data.routes[0];
    }
  } catch (e) { console.warn('OSRM routing error:', e); }
  return null;
}

function shouldReroute(fromLng, fromLat) {
  if (!navLastRouteFrom) return true;
  const moved = haversineJS(navLastRouteFrom.lat, navLastRouteFrom.lng, fromLat, fromLng);
  return moved > 50; // reroute if user moved >50m from last route start
}

async function startNavigation(place) {
  navTarget = { lat: place.coordinates.lat, lng: place.coordinates.lng, name: place.name || 'Destination' };
  navRouteCoords = null; navRouteDist = null; navRouteDur = null;
  document.getElementById('nav-distance').classList.add('visible');
  document.getElementById('nav-dist-text').textContent = 'Building route...';
  if (!gpsTracking) startGpsTracking();

  // Fetch real walking route
  await buildNavRoute();

  // Fit map to show entire route
  if (navRouteCoords && navRouteCoords.length > 1) {
    const bounds = navRouteCoords.reduce((b, c) => b.extend(c), new maplibregl.LngLatBounds(navRouteCoords[0], navRouteCoords[0]));
    map.fitBounds(bounds, { padding: 60, maxZoom: 16 });
  } else {
    map.flyTo({ center: [state.userLng, state.userLat], zoom: Math.max(map.getZoom(), 14) });
  }
  toast('🧭 Route to ' + navTarget.name, 'success');
}

async function buildNavRoute() {
  if (!navTarget || !state.userLat || navRouteLoading) return;
  navRouteLoading = true;
  const route = await fetchRoute(state.userLng, state.userLat, navTarget.lng, navTarget.lat);
  navRouteLoading = false;
  if (route) {
    navRouteCoords = route.geometry.coordinates;
    navRouteDist = route.distance;
    navRouteDur = route.duration;
    navLastRouteFrom = { lat: state.userLat, lng: state.userLng };
    drawNavLine();
    updateNavBadge();
  } else {
    // Fallback: straight line
    navRouteCoords = [[state.userLng, state.userLat], [navTarget.lng, navTarget.lat]];
    navRouteDist = haversineJS(state.userLat, state.userLng, navTarget.lat, navTarget.lng);
    navRouteDur = navRouteDist / 1.2; // ~4.3 km/h walking
    navLastRouteFrom = { lat: state.userLat, lng: state.userLng };
    drawNavLine();
    updateNavBadge();
    toast('⚠️ Could not build route — showing straight line', 'warning');
  }
}

function drawNavLine() {
  if (!navRouteCoords) return;
  const geojson = { type:'Feature', geometry:{ type:'LineString', coordinates: navRouteCoords } };
  if (map.getSource('nav-line')) {
    map.getSource('nav-line').setData(geojson);
  } else {
    try {
      map.addSource('nav-line', { type:'geojson', data: geojson });
      map.addLayer({ id:'nav-line-layer', type:'line', source:'nav-line', paint:{
        'line-color':'#ff6b35', 'line-width': 4, 'line-opacity': 0.85
      }});
    } catch(e) { console.warn('Nav line error:', e); }
  }
}

function updateNavBadge() {
  if (!navTarget) return;
  // Distance remaining: haversine to target (close enough for badge, avoids re-routing)
  const remaining = haversineJS(state.userLat, state.userLng, navTarget.lat, navTarget.lng);
  let distText, durText;
  if (navRouteDist && navLastRouteFrom) {
    // Scale route distance by proportion of haversine remaining vs original haversine
    const origHav = haversineJS(navLastRouteFrom.lat, navLastRouteFrom.lng, navTarget.lat, navTarget.lng);
    const ratio = origHav > 0 ? remaining / origHav : 1;
    const estDist = navRouteDist * ratio;
    const estDur = navRouteDur * ratio;
    distText = estDist < 1000 ? Math.round(estDist) + ' m' : (estDist/1000).toFixed(1) + ' km';
    const mins = Math.ceil(estDur / 60);
    durText = mins < 60 ? mins + ' min' : Math.floor(mins/60) + 'h ' + (mins%60) + 'min';
  } else {
    distText = remaining < 1000 ? Math.round(remaining) + ' m' : (remaining/1000).toFixed(1) + ' km';
    durText = Math.ceil(remaining / 1.2 / 60) + ' min';
  }
  document.getElementById('nav-dist-text').textContent = '🚶 ' + distText + ' · ' + durText + ' → ' + navTarget.name;

  // Auto-arrive: if within 30m
  if (remaining < 30) {
    toast('🎉 You arrived at ' + navTarget.name + '!', 'success');
    stopNavigation();
  }
}

function stopNavigation() {
  navTarget = null; navRouteCoords = null; navRouteDist = null; navRouteDur = null;
  navLastRouteFrom = null;
  document.getElementById('nav-distance').classList.remove('visible');
  if (map.getSource('nav-line')) { map.removeLayer('nav-line-layer'); map.removeSource('nav-line'); }
}

function updateNavLine() {
  if (!navTarget || !state.userLat) return;
  // Reroute if user moved significantly from route start
  if (shouldReroute(state.userLng, state.userLat)) {
    buildNavRoute();
  } else {
    updateNavBadge();
  }
}

