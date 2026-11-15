import { useEffect, useRef, useState } from 'react'
import { Fluid, type FluidParams } from './bubble/fluid'
import { LiquidRenderer, type Look } from './bubble/renderer'
import { liquidCursorStore } from '../cursor/liquidCursorSettings'

// Mockups for the Artemis agent bubble: instead of a rigid glass ball, liquid glass held inside an
// invisible circle, with real fluid physics. Drag a bubble (or press Zip / Shake) and the liquid
// sloshes, splits and merges against the invisible wall.

type Variant = {
  id: string
  title: string
  blurb: string
  fluid: FluidParams
  look: Look
  /** splat radius as a multiple of h, and the surface threshold */
  splat: number
  threshold: number
}

const R = 74 // container radius (px)

const VARIANTS: Variant[] = [
  {
    id: 'slosh',
    title: 'Half-full',
    blurb: 'Liquid settles at the bottom of the invisible sphere under gravity. Every move sloshes it up the wall.',
    fluid: { count: 150, h: 16, densityScale: 1.02, stiffness: 0.45, nearStiffness: 1.1, gravity: 0.03, viscosity: 0.03, viscosityQuad: 0.01, swirl: 0, centring: 0, radius: R, wallFriction: 0.02, start: 'bottom' },
    look: { refraction: 30, dispersion: 0.35, tint: [0.86, 0.93, 1.0], highlights: 1.0, thickness: 2.3 },
    splat: 1.5, threshold: 2.6,
  },
  {
    id: 'blob',
    title: 'Floating blob',
    blurb: 'Zero gravity: one cohesive blob drifts and wobbles in the middle, and squashes against the wall when pushed.',
    fluid: { count: 140, h: 16, densityScale: 1.12, stiffness: 0.35, nearStiffness: 0.9, gravity: 0, viscosity: 0.05, viscosityQuad: 0.02, swirl: 0, centring: 0.004, radius: R, wallFriction: 0.05, start: 'centre' },
    look: { refraction: 32, dispersion: 0.45, tint: [0.9, 0.92, 1.0], highlights: 1.1, thickness: 2.6 },
    splat: 1.5, threshold: 2.6,
  },
  {
    id: 'beads',
    title: 'Beads',
    blurb: 'High surface tension: a shake breaks it into droplets that roll around the wall and merge back together.',
    fluid: { count: 120, h: 16, densityScale: 1.3, stiffness: 0.6, nearStiffness: 1.6, gravity: 0.012, viscosity: 0.08, viscosityQuad: 0.02, swirl: 0, centring: 0.002, radius: R, wallFriction: 0.03, start: 'centre' },
    look: { refraction: 28, dispersion: 0.5, tint: [0.95, 0.9, 1.0], highlights: 1.15, thickness: 2.1 },
    splat: 1.35, threshold: 2.4,
  },
  {
    id: 'vortex',
    title: 'Thinking swirl',
    blurb: 'A slow vortex spins the liquid into a ring around the inside of the wall, a possible "working" state.',
    fluid: { count: 150, h: 16, densityScale: 1.05, stiffness: 0.45, nearStiffness: 1.1, gravity: 0, viscosity: 0.03, viscosityQuad: 0.01, swirl: 0.01, centring: 0, radius: R, wallFriction: 0, start: 'centre' },
    look: { refraction: 30, dispersion: 0.4, tint: [0.88, 0.95, 1.0], highlights: 1.0, thickness: 2.3 },
    splat: 1.5, threshold: 2.6,
  },
]

const STEPS_PER_FRAME = 2
const STEP_RATE = 60 * STEPS_PER_FRAME // steps per second
const MAX_ACCEL = 9000 // px/s²: a hard yank is capped here

