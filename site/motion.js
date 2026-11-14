// artemis. site motion: the Pause motion button (WCAG 2.2.2), the home page marquee and the
// scroll-lit statement. No network, no tracking. Reduced motion starts paused; the choice is
// remembered when storage works. Without this script every word is shown fully lit.
(function () {
  "use strict";
  var root = document.documentElement;
  root.classList.remove("no-js");

  var reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  var saved = null;
  try { saved = localStorage.getItem("artemis-motion"); } catch (e) { /* storage blocked: use the default */ }
  var paused = saved ? saved === "paused" : reduce;
  var onChange = [];

  var button = document.querySelector(".pause");
  function sync() {
    root.classList.toggle("motion-paused", paused);
    if (button) {
      button.setAttribute("aria-pressed", String(paused));
      button.textContent = paused ? "Play motion" : "Pause motion";
    }
    onChange.forEach(function (f) { f(); });
  }
  if (button) {
    button.addEventListener("click", function () {
      paused = !paused;
      try { localStorage.setItem("artemis-motion", paused ? "paused" : "playing"); } catch (e) { /* ignore */ }
      sync();
    });
  }

  // Scroll-lit statement: split into words (visual only); the paragraph keeps its text for screen readers.
  var statements = document.querySelectorAll("[data-light]");
  var words = [];
  Array.prototype.forEach.call(statements, function (p) {
    var text = p.textContent.trim();
    p.textContent = "";
    var sr = document.createElement("span");
    sr.className = "visually-hidden";
    sr.textContent = text;
    p.appendChild(sr);
    var vis = document.createElement("span");
    vis.setAttribute("aria-hidden", "true");
    text.split(/\s+/).forEach(function (w, i) {
      if (i) vis.appendChild(document.createTextNode(" "));
      var s = document.createElement("span");
      s.className = "w";
      s.textContent = w;
      vis.appendChild(s);
      words.push(s);
    });
    p.appendChild(vis);
  });
  function light() {
    if (paused) { words.forEach(function (w) { w.classList.add("lit"); }); return; }
    var line = window.innerHeight * 0.62;
    words.forEach(function (w) { w.classList.toggle("lit", w.getBoundingClientRect().top < line); });
  }
  if (words.length) {
    window.addEventListener("scroll", light, { passive: true });
    window.addEventListener("resize", light);
    onChange.push(light);
  }

  // Marquee: two rows of spoken lines drift in opposite directions and speed up with scrolling.
  var tracks = document.querySelectorAll(".track");
  var lines = [
    ["You say", "create a Python program"],
    ["Artemis", "Heard: create a Python program"],
    ["Artemis", "Waiting for your approval"],
    ["Artemis", "No security findings"],
    ["Artemis", "It printed: Hello, World!"],
    ["Artemis", "Exit code 1. NameError, line 1"],
    ["Artemis", "1 high-severity issue, line 4"],
    ["You press", "Escape"]
  ];
  Array.prototype.forEach.call(tracks, function (track, i) {
    var set = i ? lines.slice().reverse() : lines;
    for (var copy = 0; copy < 2; copy++) {
      set.forEach(function (l) {
        var item = document.createElement("span");
        var who = document.createElement("b");
        who.textContent = l[0];
        item.appendChild(who);
        item.appendChild(document.createTextNode(" " + l[1]));
        track.appendChild(item);
        var sep = document.createElement("span");
        sep.className = "sep";
        track.appendChild(sep);
      });
    }
  });
  var pos = [0, 0], vel = 0, lastY = window.scrollY;
  if (tracks.length) {
    window.addEventListener("scroll", function () { vel += (window.scrollY - lastY) * 0.6; lastY = window.scrollY; }, { passive: true });
    (function frame() {
      if (!paused) {
        vel *= 0.92;
        Array.prototype.forEach.call(tracks, function (t, i) {
          var dir = i ? -1 : 1, half = t.scrollWidth / 2;
          pos[i] -= dir * (0.5 + Math.min(Math.abs(vel), 40) * 0.35);
          if (pos[i] <= -half) pos[i] += half;
          if (pos[i] > 0) pos[i] -= half;
          t.style.transform = "translateX(" + pos[i] + "px)";
        });
      }
      requestAnimationFrame(frame);
    })();
  }

  sync();
  light();
})();
