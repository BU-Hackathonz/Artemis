import { Fragment, useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import { buildBallMap, buildDropletMap, canRefract } from './dropletMap'
import { liquidCursorStore } from './liquidCursorSettings'
import { springTo, type Spring } from './spring'
import WaterCursor from './WaterCursor'

// Site-wide liquid-glass cursor that refracts the real page under it.
//
// Rendering: one fixed, pointer-events:none element with backdrop-filter: url(#filter). The SVG
// filter displaces the live backdrop with a precomputed lens map, so nothing is captured or
// re-rendered; per frame we only write a transform. Because the map lives in the element's local
// space, stretching the element stretches the distortion with it. Red, green and blue are displaced
// separately (slightly different strengths) for the colour fringe on hard edges.
//
// Two variants (see liquidCursorSettings.ts):
//   droplet: the head is pinned to the pointer and replaces the arrow. Speed stretches it; the extra
//            length goes behind, so the trailing edge lags.
//   ball:    a glass ball lens (ray-traced map) that trails the still-visible arrow on a 2D spring
//            and grows in/out when the pointer enters/leaves.
// All springs are integrated in fixed 1/240 s substeps for frame-rate independence.

const FILTER_ID = 'artemis-liquid-cursor'
const NATIVE = 'input, textarea, select, [contenteditable=""], [contenteditable="true"], [data-native-cursor], [data-no-liquid-cursor]'
const CHANNELS = [
  '1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0',
  '0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0',
  '0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0',
]

export default function LiquidCursor() {
  const settings = useSyncExternalStore(liquidCursorStore.subscribe, liquidCursorStore.get)
  const finePointer = useSyncExternalStore(subscribeMedia('(pointer: fine)'), () => matchMedia('(pointer: fine)').matches)
  const active = settings.enabled && finePointer
  const ball = settings.variant === 'ball'
  const water = settings.variant === 'water'
  const reducedMotion = useSyncExternalStore(subscribeMedia('(prefers-reduced-motion: reduce)'), () => matchMedia('(prefers-reduced-motion: reduce)').matches)
  const refracts = useMemo(canRefract, [])
  const map = useMemo(() => (active && !water ? (ball ? buildBallMap() : buildDropletMap()) : null), [active, ball, water])

  const drop = useRef<HTMLDivElement>(null)
  const disp = useRef<(SVGFEDisplacementMapElement | null)[]>([])
  const live = useRef(settings)
  live.current = settings
  const mapMax = useRef(1)
  mapMax.current = map?.max ?? 1

  useEffect(() => {
    if (!active || water) return
    const el = drop.current!
    const root = document.documentElement
    if (!ball) root.classList.add('liquid-cursor') // the droplet replaces the arrow; the ball keeps it
    const reduce = matchMedia('(prefers-reduced-motion: reduce)')

    const ptr = { x: -100, y: -100, t: 0 }
    const raw = { x: 0, y: 0 }      // latest measured pointer velocity (px/s)
    const vel = { x: 0, y: 0 }      // smoothed
    const dir = { x: 1, y: 0 }      // last travel direction
    const cx: Spring = { x: -100, v: 0 }, cy: Spring = { x: -100, v: 0 } // ball centre
    const stretch: Spring = { x: 0, v: 0 }
    const press: Spring = { x: 0, v: 0 }
    const appear: Spring = { x: 0, v: 0 }
    let pressed = false, visible = false, overNative = false, last = performance.now(), raf = 0
    const lastScale = [-1, -1, -1]

    const onMove = (e: PointerEvent) => {
      if (e.pointerType !== 'mouse') { visible = false; return } // touch and pen keep the native behaviour
      const dt = (e.timeStamp - ptr.t) / 1000
      if (ptr.t && dt > 0 && dt < 0.1) { raw.x = (e.clientX - ptr.x) / dt; raw.y = (e.clientY - ptr.y) / dt }
      ptr.x = e.clientX; ptr.y = e.clientY; ptr.t = e.timeStamp
      if (!visible && appear.x < 0.05) { cx.x = ptr.x; cy.x = ptr.y; cx.v = cy.v = 0 } // re-enter where the pointer is
      visible = true
      overNative = !!(e.target as Element | null)?.closest?.(NATIVE)
    }
    const onDown = (e: PointerEvent) => { if (e.pointerType === 'mouse') pressed = true }
    const onUp = () => { pressed = false }
    const onOut = (e: PointerEvent) => { if (!e.relatedTarget) visible = false }
    const onBlur = () => { visible = false; pressed = false }
    addEventListener('pointermove', onMove, { passive: true })
    addEventListener('pointerdown', onDown, { passive: true })
    addEventListener('pointerup', onUp, { passive: true })
    addEventListener('pointercancel', onUp, { passive: true })
    document.addEventListener('pointerout', onOut, { passive: true })
    addEventListener('blur', onBlur)

    const frame = (now: number) => {
      const dt = Math.min((now - last) / 1000, 0.05)
      last = now
      const { size, refraction, stretch: maxStretch, stiffness, damping, dispersion } = live.current
      const R = size / 2
      const still = reduce.matches
      const show = visible && !overNative

      // Motion source: the pointer itself (droplet) or the ball's trailing centre.
      let speed: number
      if (ball) {
        if (still) { cx.x = ptr.x; cy.x = ptr.y; cx.v = cy.v = 0 }
        else { springTo(cx, ptr.x, stiffness, damping, dt); springTo(cy, ptr.y, stiffness, damping, dt) }
        vel.x = cx.v; vel.y = cy.v
        speed = Math.hypot(vel.x, vel.y)
      } else {
        // Velocity: decays to zero once the pointer stops sending events (a hard stop).
        if (performance.now() - ptr.t > 40) { raw.x = 0; raw.y = 0 }
        const a = 1 - Math.exp(-dt / 0.035)
        vel.x += (raw.x - vel.x) * a
        vel.y += (raw.y - vel.y) * a
        speed = Math.hypot(vel.x, vel.y)
      }
      if (speed > 40) { dir.x = vel.x / speed; dir.y = vel.y / speed }

      // Stretch target saturates smoothly at maxStretch, so fast flicks keep their shape.
      const target = still ? 0 : maxStretch * (1 - Math.exp(-speed / 1100))
      if (still) {
        stretch.x = 0; stretch.v = 0; press.x = pressed ? 1 : 0; press.v = 0; appear.x = show ? 1 : 0; appear.v = 0
      } else {
        springTo(stretch, target, stiffness, damping, dt)
        springTo(press, pressed ? 1 : 0, stiffness, damping, dt)
        springTo(appear, show ? 1 : 0, 260, 26, dt)
      }

      // Long along travel, thin across it. A negative stretch (the overshoot after a stop) briefly
      // squashes it the other way. Hard cap: the spring may overshoot its target, the shape may not.
      const s = Math.min(stretch.x, maxStretch)
      const along = Math.max(1 + s, 0.7)
      const across = Math.min(Math.max(1 - 0.45 * s, 0.6), 1.25)
      const widen = 1 + 0.14 * press.x                 // pressing flattens and widens it
      const grow = ball ? Math.max(appear.x, 0) : 1     // the ball grows in and out
      const ang = (Math.atan2(dir.y, dir.x) * 180) / Math.PI
      let x: number, y: number
      if (ball) { x = cx.x - R; y = cy.x - R }
      else {
        // Keep the head on the pointer: extra length goes behind it, so the tail lags.
        const back = R * (along - 1) * widen
        x = ptr.x - dir.x * back - R
        y = ptr.y - dir.y * back - R
      }
      el.style.transform =
        `translate3d(${x}px, ${y}px, 0) rotate(${ang}deg) scale(${along * widen * grow}, ${across * widen * grow}) rotate(${-ang}deg)`
      el.style.opacity = ball ? (appear.x > 0.02 ? '1' : '0') : show ? '1' : '0'

      // A flatter (pressed) lens bends less. Red bends least, blue most.
      const base = 2 * mapMax.current * R * refraction * (1 - 0.35 * press.x)
      for (let i = 0; i < 3; i++) {
        const v = base * (1 + (i - 1) * dispersion)
        const node = disp.current[i]
        if (node && Math.abs(v - lastScale[i]) > 0.02) { node.setAttribute('scale', v.toFixed(2)); lastScale[i] = v }
      }
      raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)

    return () => {
      cancelAnimationFrame(raf)
      root.classList.remove('liquid-cursor')
      removeEventListener('pointermove', onMove)
      removeEventListener('pointerdown', onDown)
      removeEventListener('pointerup', onUp)
      removeEventListener('pointercancel', onUp)
      document.removeEventListener('pointerout', onOut)
      removeEventListener('blur', onBlur)
    }
  }, [active, ball, water])

  if (!active) return null
  // Water is all motion, so with reduced motion it simply isn't shown.
  if (water) return reducedMotion ? null : <WaterCursor settings={settings} />
  if (!map) return null
  const size = settings.size
  return (
    <>
      <svg width="0" height="0" aria-hidden className="pointer-events-none fixed">
        <filter id={FILTER_ID} x="0" y="0" width={size} height={size} filterUnits="userSpaceOnUse" colorInterpolationFilters="sRGB">
          <feImage href={map.url} x="0" y="0" width={size} height={size} preserveAspectRatio="none" result="map" />
          {CHANNELS.map((m, i) => (
            <Fragment key={i}>
              <feDisplacementMap ref={(n) => { disp.current[i] = n }} in="SourceGraphic" in2="map"
                scale={2 * map.max * (size / 2) * settings.refraction * (1 + (i - 1) * settings.dispersion)}
                xChannelSelector="R" yChannelSelector="G" result={`d${i}`} />
              <feColorMatrix in={`d${i}`} type="matrix" values={m} result={`c${i}`} />
            </Fragment>
          ))}
          <feBlend in="c0" in2="c1" mode="screen" result="rg" />
          <feBlend in="rg" in2="c2" mode="screen" />
        </filter>
      </svg>
      <div
        ref={drop}
        aria-hidden
        className={`liquid-cursor-drop ${ball ? 'is-ball' : ''}`}
        style={{ width: size, height: size, backdropFilter: refracts ? `url(#${FILTER_ID})` : undefined }}
      >
        <span className="liquid-cursor-highlight" />
      </div>
    </>
  )
}

function subscribeMedia(q: string) {
  return (cb: () => void) => {
    const m = matchMedia(q)
    m.addEventListener('change', cb)
    return () => m.removeEventListener('change', cb)
  }
}
