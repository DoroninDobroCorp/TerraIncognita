/* Terra Incognita service worker — scope /map/ (it lives in map/, so that is its default scope).
 *
 * What is cached and how (see map/README.md):
 *   app shell  (app.html, css, js, icons, manifest)   precached at install, stale-while-revalidate afterwards
 *   CDN libs   (maplibre-gl js/css from unpkg)         precached with their SRI hashes, stale-while-revalidate
 *   map tiles  (OSM, OpenTopoMap, Esri, OHM, ...)      cache-first, bounded to TILE_CACHE_MAX entries, oldest evicted
 *   map fonts  (demotiles glyph .pbf)                  stale-while-revalidate
 *   API GETs   (ONLY the allow-list in API_CACHEABLE)  network-first, cached copy when the network fails
 *   everything else (POST/PUT/DELETE, /api/chat, journal/gamification/community, /health, OSRM, ...) is
 *   never touched: it goes straight to the network.
 *
 * To ship a new version of any shell file: edit SHELL_FILES if needed and bump SHELL_VERSION below.
 * Old caches are deleted on activate. The tile cache has its own name and survives version bumps.
 */
'use strict';

const SHELL_VERSION = 'v5';
const SHELL_CACHE = `ti-shell-${SHELL_VERSION}`;
const STATIC_CACHE = `ti-static-${SHELL_VERSION}`;
const API_CACHE = `ti-api-${SHELL_VERSION}`;
const TILE_CACHE = 'ti-tiles-v1';
const KNOWN_CACHES = [SHELL_CACHE, STATIC_CACHE, API_CACHE, TILE_CACHE];

const TILE_CACHE_MAX = 800;          // entries; the oldest are evicted first
const TILE_MAX_AGE_MS = 14 * 24 * 3600 * 1000; // older tiles are refreshed when online (and still used when offline)
const API_CACHE_MAX = 60;
const API_NETWORK_TIMEOUT_MS = 8000;

// Paths are relative to the service worker scope (/map/). A test keeps this list in sync with app.html.
const SHELL_FILES = [
  'app.html',
  'css/app.css',
  'js/config.js',
  'js/util.js',
  'js/actions.js',
  'js/ui.js',
  'js/help.js',
  'js/settings.js',
  'js/theme-init.js',
  'js/api.js',
  'js/map.js',
  'js/nav.js',
  'js/places.js',
  'js/place-extras.js',
  'js/daily.js',
  'js/heatmap.js',
  'js/chat.js',
  'js/voice.js',
  'js/journal.js',
  'js/routes.js',
  'js/profile.js',
  'js/community.js',
  'js/offline.js',
  'js/pwa.js',
  'js/main.js',
  'manifest.webmanifest',
  'favicon.svg',
  'icons/icon-192.png',
  'icons/icon-512.png',
  'icons/icon-maskable-192.png',
  'icons/icon-maskable-512.png',
  'icons/apple-touch-icon.png',
];

// Same URLs and SRI hashes as the <script>/<link> tags in app.html (a test compares them).
const CDN_FILES = [
  { url: 'https://unpkg.com/maplibre-gl@4.1.2/dist/maplibre-gl.js', integrity: 'sha256-G0W6/JRzbzxZYK5MP4SRlKnxYFr7F8kmnF0ZT1fnlxk=' },
  { url: 'https://unpkg.com/maplibre-gl@4.1.2/dist/maplibre-gl.css', integrity: 'sha256-2O5Q3ujU9bBBT9Jnuc6Lu9m0bZVKMRW9xE164V9gqZQ=' },
];

// The only API responses worth keeping: public, non-personal, idempotent.
const API_CACHEABLE = [
  /^\/api\/map\/(config|presets|historical)$/,
  /^\/api\/daily$/,
];

const TILE_HOSTS = [
  /^tile\.openstreetmap\.org$/,
  /^([a-c]\.)?tile\.opentopomap\.org$/,
  /^server\.arcgisonline\.com$/,
  /^tile\.openhistoricalmap\.org$/,
  /^maps\.georeferencer\.com$/,
];
const TILE_PATH = /\/\d{1,2}\/\d+\/\d+(\.(png|jpe?g|webp|pbf))?$/;

const scopeUrl = new URL(self.registration.scope);
const shellUrls = new Set(SHELL_FILES.map((f) => new URL(f, scopeUrl).href));
const appShellUrl = new URL('app.html', scopeUrl).href;

