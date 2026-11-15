// Water wallpaper prototype: the desktop wallpaper as a sheet of shallow water that the mouse drags
// waves through. Same wave simulation as the playground's water cursor (simFS); here the wallpaper
// itself is the texture being refracted, drawn at the monitor's full device resolution.
//
// Launched by Launch-WaterWallpaper.ps1, which writes window.WALLPAPER (image as a data: URL plus the
// Windows fit style and background colour). Opened without it, the page asks for an image instead.

import { quadVS, simFS } from '../../src/experiments/water/waterShaders'

type Fit = 'fill' | 'fit' | 'stretch' | 'center' | 'tile' | 'span'
declare global { interface Window { WALLPAPER?: { src: string; style: Fit; bg: string } } }

// ---- Tunables ---------------------------------------------------------------------------------
const SIM_SCALE = 2          // CSS px per simulation texel (2 = crisp, glassy wave fronts)
const STEP = 1 / 120         // fixed simulation step (s): wave speed is frame-rate independent
const IDLE_SLEEP = 9         // s without input before the loop sleeps (the water is flat by then)
const HIGHLIGHT = 0.6         // thin glints only; 1 = playground strength
const SLOPE = 22             // wave contrast: lower = softer, rounder waves
const DISPERSION = 0.035     // faint colour fringe on bent edges, like the reference
const LIGHT_GAIN = 7         // how much the light exaggerates the slope (glossy lines on gentle water)
const AREA_FOLLOW = 0.12     // how quickly the active area glides after the cursor (per step)
const SPONGE = 24            // texels of absorbing border: rings fade at the edges, no echoes
const PUSH_MAX = 0.08        // strongest push a fast sweep can make (speed saturates toward this)
const PUSH_SPEED = 7         // px moved per step at which the push is ~63% of PUSH_MAX (lower = slow drifts show more)

type Settings = { wake: number; distortion: number; damping: number; radius: number }
// Comfort-tuned: a wide, soft wake, bending that can't tear the picture, and a calm settle.
// Subtle by default: water only around the cursor (radius), gentle bending.
const DEFAULTS: Settings = { wake: 72, distortion: 1, damping: 11, radius: 220 }
// damping 1…30 → what's kept per step of wave velocity and of height. 8 ≈ 0.996 velocity (the
// playground's value): waves travel a couple of seconds after you stop, and the surface relaxes
// smoothly back to flat. Lower = longer-lasting ripples, higher = quicker calm.
const dampToKeep = (d: number) => 1 - d / 2000
const relaxToKeep = (d: number) => 1 - d / 2500

// ---- Render shader: refract the wallpaper through the water --------------------------------------
const wallFS = `#version 300 es
precision highp float;
uniform sampler2D uState;
uniform sampler2D uImg;
uniform vec2 uTexel;
uniform vec2 uRes;        // CSS px
uniform float uSlope;
uniform float uRefractPx; // offset in CSS px per unit slope
uniform float uDisp;
uniform float uLight;
uniform vec2 uScale;      // screen uv → image uv (Windows fit style)
uniform vec2 uOffset;
uniform float uTile;
uniform float uHasImg;
uniform vec3 uBg;
uniform vec2 uArea;       // centre of the active area (uv)
uniform float uAreaR;     // its radius (CSS px)
in vec2 vUv;
out vec4 o;

vec3 wall(vec2 uv) {
  if (uHasImg < 0.5) return uBg;
  vec2 p = uv * uScale + uOffset;
  if (uTile > 0.5) p = fract(p);
  else if (any(lessThan(p, vec2(0.0))) || any(greaterThan(p, vec2(1.0)))) return uBg;
  return texture(uImg, p).rgb;
}

void main() {
  vec2 tx = vec2(uTexel.x * 2.0, 0.0), ty = vec2(0.0, uTexel.y * 2.0);
  vec2 g = vec2(texture(uState, vUv + tx).r - texture(uState, vUv - tx).r,
                texture(uState, vUv + ty).r - texture(uState, vUv - ty).r) * 0.5 * uSlope;
  // Only the area around the cursor is live water; it fades to perfectly still at the rim.
  float area = 1.0 - smoothstep(uAreaR * 0.55, uAreaR, length((vUv - uArea) * uRes));
  g *= area;
  vec3 n = normalize(vec3(-g, 1.0));
  // Soft limit: small ripples bend detail ~linearly, big waves top out at uRefractPx / 2,
  // so a fast sweep can never tear the picture apart.
  float s = length(n.xy);
  vec2 off = (s > 1e-5 ? n.xy / s : vec2(0.0)) * (0.5 * tanh(2.0 * s)) * uRefractPx / uRes;
  vec3 col = vec3(wall(vUv + off * (1.0 - uDisp)).r, wall(vUv + off).g, wall(vUv + off * (1.0 + uDisp)).b);

  // Light like the reference: clear glass, no broad shading. Thin bright lines where a crest
  // catches the light, a faint sheen along steep fronts, a whisper of shade on the far side.
  // Light the water with an exaggerated copy of its slope (LIGHT_GAIN), so even very gentle water
  // shows the reference's thin glossy lines; the bending above still uses the true, subtle slope.
  vec3 nl = normalize(vec3(-g * ${LIGHT_GAIN.toFixed(1)}, 1.0));
  float steep = 1.0 - nl.z;
  vec3 L = normalize(vec3(-0.45, 0.65, 0.62));
  float spec = pow(max(reflect(-L, nl).z, 0.0), 220.0) * smoothstep(0.004, 0.04, steep);
  float sheen = smoothstep(0.006, 0.08, steep) * 0.07;
  float shade = clamp(dot(nl.xy, normalize(vec2(0.55, -0.85))), 0.0, 0.6) * 0.1;
  col = col * (1.0 - shade * uLight) + vec3(1.0, 0.98, 0.95) * (spec * 1.2 + sheen) * uLight;
  o = vec4(col, 1.0);
}
`

