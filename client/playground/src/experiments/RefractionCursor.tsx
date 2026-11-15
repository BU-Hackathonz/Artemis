import { useEffect, useRef, useState } from 'react'
import { fragmentSrc, vertexSrc } from './refractionShader'

// Glass cursor that refracts the page content, modelled on the Framer "Glassy Cursor" component:
// the page is drawn to a canvas and uploaded as a texture; a WebGL shader bends it through a glass
// shape with per-wavelength dispersion, Fresnel and highlights. Clicking changes the background:
// the new one spreads from the click point behind a refracting ripple.

const SHAPES = ['Sphere', 'Pill', 'Lens', 'Blob'] as const

type Theme = { name: string; bg: (c: CanvasRenderingContext2D, w: number, h: number) => void; ink: string; soft: string; card: string }

const THEMES: Theme[] = [
  {
    name: 'Dawn', ink: '#2b2140', soft: '#5a4d73', card: 'rgba(255,255,255,.55)',
    bg: (c, w, h) => {
      const g = c.createLinearGradient(0, 0, w, h)
      g.addColorStop(0, '#ffd9e8'); g.addColorStop(0.5, '#ffe9d6'); g.addColorStop(1, '#dcd6ff')
      c.fillStyle = g; c.fillRect(0, 0, w, h)
      blob(c, w * 0.15, h * 0.2, w * 0.22, 'rgba(255,150,190,.45)')
      blob(c, w * 0.85, h * 0.85, w * 0.25, 'rgba(150,170,255,.45)')
    },
  },
  {
    name: 'Ocean', ink: '#f2fbff', soft: '#bfe6f2', card: 'rgba(255,255,255,.14)',
    bg: (c, w, h) => {
      const g = c.createLinearGradient(0, 0, 0, h)
      g.addColorStop(0, '#0e6e83'); g.addColorStop(1, '#0a2f5c')
      c.fillStyle = g; c.fillRect(0, 0, w, h)
      c.strokeStyle = 'rgba(255,255,255,.12)'; c.lineWidth = 2
      for (let y = 20; y < h; y += 26) {
        c.beginPath()
        for (let x = 0; x <= w; x += 8) c.lineTo(x, y + Math.sin(x / 40 + y) * 6)
        c.stroke()
      }
    },
  },
  {
    name: 'Grid', ink: '#1d1d1f', soft: '#55555b', card: 'rgba(255,255,255,.8)',
    bg: (c, w, h) => {
      c.fillStyle = '#f4f4f2'; c.fillRect(0, 0, w, h)
      c.strokeStyle = 'rgba(0,0,0,.08)'; c.lineWidth = 1
      for (let x = 0; x < w; x += 28) { c.beginPath(); c.moveTo(x + 0.5, 0); c.lineTo(x + 0.5, h); c.stroke() }
      for (let y = 0; y < h; y += 28) { c.beginPath(); c.moveTo(0, y + 0.5); c.lineTo(w, y + 0.5); c.stroke() }
      blob(c, w * 0.8, h * 0.25, w * 0.12, 'rgba(255,90,60,.9)')
    },
  },
  {
    name: 'Mint', ink: '#123528', soft: '#3f6b5a', card: 'rgba(255,255,255,.5)',
    bg: (c, w, h) => {
      const g = c.createRadialGradient(w * 0.3, h * 0.3, 10, w * 0.5, h * 0.5, w * 0.8)
      g.addColorStop(0, '#e9fff4'); g.addColorStop(1, '#a8e6cf')
      c.fillStyle = g; c.fillRect(0, 0, w, h)
      for (let i = 0; i < 40; i++) blob(c, (i * 97) % w, (i * 53) % h, 6 + (i % 5) * 3, 'rgba(255,255,255,.55)')
    },
  },
]

function blob(c: CanvasRenderingContext2D, x: number, y: number, r: number, color: string) {
  const g = c.createRadialGradient(x, y, 0, x, y, r)
  g.addColorStop(0, color); g.addColorStop(1, 'rgba(255,255,255,0)')
  c.fillStyle = g; c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2); c.fill()
}

