// Optional voice: dictate chat messages (SpeechRecognition) and read replies aloud (speechSynthesis).
// Both are feature-detected — the buttons stay hidden where the browser lacks the API — and the speaker is OFF by default.
// Classic script (no modules): every file in map/js shares one global scope.
'use strict';

const SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
const canDictate = !!SpeechRecognitionCtor;
const canSpeak = 'speechSynthesis' in window && typeof window.SpeechSynthesisUtterance === 'function';

let speakOn = false;
let recognizer = null;
let listening = false;

// ── Read replies aloud ──
function setSpeak(on, { persist = true } = {}) {
  speakOn = !!on && canSpeak;
  if (!speakOn && canSpeak) window.speechSynthesis.cancel();
  if (persist) store.set(LS.speak, speakOn ? '1' : '0');
  const b = $('chat-speak');
  b.setAttribute('aria-pressed', String(speakOn));
  b.textContent = speakOn ? '🔊' : '🔈';
  if (typeof refreshSettingsDialog === 'function') refreshSettingsDialog();
}

/** Speak `text` if the user turned the speaker on (called for chat replies and place answers). */
function speakReply(text) {
  if (!speakOn || !canSpeak || !text) return;
  const synth = window.speechSynthesis;
  synth.cancel();
  const u = new SpeechSynthesisUtterance(String(text).slice(0, 1500));
  u.lang = /[Ѐ-ӿ]/.test(text) ? 'ru-RU' : (navigator.language || 'en-US');
  synth.speak(u);
}

// ── Dictation ──
function stopDictation() {
  if (recognizer) { try { recognizer.stop(); } catch { /* already stopped */ } }
}
function setMicState(on) {
  listening = on;
  const b = $('chat-mic');
  b.classList.toggle('active', on);
  b.setAttribute('aria-pressed', String(on));
  b.setAttribute('aria-label', on ? 'Stop dictation' : 'Dictate a message');
}

function startDictation() {
  if (!canDictate) return;
  if (listening) { stopDictation(); return; }
  const input = $('chat-input');
  const base = input.value ? input.value.trim() + ' ' : '';
  const rec = new SpeechRecognitionCtor();
  recognizer = rec;
  rec.lang = navigator.language || 'en-US';
  rec.interimResults = true;
  rec.continuous = false;
  rec.maxAlternatives = 1;
  rec.onstart = () => { setMicState(true); toast('🎤 Listening…', 'info', 1500); };
  rec.onresult = (ev) => {
    let text = '';
    for (const r of ev.results) text += r[0].transcript;
    input.value = (base + text).slice(0, 1000);      // review it, then press Send — nothing is sent automatically
  };
  rec.onerror = (ev) => {
    const msg = {
      'not-allowed': '🎤 Microphone access was denied — allow it in the browser settings',
      'service-not-allowed': '🎤 Speech recognition is blocked in this browser',
      'no-speech': '🎤 Did not hear anything — try again',
      'audio-capture': '🎤 No microphone found',
      'network': '🎤 Speech recognition needs an internet connection',
    }[ev.error];
    if (msg && ev.error !== 'aborted') toast(msg, 'warning');
  };
  rec.onend = () => { setMicState(false); recognizer = null; input.focus(); };
  try { rec.start(); } catch (e) { console.warn('SpeechRecognition.start failed', e); setMicState(false); }
}

/** Row for the settings dialog (only when speech synthesis exists). */
function voiceSettingsHtml() {
  if (!canSpeak) return '';
  return html`<div class="setting-row">
    <div><div class="setting-title" id="lbl-speak">🔊 Read replies aloud</div><div class="setting-sub">Chat replies and place answers are spoken. Off by default.</div></div>
    ${switchHtml('toggle-speak', speakOn, 'lbl-speak')}
  </div>`;
}

registerActions({
  'chat-mic': () => startDictation(),
  'toggle-speak': () => { setSpeak(!speakOn); toast(speakOn ? '🔊 Replies will be read aloud' : '🔈 Voice replies off', 'info', 1800); },
});

function initVoice() {
  $('chat-mic').hidden = !canDictate;
  $('chat-speak').hidden = !canSpeak;
  setSpeak(canSpeak && store.get(LS.speak) === '1', { persist: false });
  // Never keep talking or listening in the background.
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { if (canSpeak) window.speechSynthesis.cancel(); stopDictation(); }
  });
}
