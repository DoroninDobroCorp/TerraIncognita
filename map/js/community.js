// Community tab: submitted places, reviews, published routes, follows, karma.
// Everything rendered here is written by OTHER users — it all goes through html`` (escaped).
// Classic script (no modules): every file in map/js shares one global scope.
'use strict';

const lengthOf = (v) => Array.isArray(v) ? v.length : (parseInt(v) || 0);  // confirmations/votes are id lists

async function loadCommunityPlaces() {
  const list=$('community-places-list');
  const filter=$('comm-places-filter')?.value||'';
  showSkeleton(list);
  try {
    const q = filter?`?status=${encodeURIComponent(filter)}&limit=30`:'?limit=30';
    const data = await api('/api/community/places'+q);
    if(!data.places||!data.places.length){setHtml(list, emptyStateHtml('👥', 'No community places yet. Submit the first one!'));return;}
    setHtml(list, html`${data.places.map(p => html`
      <div class="card mb-10">
        <div class="row-between align-start">
          <div class="card-title">${p.name}</div>
          <span class="tag ${p.moderation_status==='confirmed'||p.moderation_status==='approved'?'green':p.moderation_status==='rejected'?'red':'yellow'}">${p.moderation_status}</span>
        </div>
        <div class="text-small mt-4">${(p.description||'').substring(0,150)}</div>
        <div class="card-meta mt-4">${(p.categories||[]).map(c => html`<span class="tag blue">${c}</span>`)}</div>
        <div class="text-tiny text-secondary mt-8">by ${p.submitted_by||'anonymous'} · 👍 ${lengthOf(p.confirmations)} 👎 ${lengthOf(p.rejections)}</div>
        <div class="card-actions mt-8">
          <button class="btn btn-sm btn-secondary" data-action="place-reviews" data-id="${p.id}">💬 Reviews</button>
          <button class="btn btn-sm btn-secondary" data-action="show-on-map" data-lat="${num(p.coordinates?.lat)}" data-lng="${num(p.coordinates?.lng)}">🗺️ Map</button>
        </div>
      </div>`)}`);
  } catch { setHtml(list, errorStateHtml('👥', 'Could not load community places', 'load-community-places')); }
}

function showSubmitPlace() { $('submit-place-form').hidden = false; $('cp-name').focus(); }
function hideSubmitPlace() { $('submit-place-form').hidden = true; }

/** "Abandoned, ruins" -> ["abandoned","ruins"] keeping only known category ids. */
function parseCategories(text) {
  const wanted = text.split(',').map(s => s.trim().toLowerCase().replace(/[\s-]+/g, '_')).filter(Boolean);
  const known = wanted.filter(c => CATEGORIES[c]);
  return { known, dropped: wanted.filter(c => !CATEGORIES[c]) };
}

async function submitCommunityPlace() {
  const name=$('cp-name').value.trim(), desc=$('cp-desc').value.trim();
  const lat=parseFloat($('cp-lat').value), lng=parseFloat($('cp-lng').value);
  if(!name){toast('Please enter a place name','warning');return;}
  if(name.length>200){toast('Name is too long (max 200 characters)','warning');return;}
  if(desc.length>2000){toast('Description is too long (max 2000 characters)','warning');return;}
  if(isNaN(lat)||isNaN(lng)||lat<-90||lat>90||lng<-180||lng>180){toast('Enter valid coordinates (lat: -90 to 90, lng: -180 to 180)','warning');return;}
  const { known: cats, dropped } = parseCategories($('cp-cats').value);
  if (dropped.length) toast(`Unknown categories ignored: ${dropped.join(', ')}`, 'warning');
  const tags=$('cp-tags').value.split(',').map(s=>s.trim()).filter(Boolean);
  const btn=$('cp-submit'); btn.disabled=true; btn.textContent='Submitting...';
  analytics.track('community_place_submit');
  try {
    await api('/api/community/places',{method:'POST',body:{name,description:desc,categories:cats,lat,lng,tags,author_id:getActiveUserId()}});
    toast('Place submitted for moderation!','success'); hideSubmitPlace(); loadCommunityPlaces();
  } catch(e) { toast('Submission failed: '+(e.message||'check connection'),'error'); } finally { btn.disabled=false; btn.textContent='Submit'; }
}

