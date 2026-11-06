// Show or hide what is typed in a password box. Wired by addEventListener
// because the CSP forbids inline handlers; each button stays hidden until this
// runs, so nobody meets a button that does nothing.
'use strict';

(function () {
  document.querySelectorAll('.password-field .reveal').forEach(function (button) {
    var field = document.getElementById(button.getAttribute('aria-controls'));
    if (!field) return;
    button.hidden = false;
    button.addEventListener('click', function () {
      var show = field.type === 'password';
      field.type = show ? 'text' : 'password';
      var label = show ? 'Hide password' : 'Show password';
      button.setAttribute('aria-pressed', show ? 'true' : 'false');
      button.setAttribute('aria-label', label);
      button.setAttribute('title', label);
      button.parentElement.classList.toggle('shown', show);
      // Keep typing where you were.
      var end = field.value.length;
      field.focus();
      field.setSelectionRange(end, end);
    });
  });
  // Never send a form with the password left showing in the page.
  document.querySelectorAll('form').forEach(function (form) {
    form.addEventListener('submit', function () {
      form.querySelectorAll('.password-field input').forEach(function (field) {
        field.type = 'password';
      });
    });
  });
})();
