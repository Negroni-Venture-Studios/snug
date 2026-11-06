// Settings: keep the colour picker and the typed colour in step, and show the
// choice in the preview straight away. The server works out the exact shades
// when it saves (snug.core.brand); this is a close, quick approximation.
'use strict';

(function () {
  var picker = document.querySelector('[data-accent-picker]');
  var typed = document.getElementById('accent');
  var preview = document.querySelector('[data-brand-preview]');
  if (!picker || !typed || !preview) return;

  function channel(hex, start) {
    var c = parseInt(hex.substr(start, 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  }
  function luminance(hex) {
    return 0.2126 * channel(hex, 1) + 0.7152 * channel(hex, 3) + 0.0722 * channel(hex, 5);
  }
  function show(hex) {
    if (!/^#[0-9a-f]{6}$/i.test(hex)) return;
    // A white label on dark colours, a dark one on light colours.
    var label = luminance(hex) > 0.18 ? '#1c1917' : '#ffffff';
    preview.style.setProperty('--brand', hex);
    preview.style.setProperty('--brand-text', luminance(hex) > 0.18 ? '#1c1917' : hex);
    preview.style.setProperty('--on-brand', label);
  }

  picker.addEventListener('input', function () {
    typed.value = picker.value;
    show(picker.value);
  });
  typed.addEventListener('input', function () {
    var value = typed.value.trim();
    if (/^#?[0-9a-f]{6}$/i.test(value)) {
      if (value.charAt(0) !== '#') value = '#' + value;
      picker.value = value.toLowerCase();
      show(value);
    }
  });
})();
