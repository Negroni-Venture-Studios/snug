// About this link, at the top right of the reader and the video page: a
// <details> element, which opens and closes without this script. This only
// closes it on Escape, or on a click anywhere else, as a menu would.
'use strict';

(function () {
  document.querySelectorAll('details[data-about]').forEach(function (about) {
    document.addEventListener('click', function (event) {
      if (about.open && !about.contains(event.target)) about.open = false;
    });
    document.addEventListener('keydown', function (event) {
      if (event.key !== 'Escape' || !about.open) return;
      about.open = false;
      about.querySelector('summary').focus();
    });
  });
})();