// ---- Settings persistence (per machine, best effort) -------------------------------------------
const KEY = 'artemis-water-wallpaper-v4' // v4: very subtle, reference-style refraction
const load = (): Settings => {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) ?? '{}') } } catch { return { ...DEFAULTS } }
}
const save = (s: Settings) => { try { localStorage.setItem(KEY, JSON.stringify(s)) } catch { /* private mode */ } }
const settings = load()

// ---- GL setup -----------------------------------------------------------------------------------
const cv = document.getElementById('water') as HTMLCanvasElement
const gl: WebGL2RenderingContext = (() => {
  const g = cv.getContext('webgl2', { antialias: false })
  if (!g || !g.getExtension('EXT_color_buffer_float')) {
    document.getElementById('fatal')!.hidden = false
    throw new Error('WebGL2 with float render targets is required')
  }
  return g
})()
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
const sim = program(simFS), ren = program(wallFS)
gl.bindVertexArray(gl.createVertexArray())
gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer())
gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW)
gl.enableVertexAttribArray(0)
gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0)

const texture = () => {
  const t = gl.createTexture()!
  gl.bindTexture(gl.TEXTURE_2D, t)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
  return t
}

let W = 0, H = 0, dpr = 1, SW = 0, SH = 0, cur = 0
let state: { t: WebGLTexture; f: WebGLFramebuffer }[] = []
const resize = () => {
  W = innerWidth; H = innerHeight; dpr = devicePixelRatio || 1
  cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr)
  SW = Math.ceil(W / SIM_SCALE); SH = Math.ceil(H / SIM_SCALE)
  state.forEach((s) => { gl.deleteTexture(s.t); gl.deleteFramebuffer(s.f) })
  state = [0, 1].map(() => {
    const t = texture()
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA16F, SW, SH, 0, gl.RGBA, gl.HALF_FLOAT, null)
    const f = gl.createFramebuffer()!
    gl.bindFramebuffer(gl.FRAMEBUFFER, f)
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0)
    gl.clearColor(0, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT)
    return { t, f }
  })
  fitWallpaper()
  wake()
}

// ---- Wallpaper ----------------------------------------------------------------------------------
const img = { tex: texture(), w: 0, h: 0, has: false, fit: 'fill' as Fit, bg: [0, 0, 0] as number[] }
const parseBg = (css: string) => {
  const m = css.match(/(\d+)\D+(\d+)\D+(\d+)/)
  return m ? [+m[1] / 255, +m[2] / 255, +m[3] / 255] : [0, 0, 0]
}
const scale = { x: 1, y: 1, ox: 0, oy: 0 }
// Map screen uv (y up) to image uv the way Windows places the wallpaper on this monitor.
function fitWallpaper() {
  if (!img.has) return
  const sw = W * dpr, sh = H * dpr, ia = img.w / img.h, sa = sw / sh
  let sx = 1, sy = 1
  switch (img.fit) {
    case 'fill': case 'span': if (ia > sa) sx = sa / ia; else sy = ia / sa; break
    case 'fit': if (ia > sa) sy = ia / sa; else sx = sa / ia; break
    case 'stretch': break
    case 'center': case 'tile': sx = sw / img.w; sy = sh / img.h; break
  }
  scale.x = sx; scale.y = sy
  if (img.fit === 'tile') { scale.ox = 0; scale.oy = 1 - sy }  // tiles start at the top-left
  else { scale.ox = (1 - sx) / 2; scale.oy = (1 - sy) / 2 }
}
async function applyWallpaper(src: string, fit: Fit, bg: string) {
  img.fit = fit; img.bg = parseBg(bg)
  if (src) {
    const im = new Image()
    im.src = src
    await im.decode()
    gl.bindTexture(gl.TEXTURE_2D, img.tex)
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true)
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, im)
    img.w = im.naturalWidth; img.h = im.naturalHeight; img.has = true
  } else img.has = false // solid-colour desktop
  document.getElementById('pick')!.hidden = true
  fitWallpaper()
  wake()
}

