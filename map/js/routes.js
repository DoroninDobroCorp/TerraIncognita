// Route builder (point-to-point, circular), export and story.
// Classic script (no modules): shares the global scope with the other map/js files.

// ═══════════════════════════════════════════════════════
//  ROUTES
// ═══════════════════════════════════════════════════════
// Build explore category chips
function buildExploreCats() {
  const c=document.getElementById('explore-cats'); c.innerHTML='';
  ['abandoned','ruins','nature_hidden','viewpoint','underground','military'].forEach(k=>{
    const d=CATEGORIES[k]; if(!d)return;
    const ch=document.createElement('div');ch.className='chip active';ch.dataset.cat=k;ch.innerHTML=`${d.icon} ${d.label}`;
    ch.addEventListener('click',()=>ch.classList.toggle('active'));c.appendChild(ch);
  });
}
buildExploreCats();

function parseCoords(str) {
  const parts = (str||'').split(',').map(s => parseFloat(s.trim()));
  if (parts.length < 2 || isNaN(parts[0]) || isNaN(parts[1])) return null;
  if (parts[0] < -90 || parts[0] > 90 || parts[1] < -180 || parts[1] > 180) return null;
  return { lat: parts[0], lng: parts[1] };
}
async function buildRoute() {
  const from=parseCoords(document.getElementById('route-from').value);
  const to=parseCoords(document.getElementById('route-to').value);
  if(!from||!to){toast('Enter valid coordinates as "lat, lng" (e.g., 42.441, 18.771)','warning');return;}
  const btn = document.querySelector('#rtab-build .btn-primary'); btn.disabled=true;
  toast('Building route...','info');
  analytics.track('route_build', { mode: document.getElementById('route-mode').value });
  try {
    const data = await api('/api/route',{method:'POST',body:{origin:from,destination:to,time_budget_hours:clamp(parseFloat(document.getElementById('route-time').value)||2,0.5,24),transport_mode:document.getElementById('route-mode').value}});
    state.currentRoute = data.route || data;
    showRouteResult(state.currentRoute);
    toast('Route built!','success');
  } catch(e) { toast('Route building failed: '+(e.message||'check connection'),'error'); } finally { btn.disabled=false; }
}

async function exploreRoute() {
  const center = parseCoords(document.getElementById('explore-center').value);
  if(!center){toast('Enter valid center point as "lat, lng" (e.g., 42.441, 18.771)','warning');return;}
  const cats = [...document.querySelectorAll('#explore-cats .chip.active')].map(c=>c.dataset.cat);
  const btn = document.querySelector('#rtab-explore .btn-primary'); btn.disabled=true;
  toast('Generating circular walk...','info');
  analytics.track('route_explore');
  try {
    const data = await api('/api/route/explore',{method:'POST',body:{lat:center.lat,lng:center.lng,time_budget_hours:clamp(parseFloat(document.getElementById('explore-time').value)||2,0.5,8),categories:cats}});
    state.currentRoute = data.route || data;
    showRouteResult(state.currentRoute);
    toast('Circular walk generated!','success');
  } catch(e) { toast('Generation failed: '+(e.message||'check connection'),'error'); } finally { btn.disabled=false; }
}

function showRouteResult(route) {
  if (!route || !route.total_distance_m) { toast('Invalid route data','error'); return; }
  document.getElementById('route-result').style.display='block';
  document.getElementById('route-title').textContent = route.is_circular?'🔄 Circular Walk':'🛤️ Route';
  document.getElementById('route-meta').textContent = `${((route.total_distance_m||0)/1000).toFixed(1)} km · ${Math.round((route.total_duration_s||0)/60)} min · ${route.places_count||route.waypoints?.length||0} places`;
  const wps = route.waypoints||[];
  document.getElementById('route-waypoints').innerHTML = wps.length ? wps.map((w,i)=>`
    <div class="list-item" style="padding:6px 10px;margin-bottom:4px">
      <div style="width:24px;height:24px;background:var(--accent);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:#fff;flex-shrink:0">${i+1}</div>
      <div class="item-body"><div class="item-title" style="font-size:12px">${escHtml(w.place?.name||'Waypoint '+(i+1))}</div><div class="item-sub">${w.detour_distance_m?'Detour: '+(w.detour_distance_m/1000).toFixed(1)+' km':''}</div></div>
    </div>`).join('') : '<div style="color:var(--text-secondary);font-size:12px">No waypoints in route</div>';
}

async function exportRoute(fmt) {
  if(!state.currentRoute){toast('No route to export','warning');return;}
  try {
    const data = await api('/api/route/export',{method:'POST',body:{route:state.currentRoute,format:fmt,include_descriptions:true}});
    const blob=new Blob([data.content],{type:'application/xml'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=data.filename||`route.${fmt}`;a.click();URL.revokeObjectURL(url);
    toast(`Exported as ${fmt.toUpperCase()}!`,'success');
  } catch { toast('Export failed','error'); }
}

async function getRouteStory() {
  if(!state.currentRoute){return;}
  toast('Generating route story...','info');
  try {
    const places = (state.currentRoute.waypoints||[]).filter(w=>w.place).map(w=>({place_id:w.place.id,place_name:w.place.name,lat:w.place.coordinates?.lat,lng:w.place.coordinates?.lng,categories:w.place.categories}));
    const data = await api('/api/story/route',{method:'POST',body:{places,language:'ru'}});
    document.getElementById('route-story').style.display='block';
    document.getElementById('route-story-text').textContent = data.story;
    toast('Story ready!','success');
  } catch { toast('Story failed','error'); }
}
function saveRouteLocal(){if(!state.currentRoute)return;const saved=JSON.parse(localStorage.getItem('ti_routes')||'[]');saved.push({...state.currentRoute,saved_at:new Date().toISOString()});localStorage.setItem('ti_routes',JSON.stringify(saved));toast('Route saved locally!','success');}
