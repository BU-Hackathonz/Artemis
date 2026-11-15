import { useEffect, useRef, useState } from 'react'
import { quadVS, renderFS, simFS } from './water/waterShaders'

// Hero section with a water surface the cursor drags through (reference: a landing page where the
// pointer pulls a glossy wake through the page, with rings spreading and the type tearing apart
// under the waves).
//
// How it works:
//   - The hero's text and emblem stay in the DOM (selectable, read by screen readers) but are drawn
//     invisibly. Their exact layout is measured and redrawn into an "art" canvas, only on resize and
//     font load, never per frame. Buttons and nav sit above the water as normal, clickable DOM.
//   - A WebGL2 height-field simulation (wave equation, float textures, fixed 120 Hz steps) runs at
//     a third of the hero's resolution. The pointer's path presses the surface down every step.
//   - The render pass bends the art by the surface slope, splits colour slightly, shades slopes and
//     adds crest highlights.
//   - The loop sleeps when the hero is off-screen or the water has been idle for a while.

const SIM_SCALE = 3          // sim texel = 3 CSS px
const STEP = 1 / 120         // fixed sim step (s)
const IDLE_SLEEP = 9         // s after the last input before the loop sleeps

type Ink = { color: string }

export default function RainHero() {
  const hero = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [glOk, setGlOk] = useState(true)
  const [clicks, setClicks] = useState(0)

  useEffect(() => {
    const el = hero.current!
    const cv = canvas.current!
    const gl = cv.getContext('webgl2', { antialias: false, premultipliedAlpha: false })
    if (!gl || !gl.getExtension('EXT_color_buffer_float')) { setGlOk(false); return }
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches

    // --- GL plumbing -------------------------------------------------------------------------
    const compile = (type: number, src: string) => {
      const s = gl.createShader(type)!
      gl.shaderSource(s, src); gl.compileShader(s)
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) ?? 'shader')
      return s
    }
    const program = (fs: string) => {
      const p = gl.createProgram()!
      gl.attachShader(p, compile(gl.VERTEX_SHADER, quadVS))
      gl.attachShader(p, compile(gl.FRAGMENT_SHADER, fs))
      gl.bindAttribLocation(p, 0, 'aPos')
      gl.linkProgram(p)
      const u = (n: string) => gl.getUniformLocation(p, n)
      return { p, u }
    }
    const sim = program(simFS)
    const ren = program(renderFS)
    const vao = gl.createVertexArray()
    gl.bindVertexArray(vao)
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer())
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW)
    gl.enableVertexAttribArray(0)
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0)

    const tex = (w: number, h: number, data: TexImageSource | null, float: boolean) => {
      const t = gl.createTexture()!
      gl.bindTexture(gl.TEXTURE_2D, t)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
      if (float) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA16F, w, h, 0, gl.RGBA, gl.HALF_FLOAT, null)
      else if (data) { gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true); gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, data) }
      return t
    }

    let W = 0, H = 0, SW = 0, SH = 0, dpr = 1
    let state: { t: WebGLTexture; f: WebGLFramebuffer }[] = []
    let artTex: WebGLTexture | null = null
    let cur = 0

    // --- The art texture: redraw the hero's (invisible) DOM type at its measured positions ----
    const art = document.createElement('canvas')
    const drawArt = async () => {
      const r = el.getBoundingClientRect()
      art.width = Math.round(W * dpr); art.height = Math.round(H * dpr)
      const c = art.getContext('2d')!
      c.setTransform(dpr, 0, 0, dpr, 0, 0)
      c.fillStyle = getComputedStyle(el).backgroundColor
      c.fillRect(0, 0, W, H)
      for (const node of el.querySelectorAll<HTMLElement>('[data-water-text]')) {
        const s = getComputedStyle(node)
        // Measure the text itself (not the padded box): a Range's rect is the glyphs' content area.
        const range = document.createRange()
        range.selectNodeContents(node)
        const b = range.getBoundingClientRect()
        c.font = `${s.fontStyle} ${s.fontWeight} ${s.fontSize} ${s.fontFamily}`
        ;(c as CanvasRenderingContext2D & { letterSpacing: string }).letterSpacing = s.letterSpacing === 'normal' ? '0px' : s.letterSpacing
        c.fillStyle = (node.dataset as Ink & DOMStringMap).color ?? '#fff'
        c.textBaseline = 'alphabetic'
        const m = c.measureText(node.textContent ?? '')
        const asc = m.fontBoundingBoxAscent, desc = m.fontBoundingBoxDescent
        const baseline = b.top - r.top + (b.height - (asc + desc)) / 2 + asc // content area top + ascent
        c.fillText(node.textContent ?? '', b.left - r.left, baseline)
      }
      await Promise.all([...el.querySelectorAll<SVGSVGElement>('[data-water-svg]')].map((svg) => new Promise<void>((done) => {
        const b = svg.getBoundingClientRect()
        const clone = svg.cloneNode(true) as SVGSVGElement
        clone.setAttribute('width', String(b.width)); clone.setAttribute('height', String(b.height))
        clone.style.color = svg.dataset.color ?? '#fff'
        clone.setAttribute('color', svg.dataset.color ?? '#fff')
        const img = new Image()
        img.onload = () => { c.drawImage(img, b.left - r.left, b.top - r.top, b.width, b.height); done() }
        img.onerror = () => done()
        img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(new XMLSerializer().serializeToString(clone))
      })))
      if (artTex) gl.deleteTexture(artTex)
      artTex = tex(0, 0, art, false)
      wake()
    }

    const resize = () => {
      W = el.clientWidth; H = el.clientHeight
      dpr = Math.min(devicePixelRatio || 1, 2)
      cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr)
      SW = Math.max(2, Math.round(W / SIM_SCALE)); SH = Math.max(2, Math.round(H / SIM_SCALE))
      state.forEach((s) => { gl.deleteTexture(s.t); gl.deleteFramebuffer(s.f) })
      state = [0, 1].map(() => {
        const t = tex(SW, SH, null, true)
        const f = gl.createFramebuffer()!
        gl.bindFramebuffer(gl.FRAMEBUFFER, f)
        gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0)
        gl.clearColor(0, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT)
        return { t, f }
      })
      gl.bindFramebuffer(gl.FRAMEBUFFER, null)
      void drawArt()
    }

    // --- Input: the pointer's path, consumed one segment per sim step --------------------------
    const ptr = { x: 0, y: 0, has: false }
    const applied = { x: 0, y: 0 }
    let lastInput = performance.now()
    let splash = 0
    const toUv = (e: PointerEvent) => {
      const r = el.getBoundingClientRect()
      return { x: (e.clientX - r.left) / r.width, y: 1 - (e.clientY - r.top) / r.height }
    }
    const onMove = (e: PointerEvent) => {
      const p = toUv(e)
      if (!ptr.has) { applied.x = p.x; applied.y = p.y }
      ptr.x = p.x; ptr.y = p.y; ptr.has = true
      lastInput = performance.now()
      if (!reduce) wake() // with reduced motion the water is still: no need to redraw
    }
    const onLeave = () => { ptr.has = false }
    const onDown = (e: PointerEvent) => { onMove(e); splash = 1 }
    el.addEventListener('pointermove', onMove, { passive: true })
    el.addEventListener('pointerdown', onDown, { passive: true })
    el.addEventListener('pointerleave', onLeave, { passive: true })

    // --- Loop ----------------------------------------------------------------------------------
    let raf = 0, last = performance.now(), acc = 0, onScreen = true
    const step = () => {
      const nxt = 1 - cur
      gl.bindFramebuffer(gl.FRAMEBUFFER, state[nxt].f)
      gl.viewport(0, 0, SW, SH)
      gl.useProgram(sim.p)
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, state[cur].t)
      gl.uniform1i(sim.u('uState'), 0)
      gl.uniform2f(sim.u('uTexel'), 1 / SW, 1 / SH)
      gl.uniform1f(sim.u('uAspect'), W / H)
      gl.uniform1f(sim.u('uDamp'), 0.996)
      gl.uniform1f(sim.u('uRelax'), 0.9995)
      // Consume part of the pointer's path; strength follows how far it moved (speed).
      let strength = 0
      const ax = applied.x, ay = applied.y
      if (ptr.has && !reduce) {
        const dx = ptr.x - applied.x, dy = ptr.y - applied.y
        applied.x += dx * 0.5; applied.y += dy * 0.5
        const movedPx = Math.hypot(dx * 0.5 * W, dy * 0.5 * H)
        strength = Math.min(movedPx * 0.006, 0.12) + splash * 0.6
      }
      splash = 0
      gl.uniform2f(sim.u('uA'), ax, ay)
      gl.uniform2f(sim.u('uB'), applied.x, applied.y)
      gl.uniform1f(sim.u('uRadius'), 38 / H)
      gl.uniform1f(sim.u('uStrength'), strength)
      gl.drawArrays(gl.TRIANGLES, 0, 3)
      cur = nxt
    }
    const render = () => {
      gl.bindFramebuffer(gl.FRAMEBUFFER, null)
      gl.viewport(0, 0, cv.width, cv.height)
      gl.useProgram(ren.p)
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, state[cur].t)
      gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, artTex)
      gl.uniform1i(ren.u('uState'), 0)
      gl.uniform1i(ren.u('uArt'), 1)
      gl.uniform2f(ren.u('uTexel'), 1 / SW, 1 / SH)
      gl.uniform1f(ren.u('uRefract'), 0.06)
      gl.uniform1f(ren.u('uDisp'), 0.12)
      gl.uniform1f(ren.u('uSlope'), 26)
      gl.drawArrays(gl.TRIANGLES, 0, 3)
    }
    const frame = (now: number) => {
      raf = 0
      const dt = Math.min((now - last) / 1000, 0.1)
      last = now
      if (artTex && state.length) {
        if (!reduce) {
          acc += dt
          let n = 0
          while (acc >= STEP && n < 6) { step(); acc -= STEP; n++ }
        }
        render()
      }
      const idle = (performance.now() - lastInput) / 1000 > IDLE_SLEEP
      if (idle && artTex && state.length) {
        // Going to sleep: flatten the water and show one still frame (don't freeze a bent one).
        for (const st of state) { gl.bindFramebuffer(gl.FRAMEBUFFER, st.f); gl.clearColor(0, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT) }
        render()
      }
      if (onScreen && !idle && !reduce) raf = requestAnimationFrame(frame)
    }
    function wake() {
      if (!raf && onScreen) { last = performance.now(); raf = requestAnimationFrame(frame) }
    }

    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(el)
    const io = new IntersectionObserver(([entry]) => { onScreen = entry.isIntersecting; if (onScreen) wake() })
    io.observe(el)
    document.fonts?.ready.then(() => { void drawArt() })

    return () => {
      if (raf) cancelAnimationFrame(raf)
      ro.disconnect(); io.disconnect()
      el.removeEventListener('pointermove', onMove)
      el.removeEventListener('pointerdown', onDown)
      el.removeEventListener('pointerleave', onLeave)
      state.forEach((st) => { gl.deleteTexture(st.t); gl.deleteFramebuffer(st.f) })
      if (artTex) gl.deleteTexture(artTex)
      // (No loseContext: React's dev mode re-runs this effect on the same canvas.)
    }
  }, [])

  // DOM text is transparent when the water renders it; visible again if WebGL2 isn't available.
  const ink = glOk ? 'text-transparent selection:bg-white/40' : 'text-[#fbeeb8]'

  return (
    <section aria-labelledby="rh-h" className="space-y-4">
      <h2 id="rh-h" className="text-xl font-semibold">Rain hero</h2>
      <p className="text-sm text-neutral-600 dark:text-neutral-400">
        Drag the cursor through the hero: it pulls a wake through the water, rings spread, and the type bends under the waves. Click for a splash.
        {!glOk && ' WebGL2 with float render targets is unavailable here, so the hero is shown without water.'}
      </p>

      <div ref={hero} data-native-cursor data-testid="rain-hero"
        className="relative isolate h-[34rem] overflow-hidden rounded-3xl bg-[#f0602a]">
        <canvas ref={canvas} aria-hidden className={`pointer-events-none absolute inset-0 -z-10 size-full ${glOk ? '' : 'hidden'}`} />

        <nav aria-label="Hero" className="relative z-10 flex items-center justify-between px-6 pt-4 text-[13px] font-medium text-[#fbeeb8]">
          <span>artemis</span>
          <span className="flex gap-6"><a href="#rain-hero" onClick={(e) => e.preventDefault()}>Product</a><a href="#rain-hero" onClick={(e) => e.preventDefault()}>Voice</a><a href="#rain-hero" onClick={(e) => e.preventDefault()}>Docs</a></span>
          <button type="button" className="rounded-full border border-[#fbeeb8] px-3 py-0.5">Try now</button>
        </nav>

        <h3 data-water-text data-color="#fbeeb8"
          className={`px-5 pt-1 text-[clamp(64px,13.5vw,168px)] leading-[0.95] font-extrabold tracking-[-0.045em] whitespace-nowrap ${ink}`}>
          artemis
        </h3>

        <svg data-water-svg data-color="#fbeeb8" viewBox="0 0 100 100" aria-hidden
          className="absolute top-[56%] left-1/2 size-20 -translate-x-1/2 -translate-y-1/2" style={{ color: glOk ? 'transparent' : '#fbeeb8' }}>
          {[14, 26, 38].map((r) => <circle key={r} cx="50" cy="50" r={r} fill="none" stroke="currentColor" strokeWidth="7" strokeDasharray={`${r * 1.1} ${r * 0.5}`} />)}
        </svg>

        <div className="absolute inset-x-6 bottom-6 flex items-end justify-between gap-4">
          <p className={`text-[19px] leading-tight font-semibold ${ink}`}>
            <span data-water-text data-color="#fbeeb8" className="block whitespace-nowrap">Voice-first development for</span>
            <span data-water-text data-color="#fbeeb8" className="block whitespace-nowrap">blind and low-vision developers</span>
          </p>
          <div className="relative z-10 flex items-center gap-4 text-[13px] text-[#fbeeb8]">
            <button type="button" onClick={() => setClicks((c) => c + 1)} data-testid="hero-cta"
              className="rounded-full border border-[#fbeeb8] px-4 py-1.5">Get started{clicks ? ` (${clicks})` : ''}</button>
            <a href="#rain-hero" onClick={(e) => e.preventDefault()}>Log in</a>
          </div>
        </div>
      </div>
    </section>
  )
}
