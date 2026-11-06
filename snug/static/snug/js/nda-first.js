// With no NDA yet, a form offers a box to write version 1. Show it only while
// "Ask for the NDA" is ticked. Without this script the box simply stays shown.
'use strict';

(function () {
  document.querySelectorAll('[data-nda-for]').forEach(function (box) {
    var tick = document.getElementById(box.getAttribute('data-nda-for'));
    if (!tick) return;
    function update() {
      // An error about the NDA stays visible, whatever the tick says.
      box.hidden = !tick.checked && !box.querySelector('[aria-invalid="true"]');
    }
    tick.addEventListener('change', function () {
      update();
      if (tick.checked) box.querySelector('textarea').focus();
    });
    update();
  });
})();
