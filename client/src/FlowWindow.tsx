import {useEffect, useRef} from 'react';
import {platform} from './platform';
import type {AgentStatus} from './platform';
import {ISLAND_MOTION, createIslandMotion, stepIslandMotion} from './motion';
import type {IslandFrame} from './motion';
import {flowMode, wanderTarget} from './flowPath';

// The flow: a transparent, click-through window over the primary screen's work area (label
// "flow", index.html#flow), shown only while Artemis works. The island's ring collapses into a
// glassy droplet that leaves it, follows a slow path across the screen leaving a short trail, and
// flies back into the ring, landing with a squash and a wobble; then the window hides. All of the
// movement comes from the motion model (motion.ts, the numbers tuned in the GNOME prototype). It
// never takes clicks or focus, and it is decoration only: the main window announces everything,
// and while an approval waits the droplet comes home and holds still.

type Pt = {x: number; y: number};
const BUSY = new Set(['queued', 'working', 'running']);
const R = 13; // droplet radius, px

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
    document.documentElement.classList.add('agent-page');
    if (!platform.native) return () => document.documentElement.classList.remove('agent-page');
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
    let active = true, raf = 0, shown = false, allowed = true, busy = false, approval = false;
    let origin = {x: 0, y: 0}, ring: Pt = {x: innerWidth / 2 - 76, y: 36};
    let motion = createIslandMotion(ring), t = 0, last = performance.now();
    const off: (() => void)[] = [];

    void (async () => {
      const win = await import('@tauri-apps/api/window');
      const me = win.getCurrentWindow();
      await me.setIgnoreCursorEvents(true);
      const monitor = (await win.primaryMonitor()) ?? (await win.currentMonitor());
      if (monitor) {
        const a = monitor.workArea;
        origin = {x: a.position.x, y: a.position.y};
        await me.setPosition(new win.PhysicalPosition(a.position.x, a.position.y));
        await me.setSize(new win.PhysicalSize(a.size.width, a.size.height));
      }
      // Where the island's ring is right now (the island can be dragged).
      async function findRing() {
        const island = await win.Window.getByLabel('agent');
        if (!island) return;
        const p = await island.outerPosition();
        const scale = await me.scaleFactor();
        ring = {x: (p.x - origin.x) / scale + 22, y: (p.y - origin.y) / scale + 24};
      }
      const update = async (s: AgentStatus) => {
        approval = s.approval;
        const next = allowed && !reduced && BUSY.has(s.taskState);
        if (next && !busy) {
          await findRing();
          motion = createIslandMotion(ring); t = 0;
          if (!shown) { shown = true; await me.show(); }
        }
        busy = next;
      };
      const keep = (fn: () => void) => { if (active) off.push(fn); else fn(); };
      keep(await platform.agentWindow.onStatus(s => { void update(s); }));
      keep(await platform.agentWindow.onVisible(v => { allowed = v; if (!v) { busy = false; shown = false; void me.hide(); } }));
      await platform.agentWindow.ready();
    })();

    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      const c = canvas.current;
      if (!c || !shown) { last = now; return; }
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
        target: busy ? wanderTarget(t, innerWidth, innerHeight) : null,
        reducedMotion: reduced,
        enabled: allowed,
      }, dt);
      if (!busy && f.settled) {
        // Home, landed and calm: nothing left to draw, so the window goes away.
        shown = false;
        void import('@tauri-apps/api/window').then(w => w.getCurrentWindow().hide());
        return;
      }
      drawTrail(g, f);
      drawDroplet(g, f, t);
    };
    raf = requestAnimationFrame(frame);
    return () => { active = false; cancelAnimationFrame(raf); off.forEach(fn => fn()); document.documentElement.classList.remove('agent-page'); };
  }, []);

  return <canvas ref={canvas} aria-hidden="true" style={{position: 'fixed', inset: 0, width: '100vw', height: '100vh'}}/>;
}