// ---- Input --------------------------------------------------------------------------------------
const panel = document.getElementById('panel')!
const ptr = { x: 0, y: 0, has: false }
const applied = { x: 0, y: 0 }
let idleSteps = 0, overPanel = false, splash = 0   // simulation steps since the last input
const area = { x: 0.5, y: 0.5, set: false }
addEventListener('pointermove', (e) => {
  overPanel = panel.contains(e.target as Node)
  const x = e.clientX / W, y = 1 - e.clientY / H
  if (!ptr.has) { applied.x = x; applied.y = y }
  ptr.x = x; ptr.y = y; ptr.has = true
  if (!overPanel) { idleSteps = 0; wake() }
}, { passive: true })
document.addEventListener('pointerleave', () => { ptr.has = false })
addEventListener('pointerdown', (e) => { if (!panel.contains(e.target as Node)) { splash = 1; idleSteps = 0; wake() } })

// ---- Loop ---------------------------------------------------------------------------------------
function step() {
  const nxt = 1 - cur
  gl.bindFramebuffer(gl.FRAMEBUFFER, state[nxt].f)
  gl.viewport(0, 0, SW, SH)
  gl.useProgram(sim.p)
  gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, state[cur].t)
  gl.uniform1i(sim.u('uState'), 0)
  gl.uniform2f(sim.u('uTexel'), 1 / SW, 1 / SH)
  gl.uniform1f(sim.u('uAspect'), W / H)
  gl.uniform1f(sim.u('uDamp'), dampToKeep(settings.damping))
  gl.uniform1f(sim.u('uRelax'), relaxToKeep(settings.damping))
  gl.uniform1f(sim.u('uSponge'), SPONGE)
  gl.uniform2f(sim.u('uCalmAt'), area.x, area.y)
  gl.uniform1f(sim.u('uCalmR'), settings.radius / H)
  // The pointer's path presses the surface down. Strength follows speed: a slow drift barely
  // dents it (a subtle wake), a fast sweep pushes hard along a long stroke (trail + rings).
  let strength = 0
  const ax = applied.x, ay = applied.y
  if (ptr.has && !overPanel) {
    const dx = ptr.x - applied.x, dy = ptr.y - applied.y
    applied.x += dx * 0.5; applied.y += dy * 0.5
    const moved = Math.hypot(dx * 0.5 * W, dy * 0.5 * H)
    // Saturating push: a slow drift still makes a visible wake, a flick can't make chaos.
    strength = PUSH_MAX * (1 - Math.exp(-moved / PUSH_SPEED))
  } else { applied.x = ptr.x; applied.y = ptr.y }
  if (ptr.has) {
    if (!area.set) { area.x = ptr.x; area.y = ptr.y; area.set = true }
    area.x += (applied.x - area.x) * AREA_FOLLOW
    area.y += (applied.y - area.y) * AREA_FOLLOW
  }
  strength += splash * 0.18   // a click drops a small, soft ripple
  splash = 0
  gl.uniform2f(sim.u('uA'), ax, ay)
  gl.uniform2f(sim.u('uB'), applied.x, applied.y)
  gl.uniform1f(sim.u('uRadius'), (settings.wake / 2) / H)
  gl.uniform1f(sim.u('uStrength'), strength)
  gl.drawArrays(gl.TRIANGLES, 0, 3)
  cur = nxt
  idleSteps++
}
function render() {
  gl.bindFramebuffer(gl.FRAMEBUFFER, null)
  gl.viewport(0, 0, cv.width, cv.height)
  gl.useProgram(ren.p)
  gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, state[cur].t)
  gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, img.tex)
  gl.uniform1i(ren.u('uState'), 0)
  gl.uniform1i(ren.u('uImg'), 1)
  gl.uniform2f(ren.u('uTexel'), 1 / SW, 1 / SH)
  gl.uniform2f(ren.u('uRes'), W, H)
  gl.uniform1f(ren.u('uSlope'), SLOPE)
  gl.uniform1f(ren.u('uRefractPx'), 40 * settings.distortion)
  gl.uniform1f(ren.u('uDisp'), DISPERSION)
  gl.uniform1f(ren.u('uLight'), HIGHLIGHT)
  gl.uniform2f(ren.u('uScale'), scale.x, scale.y)
  gl.uniform2f(ren.u('uOffset'), scale.ox, scale.oy)
  gl.uniform1f(ren.u('uTile'), img.fit === 'tile' ? 1 : 0)
  gl.uniform1f(ren.u('uHasImg'), img.has ? 1 : 0)
  gl.uniform3f(ren.u('uBg'), img.bg[0], img.bg[1], img.bg[2])
  gl.uniform2f(ren.u('uArea'), area.x, area.y)
  gl.uniform1f(ren.u('uAreaR'), settings.radius)
  gl.drawArrays(gl.TRIANGLES, 0, 3)
}
let raf = 0, last = 0, acc = 0
function frame(now: number) {
  raf = 0
  acc += Math.min((now - last) / 1000, 0.1)
  last = now
  let n = 0
  while (acc >= STEP && n < 8) { step(); acc -= STEP; n++ }
  // Idle time is counted in simulated time, so a slow machine never sleeps before the water is flat.
  if (idleSteps * STEP < IDLE_SLEEP) { render(); raf = requestAnimationFrame(frame); return }
  // Going to sleep: the water is practically flat by now; clear the last trace and draw it still.
  for (const st of state) { gl.bindFramebuffer(gl.FRAMEBUFFER, st.f); gl.clearColor(0, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT) }
  render()
}
function wake() {
  if (raf) return
  last = performance.now(); acc = 0
  raf = requestAnimationFrame(frame)
}

