// The Artemis liquid agent ring: decorative, aria-hidden, drawn on a transparent WebGL2 canvas.
// It mirrors what the app already says in text (task, voice and approval state); it carries no
// information of its own. A browser canvas cannot see the page behind it, so the glass refracts a
// soft backdrop that matches the page colour, and only the additive glow shows outside the liquid.
//
// State is set as targets (setTarget); the renderer eases toward them every frame, and kick()
// adds one-off splashes (a finished task). With reduced motion there is no loop and no splash:
// the ring is drawn once, still, and the state shows only through glow (brightness) and dim (colour).

export type RingTarget = {
  /** 0 = ring, 1 = collapsed into one droplet */
  morph: number
  /** how fast the swells travel around (rad/s) */
  spin: number
  /** steady wobble (needs-attention, held) */
  wobble: number
  /** halo strength */
  glow: number
  /** radius of the droplet's orbit when collapsed (ring units) */
  orbit: number
  /** voice level 0..1 (listening) */
  level: number
  /** 0 = purple, 1 = dimmed and slightly warm (failed) */
  dim: number
}

export const RING_STATES: Record<string, RingTarget> = {
  ready:     { morph: 0, spin: 0.4, wobble: 0,    glow: 0.32, orbit: 0,    level: 0, dim: 0 },
  listening: { morph: 0, spin: 0.9, wobble: 0.12, glow: 0.55, orbit: 0,    level: 1, dim: 0 },
  working:   { morph: 1, spin: 1.2, wobble: 0,    glow: 0.5,  orbit: 0.55, level: 0, dim: 0 },
  attention: { morph: 0, spin: 0.7, wobble: 0.32, glow: 0.72, orbit: 0,    level: 0, dim: 0 },
  running:   { morph: 0, spin: 2.6, wobble: 0.05, glow: 0.6,  orbit: 0,    level: 0, dim: 0 },
  done:      { morph: 0, spin: 0.4, wobble: 0,    glow: 0.42, orbit: 0,    level: 0, dim: 0 },
  failed:    { morph: 0, spin: 0.2, wobble: 0,    glow: 0.16, orbit: 0,    level: 0, dim: 1 },
}

/** Reduced motion: one still ring. Shape and movement are dropped; only glow and dim carry the state. */
export function staticRingTarget(t: RingTarget): RingTarget {
  return { ...t, morph: 0, spin: 0, wobble: 0, orbit: 0, level: 0 }
}

const VS = `#version 300 es
in vec2 aPos;
out vec2 vUv;
void main() { vUv = aPos * 0.5 + 0.5; gl_Position = vec4(aPos, 0.0, 1.0); }`