/** A desktop-like scene with fine detail, so refraction is easy to see. */
function paintBackground(w: number, h: number) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  const c = document.createElement('canvas')
  c.width = Math.round(w * dpr)
  c.height = Math.round(h * dpr)
  const g = c.getContext('2d')!
  g.scale(dpr, dpr)
  const grad = g.createLinearGradient(0, 0, w, h)
  grad.addColorStop(0, '#0b1020')
  grad.addColorStop(0.55, '#141a33')
  grad.addColorStop(1, '#251433')
  g.fillStyle = grad
  g.fillRect(0, 0, w, h)
  g.strokeStyle = 'rgba(255,255,255,0.05)'
  for (let x = 0; x < w; x += 16) { g.beginPath(); g.moveTo(x + 0.5, 0); g.lineTo(x + 0.5, h); g.stroke() }
  for (let y = 0; y < h; y += 16) { g.beginPath(); g.moveTo(0, y + 0.5); g.lineTo(w, y + 0.5); g.stroke() }
  // an editor window
  const wx = w * 0.12, wy = h * 0.14, ww = w * 0.76, wh = h * 0.5
  g.fillStyle = 'rgba(20,24,40,0.92)'
  g.beginPath(); g.roundRect(wx, wy, ww, wh, 10); g.fill()
  g.fillStyle = 'rgba(255,255,255,0.06)'
  g.fillRect(wx, wy, ww, 22)
  ;['#ff5f57', '#febc2e', '#28c840'].forEach((col, i) => { g.fillStyle = col; g.beginPath(); g.arc(wx + 14 + i * 14, wy + 11, 4, 0, 7); g.fill() })
  g.font = '11px ui-monospace, SFMono-Regular, Menlo, monospace'
  const lines: [string, string][] = [
    ['#c792ea', 'const artemis = agent({'], ['#82aaff', '  voice: true,'], ['#c3e88d', "  task: 'hello world',"],
    ['#f78c6c', '  steps: 3,'], ['#c792ea', '})'], ['#89ddff', 'await artemis.run()'], ['#676e95', '// liquid glass ✦'],
  ]
  lines.forEach(([col, text], i) => { g.fillStyle = col; g.fillText(text, wx + 14, wy + 42 + i * 15) })
  // dock icons
  const icons = ['#3b82f6', '#f59e0b', '#10b981', '#ef4444', '#a855f7']
  icons.forEach((col, i) => {
    const x = w / 2 + (i - 2) * 30, y = h - 26
    g.fillStyle = col
    g.beginPath(); g.roundRect(x - 11, y - 11, 22, 22, 6); g.fill()
    g.fillStyle = 'rgba(255,255,255,0.85)'
    g.fillRect(x - 5, y - 1, 10, 2)
  })
  g.fillStyle = 'rgba(255,255,255,0.9)'
  g.font = '600 13px system-ui, sans-serif'
  g.fillText('Artemis', wx + ww - 64, wy + wh - 12)
  return c
}

const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2)

type Controls = { zip(): void; shake(): void; reset(): void }

