// Applies the saved theme before first paint. Loaded in <head>, not deferred,
// as a file rather than inline because the CSP forbids inline scripts.
// No saved choice means dark, the default.
'use strict';
(function () {
  try {
    var saved = localStorage.getItem('snug-theme');
    if (saved === 'light' || saved === 'auto') {
      document.documentElement.setAttribute('data-theme', saved);
    }
  } catch (e) {
    // Storage can throw in a locked-down browser; dark is the right fallback.
  }
})();
