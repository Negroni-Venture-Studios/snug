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

  // Reading time: a heartbeat while the tab is visible, "end" when it is
  // hidden or closed, "resume" when it is shown again. Snug measures the time
  // itself; these only say when the page was really in view.
  var reader = document.querySelector('.reader[data-visit]');
  if (reader && navigator.sendBeacon) {
    var token = reader.querySelector('input[name="csrfmiddlewaretoken"]');
    var send = function (kind) {
      var body = new URLSearchParams({
        visit: reader.dataset.visit,
        page: reader.dataset.page,
        kind: kind,
        csrfmiddlewaretoken: token ? token.value : ''
      });
      navigator.sendBeacon(reader.dataset.beatUrl, body);
    };
    var every = (parseInt(reader.dataset.interval, 10) || 15) * 1000;
    setInterval(function () {
      if (document.visibilityState === 'visible') send('beat');
    }, every);
    document.addEventListener('visibilitychange', function () {
      send(document.visibilityState === 'visible' ? 'resume' : 'end');
    });
    window.addEventListener('pagehide', function () {
      send('end');
    });
  }

  // Choosing a page goes straight to it.
  document.querySelectorAll('select[data-autosubmit]').forEach(function (select) {
    select.addEventListener('change', function () {
      select.form.submit();
    });
  });
})();
