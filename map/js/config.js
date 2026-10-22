// Constants, category/preset tables, map styles and the shared `state` object.
// Classic script (no modules): shares the global scope with the other map/js files.

const APP_VERSION = '1.1.0';
const API = window.location.origin || 'http://localhost:1312';

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

const FOG_REVEAL_RADIUS_DEG = 0.002;
const FOG_CIRCLE_SEGMENTS = 24;

let state = {
  places: [], filteredPlaces: [],
  activeCategories: new Set(Object.keys(CATEGORIES)),
  radius: 5.0, minConfidence: 0, hideVisited: false, fogEnabled: true,
  visitedPlaces: new Set(JSON.parse(localStorage.getItem('ti_visited') || '[]')),
  savedPlaces: new Set(JSON.parse(localStorage.getItem('ti_saved') || '[]')),
  userLat: 42.4410, userLng: 18.7712,
  currentPreset: 'all', selectedPlace: null, currentRoute: null,
};

const STYLES = {
  dark:{ version:8, glyphs:'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf', sources:{ osm:{ type:'raster', tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize:256, attribution:'&copy; <a href="https://openstreetmap.org">OpenStreetMap</a>' }}, layers:[{id:'osm',type:'raster',source:'osm'}] },
  satellite:{ version:8, glyphs:'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf', sources:{ sat:{ type:'raster', tiles:['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'], tileSize:256 }}, layers:[{id:'sat',type:'raster',source:'sat'}] },
  terrain:{ version:8, glyphs:'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf', sources:{ ter:{ type:'raster', tiles:['https://a.tile.opentopomap.org/{z}/{x}/{y}.png'], tileSize:256, attribution:'&copy; <a href="https://opentopomap.org">OpenTopoMap</a>' }}, layers:[{id:'ter',type:'raster',source:'ter'}] },
};
