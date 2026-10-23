// Place card extras that depend on newer backend features (each hides itself when the server lacks it):
//   * "Why it's interesting" chips + unusual-score badge     (Place.unusual_reasons / unusual_score)
//   * Wikipedia extract + Commons photo strip                (Place.wikipedia, POST /api/place/enrich — lazy, cached)
//   * "Ask about this place" mini chat                       (POST /api/place/ask)
// Everything shown here comes from external sources: text goes through html``, URLs through safeUrl().
// Classic script (no modules): every file in map/js shares one global scope.
'use strict';

// ── Unusual score + reasons ──
function renderUnusual(place, inFog) {
  const badge = $('card-unusual'), box = $('card-reasons');
  const score = Number(place.unusual_score);
  const hasScore = !inFog && place.unusual_score != null && Number.isFinite(score);
  badge.hidden = !hasScore;
  if (hasScore) {
    const pct = Math.round(clamp(score, 0, 1) * 100);
    badge.textContent = `✨ ${pct}`;
    badge.title = `Unusualness score ${pct} / 100`;
    badge.setAttribute('aria-label', `Unusualness score ${pct} out of 100`);
  }
  const reasons = (!inFog && Array.isArray(place.unusual_reasons) ? place.unusual_reasons : [])
    .filter(r => typeof r === 'string' && r.trim()).slice(0, 8);
  box.hidden = reasons.length === 0;
  if (reasons.length) {
    setHtml(box, html`<div class="reasons-title">Why it's interesting</div><ul class="reason-chips">${reasons.map(r => html`<li class="reason-chip">${r}</li>`)}</ul>`);
  }
}

// ── Enrichment (Wikipedia + Commons) ──
const enrichCache = new Map();   // place id -> Promise<result|null>   (memory only)
const ENRICH_CACHE_MAX = 100;

/** Q-number of a place if we know one (Wikidata-sourced places, or OSM `wikidata` tag). */
function wikidataIdOf(place) {
  const m = place.metadata || {};
  const cand = m.wikidata_id || m.osm_tags?.wikidata || (String(place.id || '').startsWith('wiki_') ? String(place.id).slice(5) : null);
  return /^Q\d+$/.test(String(cand || '')) ? String(cand) : null;
}

function fetchEnrichment(place) {
  let p = enrichCache.get(place.id);
  if (p) return p;
  p = api('/api/place/enrich', { method: 'POST', retries: 0, timeout: 15000, body: {
    name: place.name || null, lat: place.coordinates.lat, lng: place.coordinates.lng,
    wikidata_id: wikidataIdOf(place), lang: uiLang(),
  } }).catch(err => {
    enrichCache.delete(place.id);          // try again next time the card opens
    featureUnavailable('enrich', err);
    return null;
  });
  enrichCache.set(place.id, p);
  if (enrichCache.size > ENRICH_CACHE_MAX) enrichCache.delete(enrichCache.keys().next().value);
  return p;
}

function renderWiki(wiki) {
  const el = $('card-wiki');
  if (!wiki || typeof wiki.extract !== 'string' || !wiki.extract.trim()) { el.hidden = true; return; }
  const url = safeUrl(wiki.url);
  setHtml(el, html`
    <div class="wiki-title">📖 ${wiki.title || 'Wikipedia'}</div>
    <p class="wiki-extract">${wiki.extract}</p>
    <div class="attribution">${url ? html`<a href="${url}" target="_blank" rel="noopener noreferrer" data-native>Wikipedia${wiki.lang ? ' (' + wiki.lang + ')' : ''}</a>` : 'Wikipedia'} · <a href="https://creativecommons.org/licenses/by-sa/4.0/" target="_blank" rel="noopener noreferrer" data-native>CC BY-SA 4.0</a></div>`);
  el.hidden = false;
}

