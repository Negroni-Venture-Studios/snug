// The theme toggle: dark (the default), light, auto. Wired by addEventListener
// because the CSP forbids inline handlers. theme-init.js applies the saved
// choice before first paint.
'use strict';

(function () {
  var ORDER = ['dark', 'light', 'auto'];

  function current() {
    return document.documentElement.getAttribute('data-theme') || 'dark';
  }

  // The label names the state you are in, matching the glyph.
  function label(mode) {
    document.querySelectorAll('.theme-toggle').forEach(function (button) {
      button.setAttribute('aria-label', 'Theme: ' + mode);
      button.setAttribute('title', 'Theme: ' + mode);
    });
  }

  function apply(mode) {
    var root = document.documentElement;
    if (mode === 'dark') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', mode);
    try {
      if (mode === 'dark') localStorage.removeItem('snug-theme');
      else localStorage.setItem('snug-theme', mode);
    } catch (e) {
      // Storage can throw in a locked-down browser; the choice lasts this page.
    }
    label(mode);
  }

  document.querySelectorAll('.theme-toggle').forEach(function (button) {
    button.addEventListener('click', function () {
      apply(ORDER[(ORDER.indexOf(current()) + 1) % ORDER.length]);
    });
  });
  label(current());
})();