function BubbleCard({ v, showShell, reduced }: { v: Variant; showShell: boolean; reduced: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const controls = useRef<Controls | null>(null)
  const shellRef = useRef(showShell)
  shellRef.current = showShell
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current!
    let renderer: LiquidRenderer
    try {
      renderer = new LiquidRenderer(canvas)
    } catch (e) {
      setError((e as Error).message)
      return
    }
    const fluid = new Fluid({ ...v.fluid })
    let W = 0, H = 0
    const resize = () => {
      const r = canvas.getBoundingClientRect()
      W = r.width; H = r.height
      renderer.resize(W, H, paintBackground(W, H))
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(canvas)

    // The invisible container: position, velocity and how it is being moved.
    const c = { x: W / 2, y: H / 2, vx: 0, vy: 0, pvx: 0, pvy: 0 }
    let drag: { ox: number; oy: number; px: number; py: number } | null = null
    let zip: { pts: [number, number][]; leg: number; t0: number } | null = null
    const shakes: number[] = []
    const home = () => ({ x: W / 2, y: H / 2 })
    const clampC = (x: number, y: number) => ({
      x: Math.min(Math.max(x, R + 4), W - R - 4),
      y: Math.min(Math.max(y, R + 4), H - R - 4),
    })

    controls.current = {
      zip() {
        const m = R + 8
        zip = { pts: [[W - m, m], [m, H * 0.55], [W - m, H - m], [W / 2, H / 2]], leg: 0, t0: performance.now() }
      },
      shake() {
        const t = performance.now()
        for (let i = 0; i < 6; i++) shakes.push(t + i * 70)
      },
      reset() { fluid.reset(); zip = null },
    }

    const local = (e: PointerEvent) => { const r = canvas.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top } }
    const down = (e: PointerEvent) => {
      const p = local(e)
      if (Math.hypot(p.x - c.x, p.y - c.y) > R + 10) return
      canvas.setPointerCapture(e.pointerId)
      drag = { ox: c.x - p.x, oy: c.y - p.y, px: p.x, py: p.y }
      zip = null
    }
    const move = (e: PointerEvent) => { if (drag) { const p = local(e); drag.px = p.x; drag.py = p.y } }
    const up = () => { drag = null }
    canvas.addEventListener('pointerdown', down)
    canvas.addEventListener('pointermove', move)
    canvas.addEventListener('pointerup', up)
    canvas.addEventListener('pointercancel', up)

    let visible = true
    const io = new IntersectionObserver(([en]) => { visible = en.isIntersecting })
    io.observe(canvas)

    let raf = 0
    let last = performance.now()
    const t0 = last
    const frame = (now: number) => {
      raf = requestAnimationFrame(frame)
      const dt = Math.min(Math.max((now - last) / 1000, 1e-3), 1 / 20)
      last = now
      if (!visible) return

      if (drag) {
        const p = clampC(drag.px + drag.ox, drag.py + drag.oy)
        const nvx = (p.x - c.x) / dt, nvy = (p.y - c.y) / dt
        c.vx += (nvx - c.vx) * 0.5; c.vy += (nvy - c.vy) * 0.5
        c.x = p.x; c.y = p.y
      } else {
        let target = home()
        let k = 110
        if (zip) {
          // like a command: quick eased hops from stop to stop
          const LEG = 520
          const t = Math.max(0, (now - zip.t0) / LEG) // rAF time can precede the click
          const leg = Math.floor(t)
          if (leg >= zip.pts.length) zip = null
          else {
            const a = leg === 0 ? home() : { x: zip.pts[leg - 1][0], y: zip.pts[leg - 1][1] }
            const b = { x: zip.pts[leg][0], y: zip.pts[leg][1] }
            const e = easeInOut(Math.min(t - leg, 1))
            target = { x: a.x + (b.x - a.x) * e, y: a.y + (b.y - a.y) * e }
            k = 700
          }
        } else if (!reduced) {
          // alive at rest: a slow bob
          const s = (now - t0) / 1000
          target = { x: target.x + Math.sin(s * 0.9) * 4, y: target.y + Math.sin(s * 1.3 + 1) * 3 }
        }
        const d = 2 * Math.sqrt(k) * 0.75
        c.vx += (k * (target.x - c.x) - d * c.vx) * dt
        c.vy += (k * (target.y - c.y) - d * c.vy) * dt
        while (shakes.length && shakes[0] <= now) {
          shakes.shift()
          const a = Math.random() * Math.PI * 2
          c.vx += Math.cos(a) * 700; c.vy += Math.sin(a) * 700
        }
        c.x += c.vx * dt; c.y += c.vy * dt
        const cl = clampC(c.x, c.y)
        if (cl.x !== c.x) c.vx = 0
        if (cl.y !== c.y) c.vy = 0
        c.x = cl.x; c.y = cl.y
      }
      let ax = (c.vx - c.pvx) / dt, ay = (c.vy - c.pvy) / dt
      c.pvx = c.vx; c.pvy = c.vy
      const am = Math.hypot(ax, ay)
      if (am > MAX_ACCEL) { ax *= MAX_ACCEL / am; ay *= MAX_ACCEL / am }
      const sx = ax / (STEP_RATE * STEP_RATE), sy = ay / (STEP_RATE * STEP_RATE)
      for (let i = 0; i < STEPS_PER_FRAME; i++) fluid.step(sx, sy)

      const xs = new Float32Array(fluid.n), ys = new Float32Array(fluid.n)
      for (let i = 0; i < fluid.n; i++) { xs[i] = c.x + fluid.x[i]; ys[i] = c.y + fluid.y[i] }
      renderer.draw(xs, ys, v.fluid.h * v.splat, v.threshold, v.look, [c.x, c.y, R, shellRef.current ? 0.35 : 0], (now - t0) / 1000)
    }
    raf = requestAnimationFrame(frame)

    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      io.disconnect()
      canvas.removeEventListener('pointerdown', down)
      canvas.removeEventListener('pointermove', move)
      canvas.removeEventListener('pointerup', up)
      canvas.removeEventListener('pointercancel', up)
      renderer.dispose()
      controls.current = null
    }
  }, [v, reduced])

  return (
    <figure className="overflow-hidden rounded-2xl border border-neutral-300 bg-neutral-950 dark:border-neutral-800">
      {error ? (
        <p className="grid aspect-square place-items-center p-6 text-sm text-neutral-300">Can't draw this mockup: {error}</p>
      ) : (
        <canvas ref={canvasRef} data-variant={v.id} role="img"
          aria-label={`${v.title}: liquid glass inside an invisible circle. Drag it to slosh the liquid.`}
          className="block aspect-square w-full touch-none cursor-grab active:cursor-grabbing" />
      )}
      <figcaption className="space-y-2 p-4 text-neutral-200">
        <h3 className="font-semibold text-white">{v.title}</h3>
        <p className="text-sm text-neutral-400">{v.blurb}</p>
        <div className="flex flex-wrap gap-2 pt-1">
          {(['zip', 'shake', 'reset'] as const).map((a) => (
            <button key={a} type="button" onClick={() => controls.current?.[a]()}
              className="rounded-full border border-neutral-600 px-3 py-1 text-sm capitalize hover:bg-neutral-800 focus-visible:outline-2 focus-visible:outline-amber-400">
              {a === 'zip' ? 'Zip (like a command)' : a}
            </button>
          ))}
        </div>
      </figcaption>
    </figure>
  )
}

