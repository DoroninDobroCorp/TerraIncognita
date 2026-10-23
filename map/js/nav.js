// GPS tracking (battery-aware), the user marker, and walking navigation to a place.
// Classic script (no modules): every file in map/js shares one global scope.
'use strict';

let userMarker = null;
let gpsWatchId = null;
let gpsTracking = false;   // user wants tracking (button on); the watch itself pauses while the tab is hidden
let lastGpsFix = null;     // {lat, lng, t} of the last accepted fix
let navTarget = null;

let navRouteCoords = null; // cached route geometry from OSRM
let navRouteDist = null;   // route distance in meters
let navRouteDur = null;    // route duration in seconds
let navRouteLoading = false;
let navLastRouteFrom = null; // to avoid re-fetching on every GPS tick

function mapCenter() {
  if (mapAvailable) { const c = map.getCenter(); if (c && typeof c.lat === 'number') return { lat: c.lat, lng: c.lng }; }
  return { lat: state.userLat, lng: state.userLng };
}

/** Keep the "From"/"Start" inputs of the route builder in sync with the current location. */
function syncLocationInputs() {
  const v = fmtCoords(state.userLat, state.userLng);
  ['explore-center', 'route-from', 'loop-center'].forEach(id => { const el = $(id); if (el) el.value = v; });
}

/** Move "me" (pin mode, long press, right click, map centre fallback). */
function setUserLocation(lat, lng, label) {
  state.userLat = lat; state.userLng = lng;
  updateUserMarker(); fetchPlaces(); syncLocationInputs();
  if (label) toast(`${label}: ${fmtCoords(lat, lng)}`);
}

function updateUserMarker() {
  if (!mapAvailable) return;
  if (!userMarker) {
    const el = document.createElement('div');
    el.className = 'user-marker-tracking';
    userMarker = new maplibregl.Marker({ element: el }).setLngLat([state.userLng, state.userLat]).addTo(map);
  } else {
    userMarker.setLngLat([state.userLng, state.userLat]);
  }
  userMarker.getElement().classList.toggle('active', gpsTracking && !state.lowPower);
  updateNavLine();
}

// ── GPS ──
/** Fewer/cheaper fixes in low-power mode: coarse accuracy, cached positions allowed. */
function gpsOptions() {
  return state.lowPower
    ? { enableHighAccuracy: false, maximumAge: 20000, timeout: 30000 }
    : { enableHighAccuracy: true, maximumAge: 3000, timeout: 15000 };
}

function setLocateActive(on) {
  const b = $('btn-locate');
  b.classList.toggle('active', on);
  b.setAttribute('aria-pressed', String(on));
}

function onLocateClick() {
  if (gpsTracking) {
    stopGpsTracking();
    toast('📍 GPS tracking off');
    return;
  }
  const fallbackToCenter = (msg, type) => {
    const c = mapCenter();
    state.userLat = c.lat; state.userLng = c.lng;
    updateUserMarker(); fetchPlaces(); syncLocationInputs();
    if (msg) toast(msg, type);
  };
  if (!navigator.geolocation) { fallbackToCenter('📍 Location set to map center (GPS not available)'); return; }
  // First: get current position quickly, then start tracking
  toast('📍 Getting location...', 'info');
  navigator.geolocation.getCurrentPosition(pos => {
    state.userLat = pos.coords.latitude; state.userLng = pos.coords.longitude;
    lastGpsFix = { lat: state.userLat, lng: state.userLng, t: Date.now() };
    map.flyTo({center:[state.userLng,state.userLat],zoom:14});
    updateUserMarker(); fetchPlaces(); syncLocationInputs();
    startGpsTracking();
  }, (err) => {
    console.warn('Geolocation error:', err.code, err.message);
    if (err.code === 1) fallbackToCenter('📍 Location access denied — check browser permissions', 'error');
    else fallbackToCenter('📍 GPS unavailable — tap 📌 to set location manually');
  }, { enableHighAccuracy: !state.lowPower, timeout: 15000 });
}

function startGpsTracking() {
  if (gpsTracking && gpsWatchId !== null) return;
  if (!navigator.geolocation) { toast('GPS not available','error'); return; }
  gpsTracking = true;
  setLocateActive(true);
  toast('📍 GPS tracking on', 'success');
  beginGpsWatch();
}

