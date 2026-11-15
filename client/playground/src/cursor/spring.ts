// Damped spring, integrated in fixed substeps so the motion is the same at 30, 60 or 144 fps.
export type Spring = { x: number; v: number }
export const SUBSTEP = 1 / 240

export function springTo(s: Spring, target: number, stiffness: number, damping: number, dt: number) {
  for (let t = 0; t < dt - 1e-9; t += SUBSTEP) {
    const h = Math.min(SUBSTEP, dt - t)
    s.v += (stiffness * (target - s.x) - damping * s.v) * h
    s.x += s.v * h
  }
}