export default function BubbleMockups() {
  const [showShell, setShowShell] = useState(false)
  const [reduced, setReduced] = useState(() => matchMedia('(prefers-reduced-motion: reduce)').matches)

  // The site-wide water cursor would ripple these canvases while you drag a bubble: off on this page.
  useEffect(() => {
    const was = liquidCursorStore.get().enabled
    liquidCursorStore.set({ enabled: false })
    return () => liquidCursorStore.set({ enabled: was })
  }, [])

  return (
    <section aria-labelledby="bm-h" className="space-y-5">
      <div>
        <h2 id="bm-h" className="text-xl font-semibold">Agent bubble mockups</h2>
        <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-400">
          Free-form liquid glass held inside an invisible circle, with fluid physics (particles with pressure, surface tension
          and viscosity). Drag a bubble, or press Zip to move it the way a command would.
        </p>
      </div>
      <div className="flex flex-wrap gap-4 text-sm">
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={showShell} onChange={(e) => setShowShell(e.target.checked)} />
          Show the invisible container
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={reduced} onChange={(e) => setReduced(e.target.checked)} />
          Reduce motion (no idle bobbing)
        </label>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        {VARIANTS.map((v) => <BubbleCard key={v.id} v={v} showShell={showShell} reduced={reduced} />)}
      </div>
    </section>
  )
}
