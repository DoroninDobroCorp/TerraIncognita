# Terra Incognita — map UI

Vanilla JS + MapLibre GL, no build step, no npm. FastAPI serves this folder at `/map/` (`/` redirects to
`/map/app.html`), so the whole UI is plain static files.

```
map/
├── app.html               markup only (no inline script, no inline handlers)
├── css/app.css            all styles: tokens + 3 themes, components, utilities
├── js/                    classic <script> files, one global scope (order matters, see below)
├── sw.js                  service worker (scope /map/)
├── manifest.webmanifest   PWA manifest
├── icons/                 PNG icons (192/512 any + maskable, 180 apple-touch)
├── favicon.svg
└── explorer.html, index.html, debug.html   legacy standalone pages (not part of app.html)
```

Run it: `cd backend && uvicorn app.main:app --port 8000` → <http://localhost:8000/map/app.html>.
Tests for everything in this folder: `backend/tests/test_map_assets.py` (static, no browser needed).

## Scripts and load order

Classic scripts share one global scope; `app.html` loads them in dependency order and `main.js` boots last.
Top-level code only *defines* things and registers actions — anything touching the DOM or the map runs from an
`init*()` function that `boot()` in `main.js` calls.

| file | role |
|---|---|
| `theme-init.js` | in `<head>`: sets `html[data-theme]` before first paint |
| `config.js` | constants, `CATEGORIES`/`PRESETS`, `BASE_LAYERS`, `LS` (localStorage keys), shared `state` |
| `util.js` | `$`/`$$`, `store` (safe localStorage), **`html` / `setHtml` / `escHtml` / `safeUrl` / `safeColor`**, analytics, geo helpers |
| `actions.js` | action registry + the delegated listeners (see below) |
| `ui.js` | sections, tabs, toasts, skeletons, modal dialog, onboarding |
| `help.js` | static help texts per section |
| `settings.js` | themes, low-power mode, settings dialog |
| `api.js` | `api()` (timeout/retry, `ApiError.status`), health polling, `TI.net`, feature probing |
| `map.js` | MapLibre map, base layers + per-theme paint, place markers, fog, history overlay, map gestures |
| `nav.js` | GPS tracking (battery aware), user marker, walking navigation |
| `places.js` | discover / filters / place card |
| `place-extras.js` | unusual score + reasons, Wikipedia/photos enrichment, "ask this place" |
| `daily.js`, `heatmap.js` | place of the day, life heatmap layer |
| `chat.js`, `voice.js` | AI chat; dictation + spoken replies |
| `journal.js`, `routes.js`, `profile.js`, `community.js`, `offline.js` | the tab modules |
| `pwa.js` | service worker registration, offline banner, install prompt |
| `main.js` | `boot()` |

## Actions: no inline handlers

`script-src` has no `'unsafe-inline'`, so markup never contains `onclick=` and friends. Instead:

| attribute | registry | fires on |
|---|---|---|
| `data-action="name"` | `registerActions({name: (el, ev) => …})` | click, Enter/Space on non-native controls |
| `data-change="name"` | `registerChange` | `change` |
| `data-input="name"` | `registerInput` | `input` |
| `data-enter="name"` | `registerEnter` | Enter in a text field |

Arguments travel in `data-*` attributes (`el.dataset.id`). One listener per event type lives on `document`,
so dynamic markup (templates, map popups) needs no wiring:

```js
setHtml(list, html`<button class="btn" data-action="delete-visit" data-id="${v.id}">🗑️</button>`);
registerActions({ 'delete-visit': (el) => deleteVisit(el.dataset.id) });
```

* Put `data-needs-network` on anything that calls the API: offline it gets `aria-disabled`, dims, and the click
  shows a toast instead of running (the guard is in `actions.js`, so it also covers templates).
* A test fails when markup uses an action nobody registered, or a registered action nobody uses.

## Escaping (XSS)

Anything from the API, other users or the LLM is untrusted. Rules (enforced by tests):

* Build markup with the `html` tag — it escapes every `${…}` unless the value is itself `html`…`/raw()`.
  `raw()` is for static strings only (e.g. the help texts).
* Put it in the DOM with `setHtml(el, safe)` / `appendHtml(el, safe)`. Raw `.innerHTML =`,
  `insertAdjacentHTML`, `document.write`, `eval` are banned outside `util.js`.
* Links/images from data go through `safeUrl()` (http/https only); colours through `safeColor()`.
* Plain text → `el.textContent`.

## Adding things

**A tab inside a section.** In `app.html` add a `role="tab"` button (`data-action="tab" data-group="r"
data-tab="mytab"`, `aria-controls="rtab-mytab"`) and a panel `<div id="rtab-mytab" class="rtab"
role="tabpanel" hidden>`. Optional loader: `TI.tabHooks['r:mytab'] = () => loadMyTab();`.

**A sidebar section.** Add a nav button (`data-action="show-section" data-section="foo"`) and
`<div id="section-foo" class="section">` in `app.html`; if it needs data on open, add an entry to
`SECTION_HOOKS` in `ui.js`.

**A module.** Create `js/foo.js` (`'use strict';`, define functions, `registerActions({…})`), add the
`<script>` to `app.html` before `main.js`, call its `initFoo()` from `boot()`, and add the file to
`SHELL_FILES` in `sw.js` (and bump `SHELL_VERSION`).

**A feature that needs a newer backend.** Mark markup `data-feature="name"`, add the endpoint to
`FEATURE_PATHS` and the flag to `features` (`api.js`). `probeFeatures()` reads `/openapi.json` at start-up;
if docs are disabled the UI stays optimistic and a 404/405/501 from the endpoint calls
`featureUnavailable(name, err)`, which hides the UI for the rest of the session.

