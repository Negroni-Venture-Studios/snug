// Admin page helpers, wired with addEventListener (the CSP forbids inline handlers).
'use strict';

(function () {
  // On a narrow screen the tabs scroll sideways. Start with the current one
  // in view, so you can see where you are.
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

  function wireCopy(root) {
    root.querySelectorAll('[data-copy]').forEach(function (button) {
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
  }

  // Upload forms (<form data-upload>): send the form here, to show a progress
  // bar and stop a second click, then show the page the server sends back, as
  // a plain submission would. Without this script the form posts as before.
  function wireUploads(root) {
    root.querySelectorAll('form[data-upload]').forEach(function (form) {
      var status = form.querySelector('[data-upload-status]');
      if (!status || !window.FormData || !window.XMLHttpRequest) return;
      var bar = status.querySelector('progress');
      var words = status.querySelector('[role="status"]');
      form.addEventListener('submit', function (event) {
        event.preventDefault();
        var button = form.querySelector('button[type="submit"]');
        if (button) button.disabled = true;
        status.hidden = false;
        var request = new XMLHttpRequest();
        request.open('POST', form.action);
        request.setRequestHeader('X-Snug-Upload', '1');
        request.upload.addEventListener('progress', function (progress) {
          if (!progress.lengthComputable) return;
          var percent = Math.round((progress.loaded / progress.total) * 100);
          bar.value = percent;
          words.textContent = 'Uploading, ' + percent + '%';
        });
        request.upload.addEventListener('load', function () {
          bar.removeAttribute('value');  // indeterminate while the server reads it
          words.textContent = 'Checking the file';
        });
        request.addEventListener('load', function () {
          // Saved: the server says where to go, so the page shows its notice.
          var next = request.getResponseHeader('Snug-Location');
          if (request.status === 204 && next) {
            window.location.assign(next);
            return;
          }
          // Refused: show the form the server sent back, with its error.
          show(request.responseText);
        });
        request.addEventListener('error', function () {
          if (button) button.disabled = false;
          bar.removeAttribute('value');
          words.textContent = 'The upload stopped. Check your connection, then try again.';
        });
        request.send(new FormData(form));
      });
    });
  }

  // Show the page the server sent back for a refused upload. It is parsed,
  // never run, and only its main part replaces this page's, so this page's
  // header and scripts stay. A page of another kind, such as the sign-in
  // page after a session has ended, replaces the whole body.
  function show(html) {
    var sent = new DOMParser().parseFromString(html, 'text/html');
    var main = document.querySelector('main');
    var fresh = sent.querySelector('main');
    if (main && fresh && fresh.className === main.className) {
      fresh = document.importNode(fresh, true);
      main.replaceWith(fresh);
      wire(fresh);
    } else {
      document.body.replaceWith(document.importNode(sent.body, true));
    }
    if (sent.title) document.title = sent.title;
    var error = document.querySelector('.notice-error');
    if (error) error.scrollIntoView({ block: 'center' });
  }

  function wire(root) {
    wireCopy(root);
    wireUploads(root);
  }

  wire(document);
})();
