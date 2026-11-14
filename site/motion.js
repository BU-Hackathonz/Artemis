// artemis. site motion: the Pause motion button (WCAG 2.2.2) and the home page marquee.
// No network, no tracking. Reduced motion starts paused; the choice is remembered when storage works.
(function () {
  "use strict";
  var root = document.documentElement;
  root.classList.remove("no-js");

  var reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  var saved = null;
  try { saved = localStorage.getItem("artemis-motion"); } catch (e) { /* storage blocked: use the default */ }
  var paused = saved ? saved === "paused" : reduce;

  var button = document.querySelector(".pause");
  function sync() {
    root.classList.toggle("motion-paused", paused);
    if (button) {
      button.setAttribute("aria-pressed", String(paused));
      button.textContent = paused ? "Play motion" : "Pause motion";
    }
  }
  if (button) {
    button.addEventListener("click", function () {
      paused = !paused;
      try { localStorage.setItem("artemis-motion", paused ? "paused" : "playing"); } catch (e) { /* ignore */ }
      sync();
    });
  }
  sync();

  // Marquee: two rows of spoken lines drift in opposite directions and speed up with scrolling.
  var tracks = document.querySelectorAll(".track");
  if (!tracks.length) return;
  var lines = [
    ["You say", "create a Python program that prints Hello World"],
    ["Artemis", "Heard: create a Python program that prints Hello World"],
    ["Artemis", "Waiting for your approval to create main.py"],
    ["You press", "Enter"],
    ["Artemis", "Run main.py with Python. No security findings."],
    ["Artemis", "It printed: Hello, World!"],
    ["Artemis", "main.py failed with exit code 1. NameError, line 1."],
    ["Artemis", "1 high-severity issue in main.py, line 4"]
  ];
  Array.prototype.forEach.call(tracks, function (track, i) {
    var set = (i ? lines.slice().reverse() : lines);
    for (var copy = 0; copy < 2; copy++) {
      set.forEach(function (l) {
        var chip = document.createElement("span");
        chip.className = "chip glass";
        var who = document.createElement("b");
        who.textContent = l[0];
        chip.appendChild(who);
        chip.appendChild(document.createTextNode(" " + l[1]));
        track.appendChild(chip);
      });
    }
  });
  var pos = [0, 0], vel = 0, lastY = window.scrollY;
  window.addEventListener("scroll", function () { vel += (window.scrollY - lastY) * 0.6; lastY = window.scrollY; }, { passive: true });
  function frame() {
    if (!paused) {
      vel *= 0.92;
      Array.prototype.forEach.call(tracks, function (t, i) {
        var dir = i ? -1 : 1, half = t.scrollWidth / 2;
        pos[i] -= dir * (0.6 + Math.min(Math.abs(vel), 40) * 0.4);
        if (pos[i] <= -half) pos[i] += half;
        if (pos[i] > 0) pos[i] -= half;
        var skew = Math.max(-8, Math.min(8, vel * -0.3 * dir));
        t.style.transform = "translateX(" + pos[i] + "px) skewX(" + skew + "deg)";
      });
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
})();