## Service worker (`sw.js`)

| what | strategy |
|---|---|
| app shell (`SHELL_FILES`) + maplibre-gl from unpkg (SRI-pinned `CDN_FILES`) | precached at install, then stale-while-revalidate |
| map tiles (OSM, OpenTopoMap, Esri, Georeferencer) | cache-first in `ti-tiles-v1`, capped at `TILE_CACHE_MAX` (800) entries, oldest evicted, refreshed after 14 days |
| map glyphs (demotiles) | stale-while-revalidate |
| `API_CACHEABLE` GETs (map config/presets/historical, `/api/daily`) | network-first; with a cached copy, an answer slower than 8 s or a 5xx falls back to it, with nothing cached the network is awaited in full (a cold `/api/daily` can take 25 s) |
| everything else — POST/PUT/DELETE, `/api/chat`, journal/gamification/community, `/health`, OSRM | **never touched** |

* **Shipping a change to any shell file:** bump `SHELL_VERSION` in `sw.js`. Old `ti-*` caches (except the tile
  cache, which has its own name) are deleted on activate, and `skipWaiting` + `clients.claim` make the new worker
  take over at once; the page shows a "Reload" toast when that happens.
* New script/CSS/icon referenced from `app.html` ⇒ add it to `SHELL_FILES` (a test checks this).
  Changing the maplibre version/SRI in `app.html` ⇒ change `CDN_FILES` too (also tested).
* Also keep the maplibre `https://unpkg.com` host in the CSP `connect-src`; the worker re-fetches it.
* The UI stores two things offline on purpose: the last `/api/discover` result (`ti_last_places`, public place data
  only), shown when the server is unreachable, and check-ins made while offline (`ti_pending_visits`, at most 100),
  which `flushPendingVisits()` (`places.js`) replays when the connection is back — on the `online` transition and at
  start. They are recorded with the time they are synced. The worker's API cache never holds journal data, and the
  daily-place request carries coordinates rounded to ~1 km.
* Development: serve a page with `?nosw=1` to unregister the worker and delete its caches (and skip registering),
  or tick "Update on reload" in DevTools → Application → Service Workers. Workers need `https` or `localhost`.

## Themes

`css/app.css` defines the same colour tokens three times: `:root` (dark, default), `:root[data-theme="light"]`,
`:root[data-theme="sun"]` (high contrast, thick borders, 48 px hit areas — for direct sunlight). `html[data-theme]`
holds the **resolved** theme, `html[data-theme-pref]` the user's choice (`auto` follows `prefers-color-scheme`
and `prefers-contrast: more`). `theme-init.js` applies it before paint; `settings.js` switches it live and
recolours the map: base layers get raster paint tweaks and overlays get colours from `MAP_THEMES` in `map.js`
(dark inverts the OSM tiles so labels stay legible).

Use tokens (`var(--text-secondary)`, `var(--on-accent)`, …) — never raw colours — in new CSS. The test suite checks
WCAG contrast of every text/background pair per theme (4.5:1, 7:1 for sun) and that all themes define all tokens.

## Outdoor use

* Hit areas: `--hit` (44 px on touch devices, 48 px in the sun theme) is applied to buttons, tabs, chips, inputs.
* GPS: `watchPosition` options come from `gpsOptions()` (`nav.js`); fixes closer than ~5–25 m or arriving faster
  than once per second are dropped; the watch is released while `document.hidden`; **Low-power mode**
  (Settings) uses coarse GPS, a 20 s `maximumAge`, a 30 m move filter, a 1× map pixel ratio and no animations.
  On Chromium a battery-below-20 % toast offers to enable it.
* Health polling also pauses while the tab is hidden.

## CSP and the legacy pages

`DEFAULT_CSP` (`backend/app/middleware.py`): `script-src 'self' https://unpkg.com` plus **sha256 hashes** of the one
inline `<script>` each in `explorer.html`, `index.html`, `debug.html` (a test recomputes them: if you edit one of
those inline scripts the test tells you the new hash). `style-src` still allows `'unsafe-inline'` (dynamic widths
and colours in templates, inline `<style>` of the legacy pages).
(`explorer.html`'s marker popup is built with DOM APIs and `addEventListener`; an `onclick=` string built at runtime
could not be allowed by any hash, so keep it that way rather than loosening the CSP.)

## Small conventions that matter

* **Ids in URL paths go through `enc()`** (`util.js`), never raw `encodeURIComponent`: `api()` strips `! ' ( ) * ~`
  from paths, which would silently turn the id `o'brien` into `obrien`; `enc()` percent-encodes them. A test enforces it.
* **Date-only values stored as midnight UTC** (trip start/end) are shown with `utcDateStr()`, so they never slip to
  the previous day west of UTC.
* **Errors from the backend**: `isMissingEndpoint(err)` means "this server does not have the route" (405/501, or
  the router's own `{"detail":"Not Found"}`); a 404 with a message of its own ("no places nearby") is a real
  answer and must not hide a feature. `apiDetail(err)` extracts the server's message for toasts.

## Backend contract the UI codes against

`unusual_score`, `unusual_reasons`, `wikipedia` on places; `sort_by: "unusual"` for `/api/discover`;
`POST /api/place/enrich`, `GET /api/daily`, `POST /api/route/loop`, `GET /api/collections`,
`GET /api/streak?tz_offset_minutes=` (**minutes east of UTC**, i.e. `-new Date().getTimezoneOffset()`),
`POST /api/place/ask` (503 ⇒ "AI unavailable" message), and the existing `GET /api/stats/heatmap`.
Missing endpoints simply hide their UI.
