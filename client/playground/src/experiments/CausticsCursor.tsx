import { useEffect, useRef, useState } from 'react'
import { MAX_BLOBS, fragmentSrc, vertexSrc } from './causticShader'
import { gripWeights, makeWater, stepWater } from './waterSim'

// Cursor modelled on Jeff Falcon's "Caustics in (slo-)motion": a glossy, iridescent body of water
// that casts caustic light into its own shadow.
//
// The water is one cohesive body (see waterSim.ts), not a chain of beads. The cursor is a slow
// "finger" that touches the water nearest to it; pressing grips a small patch, and the rest of the
// body is dragged after it by surface tension against floor friction, so it streams and then
// gathers back into a pool. The shader melts all particles into one surface.

const RENDER_R = 16 // px per particle in the shader; overlapping kernels keep the surface smooth

export default function CausticsCursor() {
  const stage = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [glOk, setGlOk] = useState(true)

  useEffect(() => {
    const el = stage.current!
    const cv = canvas.current!
    const gl = cv.getContext('webgl', { antialias: false, premultipliedAlpha: false })
    if (!gl) { setGlOk(false); return }

    const shader = (type: number, src: string) => {
      const s = gl.createShader(type)!
      gl.shaderSource(s, src)
      gl.compileShader(s)
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) ?? 'shader')
      return s
    }
    const prog = gl.createProgram()!
    gl.attachShader(prog, shader(gl.VERTEX_SHADER, vertexSrc))
    gl.attachShader(prog, shader(gl.FRAGMENT_SHADER, fragmentSrc))
    gl.linkProgram(prog)
    gl.useProgram(prog)
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer())
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW)
    const aPos = gl.getAttribLocation(prog, 'aPos')
    gl.enableVertexAttribArray(aPos)
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0)
    const uRes = gl.getUniformLocation(prog, 'uRes')
    const uDpr = gl.getUniformLocation(prog, 'uDpr')
    const uTime = gl.getUniformLocation(prog, 'uTime')
    const uB = gl.getUniformLocation(prog, 'uB')
    const blobs = new Float32Array(MAX_BLOBS * 3)

    // Cap resolution: the shader is per-pixel and the look is soft anyway.
    const dpr = Math.min(devicePixelRatio || 1, 1.25)
    const resize = () => {
      cv.width = Math.round(el.clientWidth * dpr)
      cv.height = Math.round(el.clientHeight * dpr)
      gl.viewport(0, 0, cv.width, cv.height)
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(el)


    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches
    const target = { x: el.clientWidth * 0.4, y: el.clientHeight * 0.45 }
    const finger = { ...target }
    const ps = makeWater(MAX_BLOBS, target.x, target.y)
    let grip: Float32Array | null = null
    let inside = false, show = 0, raf = 0
    const t0 = performance.now()

    const move = (e: PointerEvent) => {
      const r = el.getBoundingClientRect()
      target.x = e.clientX - r.left
      target.y = e.clientY - r.top
      inside = true
    }
    const down = (e: PointerEvent) => { move(e); grip = gripWeights(ps, finger.x, finger.y) }
    const up = () => { grip = null }
    const leave = () => { inside = false; grip = null }
    el.addEventListener('pointermove', move)
    el.addEventListener('pointerdown', down)
    window.addEventListener('pointerup', up)
    el.addEventListener('pointerleave', leave)

    const tick = () => {
      const time = (performance.now() - t0) / 1000
      show += ((inside ? 1 : 0) - show) * 0.08
      // The finger drifts toward the pointer: slow, heavy, unhurried.
      const ease = reduce ? 1 : grip ? 0.05 : 0.04
      finger.x += (target.x - finger.x) * ease
      finger.y += (target.y - finger.y) * ease
      stepWater(ps, finger.x, finger.y, grip)
      stepWater(ps, finger.x, finger.y, grip)
      ps.forEach((p, i) => blobs.set([p.x, p.y, RENDER_R * show], i * 3))

      gl.uniform2f(uRes, el.clientWidth, el.clientHeight)
      gl.uniform1f(uDpr, dpr)
      gl.uniform1f(uTime, reduce ? 0 : time)
      gl.uniform3fv(uB, blobs)
      gl.drawArrays(gl.TRIANGLES, 0, 3)
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)

    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      el.removeEventListener('pointermove', move)
      el.removeEventListener('pointerdown', down)
      window.removeEventListener('pointerup', up)
      el.removeEventListener('pointerleave', leave)
    }
  }, [])

  return (
    <section aria-labelledby="cc-h" className="space-y-4">
      <h2 id="cc-h" className="text-xl font-semibold">Caustics cursor</h2>
      <p className="text-sm text-neutral-600 dark:text-neutral-400">
        Move over the stage. Press and drag to pull the water into a stream; let go and it gathers back into a pool.
        {!glOk && ' WebGL is unavailable here, so this experiment cannot render.'}
      </p>
      <div ref={stage} aria-hidden data-no-liquid-cursor className="relative h-[28rem] cursor-none touch-none overflow-hidden rounded-3xl bg-[#e8e1d3] select-none">
        <canvas ref={canvas} className="absolute inset-0 size-full" />
      </div>
    </section>
  )
}