function renderPhotos(place, photos) {
  const el = $('card-photos');
  const items = (Array.isArray(photos) ? photos : []).map(ph => ({
    thumb: safeUrl(ph.thumb_url || ph.url), full: safeUrl(ph.source_url || ph.url),
    title: ph.title, author: ph.author, license: ph.license,
  })).filter(ph => ph.thumb).slice(0, 8);
  if (!items.length) { el.hidden = true; return; }
  setHtml(el, html`${items.map(ph => html`
    <figure class="photo">
      ${ph.full ? html`<a href="${ph.full}" target="_blank" rel="noopener noreferrer" data-native aria-label="Open photo page${ph.title ? ': ' + ph.title : ''}"><img src="${ph.thumb}" alt="${ph.title || place.name || 'Photo of the place'}" loading="lazy" referrerpolicy="no-referrer"></a>` : html`<img src="${ph.thumb}" alt="${ph.title || place.name || 'Photo of the place'}" loading="lazy" referrerpolicy="no-referrer">`}
      <figcaption>${ph.full ? html`<a href="${ph.full}" target="_blank" rel="noopener noreferrer" data-native>${[ph.author, ph.license].filter(Boolean).join(' · ') || 'Wikimedia Commons'}</a>` : ([ph.author, ph.license].filter(Boolean).join(' · ') || 'Wikimedia Commons')}</figcaption>
    </figure>`)}`);
  el.hidden = false;
}

async function loadEnrichment(place) {
  if (features.enrich === false || !TI.net.ok || String(place.id).startsWith('demo-')) return;
  const data = await fetchEnrichment(place);
  if (!data || !state.selectedPlace || state.selectedPlace.id !== place.id) return; // closed or switched meanwhile
  if (data.wikipedia) renderWiki(data.wikipedia);
  renderPhotos(place, data.photos);
}

// ── Ask this place ──
const askLogs = new Map();   // place id -> [{q, a, err}]
let askBusy = false;

function renderAskLog(placeId) {
  const log = askLogs.get(placeId) || [];
  setHtml($('ask-log'), html`${log.map(m => html`<div class="ask-q">${m.q}</div><div class="ask-a${m.err ? ' err' : ''}">${m.a}</div>`)}`);
  $('ask-log').scrollTop = $('ask-log').scrollHeight;
}

function askErrorMessage(e) {
  if (e && e.status === 503) return '🤖 The AI guide is unavailable right now (the daily AI budget is used up or no AI key is configured). Please try again later.';
  if (e && e.status === 429) return 'You are asking too fast — wait a minute and try again.';
  if (e && [404, 405, 501].includes(e.status)) return 'This server cannot answer questions about places yet.';
  return 'Could not get an answer. Check your connection and try again.';
}

async function askPlace() {
  const p = state.selectedPlace;
  const input = $('ask-input');
  const q = input.value.trim();
  if (!p || !q || askBusy) return;
  askBusy = true;
  input.value = '';
  const log = askLogs.get(p.id) || [];
  const entry = { q, a: '…', err: false };
  log.push(entry);
  askLogs.set(p.id, log.slice(-10));
  if (askLogs.size > 50) askLogs.delete(askLogs.keys().next().value);
  renderAskLog(p.id);
  try {
    const data = await api('/api/place/ask', { method: 'POST', retries: 0, timeout: 30000, body: {
      place: { name: p.name || 'Unnamed place', lat: p.coordinates.lat, lng: p.coordinates.lng, categories: p.categories || [], description: p.description || null },
      question: q, language: uiLang(),
    } });
    entry.a = String(data.answer || '').trim() || 'No answer.';
    speakReply(entry.a);
  } catch (e) {
    featureUnavailable('ask', e);
    entry.a = askErrorMessage(e);
    entry.err = true;
  }
  askBusy = false;
  if (state.selectedPlace && state.selectedPlace.id === p.id) renderAskLog(p.id);
}

/** Called by showPlaceCard() after the basic fields are filled in. */
function renderPlaceExtras(place, inFog) {
  renderUnusual(place, inFog);
  // Wikipedia text that came with the place is shown at once; enrichment adds photos / fresher text.
  if (!inFog && place.wikipedia && place.wikipedia.extract) renderWiki(place.wikipedia); else $('card-wiki').hidden = true;
  $('card-photos').hidden = true;
  const ask = $('card-ask');
  ask.hidden = inFog || features.ask === false;
  if (!inFog) { renderAskLog(place.id); loadEnrichment(place); }
}

registerActions({ 'ask-send': () => askPlace() });
registerEnter({ 'ask-send': () => askPlace() });
