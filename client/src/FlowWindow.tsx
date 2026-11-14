import {useEffect, useRef} from 'react';
import {platform} from './platform';
import type {FlowState} from './platform';
import {ISLAND_MOTION, createIslandMotion, stepIslandMotion} from './motion';
import type {IslandFrame} from './motion';
import {chooseTarget, flowMode} from './flowPath';

// The flow: a transparent, click-through window over the primary screen's work area (label
// "flow", index.html#flow), created only with the compact notch and shown only while Artemis works.
// The notch's ring collapses into a glassy droplet that leaves it, follows the text caret when
// there is one (otherwise a slow path across the screen) leaving a short trail, then flies back and
// lands with a squash and a wobble; the window then hides. All movement comes from the motion model
// (motion.ts, the numbers tuned in the GNOME prototype). It never takes clicks or focus. Decoration
// only: the status text, speech and approvals are elsewhere, and while an approval waits the
// droplet comes home and holds still.

type Pt = {x: number; y: number};
const R = 13; // droplet radius, px
const POLL_MS = 100;
const CARET_MS = 120;

function drawTrail(g: CanvasRenderingContext2D, f: IslandFrame) {
  g.lineCap = 'round'; g.lineJoin = 'round';
  for (let i = 1; i < f.trail.length; i++) {
    const a = Math.max(0, 1 - f.trail[i].age / ISLAND_MOTION.TRAIL_LIFE);
    g.strokeStyle = `rgba(192, 132, 252, ${0.5 * a})`;
    g.lineWidth = 2 + 9 * a;
    g.shadowColor = 'rgba(168, 85, 247, 0.8)'; g.shadowBlur = 14 * a;
    g.beginPath(); g.moveTo(f.trail[i - 1].x, f.trail[i - 1].y); g.lineTo(f.trail[i].x, f.trail[i].y); g.stroke();
  }
  g.shadowBlur = 0;
}

// The droplet: glassy, lit from the top left. It grows out of the ring as the ring collapses
// (morph), stretches along its direction of travel, and wobbles while it is lively.
function drawDroplet(g: CanvasRenderingContext2D, f: IslandFrame, t: number) {
  if (f.morph <= 0.02) return;
  const wobble = 1 + 0.07 * f.energy * Math.sin(t * 19);
  const r = R * (0.45 + 0.55 * f.morph) * f.scale * wobble;
  const along = r * (1 + f.stretch), across = r / Math.sqrt(1 + f.stretch);
  g.save();
  g.translate(f.x, f.y);
  g.rotate(Math.atan2(f.dirY, f.dirX));
  g.globalAlpha = Math.min(1, f.morph * 1.4);
  const grad = g.createRadialGradient(-along * 0.3, -across * 0.4, 1, 0, 0, Math.max(along, across));
  grad.addColorStop(0, 'rgba(255,255,255,0.95)');
  grad.addColorStop(0.3, 'rgba(233,213,255,0.9)');
  grad.addColorStop(0.75, 'rgba(192,132,252,0.85)');
  grad.addColorStop(1, 'rgba(217,70,239,0.55)');
  g.shadowColor = 'rgba(168, 85, 247, 0.9)'; g.shadowBlur = 26;
  g.fillStyle = grad;
  g.beginPath(); g.ellipse(0, 0, along, across, 0, 0, Math.PI * 2); g.fill();
  g.restore();
}

export function FlowWindow() {
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    document.documentElement.classList.add('flow-page');
    if (!platform.native) return () => document.documentElement.classList.remove('flow-page');
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
    let active = true, raf = 0, shown = false, busy = false, approval = false;
    let ring: Pt | null = null, caret: Pt | null = null, origin: Pt = {x: 0, y: 0}, scale = 1;
    let motion = createIslandMotion({x: innerWidth / 2, y: 24}), t = 0, last = performance.now();
    const timers: number[] = [];

    const setVisible = (visible: boolean) => {
      if (visible === shown) return;
      shown = visible;
      void platform.setFlowVisible(visible).catch(() => { shown = !visible; });
    };

    async function poll() {
      try {
        const s: FlowState = await platform.getFlowState();
        approval = s.approval;
        origin = {x: s.origin[0], y: s.origin[1]}; scale = s.scale || 1;
        ring = s.ring ? {x: s.ring[0], y: s.ring[1]} : null;
        const next = !reduced && ring !== null && s.busy;
        if (next && !busy) { motion = createIslandMotion(ring!); t = 0; setVisible(true); }
        busy = next;
      } catch { /* the notch or core is not ready: try again */ }
    }
    async function askCaret() {
      if (!busy) { caret = null; return; }
      const at = await platform.caretPosition();
      caret = at ? {x: (at.x - origin.x) / scale, y: (at.y - origin.y) / scale} : null;
    }
    timers.push(window.setInterval(() => void poll(), POLL_MS));
    timers.push(window.setInterval(() => void askCaret(), CARET_MS));
    void poll();

    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      const c = canvas.current;
      if (!c || !shown || !ring) { last = now; return; }
      const dt = Math.min((now - last) / 1000, 0.05); last = now;
      const dpr = window.devicePixelRatio || 1;
      if (c.width !== Math.round(innerWidth * dpr)) { c.width = Math.round(innerWidth * dpr); c.height = Math.round(innerHeight * dpr); }
      const g = c.getContext('2d')!;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, innerWidth, innerHeight);
      t += dt;
      const f = stepIslandMotion(motion, {
        mode: flowMode({approval, busy}),
        home: ring,
        target: busy ? chooseTarget(caret, t, innerWidth, innerHeight) : null,
        reducedMotion: reduced,
        enabled: true,
      }, dt);
      if (!busy && f.settled) { setVisible(false); return; } // home, landed, calm: nothing left to draw
      drawTrail(g, f);
      drawDroplet(g, f, t);
    };
    raf = requestAnimationFrame(frame);
    return () => {
      active = false; cancelAnimationFrame(raf); timers.forEach(id => window.clearInterval(id));
      document.documentElement.classList.remove('flow-page');
      if (shown) void platform.setFlowVisible(false).catch(() => {});
    };
  }, []);

  return <canvas ref={canvas} aria-hidden="true" style={{position: 'fixed', inset: 0, width: '100vw', height: '100vh'}}/>;
}
