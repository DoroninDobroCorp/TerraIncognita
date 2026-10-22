// AI chat (natural-language place discovery).
// Classic script (no modules): every file in map/js shares one global scope.
'use strict';

let conversationId = null;
let chatBusy = false;

function sendChatFromInput() {
  const v = $('chat-input').value.trim();
  if (v) sendChat(v);
}

function chatPlaceHtml(p) {
  const icon = (CATEGORIES[p.categories?.[0]] || {icon:'📍'}).icon;
  const desc = p.description || '';
  return html`<div class="msg-place" role="button" tabindex="0" data-action="chat-place" data-id="${p.id}"><div class="msg-place-name">${icon} ${p.name}</div><div class="msg-place-desc">${desc.substring(0,100)}${desc.length>100?'...':''}</div></div>`;
}

async function sendChat(msg) {
  if (!msg || !msg.trim()) return;
  if (msg.length > 1000) { toast('Message too long (max 1000 characters)', 'warning'); return; }
  if (chatBusy) { toast('Please wait for the current response', 'info'); return; }
  chatBusy = true;
  analytics.track('chat_send', { length: msg.length });
  const container = $('chat-messages');
  appendHtml(container, html`<div class="chat-msg user"><span class="msg-text">${msg}</span></div>`);
  $('chat-input').value = '';
  setHtml($('chat-suggestions'), html``);
  appendHtml(container, html`<div class="chat-msg assistant" id="chat-loading"><div class="spinner spinner-sm"></div> Thinking...</div>`);
  container.scrollTop = container.scrollHeight;

  try {
    const data = await api('/api/chat', {method:'POST', body:{message:msg, lat:state.userLat, lng:state.userLng, radius_km:state.radius, conversation_id:conversationId, language:'ru'}});
    conversationId = data.conversation_id;
    const el = $('chat-loading');
    const places = data.places || [];
    places.forEach(p => { if (!state.places.find(x=>x.id===p.id)) state.places.push(p); });
    const reply = data.message || 'Here are some places I found:';
    if (el) {
      el.removeAttribute('id');
      setHtml(el, html`<span class="msg-text">${reply}</span>${places.length ? html`<div class="msg-places">${places.map(chatPlaceHtml)}</div>` : ''}`);
    }
    speakReply(reply);

    if (data.suggested_questions && data.suggested_questions.length) {
      setHtml($('chat-suggestions'), html`${data.suggested_questions.map(q => html`<button class="chat-suggestion" data-action="chat-suggest">${q}</button>`)}`);
    }
  } catch(e) {
    const el = $('chat-loading');
    if (el) {
      const timedOut = String(e.message).startsWith('Request timeout');
      el.removeAttribute('id');
      setHtml(el, html`❌ ${timedOut ? 'Request timed out. The server may be busy — try again.' : 'Could not get a response. Make sure the API is running.'}<br><button class="btn btn-sm btn-secondary mt-8" data-action="chat-retry" data-msg="${msg}">🔄 Retry</button>`);
    }
  } finally { chatBusy = false; }
  container.scrollTop = container.scrollHeight;
}

function chatPlaceClick(id) {
  // Switch to map and show place
  showSection('map', { runHooks: false });
  const p = state.places.find(x=>x.id===id);
  if(p){ map.flyTo({center:[p.coordinates.lng,p.coordinates.lat],zoom:15}); map.once('moveend',()=>showPlaceCard(id)); }
}

registerActions({
  'chat-send': () => sendChatFromInput(),
  'chat-suggest': (el) => sendChat(el.textContent),
  'chat-retry': (el) => sendChat(el.dataset.msg),
  'chat-place': (el) => chatPlaceClick(el.dataset.id),
});
registerEnter({ 'chat-input': () => sendChatFromInput() });

// Placeholder until the voice module (voice.js) is loaded.
function speakReply() {}
