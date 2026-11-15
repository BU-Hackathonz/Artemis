// 2D particle liquid trapped inside an invisible circle (the agent bubble's container).
//
// Method: "double density relaxation" (Clavet, Beaudoin & Poulin, "Particle-based Viscoelastic
// Fluid Simulation", 2005). Each step: forces -> viscosity impulses -> predict positions ->
// relax density (pressure + near-pressure; negative pressure gives surface tension, so the
// liquid beads, splits and merges) -> collide with the container wall -> velocities from the
// position change. Units: px and simulation steps (velocities in px/step).
//
// Particles live in container space (origin = container centre). Moving the container doesn't
// move them: its acceleration is applied the other way (an inertial force), so the liquid
// sloshes when the bubble is dragged, zipped or shaken.

export type FluidParams = {
  count: number
  /** interaction radius (px) */
  h: number
  /** rest density as a multiple of the density the liquid starts with (>1 squeezes, <1 pulls) */
  densityScale: number
  stiffness: number
  nearStiffness: number
  /** px/step², pointing down the screen */
  gravity: number
  /** linear and quadratic viscosity */
  viscosity: number
  viscosityQuad: number
  /** tangential push around the centre (px/step²): a vortex */
  swirl: number
  /** pull toward the centre (px/step² at the wall): keeps a zero-g blob gathered */
  centring: number
  /** container radius (px) */
  radius: number
  /** 0..1 tangential velocity lost on touching the wall */
  wallFriction: number
  /** where the liquid starts */
  start: 'centre' | 'bottom'
}

export class Fluid {
  readonly n: number
  readonly x: Float32Array
  readonly y: Float32Array
  private px: Float32Array
  private py: Float32Array
  private vx: Float32Array
  private vy: Float32Array
  private rho: Float32Array
  private rhoN: Float32Array
  private restDensity = 1
  // neighbour pairs, rebuilt each step
  private pi = new Int32Array(0)
  private pj = new Int32Array(0)
  private pq = new Float32Array(0)
  private pdx = new Float32Array(0)
  private pdy = new Float32Array(0)
  private pairs = 0
  p: FluidParams

  constructor(p: FluidParams) {
    this.p = p
    const n = (this.n = p.count)
    this.x = new Float32Array(n)
    this.y = new Float32Array(n)
    this.px = new Float32Array(n)
    this.py = new Float32Array(n)
    this.vx = new Float32Array(n)
    this.vy = new Float32Array(n)
    this.rho = new Float32Array(n)
    this.rhoN = new Float32Array(n)
    const cap = n * 40
    this.pi = new Int32Array(cap)
    this.pj = new Int32Array(cap)
    this.pq = new Float32Array(cap)
    this.pdx = new Float32Array(cap)
    this.pdy = new Float32Array(cap)
    this.reset()
  }

  /** Lay the particles out on a hex grid, then calibrate the rest density from that packing. */
  reset() {
    const { h, radius, start } = this.p
    const s = h * 0.48
    const pts: [number, number][] = []
    const lim = radius - h * 0.4
    for (let row = -60; row <= 60; row++) {
      for (let col = -60; col <= 60; col++) {
        const x = (col + (row & 1) * 0.5) * s
        const y = row * s * 0.866
        if (Math.hypot(x, y) < lim) pts.push([x, y])
      }
    }
    // centre: the n points nearest the middle; bottom: the n lowest
    pts.sort(start === 'bottom' ? (a, b) => b[1] - a[1] : (a, b) => Math.hypot(...a) - Math.hypot(...b))
    for (let i = 0; i < this.n; i++) {
      const [x, y] = pts[i % pts.length]
      this.x[i] = x + (Math.random() - 0.5) * 0.2
      this.y[i] = y + (Math.random() - 0.5) * 0.2
      this.vx[i] = this.vy[i] = 0
    }
    this.findPairs()
    this.densities()
    // calibrate on the most surrounded half of the particles (the inside of the liquid)
    const sorted = Array.from(this.rho).sort((a, b) => b - a)
    const inner = sorted.slice(0, Math.max(1, sorted.length >> 1))
    this.restDensity = (inner.reduce((a, b) => a + b, 0) / inner.length) * this.p.densityScale
  }

  /** Kick every particle (px/step). */
  impulse(ix: number, iy: number) {
    for (let i = 0; i < this.n; i++) {
      this.vx[i] += ix * (0.7 + Math.random() * 0.6)
      this.vy[i] += iy * (0.7 + Math.random() * 0.6)
    }
  }

  /** Mean speed (px/step): how much the liquid is moving. */
  energy() {
    let e = 0
    for (let i = 0; i < this.n; i++) e += Math.hypot(this.vx[i], this.vy[i])
    return e / this.n
  }