const FS = `#version 300 es
precision highp float;
in vec2 vUv;
out vec4 o;
uniform vec2 uRes;       // canvas px
uniform float uRadius;   // ring radius (px)
uniform float uTime;
uniform float uSpin;     // integrated spin angle
uniform float uMorph;
uniform float uWobble;
uniform vec2 uSplash;    // splash amount, seed
uniform float uGlow;
uniform vec2 uOrbit;     // droplet offset (ring units)
uniform vec2 uTrail;     // droplet orbit angle (rad), orbit radius (ring units): for its trailing tail
uniform float uLevel;
uniform float uDim;

vec3 spectral(float t) {
  return vec3(exp(-pow((t - 0.15) / 0.2, 2.0)) + 0.18 * exp(-pow((t - 0.95) / 0.1, 2.0)),
              exp(-pow((t - 0.5) / 0.19, 2.0)),
              exp(-pow((t - 0.85) / 0.2, 2.0)));
}
float smin(float a, float b, float k) {
  float h = clamp(0.5 + 0.5 * (b - a) / k, 0.0, 1.0);
  return mix(b, a, h) - k * h * (1.0 - h);
}
float halfWidth() { return mix(0.2, 0.42, uMorph); }

float liquid(vec2 q) {
  float t = uTime, spin = uSpin, m = uMorph;
  float pr = sqrt(max(max(uWobble, uSplash.x), 0.0)) * (1.0 - m);
  float sd0 = uSplash.y;
  q -= pr * 0.06 * vec2(sin(t * 11.0 + sd0), cos(t * 8.7 + sd0 * 1.7));
  vec2 q0 = q;
  q -= uOrbit * m;
  float d = length(q);
  float ang = atan(q.y, q.x);
  float wa = ang + pr * 0.9 * sin(ang + sd0 * 2.3 + t * 3.0);
  float bend = 0.07 * sin(wa + sd0 + t * 4.3) + 0.06 * sin(2.0 * wa - sd0 * 1.9 - t * 6.1)
             + 0.045 * sin(3.0 * wa + sd0 * 3.1 + t * 7.7) + 0.025 * sin(5.0 * wa - sd0 * 0.7 + t * 10.3);
  float thick = 0.45 * sin(wa - sd0 * 2.7 - t * 5.2) + 0.3 * sin(3.0 * wa + sd0 * 1.3 + t * 7.4)
              + 0.18 * sin(4.0 * wa - sd0 * 4.1 - t * 9.6);
  // listening: the ring pulses with the voice
  float lv = uLevel * (0.55 + 0.45 * sin(t * 7.0) * sin(t * 2.3 + 1.0));
  float rc = (0.66 + 0.012 * sin(2.0 * ang - t * 0.6) + pr * bend + lv * 0.025 * sin(3.0 * ang - t * 6.0)) * (1.0 - m);
  float swell = 1.0 + (0.14 * sin(ang - spin) + 0.07 * sin(2.0 * ang - spin * 1.3 + t * 0.4)) * (1.0 - m)
              + pr * thick + lv * 0.3 * (0.5 + 0.5 * sin(4.0 * ang - t * 9.0));
  float sd = abs(d - rc) - halfWidth() * max(swell, 0.25);
  // The flow: a collapsed droplet drags a tapering tail of liquid along its orbit.
  float tm = smoothstep(0.5, 1.0, m) * step(0.01, uTrail.y);
  if (tm > 0.0) {
    for (int k = 1; k <= 6; k++) {
      float fk = float(k);
      float a = uTrail.x - fk * 0.2;
      vec2 c = vec2(cos(a), sin(a)) * uTrail.y;
      sd = smin(sd, length(q0 - c) - halfWidth() * (1.0 - 0.14 * fk) * tm, 0.12);
    }
  }
  float sp = sqrt(max(uSplash.x, 0.0)) * (1.0 - m);
  if (sp > 0.02) {
    for (int k = 0; k < 3; k++) {
      float fk = float(k);
      float a = sd0 * (1.7 + fk) + fk * 2.1 + t * (0.6 - fk * 0.35);
      float reach = 0.66 + 0.42 * sp * (0.7 + 0.3 * sin(fk * 3.7 + sd0));
      sd = smin(sd, length(q - vec2(cos(a), sin(a)) * reach) - 0.075 * sp * (1.0 + 0.4 * fk), 0.16 * sp);
    }
  }
  return sd;
}
float surface(vec2 q) {
  float x = clamp(-liquid(q) / halfWidth(), 0.0, 1.0);
  return sqrt(1.0 - (1.0 - x) * (1.0 - x));
}
// What the glass "sees": the page colour with a faint texture, so refraction reads.
vec3 behind(vec2 p) {
  vec3 base = vec3(0.043, 0.031, 0.078);
  float v = 0.5 + 0.5 * sin(p.x * 9.0 + sin(p.y * 6.0) * 1.5) * sin(p.y * 8.0 - 1.0 + sin(p.x * 5.0));
  return base + vec3(0.07, 0.035, 0.13) * v * (1.0 - smoothstep(0.2, 1.4, length(p)));
}

void main() {
  vec2 p = vec2(vUv.x, 1.0 - vUv.y) * uRes;
  float R = uRadius;
  vec2 q = (p - uRes * 0.5) / R;
  float t = uTime;
  float sd = liquid(q);
  float inside = 1.0 - smoothstep(-0.7, 0.7, sd * R);
  vec3 col = vec3(0.0);
  vec3 purple = mix(vec3(0.7, 0.42, 1.0), vec3(0.55, 0.45, 0.6), uDim);
  if (inside > 0.0) {
    float ep = 0.75 / R;
    vec2 gH = vec2(surface(q + vec2(ep, 0.0)) - surface(q - vec2(ep, 0.0)),
                   surface(q + vec2(0.0, ep)) - surface(q - vec2(0.0, ep))) / (2.0 * ep);
    vec3 n = normalize(vec3(-gH * 0.18, 1.0));
    float steep = 1.0 - n.z;
    vec3 acc = vec3(0.0), ws = vec3(0.0);
    for (int i = 0; i < 5; i++) {
      float wl = (float(i) + 0.5) / 5.0;
      vec3 sw = spectral(wl);
      acc += behind(q - n.xy * 0.4 * (1.0 + (wl - 0.5) * 0.4 * (1.0 + 6.0 * steep))) * sw;
      ws += sw;
    }
    vec3 liq = acc / ws * vec3(0.93, 0.84, 1.0);
    float hl = 1.0 - uDim * 0.55;
    vec3 L = normalize(vec3(-0.5, -0.65, 0.58));
    vec3 refl = vec3(0.0);
    for (int i = 0; i < 3; i++) {
      float wl = (float(i) + 0.5) / 3.0;
      vec3 nw = normalize(n + vec3(n.xy * (wl - 0.5) * 0.6, 0.0));
      vec3 r = reflect(vec3(0.0, 0.0, -1.0), nw);
      float band = exp(-pow((dot(r, normalize(vec3(0.7, -0.45, 0.55))) - 0.25) / 0.2, 2.0))
                 * smoothstep(-0.2, 0.5, dot(r, normalize(vec3(-0.3, -0.8, 0.5))));
      float key = pow(max(dot(r, L), 0.0), 70.0) * 2.2;
      refl += (band + key) * spectral(wl) * 2.4;
    }
    liq += refl / 3.0 * vec3(0.92, 0.8, 1.0) * (0.2 + 0.8 * steep) * hl;
    float caus = pow(max(dot(n.xy, normalize(vec2(0.55, 0.8))), 0.0), 2.0) * smoothstep(0.02, 0.3, steep)
               * (1.0 - smoothstep(0.5, 0.9, steep));
    liq += vec3(1.0, 0.8, 0.98) * caus * 0.35 * hl;
    float rim = smoothstep(0.2, 0.65, steep);
    float na = atan(n.y, n.x);
    liq += mix(purple + vec3(0.08, 0.13, 0.0), spectral(0.5 + 0.48 * sin(na + t * 0.4)), 0.4) * rim * 0.75 * hl;
    float depth = -sd;
    liq += vec3(0.9, 0.78, 1.0) * smoothstep(0.0, 0.03, depth) * (1.0 - smoothstep(0.03, 0.09, depth)) * 0.24 * hl;
    col = liq * inside;
  }
  // halo (additive: alpha stays 0 outside the liquid)
  float dpx = max(sd, 0.0) * R;
  float halo = (exp(-dpx / (R * 0.2)) * 0.75 + exp(-dpx / (R * 0.06)) * 0.5) * (1.0 - smoothstep(1.2, 1.5, length(q)));
  vec3 gcol = mix(purple, spectral(0.5 + 0.45 * sin(atan(q.y, q.x) - uSpin + t * 0.3)), 0.2 * (1.0 - uDim));
  float g = uGlow * (1.0 + 0.8 * uMorph) * (0.82 + 0.18 * sin(t * 1.4));
  col += gcol * halo * (1.0 - 0.5 * inside) * g;
  o = vec4(col, inside);
}`

