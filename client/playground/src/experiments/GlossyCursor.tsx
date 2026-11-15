import { useEffect, useRef } from 'react'

// Translucent, glossy, pastel-iridescent cursor with a liquid drag trail.
// Moving fast stretches the head along its velocity; holding the mouse button
// makes the trail heavier and longer, like dragging something viscous.
// Purely visual: hidden from assistive tech, and it calms down under prefers-reduced-motion.

const TRAIL = 10

const iridescent =
  'conic-gradient(from 210deg, #ffd6e8, #e3d4ff, #c9f1ff, #d6ffe9, #fff3c9, #ffd6e8)'

const blobStyle = (size: number, alpha: number): React.CSSProperties => ({
  width: size,
  height: size,
  marginLeft: -size / 2,
  marginTop: -size / 2,
  opacity: alpha,
  background: `radial-gradient(circle at 32% 28%, rgba(255,255,255,.95) 0 12%, rgba(255,255,255,0) 34%), ${iridescent}`,
})

export default function GlossyCursor() {
  const stage = useRef<HTMLDivElement>(null)
  const head = useRef<HTMLDivElement>(null)
  const sheen = useRef<HTMLDivElement>(null)
  const trail = useRef<(HTMLDivElement | null)[]>([])

  useEffect(() => {
    const el = stage.current!
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches
    const target = { x: el.clientWidth / 2, y: el.clientHeight / 2 }
    const pos = { ...target }
    const vel = { x: 0, y: 0 }
    const pts = Array.from({ length: TRAIL }, () => ({ ...target }))
    let pressed = false
    let inside = false
    let hue = 0
    let raf = 0

    const move = (e: PointerEvent) => {
      const r = el.getBoundingClientRect()
      target.x = e.clientX - r.left
      target.y = e.clientY - r.top
      inside = true
    }
    const down = () => { pressed = true }
    const up = () => { pressed = false }
    const leave = () => { inside = false; pressed = false }

    el.addEventListener('pointermove', move)
    el.addEventListener('pointerdown', down)
    window.addEventListener('pointerup', up)
    el.addEventListener('pointerleave', leave)

    const tick = () => {
      // Spring the head toward the pointer: softer and wobblier while pressed.
      const k = reduce ? 1 : pressed ? 0.12 : 0.22
      const damp = reduce ? 0 : pressed ? 0.72 : 0.62
      vel.x = (vel.x + (target.x - pos.x) * k) * damp
      vel.y = (vel.y + (target.y - pos.y) * k) * damp
      pos.x += vel.x
      pos.y += vel.y

      const speed = Math.min(Math.hypot(vel.x, vel.y), 60)
      const angle = Math.atan2(vel.y, vel.x)
      const stretch = reduce ? 0 : speed / 60
      const press = pressed ? 0.82 : 1
      hue = (hue + 0.4 + speed * 0.05) % 360

      const h = head.current!
      h.style.transform =
        `translate3d(${pos.x}px, ${pos.y}px, 0) rotate(${angle}rad) ` +
        `scale(${(1 + stretch * 0.7) * press}, ${(1 - stretch * 0.35) * press})`
      h.style.filter = `hue-rotate(${hue}deg) saturate(1.1)`
      h.style.opacity = inside ? '1' : '0'
      // Keep the specular highlight pinned to the upper-left, no matter the rotation.
      sheen.current!.style.transform = `rotate(${-angle}rad)`

      // Each trail blob follows the one in front; heavier lag while dragging.
      const follow = reduce ? 1 : pressed ? 0.18 : 0.35
      let prev = pos
      pts.forEach((p, i) => {
        p.x += (prev.x - p.x) * follow
        p.y += (prev.y - p.y) * follow
        prev = p
        const t = trail.current[i]
        if (!t) return
        const s = (1 - i / TRAIL) * (pressed ? 1.15 : 0.9)
        t.style.transform = `translate3d(${p.x}px, ${p.y}px, 0) scale(${s})`
        t.style.filter = `hue-rotate(${hue + i * 14}deg) blur(${2 + i * 0.6}px)`
        t.style.opacity = inside && !reduce ? String(0.55 * (1 - i / TRAIL)) : '0'
      })

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
  }, [])

  return (
    <section aria-labelledby="gc-h" className="space-y-4">
      <h2 id="gc-h" className="text-xl font-semibold">Glossy cursor</h2>
      <p className="text-sm text-neutral-600 dark:text-neutral-400">
        Move inside the stage. Move fast to stretch it, hold the mouse button to drag a heavier trail.
      </p>
      <div
        ref={stage}
        aria-hidden
        data-no-liquid-cursor className="relative h-[28rem] cursor-none touch-none overflow-hidden rounded-3xl select-none"
        style={{
          background:
            'radial-gradient(120% 90% at 20% 10%, #fdf7ff 0%, #f3f0ff 40%, #eaf6ff 75%, #f2fff8 100%)',
        }}
      >
        {Array.from({ length: TRAIL }, (_, i) => (
          <div
            key={i}
            ref={(n) => { trail.current[i] = n }}
            className="pointer-events-none absolute top-0 left-0 rounded-full mix-blend-multiply will-change-transform"
            style={blobStyle(46, 0)}
          />
        ))}
        <div
          ref={head}
          className="pointer-events-none absolute top-0 left-0 rounded-full opacity-0 will-change-transform"
          style={{
            width: 56, height: 56, marginLeft: -28, marginTop: -28,
            background: iridescent,
            boxShadow:
              'inset 0 -8px 16px rgba(170,140,255,.35), inset 0 6px 12px rgba(255,255,255,.8), 0 10px 30px rgba(180,160,255,.35)',
            backdropFilter: 'blur(6px)',
          }}
        >
          <div ref={sheen} className="absolute inset-0 rounded-full">
            <div className="absolute top-[14%] left-[20%] h-[30%] w-[38%] rounded-full bg-white/85 blur-[2px]" />
            <div className="absolute right-[22%] bottom-[18%] h-[10%] w-[14%] rounded-full bg-white/60 blur-[1px]" />
          </div>
        </div>
      </div>
    </section>
  )
}
