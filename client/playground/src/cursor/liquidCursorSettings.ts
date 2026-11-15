// Tuning for the liquid-glass cursor. Change the defaults here, or call
// liquidCursorStore.set({...}) at runtime (the "Droplet test bench" page has sliders for this).

export type LiquidCursorSettings = {
  enabled: boolean
  /** 'water':   the whole page is a sheet of water; the pointer drags waves through it (no lens).
   *  'droplet': small drop pinned to the pointer, replaces the arrow.
   *  'ball':    a glass ball lens that trails the (still visible) arrow, like the Vercel-logo shot. */
  variant: 'water' | 'droplet' | 'ball'
  /** Diameter of the droplet/ball, or of the water brush (how wide a wake the pointer drags), CSS px. */
  size: number
  /** Refraction strength: 0 = flat glass, 1 = default, 2 = very thick drop. */
  refraction: number
  /** Max stretch along the direction of travel (0.5 = up to 1.5x long). Capped so flicks stay drop-shaped. */
  stretch: number
  /** Spring stiffness (1/s²). Higher = snappier shape changes. */
  stiffness: number
  /** Spring damping (1/s). Lower = more overshoot; ~2·√stiffness is critically damped (no overshoot). */
  damping: number
  /** Colour split at edges (0 = none). Red and blue bend this fraction less/more than green. */
  dispersion: number
}

export const DROPLET_PRESET: LiquidCursorSettings = {
  enabled: true,
  variant: 'droplet',
  size: 30,
  refraction: 1,
  stretch: 0.55,
  stiffness: 420,
  damping: 19, // damping ratio ≈ 0.46: one visible overshoot, then settles
  dispersion: 0,
}

// Ball lens tuned to the "Liquid Glass animation in Vercel logo" shot: ~1.8x upright centre,
// mirrored rim, orange/blue fringes on hard edges, smooth near-critically-damped trailing.
export const BALL_LENS_PRESET: LiquidCursorSettings = {
  enabled: true,
  variant: 'ball',
  size: 112,
  refraction: 1,
  stretch: 0.08,
  stiffness: 130,
  damping: 21, // damping ratio ≈ 0.92: glides in, barely overshoots
  dispersion: 0.02,
}

// Water: the Rain-hero effect for the whole site. Only size (wake width), refraction and
// dispersion apply; the wave physics live in WaterCursor.tsx.
export const WATER_PRESET: LiquidCursorSettings = {
  enabled: true,
  variant: 'water',
  size: 90,
  refraction: 1,
  stretch: 0,
  stiffness: 0,
  damping: 0,
  dispersion: 0.03,
}

/** What the site starts with. */
export const LIQUID_CURSOR_DEFAULTS = WATER_PRESET

let state = { ...LIQUID_CURSOR_DEFAULTS }
const listeners = new Set<() => void>()

export const liquidCursorStore = {
  get: () => state,
  set(patch: Partial<LiquidCursorSettings>) {
    state = { ...state, ...patch }
    listeners.forEach((l) => l())
  },
  reset() { this.set(LIQUID_CURSOR_DEFAULTS) },
  subscribe(l: () => void) {
    listeners.add(l)
    return () => { listeners.delete(l) }
  },
}
