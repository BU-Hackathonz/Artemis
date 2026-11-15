// Displacement map for a water droplet, built once (it's normalised, so it works at any size).
//
// feDisplacementMap samples the backdrop at p + scale·(R−0.5, G−0.5). Every offset here points
// toward the centre, so the glass only ever samples content that is under itself:
//   - a small magnification term across the whole drop (proportional to the radius), and
//   - a rim term that ramps up steeply in the outer half, where a real droplet's surface curves
//     away. That's what bends a straight line passing under the edge.
// Offsets are stored in units of the radius R; the filter's scale turns them into pixels.

export const MAP_MAX = 0.75 // largest offset, in radii; scale = 2 · MAP_MAX · R

const MAG = 0.16  // centre magnification
const RIM = 0.56  // rim bend
const RIM_START = 0.45

export type LensMap = { url: string; max: number }

export function buildDropletMap(res = 160): LensMap {
  const c = document.createElement('canvas')
  c.width = c.height = res
  const ctx = c.getContext('2d')!
  const img = ctx.createImageData(res, res)
  for (let y = 0; y < res; y++) {
    for (let x = 0; x < res; x++) {
      const u = ((x + 0.5) / res) * 2 - 1
      const v = ((y + 0.5) / res) * 2 - 1
      const rho = Math.hypot(u, v)
      let ox = 0, oy = 0
      if (rho < 1) {
        const t = Math.max((rho - RIM_START) / (1 - RIM_START), 0)
        const rim = RIM * Math.pow(t, 2.2)
        const nx = rho > 1e-4 ? u / rho : 0
        const ny = rho > 1e-4 ? v / rho : 0
        ox = -u * MAG - nx * rim
        oy = -v * MAG - ny * rim
      }
      const i = (y * res + x) * 4
      img.data[i] = Math.round((0.5 + ox / (2 * MAP_MAX)) * 255)
      img.data[i + 1] = Math.round((0.5 + oy / (2 * MAP_MAX)) * 255)
      img.data[i + 2] = 128
      img.data[i + 3] = 255
    }
  }
  ctx.putImageData(img, 0, 0)
  return { url: c.toDataURL(), max: MAP_MAX }
}

// Ball lens: a real ray trace through a glass sphere. Each vertical view ray refracts into the
// front surface, crosses the ball, refracts out of the back surface, and lands on the page a small
// gap below. Inside the focal length the centre is upright and magnified; rim rays bend much harder
// (spherical aberration) and fold back, which gives the mirrored band around the edge.
// With ior 1.25 / gap 0.15: ~1.8x at the centre, fold at ~78% of the radius, every ray lands inside
// the lens, so the backdrop filter never needs pixels from outside the element.
export function buildBallMap(ior = 1.25, gap = 0.15, res = 192): LensMap {
  const MAX = 1
  const c = document.createElement('canvas')
  c.width = c.height = res
  const ctx = c.getContext('2d')!
  const img = ctx.createImageData(res, res)
  for (let y = 0; y < res; y++) {
    for (let x = 0; x < res; x++) {
      const u = ((x + 0.5) / res) * 2 - 1
      const v = ((y + 0.5) / res) * 2 - 1
      const rho = Math.hypot(u, v)
      let ox = 0, oy = 0
      if (rho < 1 && rho > 1e-5) {
        const L = traceBall(Math.min(rho, 0.999), ior, gap)
        const k = (L - rho) / rho  // radial: land at L·n
        ox = u * k
        oy = v * k
      }
      const i = (y * res + x) * 4
      img.data[i] = Math.round((0.5 + ox / (2 * MAX)) * 255)
      img.data[i + 1] = Math.round((0.5 + oy / (2 * MAX)) * 255)
      img.data[i + 2] = 128
      img.data[i + 3] = 255
    }
  }
  ctx.putImageData(img, 0, 0)
  return { url: c.toDataURL(), max: MAX }
}

type V3 = [number, number, number]
const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
function refract(I: V3, N: V3, eta: number): V3 | null {
  const d = dot(I, N), k = 1 - eta * eta * (1 - d * d)
  if (k < 0) return null
  const a = eta * d + Math.sqrt(k)
  return [eta * I[0] - a * N[0], eta * I[1] - a * N[1], eta * I[2] - a * N[2]]
}
// Radial landing position (in radii) of the ray entering the unit sphere at distance rho from its axis.
export function traceBall(rho: number, ior: number, gap: number): number {
  const P: V3 = [rho, 0, Math.sqrt(1 - rho * rho)]
  const R1 = refract([0, 0, -1], P, 1 / ior)
  if (!R1) return 0
  const s = -2 * dot(P, R1)
  const Q: V3 = [P[0] + s * R1[0], P[1] + s * R1[1], P[2] + s * R1[2]]
  const R2 = refract(R1, [-Q[0], -Q[1], -Q[2]], ior)
  if (!R2) return 0 // total internal reflection: sample the centre
  const t = (Q[2] + 1 + gap) / -R2[2]
  return Math.max(Math.min(Q[0] + t * R2[0], 0.98), -0.98)
}

// SVG filters in backdrop-filter are a Chromium feature (Chrome, Edge, and Tauri's WebView2 on
// Windows). Firefox and Safari parse the property but don't apply url() filters to the backdrop.
export function canRefract(): boolean {
  const brands = (navigator as Navigator & { userAgentData?: { brands: { brand: string }[] } }).userAgentData?.brands
  const chromium = brands ? brands.some((b) => /Chromium/i.test(b.brand)) : /Chrome\//.test(navigator.userAgent)
  return chromium && typeof CSS !== 'undefined' && CSS.supports('backdrop-filter', 'url(#x)')
}
