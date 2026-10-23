// Startup. Runs last: every other script has registered its actions by now.
// Classic script (no modules): every file in map/js shares one global scope.
'use strict';

/** Runs once the map style is ready (see initMap). */
async function startApp() {
  syncLocationInputs(); // route forms start with the default/map location instead of empty fields
  try {
    const config = await api('/api/map/config');
    if (config.category_styles) {
      CATEGORIES = {};
      for (const [k, s] of Object.entries(config.category_styles)) CATEGORIES[k] = s;
      rebuildPresets();
      state.activeCategories = new Set(Object.keys(CATEGORIES));
      rebuildCategoryChips();
      buildExploreCats();
    }
  } catch { /* defaults from config.js stay */ }
  loadHistLayers();
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(p => {
      state.userLat = p.coords.latitude; state.userLng = p.coords.longitude;
      map.flyTo({center:[state.userLng,state.userLat],zoom:13});
      updateUserMarker(); fetchPlaces(); startGpsTracking(); syncLocationInputs();
    }, () => {
      toast('📍 GPS unavailable — tap 📌 to set location or 📍 to retry','info');
      fetchPlaces();
    }, {enableHighAccuracy:true,timeout:15000,maximumAge:60000});
  } else {
    toast('Geolocation not supported in this browser','info');
    fetchPlaces();
  }
  $('fog-stats').classList.toggle('visible', state.fogEnabled);
  analytics.track('app_loaded', { version: APP_VERSION });
  console.log(`Terra Incognita v${APP_VERSION} loaded`);
}

function boot() {
  loadPersistedState();
  initUi();
  initSettings();
  initPlaces();
  initVoice();
  buildExploreCats();
  initPwa();
  initMap(startApp);
  probeFeatures().then(() => loadStreak());
  flushPendingVisits(); // check-ins left over from an earlier offline session
  checkHealth();
}

boot();
