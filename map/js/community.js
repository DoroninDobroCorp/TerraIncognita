// Community: places, reviews, routes, people, karma.
// Classic script (no modules): shares the global scope with the other map/js files.

// ═══════════════════════════════════════════════════════
//  COMMUNITY
// ═══════════════════════════════════════════════════════
async function loadCommunityPlaces() {
  const list=document.getElementById('community-places-list');
  const filter=document.getElementById('comm-places-filter')?.value||'';
  list.innerHTML='<div class="skeleton skeleton-block"></div><div class="skeleton skeleton-block"></div><div class="skeleton skeleton-block"></div>';
  try {
    const q = filter?`?status=${filter}&limit=30`:'?limit=30';
    const data = await api('/api/community/places'+q);
    if(!data.places||!data.places.length){list.innerHTML='<div class="empty-state"><div class="empty-icon">👥</div><div class="empty-text">No community places yet. Submit the first one!</div></div>';return;}
    list.innerHTML = data.places.map(p=>`
      <div class="card" style="margin-bottom:10px">
        <div style="display:flex;justify-content:space-between;align-items:start">
          <div class="card-title">${escHtml(p.name)}</div>
          <span class="tag ${p.moderation_status==='approved'?'green':p.moderation_status==='rejected'?'red':'yellow'}">${escHtml(p.moderation_status)}</span>
        </div>
        <div style="font-size:12px;margin-top:4px">${escHtml((p.description||'').substring(0,150))}</div>
        <div class="card-meta" style="margin-top:4px">${(p.categories||[]).map(c=>`<span class="tag blue">${escHtml(c)}</span>`).join('')}</div>
        <div style="margin-top:8px;font-size:11px;color:var(--text-secondary)">by ${escHtml(p.submitted_by||'anonymous')} · 👍 ${parseInt(p.confirmations)||0} 👎 ${parseInt(p.rejections)||0}</div>
        <div class="card-actions" style="margin-top:8px">
          <button class="btn btn-sm btn-secondary" onclick="loadPlaceReviews('${escHtml(p.id)}')">💬 Reviews</button>
          <button class="btn btn-sm btn-secondary" onclick="showOnMap(${parseFloat(p.coordinates?.lat)||0},${parseFloat(p.coordinates?.lng)||0})">🗺️ Map</button>
        </div>
      </div>`).join('');
  } catch { list.innerHTML='<div class="empty-state">Could not load community places<br><button class="btn btn-sm btn-secondary" style="margin-top:12px" onclick="loadCommunityPlaces()">🔄 Retry</button></div>'; }
}

function showSubmitPlace(){document.getElementById('submit-place-form').style.display='block';}
function hideSubmitPlace(){document.getElementById('submit-place-form').style.display='none';}
async function submitCommunityPlace() {
  const name=document.getElementById('cp-name').value.trim(), desc=document.getElementById('cp-desc').value.trim();
  const lat=parseFloat(document.getElementById('cp-lat').value), lng=parseFloat(document.getElementById('cp-lng').value);
  if(!name){toast('Please enter a place name','warning');return;}
  if(name.length>200){toast('Name is too long (max 200 characters)','warning');return;}
  if(desc.length>2000){toast('Description is too long (max 2000 characters)','warning');return;}
  if(isNaN(lat)||isNaN(lng)||lat<-90||lat>90||lng<-180||lng>180){toast('Enter valid coordinates (lat: -90 to 90, lng: -180 to 180)','warning');return;}
  const cats=document.getElementById('cp-cats').value.split(',').map(s=>s.trim()).filter(Boolean);
  const tags=document.getElementById('cp-tags').value.split(',').map(s=>s.trim()).filter(Boolean);
  const btn=document.querySelector('#ctab-submit .btn-primary'); btn.disabled=true; btn.textContent='Submitting...';
  analytics.track('community_place_submit');
  try {
    await api('/api/community/places',{method:'POST',body:{name,description:desc,categories:cats,lat,lng,tags,author_id:getActiveUserId()}});
    toast('Place submitted for moderation!','success'); hideSubmitPlace(); loadCommunityPlaces();
  } catch(e) { toast('Submission failed: '+(e.message||'check connection'),'error'); } finally { btn.disabled=false; btn.textContent='Submit Place'; }
}

