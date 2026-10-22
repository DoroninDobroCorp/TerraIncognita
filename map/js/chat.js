// AI chat.
// Classic script (no modules): shares the global scope with the other map/js files.

let conversationId = null;

function sendChatFromInput() { const v=document.getElementById('chat-input').value.trim(); if(v)sendChat(v); }
document.getElementById('chat-input').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();sendChatFromInput();}});

let chatBusy = false;
async function sendChat(msg) {
  if (!msg || !msg.trim()) return;
  if (msg.length > 1000) { toast('Message too long (max 1000 characters)', 'warning'); return; }
  if (chatBusy) { toast('Please wait for the current response', 'info'); return; }
  chatBusy = true;
  analytics.track('chat_send', { length: msg.length });
  const container = document.getElementById('chat-messages');
  container.insertAdjacentHTML('beforeend', `<div class="chat-msg user">${escHtml(msg)}</div>`);
  document.getElementById('chat-input').value = '';
  document.getElementById('chat-suggestions').innerHTML = '';
  container.insertAdjacentHTML('beforeend', `<div class="chat-msg assistant" id="chat-loading"><div class="spinner" style="width:16px;height:16px;border-width:2px"></div> Thinking...</div>`);
  container.scrollTop = container.scrollHeight;

  try {
    const data = await api('/api/chat', {method:'POST', body:{message:msg, lat:state.userLat, lng:state.userLng, radius_km:state.radius, conversation_id:conversationId, language:'ru'}});
    conversationId = data.conversation_id;
    const el = document.getElementById('chat-loading');
    let html = escHtml(data.message || 'Here are some places I found:');
    if (data.places && data.places.length) {
      html += '<div class="msg-places">';
      data.places.forEach(p => {
        html += `<div class="msg-place" onclick="chatPlaceClick('${p.id}')"><div class="msg-place-name">${(CATEGORIES[p.categories?.[0]]||{icon:'📍'}).icon} ${escHtml(p.name)}</div><div class="msg-place-desc">${escHtml((p.description||'').substring(0,100))}${(p.description||'').length>100?'...':''}</div></div>`;
        // Add to state
        if (!state.places.find(x=>x.id===p.id)) state.places.push(p);
      });
      html += '</div>';
    }
    if (el) el.outerHTML = `<div class="chat-msg assistant">${html}</div>`;

    if (data.suggested_questions && data.suggested_questions.length) {
      document.getElementById('chat-suggestions').innerHTML = data.suggested_questions.map(q=>`<button class="chat-suggestion" onclick="sendChat(this.textContent)">${escHtml(q)}</button>`).join('');
    }
  } catch(e) {
    const el = document.getElementById('chat-loading');
    if (el) {
      const div = document.createElement('div'); div.className='chat-msg assistant';
      div.innerHTML = `❌ ${e.message === 'Request timeout' ? 'Request timed out. The server may be busy — try again.' : 'Could not get a response. Make sure the API is running.'}`;
      const retryBtn = document.createElement('button'); retryBtn.className='btn btn-sm btn-secondary'; retryBtn.style.marginTop='8px'; retryBtn.textContent='🔄 Retry';
      retryBtn.addEventListener('click', () => sendChat(msg));
      div.appendChild(document.createElement('br')); div.appendChild(retryBtn);
      el.replaceWith(div);
    }
  } finally { chatBusy = false; }
  container.scrollTop = container.scrollHeight;
}

function chatPlaceClick(id) {
  // Switch to map and show place
  document.querySelectorAll('.nav-btn[data-section]').forEach(b=>b.classList.remove('active'));
  document.querySelector('.nav-btn[data-section="map"]').classList.add('active');
  document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));
  document.getElementById('section-map').classList.add('active');
  map.resize();
  const p = state.places.find(x=>x.id===id);
  if(p){ map.flyTo({center:[p.coordinates.lng,p.coordinates.lat],zoom:15}); map.once('moveend',()=>showPlaceCard(id)); }
}