async function loadPlaceReviews(placeId) {
  try {
    const data = await api(`/api/community/places/${encodeURIComponent(placeId)}/reviews?limit=20`);
    const reviews = data.reviews||[];
    setHtml($('community-places-list'), html`
      <button class="btn btn-secondary btn-sm mb-14" data-action="load-community-places">← Back</button>
      <h3 class="subhead">💬 Reviews (${reviews.length})</h3>
      ${reviews.length ? reviews.map(r => html`
        <div class="review-card">
          <div class="review-header"><span class="review-author">${r.author_id}</span><span class="review-date">${r.visit_date||''}</span></div>
          <div class="review-text">${r.text||''}</div>
          <div class="review-votes"><button class="vote-btn" data-action="vote-review" data-id="${r.id}" data-up="1">👍 ${lengthOf(r.upvotes)}</button><button class="vote-btn" data-action="vote-review" data-id="${r.id}" data-up="0">👎 ${lengthOf(r.downvotes)}</button></div>
        </div>`) : html`<div class="text-secondary">No reviews yet</div>`}
      <div class="mt-14">
        <div class="form-group"><label class="form-label" for="review-text">Write a Review</label><textarea class="input" id="review-text" placeholder="Share your experience" maxlength="5000"></textarea></div>
        <button class="btn btn-primary btn-sm w-100" data-action="write-review" data-id="${placeId}">Submit Review</button>
      </div>`);
  } catch { toast('Could not load reviews','error'); }
}
async function writeReview(placeId) {
  const t = $('review-text').value;
  if(!t.trim()){toast('Please write a review first','warning');return;}
  try{await api(`/api/community/places/${encodeURIComponent(placeId)}/reviews`,{method:'POST',body:{author_id:getActiveUserId(),text:t,review_type:'review'}});toast('Review posted!','success');loadPlaceReviews(placeId);}
  catch{toast('Failed to post review','error');}
}
async function voteReview(reviewId, up) {
  try{await api(`/api/community/reviews/${encodeURIComponent(reviewId)}/vote`,{method:'POST',body:{user_id:getActiveUserId(),upvote:up}});toast(up?'👍':'👎','info');}catch{toast('Could not register your vote (you cannot vote on your own review)','warning');}
}

// ── Community routes ──
async function loadCommunityRoutes() {
  const list=$('community-routes-list');
  showSkeleton(list);
  try {
    const data=await api('/api/community/routes?limit=20');
    const routes = data.routes || [];
    setHtml(list, routes.length ? html`${routes.map(r => html`
      <div class="card mb-10">
        <div class="card-title">${r.title}</div>
        <div class="text-small mt-4">${r.description||''}</div>
        <div class="card-meta mt-4">${num(r.distance_km).toFixed(1)} km · ${num(r.duration_hours).toFixed(1)} h · by ${r.author_id||'?'}</div>
        ${r.tags&&r.tags.length?html`<div class="mt-4">${r.tags.map(t => html`<span class="tag gray">${t}</span>`)}</div>`:''}
      </div>`)}` : emptyStateHtml('🛤️', 'No community routes yet'));
  } catch { setHtml(list, errorStateHtml('🛤️', 'Could not load routes', 'load-community-routes')); }
}

// ── Following / followers / karma ──
async function loadFollowing() {
  const uid=getActiveUserId();
  const list=$('people-list');
  try {
    const d=await api(`/api/community/following/${encodeURIComponent(uid)}`);
    const follows = d.follows || [];
    setHtml(list, follows.length ? html`${follows.map(f => html`<div class="list-item"><div class="item-icon">👤</div><div class="item-body"><div class="item-title">${f.following_id}</div></div><button class="btn btn-danger btn-sm" data-action="unfollow" data-a="${f.follower_id}" data-b="${f.following_id}">Unfollow</button></div>`)}` : html`<div class="empty-state">Not following anyone yet</div>`);
  } catch { setHtml(list, errorStateHtml('👤', 'Could not load following', 'load-following')); }
}
async function loadFollowers() {
  const uid=getActiveUserId();
  const list=$('people-list');
  try {
    const d=await api(`/api/community/followers/${encodeURIComponent(uid)}`);
    const follows = d.follows || [];
    setHtml(list, follows.length ? html`${follows.map(f => html`<div class="list-item"><div class="item-icon">👤</div><div class="item-body"><div class="item-title">${f.follower_id}</div></div></div>`)}` : html`<div class="empty-state">No followers yet</div>`);
  } catch { setHtml(list, errorStateHtml('👤', 'Could not load followers', 'load-followers')); }
}
async function unfollow(a, b) {
  try{await api(`/api/community/follow/${encodeURIComponent(a)}/${encodeURIComponent(b)}`,{method:'DELETE'});toast('Unfollowed','info');loadFollowing();}catch{toast('Unfollow failed','error');}
}
async function loadKarma() {
  try {
    const d = await api(`/api/community/karma/${encodeURIComponent(getActiveUserId())}`);
    const k = (d.karma && typeof d.karma === 'object') ? d.karma.karma : d.karma;  // {"karma": {"karma": 12, ...}}
    $('karma-display').textContent = (k||0)+' karma';
  } catch { $('karma-display').textContent='—'; }
}

TI.tabHooks['c:routes'] = () => loadCommunityRoutes();
TI.tabHooks['c:people'] = () => loadKarma();

registerActions({
  'load-community-places': () => loadCommunityPlaces(),
  'show-submit-place': () => showSubmitPlace(),
  'hide-submit-place': () => hideSubmitPlace(),
  'submit-place': () => submitCommunityPlace(),
  'place-reviews': (el) => loadPlaceReviews(el.dataset.id),
  'show-on-map': (el) => showOnMap(parseFloat(el.dataset.lat), parseFloat(el.dataset.lng)),
  'write-review': (el) => writeReview(el.dataset.id),
  'vote-review': (el) => voteReview(el.dataset.id, el.dataset.up === '1'),
  'load-community-routes': () => loadCommunityRoutes(),
  'load-following': () => loadFollowing(),
  'load-followers': () => loadFollowers(),
  unfollow: (el) => unfollow(el.dataset.a, el.dataset.b),
});
registerChange({ 'load-community-places': () => loadCommunityPlaces() });
