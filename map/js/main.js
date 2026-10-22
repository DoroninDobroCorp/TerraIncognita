// Startup: runs after every other script has been parsed.
// Classic script (no modules): shares the global scope with the other map/js files.

// ═══════════════════════════════════════════════════════
//  INIT
// ═══════════════════════════════════════════════════════
map.on('load', async () => {
  try {
    const config = await api('/api/map/config');
    if (config.category_styles) { CATEGORIES={}; for(const[k,s]of Object.entries(config.category_styles)){CATEGORIES[k]=s;} PRESETS.all=Object.keys(CATEGORIES); PRESETS.hidden=Object.keys(CATEGORIES).filter(k=>CATEGORIES[k].group==='unusual'); PRESETS.classic=Object.keys(CATEGORIES).filter(k=>CATEGORIES[k].group==='classic'); state.activeCategories=new Set(Object.keys(CATEGORIES)); rebuildCategoryChips(); }
  } catch {}
  loadHistLayers();
  if(navigator.geolocation){navigator.geolocation.getCurrentPosition(p=>{state.userLat=p.coords.latitude;state.userLng=p.coords.longitude;map.flyTo({center:[state.userLng,state.userLat],zoom:13});updateUserMarker();fetchPlaces();startGpsTracking();},()=>{toast('📍 GPS unavailable — tap 📌 to set location or 📍 to retry','info');fetchPlaces();},{enableHighAccuracy:true,timeout:15000,maximumAge:60000});}else{toast('Geolocation not supported in this browser','info');fetchPlaces();}
  document.getElementById('fog-stats').classList.toggle('visible',state.fogEnabled);
  analytics.track('app_loaded', { version: APP_VERSION });
  console.log(`Terra Incognita v${APP_VERSION} loaded`);
});

// Start API health polling
checkHealth();
