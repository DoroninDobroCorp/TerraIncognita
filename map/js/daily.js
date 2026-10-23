// "Place of the day" card on the map screen (GET /api/daily). Hidden if the backend lacks the endpoint.
// Classic script (no modules): every file in map/js shares one global scope.
'use strict';

const daily = { key: null, data: null, failedAt: 0 };

/** Local calendar date as YYYY-MM-DD. */
function localDateStr(d = new Date()) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function renderDaily() {
  const card = $('daily-card');
  const d = daily.data;
  if (!d || !d.place || !d.place.coordinates || store.get(LS.dailyDismissed) === (d.date || localDateStr())) { card.hidden = true; return; }
  $('daily-name').textContent = d.place.name || 'Unnamed place';
  const text = [d.reason, d.story].filter(s => typeof s === 'string' && s.trim()).join(' — ');
  $('daily-reason').textContent = text;
  card.hidden = features.daily === false;
}

/** Load the daily pick for the area around "me" (once per ~10 km square; quiet on failure). */
async function loadDaily() {
  if (features.daily === false || !TI.net.ok) return;
  const key = `${state.userLat.toFixed(1)},${state.userLng.toFixed(1)}`;
  if (daily.key === key || Date.now() - daily.failedAt < 5 * 60 * 1000) return;
  daily.key = key;
  try {
    const d = await api(`/api/daily?lat=${state.userLat.toFixed(4)}&lng=${state.userLng.toFixed(4)}&radius_km=10&lang=${uiLang()}`, { retries: 1, timeout: 15000 });
    if (!d || !d.place || !d.place.coordinates) throw new Error('empty daily response');
    daily.data = d;
    renderDaily();
  } catch (e) {
    daily.key = null;
    daily.failedAt = Date.now();
    featureUnavailable('daily', e);
    $('daily-card').hidden = true;
  }
}

/** Fly to the daily place and open its card (it is named on the card, so fog does not hide it). */
function openDailyPlace() {
  const p = daily.data && daily.data.place;
  if (!p) return;
  if (!state.places.find(x => x.id === p.id)) { state.places.push(p); applyFilters(); }
  state.revealedIds.add(p.id);
  showSection('map', { runHooks: false });
  map.flyTo({ center: [p.coordinates.lng, p.coordinates.lat], zoom: 15 });
  showPlaceCard(p.id);
}

registerActions({
  'daily-open': () => openDailyPlace(),
  'daily-dismiss': () => {
    if (daily.data) store.set(LS.dailyDismissed, daily.data.date || localDateStr());
    $('daily-card').hidden = true;
  },
});
