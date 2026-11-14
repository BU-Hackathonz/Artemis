import {useEffect, useRef} from 'react';
import {platform} from './platform';
import type {AgentStatus} from './platform';

// The flow: a transparent, click-through window over the primary screen's work area (label
// "flow", index.html#flow), shown only while Artemis works. The glassy droplet leaves the island's
// ring, wanders the screen leaving a glowing trail, and flies back into the ring when the task
// ends; then the window hides. It never takes clicks or focus. Decorative: the main window
// announces everything.

type Pt = {x: number; y: number};
const BUSY = new Set(['queued', 'working', 'running']);

export function FlowWindow() {
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    document.documentElement.classList.add('agent-page');
    if (!platform.native) return () => document.documentElement.classList.remove('agent-page');
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
    let active = true, raf = 0, shown = false, allowed = true, busy = false;
    let origin = {x: 0, y: 0}, ring: Pt = {x: innerWidth / 2 - 76, y: 36};
    let pos: Pt = {...ring}, t = 0, last = performance.now(), back = 0;
    const trail: Pt[] = [];
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
        ring = {x: (p.x - origin.x) / scale + 28, y: (p.y - origin.y) / scale + 28};
      }
      const update = async (s: AgentStatus) => {
        const next = allowed && !reduced && BUSY.has(s.taskState);
        if (next && !busy) { await findRing(); pos = {...ring}; trail.length = 0; t = 0; back = 0; if (!shown) { shown = true; await me.show(); } }
        if (!next && busy) back = performance.now();
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
      if (!c || !shown) return;
      const dt = Math.min((now - last) / 1000, 0.05); last = now;
      const dpr = window.devicePixelRatio || 1;
      if (c.width !== Math.round(innerWidth * dpr)) { c.width = Math.round(innerWidth * dpr); c.height = Math.round(innerHeight * dpr); }
      const g = c.getContext('2d')!;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, innerWidth, innerHeight);
      t += dt;
      if (busy) {
        // a slow, looping wander across the screen
        const target = {x: innerWidth * (0.5 + 0.38 * Math.sin(t * 0.55)), y: innerHeight * (0.45 + 0.3 * Math.sin(t * 0.83 + 1.2))};
        const k = Math.min(1, dt * 2.2);
        pos = {x: pos.x + (target.x - pos.x) * k, y: pos.y + (target.y - pos.y) * k};
      } else {
        const p = Math.min((now - back) / 800, 1);
        const e = p < .5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
        pos = {x: pos.x + (ring.x - pos.x) * e, y: pos.y + (ring.y - pos.y) * e};
        if (p >= 1 && trail.length === 0) { shown = false; void import('@tauri-apps/api/window').then(w => w.getCurrentWindow().hide()); return; }
      }
      trail.push({...pos});
      if (trail.length > 40 || (!busy && trail.length)) trail.splice(0, busy ? 1 : 2);
      // trail: fading glow along the path
      g.lineCap = 'round'; g.lineJoin = 'round';
      for (let i = 1; i < trail.length; i++) {
        const a = i / trail.length;
        g.strokeStyle = `rgba(192, 132, 252, ${0.45 * a})`;
        g.lineWidth = 2 + 10 * a;
        g.shadowColor = 'rgba(168, 85, 247, 0.8)'; g.shadowBlur = 14 * a;
        g.beginPath(); g.moveTo(trail[i - 1].x, trail[i - 1].y); g.lineTo(trail[i].x, trail[i].y); g.stroke();
      }
      // the droplet: glassy, lit from the top left
      const r = 13;
      const grad = g.createRadialGradient(pos.x - r * 0.35, pos.y - r * 0.4, 1, pos.x, pos.y, r);
      grad.addColorStop(0, 'rgba(255,255,255,0.95)');
      grad.addColorStop(0.3, 'rgba(233,213,255,0.9)');
      grad.addColorStop(0.75, 'rgba(192,132,252,0.85)');
      grad.addColorStop(1, 'rgba(217,70,239,0.55)');
      g.shadowColor = 'rgba(168, 85, 247, 0.9)'; g.shadowBlur = 26;
      g.fillStyle = grad;
      g.beginPath(); g.arc(pos.x, pos.y, r, 0, Math.PI * 2); g.fill();
      g.shadowBlur = 0;
    };
    raf = requestAnimationFrame(frame);
    return () => { active = false; cancelAnimationFrame(raf); off.forEach(fn => fn()); document.documentElement.classList.remove('agent-page'); };
  }, []);

  return <canvas ref={canvas} aria-hidden="true" style={{position: 'fixed', inset: 0, width: '100vw', height: '100vh'}}/>;
}