// ── lifecycle ──
self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    // Same-origin shell is required: if one file is missing the install fails and the old worker stays.
    await cache.addAll(SHELL_FILES.map((f) => new Request(new URL(f, scopeUrl).href, { cache: 'reload' })));
    // CDN files are best effort (offline install); the fetch handler caches them on first use anyway.
    await Promise.all(CDN_FILES.map((f) =>
      cache.add(new Request(f.url, { integrity: f.integrity, mode: 'cors', credentials: 'omit' })).catch(() => {})));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((n) => n.startsWith('ti-') && !KNOWN_CACHES.includes(n)).map((n) => caches.delete(n)));
    await trimCache(await caches.open(TILE_CACHE), TILE_CACHE_MAX);
    await self.clients.claim();
  })());
});

// ── routing ──
self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return; // never intercept writes
  const url = new URL(req.url);

  if (url.origin === self.location.origin) {
    if (url.pathname.startsWith('/api/')) {
      if (API_CACHEABLE.some((re) => re.test(url.pathname))) event.respondWith(networkFirstApi(event));
      return; // all other API calls: network only
    }
    if (shellUrls.has(url.origin + url.pathname)) {
      event.respondWith(staleWhileRevalidate(event, SHELL_CACHE, { ignoreSearch: true, navigationFallback: req.mode === 'navigate' }));
    }
    return;
  }

  if (url.hostname === 'unpkg.com') {
    event.respondWith(staleWhileRevalidate(event, SHELL_CACHE, {}));
  } else if (url.hostname === 'demotiles.maplibre.org') {
    event.respondWith(staleWhileRevalidate(event, STATIC_CACHE, {}));
  } else if (TILE_HOSTS.some((re) => re.test(url.hostname)) && TILE_PATH.test(url.pathname)) {
    event.respondWith(cacheFirstTile(event));
  }
  // anything else (OSRM, Nominatim, ...) is not ours to cache
});

// ── strategies ──
async function staleWhileRevalidate(event, cacheName, { ignoreSearch = false, navigationFallback = false }) {
  const req = event.request;
  const cache = await caches.open(cacheName);
  const hit = await cache.match(req, { ignoreSearch, ignoreVary: true });
  const refresh = fetch(new Request(req, { cache: 'no-cache' })) // revalidate with ETag, cheap when unchanged
    .then(async (res) => {
      if (res && res.ok && (res.type === 'basic' || res.type === 'cors')) await cache.put(req, res.clone());
      return res;
    })
    .catch(() => null);
  if (hit) {
    event.waitUntil(refresh);
    return hit;
  }
  const res = await refresh;
  if (res) return res;
  if (navigationFallback) {
    const shell = await cache.match(appShellUrl, { ignoreSearch: true });
    if (shell) return shell;
  }
  return Response.error();
}

async function cacheFirstTile(event) {
  const req = event.request;
  const cache = await caches.open(TILE_CACHE);
  const hit = await cache.match(req, { ignoreVary: true });
  if (hit && !isStale(hit)) return hit;
  try {
    const res = await fetch(req);
    if (res.ok && res.type !== 'opaque') {
      event.waitUntil(cache.put(req, res.clone()).then(() => maybeTrimTiles(cache)));
    }
    return res;
  } catch (e) {
    return hit || Response.error(); // offline: an old tile beats a blank one
  }
}

function isStale(res) {
  const d = Date.parse(res.headers.get('date') || '');
  return Number.isFinite(d) && Date.now() - d > TILE_MAX_AGE_MS;
}

async function networkFirstApi(event) {
  const req = event.request;
  const cache = await caches.open(API_CACHE);
  try {
    const res = await Promise.race([
      fetch(req),
      new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), API_NETWORK_TIMEOUT_MS)),
    ]);
    const noStore = /no-store/i.test(res.headers.get('cache-control') || '');
    if (res.ok && !noStore) event.waitUntil(cache.put(req, res.clone()).then(() => trimCache(cache, API_CACHE_MAX)));
    return res;
  } catch (err) {
    const hit = await cache.match(req);
    if (hit) return hit;
    throw err;
  }
}

// ── bounded caches ──
let tilePuts = 0;
async function maybeTrimTiles(cache) {
  if (++tilePuts % 20 === 0) await trimCache(cache, TILE_CACHE_MAX);
}
async function trimCache(cache, max) {
  const keys = await cache.keys(); // oldest first (insertion order)
  for (let i = 0; i < keys.length - max; i++) await cache.delete(keys[i]);
}
