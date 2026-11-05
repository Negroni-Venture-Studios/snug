// Admin page helpers, wired with addEventListener (the CSP forbids inline handlers).
'use strict';

(function () {
  // On a narrow screen the tabs scroll sideways. Start with the current one
  // in view, so the page says where you are.
  var current = document.querySelector('nav.tabs [aria-current="page"]');
  if (current && current.parentElement.scrollWidth > current.parentElement.clientWidth) {
    current.parentElement.scrollLeft = current.offsetLeft - current.parentElement.offsetLeft - 16;
  }

  // Copy buttons: <button data-copy="element-id"><span class="label">Copy</span></button>.
  // If the browser will not let the page write to the clipboard, the text is
  // selected instead, so it can be copied by hand. It never fails silently.
  function select(element) {
    var range = document.createRange();
    range.selectNodeContents(element);
    var selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }

  document.querySelectorAll('[data-copy]').forEach(function (button) {
    var label = button.querySelector('.label');
    function say(text) {
      if (label) label.textContent = text;
    }
    button.addEventListener('click', function () {
      var source = document.getElementById(button.getAttribute('data-copy'));
      if (!source) return;
      var text = source.textContent.trim();
      var fallback = function () {
        select(source);
        say(/Mac/.test(navigator.platform) ? 'Selected: press ⌘C' : 'Selected: press Ctrl+C');
      };
      if (!navigator.clipboard || !window.isSecureContext) {
        fallback();
        return;
      }
      navigator.clipboard.writeText(text).then(function () { say('Copied'); }, fallback);
    });
  });
})();
