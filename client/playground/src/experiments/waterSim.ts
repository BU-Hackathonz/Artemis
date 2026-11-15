// A small cohesive-water simulation (SPH-flavoured), kept free of DOM so it can be tuned headless.
//   - short range: particles push apart, so the body keeps its volume
//   - longer range: they pull together (surface tension), so stretched water reels itself back in
//   - viscosity: neighbours share velocity, so the body moves as one mass
//   - floor friction: water resting on a surface doesn't glide as one piece
//   - the finger only touches the water nearest to it (on press: a held patch). Everything else is
//     dragged along by surface tension against the friction, which is what makes it stream.

export type Drop = { x: number; y: number; vx: number; vy: number }

export const WATER = {
  rest: 15,        // preferred spacing between particles (px)
  reach: 34,       // cohesion range (px): short, like real surface tension
  push: 0.2,       // volume stiffness: keeps particles near their spacing instead of crowding
  tension: 0.012,  // surface tension between neighbours
  gather: 0.0045,  // whole-body pull toward its centre: how fast a stream reels back into a pool
  viscosity: 0.01,
  friction: 0.88,  // velocity kept per step: lower = more floor drag, more stretch
  maxSpeed: 3.2,   // px per step: keeps everything slow and heavy
  gripRadius: 8,   // how big a patch the hand holds while pressing
  grip: 0.03,
  touchRadius: 26, // on hover, how far from the finger the water gets tugged
  touch: 0.012,
}

export function makeWater(n: number, x: number, y: number): Drop[] {
  // Sunflower packing gives an even, round puddle.
  return Array.from({ length: n }, (_, i) => {
    const a = i * 2.39996, d = Math.sqrt(i + 0.5) * WATER.rest * 0.5
    return { x: x + Math.cos(a) * d, y: y + Math.sin(a) * d, vx: 0, vy: 0 }
  })
}

// Weights for the particles the hand holds: the nearest one fully, falling off quickly.
export function gripWeights(ps: Drop[], fx: number, fy: number): Float32Array {
  const ds = ps.map((p) => Math.hypot(p.x - fx, p.y - fy))
  const near = Math.min(...ds)
  const s = WATER.gripRadius
  return Float32Array.from(ds, (d) => Math.exp(-((d - near) ** 2) / (2 * s * s)))
}

export function stepWater(ps: Drop[], fx: number, fy: number, grip: Float32Array | null) {
  const W = WATER
  const n = ps.length
  let cx = 0, cy = 0
  for (const p of ps) { cx += p.x; cy += p.y }
  cx /= n; cy /= n

  // The nearest particle's distance, so "near the finger" is measured from the water's edge.
  let near = Infinity
  for (const p of ps) near = Math.min(near, Math.hypot(fx - p.x, fy - p.y))

  ps.forEach((p, i) => {
    p.vx += (cx - p.x) * W.gather
    p.vy += (cy - p.y) * W.gather
    const dx = fx - p.x, dy = fy - p.y
    const w = grip
      ? W.grip * grip[i]
      : W.touch * Math.exp(-((Math.hypot(dx, dy) - near) ** 2) / (2 * W.touchRadius ** 2))
    p.vx += dx * w
    p.vy += dy * w
  })

  for (let i = 0; i < n; i++) {
    const a = ps[i]
    for (let j = i + 1; j < n; j++) {
      const b = ps[j]
      const dx = b.x - a.x, dy = b.y - a.y
      const d = Math.hypot(dx, dy) || 0.01
      if (d > W.reach) continue
      const f = d < W.rest
        ? -(W.rest - d) * W.push
        : (d - W.rest) * W.tension * (1 - (d - W.rest) / (W.reach - W.rest))
      const fx2 = (dx / d) * f, fy2 = (dy / d) * f
      a.vx += fx2; a.vy += fy2
      b.vx -= fx2; b.vy -= fy2
      if (d < W.reach) {
        const vx = (b.vx - a.vx) * W.viscosity, vy = (b.vy - a.vy) * W.viscosity
        a.vx += vx; a.vy += vy
        b.vx -= vx; b.vy -= vy
      }
    }
  }

  for (const p of ps) {
    p.vx *= W.friction
    p.vy *= W.friction
    const v = Math.hypot(p.vx, p.vy)
    if (v > W.maxSpeed) { p.vx *= W.maxSpeed / v; p.vy *= W.maxSpeed / v }
    p.x += p.vx
    p.y += p.vy
  }
}

// Elongation of the body (principal axes of the particle cloud): 1 = round, 3 = a long stream.
export function elongation(ps: Drop[]): number {
  const n = ps.length
  let cx = 0, cy = 0
  for (const p of ps) { cx += p.x; cy += p.y }
  cx /= n; cy /= n
  let xx = 0, yy = 0, xy = 0
  for (const p of ps) { const dx = p.x - cx, dy = p.y - cy; xx += dx * dx; yy += dy * dy; xy += dx * dy }
  const tr = (xx + yy) / n, det = (xx * yy - xy * xy) / (n * n)
  const disc = Math.sqrt(Math.max(tr * tr / 4 - det, 0))
  return Math.sqrt((tr / 2 + disc) / Math.max(tr / 2 - disc, 1e-6))
}
