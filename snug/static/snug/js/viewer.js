// The reader's keyboard and page picker, wired with addEventListener (the CSP
// forbids inline handlers). Without JavaScript, the links and the Go button work.
'use strict';

(function () {
  // Arrow keys follow the Previous and Next links, unless typing in a field.
  document.addEventListener('keydown', function (event) {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    var target = event.target;
    if (target && (target.isContentEditable || /^(INPUT|SELECT|TEXTAREA)$/.test(target.tagName))) return;
    var link = document.querySelector('a[data-key="' + event.key + '"]');
    if (link) {
      event.preventDefault();
      link.click();
    }
  });

  // Choosing a page goes straight to it.
  document.querySelectorAll('select[data-autosubmit]').forEach(function (select) {
    select.addEventListener('change', function () {
      select.form.submit();
    });
  });
})();