  /**
   * One simulation step. (ax, ay): the container's acceleration in px/step².
   */
  step(ax: number, ay: number) {
    const { n, x, y, vx, vy, px, py } = this
    const p = this.p

    // 1. forces: gravity, the inertial push from the container, swirl, centring
    for (let i = 0; i < n; i++) {
      vx[i] -= ax
      vy[i] += p.gravity - ay
      if (p.swirl !== 0 || p.centring !== 0) {
        const d = Math.hypot(x[i], y[i]) + 1e-3
        const t = d / p.radius
        vx[i] += (-y[i] / d) * p.swirl * t - (x[i] / d) * p.centring * t
        vy[i] += (x[i] / d) * p.swirl * t - (y[i] / d) * p.centring * t
      }
    }

    // 2. viscosity impulses between approaching neighbours
    this.findPairs()
    if (p.viscosity > 0 || p.viscosityQuad > 0) {
      for (let k = 0; k < this.pairs; k++) {
        const i = this.pi[k], j = this.pj[k], q = this.pq[k]
        const rx = this.pdx[k], ry = this.pdy[k]
        const u = (vx[i] - vx[j]) * rx + (vy[i] - vy[j]) * ry
        if (u <= 0) continue
        const I = (1 - q) * (p.viscosity * u + p.viscosityQuad * u * u) * 0.5
        vx[i] -= I * rx; vy[i] -= I * ry
        vx[j] += I * rx; vy[j] += I * ry
      }
    }

    // 3. predict
    for (let i = 0; i < n; i++) {
      px[i] = x[i]; py[i] = y[i]
      x[i] += vx[i]; y[i] += vy[i]
    }

    // 4. double density relaxation
    this.findPairs()
    this.densities()
    for (let k = 0; k < this.pairs; k++) {
      const i = this.pi[k], j = this.pj[k], q = this.pq[k]
      const P = p.stiffness * (this.rho[i] + this.rho[j] - 2 * this.restDensity) * 0.5
      const Pn = p.nearStiffness * (this.rhoN[i] + this.rhoN[j]) * 0.5
      const D = (P * (1 - q) + Pn * (1 - q) * (1 - q)) * 0.5
      const dx = this.pdx[k] * D, dy = this.pdy[k] * D
      x[i] -= dx; y[i] -= dy
      x[j] += dx; y[j] += dy
    }

    // 5. the invisible wall
    const R = p.radius - p.h * 0.25
    for (let i = 0; i < n; i++) {
      const d = Math.hypot(x[i], y[i])
      if (d > R) {
        const nx = x[i] / d, ny = y[i] / d
        x[i] = nx * R; y[i] = ny * R
        if (p.wallFriction > 0) {
          // lose some of the sliding motion along the wall
          const tx = -ny, ty = nx
          const slide = (x[i] - px[i]) * tx + (y[i] - py[i]) * ty
          x[i] -= tx * slide * p.wallFriction
          y[i] -= ty * slide * p.wallFriction
        }
      }
    }

    // 6. velocities from the motion (capped, so a hard yank can't blow it up)
    const vmax = p.h * 0.9
    for (let i = 0; i < n; i++) {
      let ux = x[i] - px[i], uy = y[i] - py[i]
      const sp = Math.hypot(ux, uy)
      if (sp > vmax) { ux *= vmax / sp; uy *= vmax / sp }
      vx[i] = ux; vy[i] = uy
    }
  }

  private findPairs() {
    const { n, x, y } = this
    const h = this.p.h, h2 = h * h
    let k = 0
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const dx = x[j] - x[i], dy = y[j] - y[i]
        const r2 = dx * dx + dy * dy
        if (r2 >= h2 || r2 < 1e-8) continue
        if (k >= this.pi.length) this.grow()
        const r = Math.sqrt(r2)
        this.pi[k] = i; this.pj[k] = j
        this.pq[k] = r / h
        this.pdx[k] = dx / r; this.pdy[k] = dy / r
        k++
      }
    }
    this.pairs = k
  }

  private densities() {
    this.rho.fill(0)
    this.rhoN.fill(0)
    for (let k = 0; k < this.pairs; k++) {
      const a = 1 - this.pq[k]
      const a2 = a * a, a3 = a2 * a
      this.rho[this.pi[k]] += a2; this.rho[this.pj[k]] += a2
      this.rhoN[this.pi[k]] += a3; this.rhoN[this.pj[k]] += a3
    }
  }

  private grow() {
    const cap = this.pi.length * 2
    const g = <T extends Int32Array | Float32Array>(a: T, C: new (n: number) => T) => { const b = new C(cap); b.set(a); return b }
    this.pi = g(this.pi, Int32Array)
    this.pj = g(this.pj, Int32Array)
    this.pq = g(this.pq, Float32Array)
    this.pdx = g(this.pdx, Float32Array)
    this.pdy = g(this.pdy, Float32Array)
  }
}