function beginGpsWatch() {
  if (!gpsTracking || gpsWatchId !== null || document.hidden || !navigator.geolocation) return;
  gpsWatchId = navigator.geolocation.watchPosition(onGpsPosition, onGpsError, gpsOptions());
}
function endGpsWatch() {
  if (gpsWatchId !== null) { navigator.geolocation.clearWatch(gpsWatchId); gpsWatchId = null; }
}
/** Re-create the watch with the current options (low-power toggled). */
function restartGpsWatch() { endGpsWatch(); beginGpsWatch(); }

function onGpsPosition(pos) {
  const lat = pos.coords.latitude, lng = pos.coords.longitude, acc = pos.coords.accuracy || 0, now = Date.now();
  if (lastGpsFix) {
    // Throttle: ignore GPS jitter and bursts; still refresh now and then so the marker never freezes.
    const moved = haversineJS(lastGpsFix.lat, lastGpsFix.lng, lat, lng);
    const minMove = state.lowPower ? 30 : Math.max(5, Math.min(acc * 0.5, 25));
    const minGap = state.lowPower ? 20000 : 1000;
    const stale = now - lastGpsFix.t > (state.lowPower ? 120000 : 30000);
    if (!stale && (moved < minMove || now - lastGpsFix.t < minGap)) return;
  }
  lastGpsFix = { lat, lng, t: now };
  state.userLat = lat;
  state.userLng = lng;
  updateUserMarker();
  syncLocationInputs();
}

function onGpsError(err) {
  console.warn('GPS error:', err.code, err.message);
  if (err.code === 1) { toast('📍 Location access denied — check browser permissions', 'error'); stopGpsTracking(); }
  else if (err.code === 3) { /* timeout — keep trying */ }
  else { toast('📍 GPS error: ' + err.message, 'error'); }
}

function stopGpsTracking() {
  endGpsWatch();
  gpsTracking = false;
  lastGpsFix = null;
  setLocateActive(false);
  updateUserMarker();
}

// Stop polling the GPS (the biggest battery drain) while the page is hidden; resume on return.
document.addEventListener('visibilitychange', () => {
  document.body.classList.toggle('page-hidden', document.hidden);
  if (document.hidden) endGpsWatch(); else beginGpsWatch();
});

// ── Navigation to a place (real walking route via OSRM, straight-line fallback) ──
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
  return moved > (state.lowPower ? 150 : 50); // reroute if user moved far from last route start
}

async function startNavigation(place) {
  navTarget = { lat: place.coordinates.lat, lng: place.coordinates.lng, name: place.name || 'Destination' };
  navRouteCoords = null; navRouteDist = null; navRouteDur = null;
  $('nav-distance').classList.add('visible');
  $('nav-dist-text').textContent = 'Building route...';
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
  if (!navTarget) return; // navigation was stopped while the request was in flight
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
  if (!navRouteCoords || !mapStyleReady) return;
  const geojson = { type:'Feature', geometry:{ type:'LineString', coordinates: navRouteCoords } };
  if (map.getSource('nav-line')) {
    map.getSource('nav-line').setData(geojson);
  } else {
    try {
      map.addSource('nav-line', { type:'geojson', data: geojson });
      map.addLayer({ id:'nav-line-layer', type:'line', source:'nav-line', paint:{
        'line-color':'#ff6b35', 'line-width': 4, 'line-opacity': 0.85
      }}, beforePlaceLayers());
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
  $('nav-dist-text').textContent = '🚶 ' + distText + ' · ' + durText + ' → ' + navTarget.name;

  // Auto-arrive: if within 30m
  if (remaining < 30) {
    toast('🎉 You arrived at ' + navTarget.name + '!', 'success');
    stopNavigation();
  }
}

function stopNavigation() {
  navTarget = null; navRouteCoords = null; navRouteDist = null; navRouteDur = null;
  navLastRouteFrom = null;
  $('nav-distance').classList.remove('visible');
  if (mapStyleReady && map.getSource('nav-line')) { map.removeLayer('nav-line-layer'); map.removeSource('nav-line'); }
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

registerActions({
  locate: () => onLocateClick(),
  'nav-stop': () => stopNavigation(),
});
