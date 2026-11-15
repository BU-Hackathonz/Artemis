import { useEffect, useMemo, useRef } from 'react'
import { buildLensMap, supportsSvgBackdrop } from './liquidLens'

// A liquid-glass lens as the cursor, modelled on the Dribbble "Liquid Glass" tab-bar shot:
//  - refracts what's underneath (flat centre, compressed + mirrored rim) via an SVG displacement map
//  - splits R/G/B by slightly different amounts for the rainbow fringe on the rim
//  - springs after the pointer, stretches along its velocity, wobbles when it stops
//  - press and drag: the lens "lifts" and flows like water: big viscous stretch, a stream of
//    glass droplets pours out behind it and merges back in when it stops; release snaps back

const SIZE = 112
const BEVEL = 26
const SHIFT = 34
const DROPS = 8

export default function LiquidGlassCursor() {
  const stage = useRef<HTMLDivElement>(null)
  const lens = useRef<HTMLDivElement>(null)
  const drops = useRef<(HTMLDivElement | null)[]>([])
  const maps = useRef<SVGFEDisplacementMapElement[]>([])
  const svgOk = useMemo(supportsSvgBackdrop, [])
  const map = useMemo(() => buildLensMap(SIZE, SIZE, SIZE / 2, BEVEL, SHIFT), [])

  useEffect(() => {
    const el = stage.current!
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches
    const target = { x: el.clientWidth * 0.3, y: el.clientHeight * 0.5 }
    const pos = { ...target }, vel = { x: 0, y: 0 }
    // Squash-and-stretch and press size are their own springs, so they overshoot and wobble.
    const stretch = { v: 0, dv: 0 }, size = { v: 1, dv: 0 }, power = { v: 1, dv: 0 }
    // The droplet stream: each drop is pulled toward the one in front, with its own momentum.
    const stream = Array.from({ length: DROPS }, () => ({ x: target.x, y: target.y, vx: 0, vy: 0 }))
    let pressed = false, inside = false, raf = 0, lastPower = -1

    const spring = (s: { v: number; dv: number }, to: number, k: number, d: number) => {
      s.dv = (s.dv + (to - s.v) * k) * d
      s.v += s.dv
    }

    const move = (e: PointerEvent) => {
      const r = el.getBoundingClientRect()
      target.x = e.clientX - r.left
      target.y = e.clientY - r.top
      inside = true
    }
    const down = (e: PointerEvent) => { pressed = true; move(e) }
    const up = () => { pressed = false }
    const leave = () => { inside = false; pressed = false }
    el.addEventListener('pointermove', move)
    el.addEventListener('pointerdown', down)
    window.addEventListener('pointerup', up)
    el.addEventListener('pointerleave', leave)

    const tick = () => {
      // Pressed = syrupy: weak pull, little friction, so it lags, overshoots and sloshes.
      const k = reduce ? 1 : pressed ? 0.055 : 0.2
      const damp = reduce ? 0 : pressed ? 0.82 : 0.66
      vel.x = (vel.x + (target.x - pos.x) * k) * damp
      vel.y = (vel.y + (target.y - pos.y) * k) * damp
      pos.x += vel.x
      pos.y += vel.y

      const speed = Math.hypot(vel.x, vel.y)
      const angle = Math.atan2(vel.y, vel.x)
      const flow = reduce ? 0 : Math.min(speed / 30, 1.6)
      // Low damping on the stretch spring = jelly wobble when the lens stops.
      spring(stretch, flow, 0.22, 0.82)
      spring(size, pressed ? 1.3 : 1, 0.18, 0.62)
      spring(power, (pressed ? 1.45 : 1) + Math.min(flow, 1) * 0.6, 0.2, 0.7)

      // Stretch along the motion direction without rotating the glass itself.
      const s = stretch.v
      const a = (angle * 180) / Math.PI
      lens.current!.style.transform =
        `translate3d(${pos.x - SIZE / 2}px, ${pos.y - SIZE / 2}px, 0) ` +
        `rotate(${a}deg) scale(${size.v * (1 + s * 0.8)}, ${size.v * Math.max(1 - s * 0.32, 0.45)}) rotate(${-a}deg)`
      lens.current!.style.opacity = inside ? '1' : '0'

      // Droplets: longer, heavier stream while dragging; they shrink into the lens at rest.
      const pull = reduce ? 1 : pressed ? 0.16 : 0.3
      const friction = pressed ? 0.7 : 0.6
      let lead = pos
      stream.forEach((d, i) => {
        d.vx = (d.vx + (lead.x - d.x) * pull) * friction
        d.vy = (d.vy + (lead.y - d.y) * pull) * friction
        d.x += d.vx
        d.y += d.vy
        const gap = Math.hypot(lead.x - d.x, lead.y - d.y)
        lead = d
        const node = drops.current[i]
        if (!node) return
        const base = (pressed ? 0.78 : 0.62) * (1 - i / (DROPS + 2))
        const grown = base * Math.min(gap / 14, 1)        // no gap = merged back into the lens
        const ds = Math.min(Math.hypot(d.vx, d.vy) / 25, 1.2)
        const da = (Math.atan2(d.vy, d.vx) * 180) / Math.PI
        node.style.transform =
          `translate3d(${d.x - SIZE / 2}px, ${d.y - SIZE / 2}px, 0) ` +
          `rotate(${da}deg) scale(${grown * (1 + ds * 0.7)}, ${grown * (1 - ds * 0.25)}) rotate(${-da}deg)`
        node.style.opacity = inside && !reduce && grown > 0.04 ? '1' : '0'
      })

      // Stronger refraction while lifted; the three channels split a little more each.
      if (Math.abs(power.v - lastPower) > 0.01) {
        lastPower = power.v
        maps.current.forEach((m, i) => m?.setAttribute('scale', String(map.scale * power.v * (1 + i * 0.035))))
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(raf)
      el.removeEventListener('pointermove', move)
      el.removeEventListener('pointerdown', down)
      window.removeEventListener('pointerup', up)
      el.removeEventListener('pointerleave', leave)
    }
  }, [map])

  const channel = (i: number, matrix: string) => (
    <>
      <feDisplacementMap
        ref={(n) => { if (n) maps.current[i] = n }}
        in="SourceGraphic" in2="map" scale={map.scale * (1 + i * 0.035)}
        xChannelSelector="R" yChannelSelector="G" result={`d${i}`}
      />
      <feColorMatrix in={`d${i}`} type="matrix" values={matrix} result={`c${i}`} />
    </>
  )

  return (
    <section aria-labelledby="lg-h" className="space-y-4">
      <h2 id="lg-h" className="text-xl font-semibold">Liquid glass cursor</h2>
      <p className="text-sm text-neutral-600 dark:text-neutral-400">
        Move over the stage. Press and drag to pour it like water.
        {!svgOk && ' This browser cannot refract (needs Chromium / WebView2), so it falls back to frosted glass.'}
      </p>

      <svg width="0" height="0" className="absolute" aria-hidden>
        <filter id="liquid-lens" x="0" y="0" width={SIZE} height={SIZE}
          filterUnits="userSpaceOnUse" colorInterpolationFilters="sRGB">
          <feImage href={map.url} x="0" y="0" width={SIZE} height={SIZE} preserveAspectRatio="none" result="map" />
          {channel(0, '1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0')}
          {channel(1, '0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0')}
          {channel(2, '0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0')}
          <feBlend in="c0" in2="c1" mode="screen" result="rg" />
          <feBlend in="rg" in2="c2" mode="screen" />
        </filter>
      </svg>

      <div
        ref={stage}
        aria-hidden
        data-no-liquid-cursor className="relative h-[28rem] cursor-none touch-none overflow-hidden rounded-3xl bg-[#f2f2f2] select-none"
      >
        <Scene />
        {Array.from({ length: DROPS }, (_, i) => (
          <div key={i} ref={(n) => { drops.current[i] = n }} className={glassClass} style={glassStyle(svgOk, true)} />
        ))}
        <div ref={lens} className={glassClass} style={glassStyle(svgOk, false)} />
      </div>
    </section>
  )
}

const glassClass = 'pointer-events-none absolute top-0 left-0 rounded-full opacity-0 will-change-transform'

const glassStyle = (svgOk: boolean, drop: boolean): React.CSSProperties => ({
  width: SIZE,
  height: SIZE,
  backdropFilter: svgOk ? 'url(#liquid-lens) saturate(1.15) brightness(1.04)' : 'blur(8px) saturate(1.3)',
  background: 'radial-gradient(120% 120% at 30% 20%, rgba(255,255,255,.28), rgba(255,255,255,.04) 55%)',
  boxShadow:
    'inset 0 1px 1.5px rgba(255,255,255,.95), inset 0 -1px 2px rgba(0,0,0,.08), inset 0 0 0 1px rgba(255,255,255,.55), ' +
    (drop ? '0 6px 14px rgba(0,0,0,.08)' : '0 12px 28px rgba(0,0,0,.12), 0 2px 6px rgba(0,0,0,.06)'),
})

// Something worth refracting: the tab bar from the reference shot, plus colour and text.
function Scene() {
  const tabs: [string, React.ReactNode][] = [
    ['Home', <path key="h" d="M12 3 3 10v10h6v-6h6v6h6V10z" />],
    ['New', <g key="n"><rect x="3" y="3" width="8" height="8" rx="2" /><rect x="13" y="3" width="8" height="8" rx="2" /><rect x="3" y="13" width="8" height="8" rx="2" /><rect x="13" y="13" width="8" height="8" rx="2" /></g>],
    ['Radio', <g key="r" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><circle cx="12" cy="12" r="1.6" fill="currentColor" /><path d="M8 8a6 6 0 0 0 0 8M16 8a6 6 0 0 1 0 8M5 5a10 10 0 0 0 0 14M19 5a10 10 0 0 1 0 14" /></g>],
    ['Library', <g key="l"><rect x="4" y="4" width="16" height="16" rx="4" /><path d="M13 7v6.5a2 2 0 1 1-1.5-1.9V7z" fill="#f2f2f2" /></g>],
  ]
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-10 text-neutral-700">
      <div className="pointer-events-none absolute -top-10 -left-10 size-56 rounded-full bg-pink-300/60 blur-2xl" />
      <div className="pointer-events-none absolute right-6 bottom-6 size-48 rounded-full bg-sky-300/60 blur-2xl" />
      <p className="relative max-w-md text-center text-2xl font-semibold tracking-tight text-neutral-800">
        Hey Artemis, run the tests and read me the failures.
      </p>
      <nav className="relative flex gap-2 rounded-full bg-white/90 px-5 py-3 shadow-[0_18px_40px_rgba(0,0,0,.10)]">
        {tabs.map(([name, icon], i) => (
          <span key={name} className={`flex w-20 flex-col items-center gap-1 text-sm font-medium ${i === 0 ? 'text-rose-600' : ''}`}>
            <svg viewBox="0 0 24 24" className="size-8" fill="currentColor">{icon}</svg>
            {name}
          </span>
        ))}
      </nav>
      <div className="relative flex gap-3">
        {['bg-rose-400', 'bg-amber-300', 'bg-emerald-300', 'bg-sky-400', 'bg-violet-400'].map((c) => (
          <span key={c} className={`size-10 rounded-xl ${c}`} />
        ))}
      </div>
    </div>
  )
}