// The "page" the glass refracts: real-looking content, so the bending is easy to read.
function drawScene(c: CanvasRenderingContext2D, w: number, h: number, t: Theme) {
  t.bg(c, w, h)
  const s = Math.min(w / 760, 1.2)
  c.textBaseline = 'alphabetic'
  c.fillStyle = t.ink
  c.font = `700 ${64 * s}px system-ui, sans-serif`
  c.fillText('Artemis', 40 * s, 100 * s)
  c.fillStyle = t.soft
  c.font = `400 ${20 * s}px system-ui, sans-serif`
  c.fillText('Voice-first coding. Say it, hear it, ship it.', 42 * s, 136 * s)

  // A code card.
  const x = 40 * s, y = 170 * s, cw = 400 * s, ch = 150 * s
  c.fillStyle = t.card
  c.beginPath(); c.roundRect(x, y, cw, ch, 18 * s); c.fill()
  c.font = `500 ${17 * s}px ui-monospace, monospace`
  const lines: [string, string][] = [
    ['#c2185b', 'def greet(name):'],
    [t.ink, '    return f"Hello, {name}"'],
    ['#2e7d32', '# 12 tests passed'],
    [t.soft, '# Artemis: "All green. Want to commit?"'],
  ]
  lines.forEach(([color, text], i) => { c.fillStyle = color; c.fillText(text, x + 22 * s, y + 40 * s + i * 28 * s) })

  // Chips.
  const chips = ['Run tests', 'Explain', 'Commit']
  const cx = 480 * s
  chips.forEach((label, i) => {
    c.font = `600 ${16 * s}px system-ui, sans-serif`
    const tw = c.measureText(label).width + 32 * s
    c.fillStyle = ['#ff5a7a', '#ffb02e', '#3cc98a'][i]
    c.beginPath(); c.roundRect(cx, 190 * s + i * 52 * s, tw, 38 * s, 19 * s); c.fill()
    c.fillStyle = '#fff'
    c.fillText(label, cx + 16 * s, 215 * s + i * 52 * s)
  })
  c.fillStyle = t.soft
  c.font = `400 ${15 * s}px system-ui, sans-serif`
  c.fillText('Click anywhere to change the background.', 42 * s, h - 28 * s)
}

