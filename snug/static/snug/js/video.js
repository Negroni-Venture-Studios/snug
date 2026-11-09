// The video player: time watched in parts, and a full screen that keeps the
// watermark. Wired with addEventListener (the CSP forbids inline handlers).
// Without JavaScript the video still plays; only the counting and the
// watermarked full screen need this.
'use strict';

(function () {
  var player = document.querySelector('.player[data-visit]');
  if (!player) return;
  var video = player.querySelector('video');
  var frame = player.querySelector('.video-frame');
  var token = player.querySelector('input[name="csrfmiddlewaretoken"]');
  var partSeconds = parseInt(player.dataset.partSeconds, 10) || 10;
  var part = 1;

  var send = function (kind) {
    if (!navigator.sendBeacon) return;
    var body = new URLSearchParams({
      visit: player.dataset.visit,
      page: String(part),
      kind: kind,
      csrfmiddlewaretoken: token ? token.value : ''
    });
    navigator.sendBeacon(player.dataset.beatUrl, body);
  };

  // Each part is a "page": Snug credits time to the part playing. The clock
  // runs only while the video plays and the tab is visible.
  var playing = function () {
    return !video.paused && !video.ended && document.visibilityState === 'visible';
  };
  video.addEventListener('timeupdate', function () {
    var now = Math.floor(video.currentTime / partSeconds) + 1;
    if (now !== part) {
      part = now;
      if (playing()) send('part');
    }
  });
  video.addEventListener('play', function () { send('part'); send('resume'); });
  video.addEventListener('pause', function () { send('end'); });
  video.addEventListener('ended', function () { send('end'); });
  var every = (parseInt(player.dataset.interval, 10) || 15) * 1000;
  setInterval(function () {
    if (playing()) send('beat');
  }, every);
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState !== 'visible') send('end');
    else if (playing()) send('resume');
  });
  window.addEventListener('pagehide', function () { send('end'); });

  // Full screen for the frame, not the bare video, so the watermark goes too.
  var button = player.querySelector('.video-fullscreen');
  if (button && frame.requestFullscreen) {
    button.hidden = false;
    button.addEventListener('click', function () {
      if (document.fullscreenElement) document.exitFullscreen();
      else frame.requestFullscreen();
    });
  }
})();