const NAMES = ['uRes', 'uRadius', 'uTime', 'uSpin', 'uMorph', 'uWobble', 'uSplash', 'uGlow', 'uOrbit', 'uTrail', 'uLevel', 'uDim'] as const

export class AgentRing {
  private gl: WebGL2RenderingContext
  private prog: WebGLProgram
  private buf: WebGLBuffer
  private u = {} as Record<(typeof NAMES)[number], WebGLUniformLocation | null>
  private cur: RingTarget = { ...RING_STATES.ready }
  private target: RingTarget = { ...RING_STATES.ready }
  private spin = Math.random() * 6
  private time = Math.random() * 50
  private orbitAngle = 0
  private splash = 0
  private seed = 0
  private raf = 0
  private last = 0
  private size = 0
  private _reduced: boolean
  private resizeObserver: ResizeObserver | null = null
  private canvas: HTMLCanvasElement

  /** reduced: draw one still frame instead of running the animation loop. */
  constructor(canvas: HTMLCanvasElement, reduced = false) {
    this.canvas = canvas
    this._reduced = reduced
    const gl = canvas.getContext('webgl2', { premultipliedAlpha: true, alpha: true, antialias: false })
    if (!gl) throw new Error('WebGL2 is not available')
    this.gl = gl
    const prog = gl.createProgram()!
    for (const [type, src] of [[gl.VERTEX_SHADER, VS], [gl.FRAGMENT_SHADER, FS]] as const) {
      const sh = gl.createShader(type)!
      gl.shaderSource(sh, src)
      gl.compileShader(sh)
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh) ?? 'shader')
      gl.attachShader(prog, sh)
    }
    gl.bindAttribLocation(prog, 0, 'aPos')
    gl.linkProgram(prog)
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog) ?? 'link')
    this.prog = prog
    for (const n of NAMES) this.u[n] = gl.getUniformLocation(prog, n)
    this.buf = gl.createBuffer()!
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buf)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW)
    if (typeof ResizeObserver !== 'undefined') {
      this.resizeObserver = new ResizeObserver(() => { if (this._reduced) this.draw() })
      this.resizeObserver.observe(canvas)
    }
    this.start()
  }

  /** True while the ring is a still frame (prefers-reduced-motion). Setting it switches modes at once. */
  get reduced(): boolean { return this._reduced }
  set reduced(value: boolean) {
    if (value === this._reduced) return
    this._reduced = value
    this.stop()
    this.splash = 0
    this.start()
  }

  setTarget(t: RingTarget) {
    this.target = { ...t }
    if (this._reduced) this.snapToTarget()
  }

  /** A one-off splash: a finished task (0.45) or a click (0.6). Never under reduced motion. */
  kick(amount = 0.6) {
    if (this._reduced) return
    this.splash = Math.max(this.splash, amount)
    this.seed = Math.random() * 50
  }

  destroy() {
    this.stop()
    this.resizeObserver?.disconnect()
    this.resizeObserver = null
    this.gl.deleteProgram(this.prog)
    this.gl.deleteBuffer(this.buf)
  }

  private start() {
    if (this._reduced) { this.snapToTarget(); return }
    this.last = 0
    this.raf = requestAnimationFrame(this.frame)
  }

  private stop() {
    cancelAnimationFrame(this.raf)
    this.raf = 0
  }

  private snapToTarget() {
    this.cur = staticRingTarget(this.target)
    this.draw()
  }

  private frame = (now: number) => {
    this.raf = requestAnimationFrame(this.frame)
    const dt = Math.min(Math.max((now - (this.last || now)) / 1000, 0), 0.05)
    this.last = now
    const ease = 1 - Math.exp(-dt * 4)
    const c = this.cur, g = this.target
    for (const k of Object.keys(c) as (keyof RingTarget)[]) c[k] += (g[k] - c[k]) * (k === 'morph' ? 1 - Math.exp(-dt * 3) : ease)
    this.time += dt
    this.spin += dt * c.spin
    this.orbitAngle += dt * 2.4
    this.splash *= Math.exp(-dt * 1.8)
    this.draw()
  }

  private draw() {
    const c = this.cur
    const r = this.canvas.getBoundingClientRect()
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    const w = Math.max(1, Math.round(r.width * dpr)), h = Math.max(1, Math.round(r.height * dpr))
    if (w !== this.canvas.width || h !== this.canvas.height) { this.canvas.width = w; this.canvas.height = h }
    this.size = Math.min(w, h)
    const gl = this.gl
    gl.viewport(0, 0, w, h)
    gl.clearColor(0, 0, 0, 0)
    gl.clear(gl.COLOR_BUFFER_BIT)
    gl.useProgram(this.prog)
    gl.uniform2f(this.u.uRes, w, h)
    gl.uniform1f(this.u.uRadius, this.size * 0.3)
    gl.uniform1f(this.u.uTime, this.time)
    gl.uniform1f(this.u.uSpin, this.spin)
    gl.uniform1f(this.u.uMorph, c.morph)
    gl.uniform1f(this.u.uWobble, c.wobble)
    gl.uniform2f(this.u.uSplash, this.splash, this.seed)
    gl.uniform1f(this.u.uGlow, c.glow)
    gl.uniform2f(this.u.uOrbit, Math.cos(this.orbitAngle) * c.orbit, Math.sin(this.orbitAngle) * c.orbit)
    gl.uniform2f(this.u.uTrail, this.orbitAngle % (Math.PI * 2), c.orbit)
    gl.uniform1f(this.u.uLevel, c.level)
    gl.uniform1f(this.u.uDim, c.dim)
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buf)
    gl.enableVertexAttribArray(0)
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0)
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
  }
}
