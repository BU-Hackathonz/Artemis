import { useEffect, useMemo, useRef } from 'react'
import { canRefract } from './dropletMap'
import type { LiquidCursorSettings } from './liquidCursorSettings'
import { mapFS, quadVS, shadeFS, simFS } from './waterCursorShaders'

// Site-wide water cursor: the whole page behaves like a sheet of shallow water. Moving the pointer
// drags a wake through it, rings spread out, and the real page content underneath bends with the
// waves. There is no lens and no outline; the native arrow stays.
//
// Pipeline (all overlays are fixed, full-viewport and pointer-events: none):
//   1. Simulation: WebGL2 height field (wave equation) at 1/SIM_SCALE of the viewport, fixed 120 Hz
//      steps. The pointer's path pushes the surface down; a click makes a splash.
//   2. Refraction of the live page: each frame the slope field is rendered into a small
//      displacement map, read back and wrapped as an uncompressed BMP blob, and fed to an SVG
//      feDisplacementMap used by a backdrop-filter overlay. Two filters alternate: the next one is
//      only switched in after its image has loaded, and a neutral grey flood underneath means a
//      missing map can never shift the page.
//   3. Light: a transparent WebGL canvas above draws slope shading and crest highlights.
// When the water has been idle for a while, both overlays are switched off completely.

const SIM_SCALE = 3     // CSS px per simulation texel
const MAP_SCALE = 6     // CSS px per displacement-map pixel
const STEP = 1 / 120
const IDLE_SLEEP = 7    // s after the last input before the water is switched off
const NATIVE = 'input, textarea, select, [contenteditable=""], [contenteditable="true"], [data-native-cursor], [data-no-liquid-cursor]'
const MAX_OFFSET = 60   // largest refraction offset the map can encode (px)
const IDS = ['artemis-water-a', 'artemis-water-b'] as const
const CHANNELS = [
  '1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0',
  '0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0',
  '0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0',
]

