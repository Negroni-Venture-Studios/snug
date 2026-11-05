// Mockup: switch screens, and the theme toggle as the product will have it.
'use strict';

// Three states, cycling dark -> light -> auto -> dark. Dark is the default
// and is the absence of data-theme; auto follows the operating system.
var THEME_ORDER = ['dark', 'light', 'auto'];

function currentTheme() {
  return document.documentElement.getAttribute('data-theme') || 'dark';
}

// The label names the state you are in, matching the glyph.
function labelTheme(mode) {
  document.querySelectorAll('.theme-toggle').forEach(function (b) {
    b.setAttribute('aria-label', 'Theme: ' + mode);
    b.setAttribute('title', 'Theme: ' + mode);
  });
}

function applyTheme(mode) {
  var root = document.documentElement;
  if (mode === 'dark') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', mode);
  try {
    if (mode === 'dark') localStorage.removeItem('snug-theme');
    else localStorage.setItem('snug-theme', mode);
  } catch (e) {}
  labelTheme(mode);
}

document.addEventListener('DOMContentLoaded', function () {
  var buttons = document.querySelectorAll('[data-screen]');
  buttons.forEach(function (b) {
    b.addEventListener('click', function () {
      document.querySelectorAll('[data-screen-id]').forEach(function (s) {
        s.hidden = s.dataset.screenId !== b.dataset.screen;
      });
      buttons.forEach(function (o) { o.setAttribute('aria-pressed', String(o === b)); });
      window.scrollTo(0, 0);
    });
  });
  document.querySelectorAll('.theme-toggle').forEach(function (t) {
    t.addEventListener('click', function () {
      applyTheme(THEME_ORDER[(THEME_ORDER.indexOf(currentTheme()) + 1) % THEME_ORDER.length]);
    });
  });
  labelTheme(currentTheme());
});
