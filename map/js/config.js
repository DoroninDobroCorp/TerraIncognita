// Constants, category/preset tables and the shared `state` object.
// Classic script (no modules): every file in map/js shares one global scope.
// Load order is fixed by app.html; nothing in here touches the DOM.
'use strict';

const APP_VERSION = '1.2.0';
const API = window.location.origin || 'http://localhost:1312';

// Category table. Replaced by GET /api/map/config at start-up (see main.js).
let CATEGORIES = {
  abandoned:{icon:'🏚️',label:'Abandoned',color:'#e74c3c',group:'unusual'},
  underground:{icon:'🕳️',label:'Underground',color:'#8e44ad',group:'unusual'},
  industrial:{icon:'🏭',label:'Industrial',color:'#d35400',group:'unusual'},
  ruins:{icon:'🏛️',label:'Ruins',color:'#c0392b',group:'unusual'},
  military:{icon:'🎖️',label:'Military',color:'#2c3e50',group:'unusual'},
  nature_hidden:{icon:'🌿',label:'Hidden Nature',color:'#27ae60',group:'unusual'},
  viewpoint:{icon:'👁️',label:'Viewpoint',color:'#2980b9',group:'classic'},
  street_art:{icon:'🎨',label:'Street Art',color:'#e91e63',group:'unusual'},
  transport:{icon:'🚂',label:'Transport',color:'#f39c12',group:'unusual'},
  water:{icon:'💧',label:'Water',color:'#3498db',group:'unusual'},
  cave:{icon:'🦇',label:'Cave',color:'#34495e',group:'unusual'},
  religious:{icon:'⛪',label:'Religious',color:'#9b59b6',group:'classic'},
  landmark:{icon:'🗿',label:'Landmark',color:'#e67e22',group:'classic'},
  museum:{icon:'🏛️',label:'Museum',color:'#1abc9c',group:'classic'},
  architecture:{icon:'🏰',label:'Architecture',color:'#16a085',group:'classic'},
  monument:{icon:'🗽',label:'Monument',color:'#f1c40f',group:'classic'},
  park:{icon:'🌳',label:'Park',color:'#2ecc71',group:'classic'},
};
const PRESETS = {
  all: Object.keys(CATEGORIES),
  hidden: Object.keys(CATEGORIES).filter(k => CATEGORIES[k].group === 'unusual'),
  urbex: ['abandoned','underground','industrial','ruins','military'],
  nature: ['nature_hidden','viewpoint','water','cave','park'],
  history: ['ruins','military','religious','landmark','museum','monument','architecture'],
  classic: Object.keys(CATEGORIES).filter(k => CATEGORIES[k].group === 'classic'),
};
/** Rebuild the derived preset lists after CATEGORIES was replaced from the API. */
function rebuildPresets() {
  PRESETS.all = Object.keys(CATEGORIES);
  PRESETS.hidden = Object.keys(CATEGORIES).filter(k => CATEGORIES[k].group === 'unusual');
  PRESETS.classic = Object.keys(CATEGORIES).filter(k => CATEGORIES[k].group === 'classic');
}

const FOG_REVEAL_RADIUS_DEG = 0.002;
const FOG_CIRCLE_SEGMENTS = 24;

// localStorage keys (kept identical to the pre-split app so existing data survives).
const LS = {
  userId: 'ti_user_id',
  storageVersion: 'ti_storage_version',
  onboarded: 'ti_onboarded',
  visited: 'ti_visited',
  saved: 'ti_saved',
  routes: 'ti_routes',
  theme: 'ti_theme',
  lowPower: 'ti_low_power',
  speak: 'ti_speak',
  dailyDismissed: 'ti_daily_dismissed',
  lastPlaces: 'ti_last_places',
  features: 'ti_features',
  pendingVisits: 'ti_pending_visits',
};

// Shared mutable app state. Persisted pieces are loaded lazily by main.js (see loadPersistedState).
const state = {
  places: [], filteredPlaces: [],
  activeCategories: new Set(Object.keys(CATEGORIES)),
  radius: 5.0, minConfidence: 0, hideVisited: false, fogEnabled: true,
  visitedPlaces: new Set(), savedPlaces: new Set(),
  userLat: 42.4410, userLng: 18.7712,
  currentPreset: 'all', selectedPlace: null, currentRoute: null,
  pinMode: false,
  lowPower: false,
  revealedIds: new Set(), // places shown despite fog this session (e.g. the place of the day)
};

// Base map layers. All three raster layers live in ONE style; switching just flips visibility,
// so overlays (places, fog, route line, history, heatmap) survive a layer change.
const BASE_LAYERS = {
  streets: {
    sourceId: 'osm', layerId: 'osm',
    source: { type:'raster', tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize:256, maxzoom:19,
      attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' },
  },
  satellite: {
    sourceId: 'sat', layerId: 'sat',
    source: { type:'raster', tiles:['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'], tileSize:256, maxzoom:19,
      attribution:'Imagery &copy; Esri, Maxar, Earthstar Geographics, and the GIS User Community' },
  },
  terrain: {
    sourceId: 'ter', layerId: 'ter',
    source: { type:'raster', tiles:['https://a.tile.opentopomap.org/{z}/{x}/{y}.png'], tileSize:256, maxzoom:17,
      attribution:'Map data: &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, SRTM | Style: &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)' },
  },
};
const GLYPHS_URL = 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf';

// Used when GET /api/map/historical fails (URLs mirror the server's list).
const DEFAULT_HIST = [
  {id:'owm_1880',name:'Old World Maps (~1880)',period:'Late 19th Century',year_start:1850,year_end:1900,url:'https://maps.georeferencer.com/georeferences/28da2318-c4b3-5f25-83dc-3da27859fea2/2019-02-19T17:27:12.514288Z/map/{z}/{x}/{y}.png',tile_size:256,min_zoom:3,max_zoom:15,attribution:'&copy; Georeferencer, David Rumsey Map Collection'},
];