async function loadPlaceReviews(placeId) {
  try {
    const data = await api(`/api/community/places/${placeId}/reviews?limit=20`);
    const reviews = data.reviews||[];
    document.getElementById('community-places-list').innerHTML = `
      <button class="btn btn-secondary btn-sm" onclick="loadCommunityPlaces()" style="margin-bottom:14px">← Back</button>
      <h3 style="font-size:14px;margin-bottom:10px">💬 Reviews (${reviews.length})</h3>
      ${reviews.map(r=>`
        <div class="review-card">
          <div class="review-header"><span class="review-author">${escHtml(r.author_id)}</span><span class="review-date">${r.visit_date||''}</span></div>
          <div class="review-text">${escHtml(r.text||'')}</div>
          <div class="review-votes"><button class="vote-btn" onclick="voteReview('${r.id}',true)">👍 ${r.upvotes||0}</button><button class="vote-btn" onclick="voteReview('${r.id}',false)">👎 ${r.downvotes||0}</button></div>
        </div>`).join('')||'<div style="color:var(--text-secondary)">No reviews yet</div>'}
      <div style="margin-top:14px">
        <div class="form-group"><label class="form-label">Write a Review</label><textarea class="input" id="review-text" placeholder="Share your experience"></textarea></div>
        <button class="btn btn-primary btn-sm" onclick="writeReview('${placeId}')" style="width:100%">Submit Review</button>
      </div>`;
  } catch { toast('Could not load reviews','error'); }
}
async function writeReview(placeId){const t=document.getElementById('review-text').value;if(!t.trim()){toast('Please write a review first','warning');return;}try{await api(`/api/community/places/${placeId}/reviews`,{method:'POST',body:{author_id:getActiveUserId(),text:t,review_type:'visit'}});toast('Review posted!','success');loadPlaceReviews(placeId);}catch{toast('Failed to post review','error');}}
async function voteReview(reviewId,up){try{await api(`/api/community/reviews/${reviewId}/vote`,{method:'POST',body:{user_id:getActiveUserId(),upvote:up}});toast(up?'👍':'👎','info');}catch{}}

// Community routes
document.querySelectorAll('[data-ctab="routes"]').forEach(t=>t.addEventListener('click', async ()=>{
  const list=document.getElementById('community-routes-list');
  list.innerHTML='<div class="skeleton skeleton-block"></div><div class="skeleton skeleton-block"></div><div class="skeleton skeleton-block"></div>';
  try {
    const data=await api('/api/community/routes?limit=20');
    list.innerHTML=(data.routes||[]).map(r=>`
      <div class="card" style="margin-bottom:10px">
        <div class="card-title">${escHtml(r.title)}</div>
        <div style="font-size:12px;margin-top:4px">${escHtml(r.description||'')}</div>
        <div class="card-meta" style="margin-top:4px">${(r.distance_km||0).toFixed(1)} km · ${(r.duration_hours||0).toFixed(1)} h · by ${r.author_id||'?'}</div>
        ${r.tags?`<div style="margin-top:4px">${r.tags.map(t=>`<span class="tag gray">${t}</span>`).join('')}</div>`:''}
      </div>`).join('')||'<div class="empty-state"><div class="empty-icon">🛤️</div><div class="empty-text">No community routes yet</div></div>';
  } catch { list.innerHTML='<div class="empty-state">Could not load routes<br><button class="btn btn-sm btn-secondary" style="margin-top:12px" onclick="loadCommunityRoutes()">🔄 Retry</button></div>'; }
}));

// Following/Followers
async function loadFollowing() {
  const uid=getActiveUserId();
  const list=document.getElementById('people-list');
  try { const d=await api(`/api/community/following/${uid}`); list.innerHTML=(d.follows||[]).map(f=>`<div class="list-item"><div class="item-icon">👤</div><div class="item-body"><div class="item-title">${escHtml(f.following_id)}</div></div><button class="btn btn-danger btn-sm" onclick="unfollow('${escHtml(f.follower_id)}','${escHtml(f.following_id)}')">Unfollow</button></div>`).join('')||'<div class="empty-state">Not following anyone yet</div>'; } catch { list.innerHTML='<div class="empty-state">Could not load following<br><button class="btn btn-sm btn-secondary" style="margin-top:8px" onclick="loadFollowing()">🔄 Retry</button></div>'; }
}
async function loadFollowers() {
  const uid=getActiveUserId();
  const list=document.getElementById('people-list');
  try { const d=await api(`/api/community/followers/${uid}`); list.innerHTML=(d.follows||[]).map(f=>`<div class="list-item"><div class="item-icon">👤</div><div class="item-body"><div class="item-title">${escHtml(f.follower_id)}</div></div></div>`).join('')||'<div class="empty-state">No followers yet</div>'; } catch { list.innerHTML='<div class="empty-state">Could not load followers<br><button class="btn btn-sm btn-secondary" style="margin-top:8px" onclick="loadFollowers()">🔄 Retry</button></div>'; }
}
async function unfollow(a,b){try{await api(`/api/community/follow/${a}/${b}`,{method:'DELETE'});toast('Unfollowed','info');loadFollowing();}catch{toast('Unfollow failed','error');}}

// Karma
document.querySelectorAll('[data-ctab="people"]').forEach(t=>t.addEventListener('click', async ()=>{
  try { const d=await api(`/api/community/karma/${getActiveUserId()}`); document.getElementById('karma-display').textContent=(d.karma||0)+' karma'; } catch { document.getElementById('karma-display').textContent='—'; }
}));
