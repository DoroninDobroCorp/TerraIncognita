// Place discovery, filters, place card.
// Classic script (no modules): shares the global scope with the other map/js files.

// Refresh
document.getElementById('btn-refresh').addEventListener('click', () => {
  if (mapAvailable) {
    const c = map.getCenter();
    if (c && typeof c.lat === 'number') { state.userLat = c.lat; state.userLng = c.lng; }
  }
  fetchPlaces();
});

// Search
document.getElementById('search-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); const q = e.target.value.trim(); if (q) fetchChat(q); }
});

async function fetchChat(q) {
  showMapLoading(true);
  analytics.track('search', { query: q.substring(0, 50) });
  try {
    const data = await api('/api/chat', {method:'POST', body:{message:q, lat:state.userLat, lng:state.userLng, radius_km:state.radius}});
    if (data.places && data.places.length > 0) { state.places = data.places; applyFilters(); toast(`Found ${data.places.length} places`,'success'); }
  } catch(e) { toast('Search failed','error'); }
  showMapLoading(false);
}

// Fetch places (guarded against concurrent calls)
let fetchBusy = false;
async function fetchPlaces() {
  if (fetchBusy) return;
  fetchBusy = true;
  showMapLoading(true);
  try {
    const data = await api('/api/discover', {method:'POST', body:{
      lat:state.userLat, lng:state.userLng, radius_km:state.radius,
      categories:[...state.activeCategories], exclude_visited:state.hideVisited?[...state.visitedPlaces]:[],
      limit:200, sort_by:'confidence'
    }});
    state.places = data.places || []; applyFilters();
  } catch(e) { loadDemoData(); toast('API unavailable — showing demo data','warning'); }
  showMapLoading(false);
  fetchBusy = false;
}

function loadDemoData() {
  state.places = [
    {id:'demo-1',source:'osm',name:'Заброшенный бункер',description:'Старый военный бункер времён WWII.',categories:['abandoned','military'],coordinates:{lat:42.4450,lng:18.7650},confidence:0.85,distance_m:350},
    {id:'demo-2',source:'osm',name:'Подземный тоннель',description:'Скрытый каменный тоннель к морю.',categories:['underground'],coordinates:{lat:42.4380,lng:18.7730},confidence:0.7,distance_m:520},
    {id:'demo-3',source:'atlas',name:'Заброшенный хлебозавод',description:'Промышленное здание с граффити.',categories:['abandoned','industrial'],coordinates:{lat:42.4470,lng:18.7800},confidence:0.6,distance_m:820},
    {id:'demo-4',source:'wiki',name:'Крепость Святого Иоанна',description:'Средневековая крепость с видом на залив.',categories:['landmark','ruins'],coordinates:{lat:42.4290,lng:18.7710},confidence:0.95,distance_m:1200},
    {id:'demo-5',source:'osm',name:'Скрытая бухта',description:'Пляж за скалами.',categories:['nature_hidden','water'],coordinates:{lat:42.4340,lng:18.7550},confidence:0.75,distance_m:1500},
    {id:'demo-6',source:'osm',name:'Смотровая площадка',description:'Панорамный вид на бухту.',categories:['viewpoint'],coordinates:{lat:42.4500,lng:18.7580},confidence:0.9,distance_m:2300},
  ];
  applyFilters();
}

function applyFilters() {
  state.filteredPlaces = state.places.filter(p => {
    const cats = p.categories || [];
    if (cats.length && !cats.some(c => state.activeCategories.has(c))) return false;
    if ((p.confidence||0) < state.minConfidence/100) return false;
    if (state.hideVisited && state.visitedPlaces.has(p.id)) return false;
    return true;
  });
  document.getElementById('place-count').textContent = `${state.filteredPlaces.length} places`;
  renderPlaces();
  if (state.fogEnabled) updateFogStats();
}

// Place card
function showPlaceCard(placeId) {
  const place = state.places.find(p=>p.id===placeId); if(!place)return;
  state.selectedPlace = place;
  const cats = place.categories||[];
  document.getElementById('card-categories').innerHTML = cats.map(c=>{const d=CATEGORIES[c]||CATEGORIES.landmark;return`<span class="card-category" style="background:${d.color}">${d.icon} ${d.label}</span>`;}).join('');
  const inFog = state.fogEnabled && !state.visitedPlaces.has(place.id);
  document.getElementById('card-name').textContent = inFog?'❓ Mystery Place':(place.name||'Unknown');
  const src={osm:'OpenStreetMap',atlas:'Atlas Obscura',wiki:'Wikidata',community:'Community'};
  document.getElementById('card-meta').textContent = `📡 ${src[place.source]||place.source}${place.distance_m?' · �📏 '+(place.distance_m/1000).toFixed(1)+' km':''}`;
  document.getElementById('card-desc').textContent = inFog?'Visit this place to reveal its secrets...':place.description||'No description';
  const conf=place.confidence||0.5, fill=document.getElementById('card-conf-fill');
  fill.style.width=(conf*100)+'%'; fill.style.background=conf>0.7?'#2ecc71':conf>0.4?'#f39c12':'#e74c3c';
  document.getElementById('card-save').textContent = state.savedPlaces.has(place.id)?'★ Saved':'⭐ Save';
  document.getElementById('place-card').classList.add('visible');
}
document.getElementById('card-close').addEventListener('click',()=>{document.getElementById('place-card').classList.remove('visible');state.selectedPlace=null;});
document.getElementById('card-navigate').addEventListener('click',()=>{if(!state.selectedPlace)return;const p=state.selectedPlace;startNavigation(p);document.getElementById('place-card').classList.remove('visible');});
document.getElementById('nav-stop').addEventListener('click',()=>{stopNavigation();});
document.getElementById('card-save').addEventListener('click',()=>{if(!state.selectedPlace)return;const id=state.selectedPlace.id;if(state.savedPlaces.has(id))state.savedPlaces.delete(id);else state.savedPlaces.add(id);localStorage.setItem('ti_saved',JSON.stringify([...state.savedPlaces]));document.getElementById('card-save').textContent=state.savedPlaces.has(id)?'★ Saved':'⭐ Save';});

