# Data sources and the terms that come with them

Terra Incognita is MIT-licensed code (see [`LICENSE`](../LICENSE)); the **data** it shows belongs to other people
and comes with its own rules. This page lists every outside service the app talks to, what for, and what the
person running an instance should know. It is a practical summary, not legal advice — read the linked policies
before you put an instance in front of other people.

## What is called, and why

| Service | Used for | Called from | Terms to know |
|---|---|---|---|
| **OpenStreetMap** via the Overpass API (`TERRA_OVERPASS_URL`, mirrors in `TERRA_OVERPASS_FALLBACK_URLS`) | The main source of places | backend | Data is © OpenStreetMap contributors under the [ODbL](https://www.openstreetmap.org/copyright): keep the attribution (the map UI shows it) and share derived databases alike. The public Overpass servers are shared and subject to fair use; for more than personal use run your own instance. |
| **Wikidata** SPARQL (`TERRA_WIKIDATA_SPARQL_URL`) | Historic and notable objects with coordinates | backend | Data is CC0. The service requires a descriptive `User-Agent` with a way to contact you: set `TERRA_WIKIMEDIA_USER_AGENT`. |
| **Wikipedia** and **Wikimedia Commons** | Summaries and photos in `POST /api/place/enrich` | backend | Article text is CC BY-SA 4.0; every Commons file has its own licence. The API returns author, licence and source URL with each photo, and the UI shows them — keep that when you reuse photos. Same `User-Agent` rule as above. |
| **Atlas Obscura** (`TERRA_ATLAS_OBSCURA_BASE_URL`) | A "listed on Atlas Obscura" signal and a few extra places | backend | There is no public API; the source reads public web pages. The site's terms may not allow automated access, and it answers most server IPs with HTTP 403 anyway. **It is optional: set `TERRA_ATLAS_OBSCURA_ENABLED=false` to never contact it.** Only names, coordinates and short descriptions are kept, in the local cache. |
| **Nominatim** (nominatim.openstreetmap.org) | Reverse geocoding for Deep Research prompts (backend); address search in the legacy `explorer.html` page (the visitor's browser) | backend, browser | [Usage policy](https://operations.osmfoundation.org/policies/nominatim/): at most one request per second, an identifying `User-Agent`, cache results, no bulk use. The backend caches lookups for good and spaces them 1.1 s apart; the browser's search is per visitor and on demand. |
| **Map tiles**: OpenStreetMap standard, OpenTopoMap, Esri World Imagery, Old World Maps (Georeferencer, served via oldmapsonline.org) | Base and historical map layers | browser | Each provider has its own tile usage policy and attribution, which the map shows. The OSM and OpenTopoMap tile servers are for light use; put a tile provider or your own server behind heavy traffic. Check a layer's terms before using it commercially. |
| **OSRM demo server** (router.project-osrm.org) | Walking directions to a place | browser | A public demo: no service guarantee and not for heavy use. Its foot profile is not honoured, so the app derives walking time itself. Self-host OSRM for real use. |
| **MapLibre GL JS** from unpkg.com, glyphs from demotiles.maplibre.org | The map library and fonts | browser | Pinned versions; host them yourself if you must not depend on a CDN (then adjust the CSP, see `TERRA_CSP`). |
| **Anthropic**, **OpenAI** (optional) | Chat, descriptions, stories, ask-a-place, recommendations | backend | You need your own key and pay per token (cap it with `TERRA_LLM_DAILY_TOKEN_CAP`). Place names, tags and the user's question are sent to the provider; follow the provider's terms and your own privacy obligations. |
| **Parallel AI**, **Google Gemini** (optional) | Deep Research enrichment | backend | Your own keys, billed by the provider and outside the token cap. |

## Keeping it polite

- Everything the backend fetches is cached on disk (`TERRA_CACHE_DIR`), and identical requests that arrive
  together share one upstream call, so a busy instance does not multiply the load on the public services.
- A failing source never fails a request and failures are not cached as "nothing here".
- Set a real contact in `TERRA_WIKIMEDIA_USER_AGENT` — the same string is sent to Overpass, Nominatim and the
  Wikimedia APIs.
- The journal, XP and community data you collect are personal data of your users. They stay in `data/` on your
  server; `deploy/backup.sh` copies them, so protect the backups as well.
