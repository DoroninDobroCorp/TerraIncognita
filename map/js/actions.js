// Action registry + ONE delegated listener per event type. This replaces inline onclick=/onchange=
// so the CSP can drop 'unsafe-inline' for scripts.
//
//   markup                              registry                    fires on
//   data-action="name"                  registerActions({name})     click, Enter/Space on non-native elements
//   data-change="name"                  registerChange({name})      change
//   data-input="name"                   registerInput({name})       input
//   data-enter="name"                   registerEnter({name})       Enter pressed in a text field
//
// Handlers get (element, event); extra arguments travel in data-* attributes (el.dataset.id, ...).
// Dynamic markup (templates, map popups) needs no wiring: the listeners live on `document`.
'use strict';

const TI = (window.TI = window.TI || {});
TI.actions = Object.create(null);
TI.changes = Object.create(null);
TI.inputs = Object.create(null);
TI.enters = Object.create(null);
TI.escapes = [];      // callbacks run on Escape, first one returning true wins
TI.tabHooks = Object.create(null); // 'group:tab' -> function run when that tab is shown

function _register(reg, kind, fns) {
  for (const [name, fn] of Object.entries(fns)) {
    if (reg[name]) console.warn(`Duplicate ${kind} handler "${name}"`);
    reg[name] = fn;
  }
}
function registerActions(fns) { _register(TI.actions, 'action', fns); }
function registerChange(fns) { _register(TI.changes, 'change', fns); }
function registerInput(fns) { _register(TI.inputs, 'input', fns); }
function registerEnter(fns) { _register(TI.enters, 'enter', fns); }
function onEscape(fn) { TI.escapes.unshift(fn); }

function _run(reg, kind, el, ev) {
  const name = el.dataset[kind];
  const fn = reg[name];
  if (!fn) { console.warn(`No ${kind} handler registered for "${name}"`); return; }
  try {
    fn(el, ev); // async handlers reject into the global unhandledrejection toast
  } catch (e) {
    console.error(`${kind} "${name}" failed`, e);
    toast('Something went wrong. Please try again.', 'error');
  }
}

const _NATIVE_ACTIVATORS = /^(BUTTON|A|INPUT|SELECT|TEXTAREA|SUMMARY)$/;

document.addEventListener('click', (ev) => {
  const el = ev.target.closest && ev.target.closest('[data-action]');
  if (!el || el.disabled) return;
  if (el.hasAttribute('data-needs-network') && TI.net && !TI.net.ok) {
    toast('You are offline — this needs a connection', 'warning');
    return;
  }
  if (el.getAttribute('aria-disabled') === 'true') return;
  if (el.tagName === 'A' && !el.hasAttribute('data-native')) ev.preventDefault();
  _run(TI.actions, 'action', el, ev);
});

document.addEventListener('change', (ev) => {
  const el = ev.target.closest && ev.target.closest('[data-change]');
  if (el) _run(TI.changes, 'change', el, ev);
});

document.addEventListener('input', (ev) => {
  const el = ev.target.closest && ev.target.closest('[data-input]');
  if (el) _run(TI.inputs, 'input', el, ev);
});

document.addEventListener('keydown', (ev) => {
  const t = ev.target;
  if (ev.key === 'Escape') {
    for (const fn of TI.escapes) { if (fn()) { ev.preventDefault(); break; } }
    return;
  }
  if (!t || !t.getAttribute) return;
  if (ev.key === 'Tab' && TI.trapFocus) { TI.trapFocus(ev); return; }

  if (ev.key === 'Enter' && t.dataset && t.dataset.enter && !ev.isComposing) {
    ev.preventDefault();
    if (t.hasAttribute('data-needs-network') && TI.net && !TI.net.ok) { toast('You are offline — this needs a connection', 'warning'); return; }
    _run(TI.enters, 'enter', t, ev);
    return;
  }

  // WAI-ARIA tabs: arrow keys move between tabs of the same tablist.
  if (t.getAttribute('role') === 'tab' && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(ev.key)) {
    const tabs = $$('[role="tab"]', t.closest('[role="tablist"]') || document).filter(x => !x.hidden);
    let i = tabs.indexOf(t);
    if (ev.key === 'ArrowRight') i = (i + 1) % tabs.length;
    else if (ev.key === 'ArrowLeft') i = (i - 1 + tabs.length) % tabs.length;
    else if (ev.key === 'Home') i = 0;
    else i = tabs.length - 1;
    ev.preventDefault();
    tabs[i].focus();
    tabs[i].click();
    return;
  }

  // Keyboard activation for role=button/switch/checkbox elements that are not native buttons.
  if ((ev.key === 'Enter' || ev.key === ' ') && t.hasAttribute('data-action') && !_NATIVE_ACTIVATORS.test(t.tagName)) {
    ev.preventDefault();
    t.click();
  }
});