// Story button
document.getElementById('card-story').addEventListener('click', async ()=>{
  if(!state.selectedPlace) return;
  const p = state.selectedPlace;
  toast('Generating story...','info');
  try {
    const data = await api('/api/story',{method:'POST',body:{place_id:p.id,place_name:p.name,lat:p.coordinates.lat,lng:p.coordinates.lng,categories:p.categories,language:'ru'}});
    document.getElementById('card-desc').innerHTML = `<strong>📖 AI Story:</strong><br>${escHtml(data.story)}`;
    toast('Story generated!','success');
  } catch { toast('Story generation failed','error'); }
});

// Check-in button
document.getElementById('card-checkin').addEventListener('click', async ()=>{
  if(!state.selectedPlace) return;
  const p = state.selectedPlace;
  analytics.track('checkin', { place_id: p.id, category: p.categories?.[0] });
  try {
    await api('/api/visits',{method:'POST',body:{place_id:p.id,lat:p.coordinates.lat,lng:p.coordinates.lng,place_name:p.name,status:'visited'}});
    state.visitedPlaces.add(p.id);
    localStorage.setItem('ti_visited',JSON.stringify([...state.visitedPlaces]));
    renderFog(); renderPlaces(); toast('✅ Checked in at '+p.name,'success');
  } catch { toast('Check-in failed — saved locally','warning'); state.visitedPlaces.add(p.id); localStorage.setItem('ti_visited',JSON.stringify([...state.visitedPlaces])); renderFog(); renderPlaces(); }
});

// Filter panel
document.getElementById('filter-handle').addEventListener('click',()=>{document.getElementById('filter-panel').classList.toggle('open');});
let touchStartY=0;
document.getElementById('filter-panel').addEventListener('touchstart',e=>{touchStartY=e.touches[0].clientY;},{passive:true});
document.getElementById('filter-panel').addEventListener('touchend',e=>{const d=e.changedTouches[0].clientY-touchStartY;if(d>50)document.getElementById('filter-panel').classList.remove('open');else if(d<-50)document.getElementById('filter-panel').classList.add('open');},{passive:true});

rebuildCategoryChips();
document.querySelectorAll('.preset-btn').forEach(btn=>{btn.addEventListener('click',()=>{document.querySelectorAll('.preset-btn').forEach(b=>b.classList.remove('active'));btn.classList.add('active');state.activeCategories=new Set(PRESETS[btn.dataset.preset]);document.querySelectorAll('.chip').forEach(c=>c.classList.toggle('active',state.activeCategories.has(c.dataset.cat)));applyFilters();});});

document.getElementById('radius-slider').addEventListener('input', function(){state.radius=parseFloat(this.value);document.getElementById('radius-value').textContent=state.radius.toFixed(1)+' km';});
document.getElementById('radius-slider').addEventListener('change', debounce(()=>fetchPlaces(), 400));
document.getElementById('confidence-slider').addEventListener('input', debounce(function(){state.minConfidence=parseInt(this.value);document.getElementById('confidence-value').textContent=state.minConfidence===0?'All':'≥ '+state.minConfidence+'%';applyFilters();}, 150));
function handleToggle(el, onChange) { el.addEventListener('click',function(){this.classList.toggle('on');this.setAttribute('aria-checked',this.classList.contains('on'));onChange(this.classList.contains('on'));}); el.addEventListener('keydown',function(e){if(e.key===' '||e.key==='Enter'){e.preventDefault();this.click();}}); }
handleToggle(document.getElementById('toggle-visited'), v=>{state.hideVisited=v;applyFilters();});
handleToggle(document.getElementById('toggle-fog'), v=>{state.fogEnabled=v;document.getElementById('btn-fog').classList.toggle('active',v);document.getElementById('fog-stats').classList.toggle('visible',v);renderFog();renderPlaces();});

function rebuildCategoryChips() {
  const c=document.getElementById('category-chips'); c.innerHTML='';
  Object.entries(CATEGORIES).forEach(([k,d])=>{const ch=document.createElement('div');ch.className='chip active';ch.dataset.cat=k;ch.setAttribute('role','checkbox');ch.setAttribute('aria-checked','true');ch.setAttribute('tabindex','0');ch.innerHTML=`<span style="margin-right:3px">${d.icon}</span>${d.label}`;ch.addEventListener('click',()=>toggleChip(ch,k));ch.addEventListener('keydown',e=>{if(e.key===' '||e.key==='Enter'){e.preventDefault();toggleChip(ch,k);}});c.appendChild(ch);});
}
function toggleChip(ch,k){ch.classList.toggle('active');const on=ch.classList.contains('active');ch.setAttribute('aria-checked',String(on));if(on)state.activeCategories.add(k);else state.activeCategories.delete(k);document.querySelectorAll('.preset-btn').forEach(b=>b.classList.remove('active'));applyFilters();}