// ---- Controls -----------------------------------------------------------------------------------
const sliders: [keyof Settings, (v: number) => string][] = [
  ['wake', (v) => `${v} px`],
  ['distortion', (v) => `${v.toFixed(2)}×`],
  ['damping', (v) => `${v}`],
  ['radius', (v) => `${v} px`],
]
for (const [key, fmt] of sliders) {
  const input = document.getElementById(key) as HTMLInputElement
  const out = document.getElementById(`${key}-v`)!
  const sync = () => { out.textContent = fmt(settings[key]) }
  input.value = String(settings[key]); sync()
  input.addEventListener('input', () => { settings[key] = Number(input.value); sync(); save(settings); idleSteps = 0; wake() })
}
document.getElementById('reset')!.addEventListener('click', () => {
  Object.assign(settings, DEFAULTS); save(settings)
  for (const [key, fmt] of sliders) {
    (document.getElementById(key) as HTMLInputElement).value = String(settings[key])
    document.getElementById(`${key}-v`)!.textContent = fmt(settings[key])
  }
})
const hint = document.getElementById('hint')!
let hintTimer = 0
const setHidden = (hidden: boolean) => {
  panel.hidden = hidden
  clearTimeout(hintTimer)
  hint.hidden = !hidden
  if (hidden) hintTimer = window.setTimeout(() => { hint.hidden = true }, 2500)
}
document.getElementById('hide')!.addEventListener('click', () => setHidden(true))
addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    window.close()
    // If the browser refuses (it was opened some other way), say how to leave.
    setTimeout(() => { hint.textContent = 'Close this window with Alt+F4'; hint.hidden = false }, 300)
  } else if ((e.key === 'h' || e.key === 'H') && !(e.target instanceof HTMLInputElement && e.target.type !== 'range')) {
    setHidden(!panel.hidden)
  }
})

// ---- Start --------------------------------------------------------------------------------------
addEventListener('resize', resize)
resize()
if (window.WALLPAPER) {
  void applyWallpaper(window.WALLPAPER.src, window.WALLPAPER.style, window.WALLPAPER.bg)
} else {
  // Opened directly (not via the launcher): let the user pick or drop an image.
  const pick = document.getElementById('pick')!
  pick.hidden = false
  const file = document.getElementById('file') as HTMLInputElement
  const read = (f?: File) => { if (f) { const r = new FileReader(); r.onload = () => void applyWallpaper(String(r.result), 'fill', 'rgb(0,0,0)'); r.readAsDataURL(f) } }
  file.addEventListener('change', () => read(file.files?.[0]))
  addEventListener('dragover', (e) => e.preventDefault())
  addEventListener('drop', (e) => { e.preventDefault(); read(e.dataTransfer?.files[0]) })
}