export default function RefractionCursor() {
  const stage = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [shape, setShape] = useState(0)
  const [themeName, setThemeName] = useState(THEMES[0].name)
  const [glOk, setGlOk] = useState(true)
  const api = useRef<{ setShape: (i: number) => void; nextTheme: (x?: number, y?: number) => void } | null>(null)

  useEffect(() => {
    const el = stage.current!
    const cv = canvas.current!
    const gl = cv.getContext('webgl', { antialias: false })
    if (!gl) { setGlOk(false); return }

    const compile = (type: number, src: string) => {
      const s = gl.createShader(type)!
      gl.shaderSource(s, src); gl.compileShader(s)
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) ?? 'shader')
      return s
    }
    const prog = gl.createProgram()!
    gl.attachShader(prog, compile(gl.VERTEX_SHADER, vertexSrc))
    gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, fragmentSrc))
    gl.linkProgram(prog); gl.useProgram(prog)
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer())
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW)
    const aPos = gl.getAttribLocation(prog, 'aPos')
    gl.enableVertexAttribArray(aPos); gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0)
    const u = (n: string) => gl.getUniformLocation(prog, n)
    const U = {
      res: u('uRes'), dpr: u('uDpr'), a: u('uA'), b: u('uB'), click: u('uClick'), reveal: u('uReveal'),
      pos: u('uPos'), vel: u('uVel'), size: u('uSize'), shape: u('uShape'), time: u('uTime'),
      ior: u('uIor'), disp: u('uDisp'), refr: u('uRefr'),
    }
    gl.uniform1i(U.a, 0); gl.uniform1i(U.b, 1)

    // Two page textures: A is showing, B is the one being revealed.
    const makeTex = () => {
      const t = gl.createTexture()!
      gl.bindTexture(gl.TEXTURE_2D, t)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
      return t
    }
    const texA = makeTex(), texB = makeTex()
    const page = document.createElement('canvas')
    const dpr = Math.min(devicePixelRatio || 1, 1.5)
    let theme = 0, next = 0
    const upload = (tex: WebGLTexture, i: number) => {
      page.width = Math.round(el.clientWidth * dpr)
      page.height = Math.round(el.clientHeight * dpr)
      const c = page.getContext('2d')!
      c.setTransform(dpr, 0, 0, dpr, 0, 0)
      drawScene(c, el.clientWidth, el.clientHeight, THEMES[i])
      gl.bindTexture(gl.TEXTURE_2D, tex)
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true)
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, page)
    }
    const resize = () => {
      cv.width = Math.round(el.clientWidth * dpr)
      cv.height = Math.round(el.clientHeight * dpr)
      gl.viewport(0, 0, cv.width, cv.height)
      upload(texA, theme)
      upload(texB, next)
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(el)

    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches
    const target = { x: el.clientWidth / 2, y: el.clientHeight / 2 }
    const pos = { ...target }, vel = { x: 0, y: 0 }, smooth = { x: 0, y: 0 }
    const size = { v: 0, dv: 0 }
    let inside = false, pressed = false, shapeIdx = 0, raf = 0
    let reveal = -1, revealStart = 0
    const click = { x: 0, y: 0 }
    const t0 = performance.now()

    const finishReveal = () => {
      if (reveal < 0) return
      theme = next
      upload(texA, theme)
      reveal = -1
    }
    const nextTheme = (x = el.clientWidth / 2, y = el.clientHeight / 2) => {
      finishReveal()
      next = (theme + 1) % THEMES.length
      upload(texB, next)
      click.x = x; click.y = y
      revealStart = performance.now()
      reveal = 0
      setThemeName(THEMES[next].name)
    }
    const setShapeIdx = (i: number) => { shapeIdx = i; size.v *= 0.55 } // pop: shrink, spring back
    api.current = { setShape: setShapeIdx, nextTheme }

    const move = (e: PointerEvent) => {
      const r = el.getBoundingClientRect()
      target.x = e.clientX - r.left; target.y = e.clientY - r.top; inside = true
    }
    const down = (e: PointerEvent) => { move(e); pressed = true }
    const up = (e: PointerEvent) => { if (pressed) nextTheme(target.x, target.y); pressed = false; void e }
    const leave = () => { inside = false; pressed = false }
    el.addEventListener('pointermove', move)
    el.addEventListener('pointerdown', down)
    el.addEventListener('pointerup', up)
    el.addEventListener('pointerleave', leave)

    const tick = () => {
      const time = (performance.now() - t0) / 1000
      // Springy follow with a little overshoot.
      const k = reduce ? 1 : 0.11, damp = reduce ? 0 : 0.72
      vel.x = (vel.x + (target.x - pos.x) * k) * damp
      vel.y = (vel.y + (target.y - pos.y) * k) * damp
      pos.x += vel.x; pos.y += vel.y
      smooth.x += (vel.x - smooth.x) * 0.3; smooth.y += (vel.y - smooth.y) * 0.3
      const want = inside ? (pressed ? 62 : 72) : 0
      size.dv = (size.dv + (want - size.v) * 0.14) * 0.68
      size.v += size.dv

      if (reveal >= 0) {
        const diag = Math.hypot(el.clientWidth, el.clientHeight)
        const t = Math.min((performance.now() - revealStart) / (reduce ? 1 : 900), 1)
        reveal = diag * (1 - Math.pow(1 - t, 3))
        if (t >= 1) finishReveal()
      }

      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, texA)
      gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, texB)
      gl.uniform2f(U.res, el.clientWidth, el.clientHeight)
      gl.uniform1f(U.dpr, dpr)
      gl.uniform2f(U.click, click.x, click.y)
      gl.uniform1f(U.reveal, reveal)
      gl.uniform2f(U.pos, pos.x, pos.y)
      gl.uniform2f(U.vel, reduce ? 0 : smooth.x, reduce ? 0 : smooth.y)
      gl.uniform1f(U.size, Math.max(size.v, 0))
      gl.uniform1f(U.shape, shapeIdx)
      gl.uniform1f(U.time, time)
      gl.uniform1f(U.ior, 1.5)
      gl.uniform1f(U.disp, 0.16)
      gl.uniform1f(U.refr, 0.9)
      gl.drawArrays(gl.TRIANGLES, 0, 3)
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)

    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      api.current = null
      el.removeEventListener('pointermove', move)
      el.removeEventListener('pointerdown', down)
      el.removeEventListener('pointerup', up)
      el.removeEventListener('pointerleave', leave)
    }
  }, [])

  return (
    <section aria-labelledby="rc-h" className="space-y-4">
      <h2 id="rc-h" className="text-xl font-semibold">Refraction cursor</h2>
      <p className="text-sm text-neutral-600 dark:text-neutral-400">
        The glass bends the page under it, splitting light into colour at the edges. Click the stage to change the background.
        {!glOk && ' WebGL is unavailable here, so this experiment cannot render.'}
      </p>
      <div ref={stage} aria-hidden data-no-liquid-cursor className="relative h-[28rem] cursor-none touch-none overflow-hidden rounded-3xl bg-neutral-200 select-none">
        <canvas ref={canvas} className="absolute inset-0 size-full" />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">Shape</span>
        {SHAPES.map((name, i) => (
          <button key={name} type="button" aria-pressed={shape === i}
            onClick={() => { setShape(i); api.current?.setShape(i) }}
            className="rounded-full border border-neutral-300 px-3 py-1 text-sm aria-pressed:bg-neutral-900 aria-pressed:text-white dark:border-neutral-700 dark:aria-pressed:bg-white dark:aria-pressed:text-neutral-900">
            {name}
          </button>
        ))}
        <button type="button" onClick={() => api.current?.nextTheme()}
          className="ml-auto rounded-full bg-amber-400 px-4 py-1 text-sm font-medium text-neutral-950">
          Next background
        </button>
        <span className="text-sm text-neutral-600 dark:text-neutral-400" aria-live="polite">Background: {themeName}</span>
      </div>
    </section>
  )
}