export default function WaterCursor({ settings }: { settings: LiquidCursorSettings }) {
  const refractEl = useRef<HTMLDivElement>(null)
  const shade = useRef<HTMLCanvasElement>(null)
  const svg = useRef<SVGSVGElement>(null)
  const live = useRef(settings)
  live.current = settings
  const refracts = useMemo(canRefract, [])

  useEffect(() => {
    const over = refractEl.current!
    const cv = shade.current!
    const gl = cv.getContext('webgl2', { alpha: true, premultipliedAlpha: true, antialias: false })
    if (!gl || !gl.getExtension('EXT_color_buffer_float')) return
    const filters = IDS.map((id) => svg.current!.querySelector<SVGFilterElement>(`#${id}`)!)
    const images = filters.map((f) => f.querySelector<SVGFEImageElement>('feImage')!)
    const disps = filters.map((f) => [...f.querySelectorAll<SVGFEDisplacementMapElement>('feDisplacementMap')])

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
      return { p, u: (n: string) => gl.getUniformLocation(p, n) }
    }
    const sim = program(simFS), map = program(mapFS), lit = program(shadeFS)
    gl.bindVertexArray(gl.createVertexArray())
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer())
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW)
    gl.enableVertexAttribArray(0)
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0)
    const target = (w: number, h: number, float: boolean) => {
      const t = gl.createTexture()!
      gl.bindTexture(gl.TEXTURE_2D, t)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
      if (float) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA16F, w, h, 0, gl.RGBA, gl.HALF_FLOAT, null)
      else gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null)
      const f = gl.createFramebuffer()!
      gl.bindFramebuffer(gl.FRAMEBUFFER, f)
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0)
      gl.clearColor(0, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT)
      return { t, f }
    }

    let W = 0, H = 0, SW = 0, SH = 0, MW = 0, MH = 0, cur = 0
    let state: { t: WebGLTexture; f: WebGLFramebuffer }[] = []
    let mapRT: { t: WebGLTexture; f: WebGLFramebuffer } | null = null
    let pixels = new Uint8Array(0)
    const free = () => {
      state.forEach((s) => { gl.deleteTexture(s.t); gl.deleteFramebuffer(s.f) })
      if (mapRT) { gl.deleteTexture(mapRT.t); gl.deleteFramebuffer(mapRT.f) }
    }
    const resize = () => {
      W = innerWidth; H = innerHeight
      const dpr = Math.min(devicePixelRatio || 1, 2)
      cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr)
      SW = Math.ceil(W / SIM_SCALE); SH = Math.ceil(H / SIM_SCALE)
      MW = Math.ceil(W / MAP_SCALE); MH = Math.ceil(H / MAP_SCALE)
      free()
      state = [target(SW, SH, true), target(SW, SH, true)]
      mapRT = target(MW, MH, false)
      pixels = new Uint8Array(MW * MH * 4)
      for (const f of filters) { f.setAttribute('width', String(W)); f.setAttribute('height', String(H)) }
      for (const im of images) { im.setAttribute('width', String(W)); im.setAttribute('height', String(H)) }
      for (const f of filters) f.querySelector('feFlood')!.setAttribute('width', String(W))
      for (const f of filters) f.querySelector('feFlood')!.setAttribute('height', String(H))
    }
    resize()

    // --- Input -------------------------------------------------------------------------------
    const ptr = { x: 0, y: 0, has: false }
    const applied = { x: 0, y: 0 }
    let splash = 0, lastInput = -1e9, blocked = false
    const onMove = (e: PointerEvent) => {
      if (e.pointerType !== 'mouse') return
      blocked = !!(e.target as Element | null)?.closest?.(NATIVE)
      const x = e.clientX / W, y = 1 - e.clientY / H
      if (!ptr.has) { applied.x = x; applied.y = y }
      ptr.x = x; ptr.y = y; ptr.has = true
      if (!blocked) { lastInput = performance.now(); wake() }
    }
    const onDown = (e: PointerEvent) => { if (e.pointerType !== 'mouse') return; onMove(e); if (!blocked) splash = 1 }
    const onOut = (e: PointerEvent) => { if (!e.relatedTarget) ptr.has = false }
    addEventListener('pointermove', onMove, { passive: true })
    addEventListener('pointerdown', onDown, { passive: true })
    document.addEventListener('pointerout', onOut, { passive: true })
    addEventListener('resize', resize)

    // --- Steps -------------------------------------------------------------------------------
    const step = () => {
      const s = live.current
      const nxt = 1 - cur
      gl.bindFramebuffer(gl.FRAMEBUFFER, state[nxt].f)
      gl.viewport(0, 0, SW, SH)
      gl.useProgram(sim.p)
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, state[cur].t)
      gl.uniform1i(sim.u('uState'), 0)
      gl.uniform2f(sim.u('uTexel'), 1 / SW, 1 / SH)
      gl.uniform1f(sim.u('uAspect'), W / H)
      gl.uniform1f(sim.u('uDamp'), 0.995)
      gl.uniform1f(sim.u('uRelax'), 0.996)
      gl.uniform1f(sim.u('uSponge'), 24)
      let strength = 0
      const ax = applied.x, ay = applied.y
      if (ptr.has && !blocked) {
        const dx = ptr.x - applied.x, dy = ptr.y - applied.y
        applied.x += dx * 0.5; applied.y += dy * 0.5
        strength = 0.075 * (1 - Math.exp(-Math.hypot(dx * 0.5 * W, dy * 0.5 * H) / 7)) + splash * 0.22
      } else { applied.x = ptr.x; applied.y = ptr.y }
      splash = 0
      gl.uniform2f(sim.u('uA'), ax, ay)
      gl.uniform2f(sim.u('uB'), applied.x, applied.y)
      gl.uniform1f(sim.u('uRadius'), (s.size / 2) / H)
      gl.uniform1f(sim.u('uStrength'), strength)
      gl.drawArrays(gl.TRIANGLES, 0, 3)
      cur = nxt
    }
    const SLOPE = 22
    const drawShade = () => {
      gl.bindFramebuffer(gl.FRAMEBUFFER, null)
      gl.viewport(0, 0, cv.width, cv.height)
      gl.useProgram(lit.p)
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, state[cur].t)
      gl.uniform1i(lit.u('uState'), 0)
      gl.uniform2f(lit.u('uTexel'), 1 / SW, 1 / SH)
      gl.uniform1f(lit.u('uSlope'), SLOPE)
      gl.uniform1f(lit.u('uLight'), 1)
      gl.drawArrays(gl.TRIANGLES, 0, 3)
    }

    // Displacement map → BMP blob → the filter that isn't showing → swap once it's decoded.
    let showing = -1, busy = false
    const urls: (string | null)[] = [null, null]
    const pushMap = () => {
      if (busy || !refracts) return
      busy = true
      const s = live.current
      gl.bindFramebuffer(gl.FRAMEBUFFER, mapRT!.f)
      gl.viewport(0, 0, MW, MH)
      gl.useProgram(map.p)
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, state[cur].t)
      gl.uniform1i(map.u('uState'), 0)
      gl.uniform2f(map.u('uTexel'), 1 / SW, 1 / SH)
      gl.uniform1f(map.u('uSlope'), SLOPE)
      gl.uniform1f(map.u('uRefract'), 40 * s.refraction)
      gl.uniform1f(map.u('uScale'), 2 * MAX_OFFSET)
      gl.drawArrays(gl.TRIANGLES, 0, 3)
      gl.readPixels(0, 0, MW, MH, gl.RGBA, gl.UNSIGNED_BYTE, pixels)
      const url = URL.createObjectURL(bmp(MW, MH, pixels))
      const next = showing === 0 ? 1 : 0
      // <feImage> never fires 'load' in Chrome, so decode the map with a plain Image first (that
      // caches the bitmap), point the filter at it, and swap one frame later.
      const pre = new Image()
      pre.src = url
      pre.decode().catch(() => undefined).then(() => {
        if (stopped) { URL.revokeObjectURL(url); return }
        images[next].setAttribute('href', url)
        requestAnimationFrame(() => {
          if (stopped) return
          over.style.backdropFilter = awake ? `url(#${IDS[next]})` : 'none'
          if (urls[next]) URL.revokeObjectURL(urls[next]!)
          urls[next] = url
          showing = next
          busy = false
        })
      })
    }

    // --- Loop --------------------------------------------------------------------------------
    let raf = 0, last = 0, acc = 0, stopped = false, awake = false
    const sleep = () => {
      awake = false
      over.style.backdropFilter = 'none'
      showing = -1
      gl.bindFramebuffer(gl.FRAMEBUFFER, null)
      gl.viewport(0, 0, cv.width, cv.height)
      gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT)
      for (const st of state) { gl.bindFramebuffer(gl.FRAMEBUFFER, st.f); gl.clearColor(0, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT) }
    }
    const frame = (now: number) => {
      raf = 0
      const dt = Math.min((now - last) / 1000, 0.1)
      last = now
      acc += dt
      let n = 0
      while (acc >= STEP && n < 6) { step(); acc -= STEP; n++ }
      // Channel split strength can change live.
      const disp = live.current.dispersion
      disps.forEach((ds) => ds.forEach((d, i) => d.setAttribute('scale', String(2 * MAX_OFFSET * (1 + (i - 1) * disp)))))
      drawShade()
      pushMap()
      if ((performance.now() - lastInput) / 1000 > IDLE_SLEEP) { sleep(); return }
      raf = requestAnimationFrame(frame)
    }
    function wake() {
      if (stopped || raf) return
      if (!awake) { awake = true; acc = 0 }
      last = performance.now()
      raf = requestAnimationFrame(frame)
    }

    return () => {
      stopped = true
      if (raf) cancelAnimationFrame(raf)
      removeEventListener('pointermove', onMove)
      removeEventListener('pointerdown', onDown)
      document.removeEventListener('pointerout', onOut)
      removeEventListener('resize', resize)
      over.style.backdropFilter = 'none'
      urls.forEach((u) => u && URL.revokeObjectURL(u))
      free()
    }
  }, [refracts])

  return (
    <>
      <svg ref={svg} width="0" height="0" aria-hidden className="pointer-events-none fixed">
        {IDS.map((id) => (
          <filter key={id} id={id} x="0" y="0" width="1" height="1" filterUnits="userSpaceOnUse" colorInterpolationFilters="sRGB">
            {/* Neutral grey = "no displacement", so an unloaded map can never shift the page. */}
            <feFlood floodColor="rgb(128,128,128)" x="0" y="0" result="neutral" />
            <feImage x="0" y="0" preserveAspectRatio="none" result="img" />
            <feMerge result="map"><feMergeNode in="neutral" /><feMergeNode in="img" /></feMerge>
            {CHANNELS.map((m, i) => (
              <ChannelPass key={i} i={i} matrix={m} />
            ))}
            <feBlend in="c0" in2="c1" mode="screen" result="rg" />
            <feBlend in="rg" in2="c2" mode="screen" />
          </filter>
        ))}
      </svg>
      <div ref={refractEl} aria-hidden className="water-cursor-refract" />
      <canvas ref={shade} aria-hidden className="water-cursor-shade" />
    </>
  )
}

function ChannelPass({ i, matrix }: { i: number; matrix: string }) {
  return (
    <>
      <feDisplacementMap in="SourceGraphic" in2="map" scale={2 * MAX_OFFSET} xChannelSelector="R" yChannelSelector="G" result={`d${i}`} />
      <feColorMatrix in={`d${i}`} type="matrix" values={matrix} result={`c${i}`} />
    </>
  )
}

// Uncompressed 32-bit BMP. WebGL's readPixels rows are bottom-up, which is exactly BMP's order,
// and the map shader already wrote B, G, R, A, so the bytes go in as they are.
function bmp(w: number, h: number, px: Uint8Array): Blob {
  const head = new ArrayBuffer(54)
  const v = new DataView(head)
  v.setUint8(0, 0x42); v.setUint8(1, 0x4d)
  v.setUint32(2, 54 + px.length, true)
  v.setUint32(10, 54, true)
  v.setUint32(14, 40, true)
  v.setInt32(18, w, true)
  v.setInt32(22, h, true)
  v.setUint16(26, 1, true)
  v.setUint16(28, 32, true)
  v.setUint32(34, px.length, true)
  return new Blob([head, px], { type: 'image/bmp' })
}
