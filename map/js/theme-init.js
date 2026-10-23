// Runs in <head>, before first paint, so the page never flashes the wrong theme.
// Only picks the theme; everything else (switching, persistence, map colours) is in settings.js.
// Keep resolveTheme() here and in settings.js in sync.
(function () {
  'use strict';
  var pref = 'auto';
  try { pref = localStorage.getItem('ti_theme') || 'auto'; } catch (e) { /* storage blocked: follow the system */ }
  var theme = pref;
  if (pref !== 'light' && pref !== 'dark' && pref !== 'sun') {
    var mq = function (q) { return window.matchMedia && window.matchMedia(q).matches; };
    theme = mq('(prefers-contrast: more)') ? 'sun' : (mq('(prefers-color-scheme: light)') ? 'light' : 'dark');
    pref = 'auto';
  }
  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.themePref = pref;
})();
