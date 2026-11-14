// artemis. site motion: the Pause motion button (WCAG 2.2.2), the scroll-lit statement, the
// tilting app frame and the glass lens on the final call to action. The marquee is pure CSS.
// No network, no tracking. Reduced motion starts paused; the choice is remembered when storage
// works. Without this script every word is shown fully lit and nothing moves on scroll.
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

  // Scroll-lit statement: split into words (visual only); a hidden copy keeps the text for screen readers.
  var words = [];
  Array.prototype.forEach.call(document.querySelectorAll("[data-light]"), function (p) {
    var text = p.textContent.trim();
    p.textContent = "";
    var sr = document.createElement("span");
    sr.className = "sr";
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

  // The app frame starts tilted back and lies flat as it scrolls into view.
  var frame = document.getElementById("frame");

  function onScroll() {
    var line = window.innerHeight * 0.62;
    words.forEach(function (w) { w.classList.toggle("lit", paused || w.getBoundingClientRect().top < line); });
    if (frame) {
      var top = frame.getBoundingClientRect().top;
      var t = paused ? 0 : Math.max(0, Math.min(1, (top - window.innerHeight * 0.2) / (window.innerHeight * 0.7)));
      frame.style.setProperty("--tilt", (t * 18).toFixed(2) + "deg");
    }
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll);
  onChange.push(onScroll);

  // Glass lens follows the pointer over the final call to action (decorative, hidden when paused).
  var lens = document.querySelector(".lens");
  if (lens) {
    lens.parentElement.addEventListener("pointermove", function (e) {
      if (paused) return;
      var r = lens.parentElement.getBoundingClientRect();
      lens.style.left = e.clientX - r.left + "px";
      lens.style.top = e.clientY - r.top + "px";
    });
  }

  sync();
})();
