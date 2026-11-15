// Builds the displacement map that makes a rounded rectangle act like a thick glass lens.
//
// feDisplacementMap moves each output pixel to sample from (x + S*(R-0.5), y + S*(G-0.5)).
// We encode an inward offset that is 0 in the flat centre and grows sharply in a bevel
// band near the rim. Because the offset grows faster than the distance to the edge
// (slope < -1), the rim samples "past" itself and the content there appears compressed
// and mirrored, like the upside-down "Home" / "Library" text along the lens rim in the shot.

export type LensMap = { url: string; scale: number }

export function buildLensMap(w: number, h: number, radius: number, bevel: number, maxShift: number): LensMap {
  const c = document.createElement('canvas')
  c.width = w
  c.height = h
  const ctx = c.getContext('2d')!
  const img = ctx.createImageData(w, h)
  const scale = maxShift * 2 // channel 0..1 maps to -maxShift..+maxShift
  const hx = w / 2, hy = h / 2
  const r = Math.min(radius, hx, hy)

  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      // Signed distance to a rounded rect (negative inside).
      const px = x + 0.5 - hx, py = y + 0.5 - hy
      const qx = Math.abs(px) - (hx - r), qy = Math.abs(py) - (hy - r)
      const ox = Math.max(qx, 0), oy = Math.max(qy, 0)
      const sdf = Math.hypot(ox, oy) + Math.min(Math.max(qx, qy), 0) - r
      const depth = -sdf

      // Outward normal of the nearest edge.
      let nx = 0, ny = 0
      if (qx > 0 && qy > 0) { const l = Math.hypot(qx, qy) || 1; nx = qx / l; ny = qy / l }
      else if (qx > qy) nx = 1
      else ny = 1
      nx *= Math.sign(px) || 1
      ny *= Math.sign(py) || 1

      let dx = 0, dy = 0
      if (depth > 0 && depth < bevel) {
        const t = 1 - depth / bevel          // 1 at rim, 0 where the flat top starts
        const shift = maxShift * t * t       // convex profile: steep at the edge
        dx = -nx * shift                     // sample inward
        dy = -ny * shift
      }
      const i = (y * w + x) * 4
      img.data[i] = Math.round((0.5 + dx / scale) * 255)
      img.data[i + 1] = Math.round((0.5 + dy / scale) * 255)
      img.data[i + 2] = 128
      img.data[i + 3] = 255
    }
  }
  ctx.putImageData(img, 0, 0)
  return { url: c.toDataURL(), scale }
}

// Chromium (and so Tauri's WebView2) can point backdrop-filter at an SVG filter. Others can't.
export const supportsSvgBackdrop = () =>
  typeof CSS !== 'undefined' && CSS.supports('backdrop-filter', 'url(#x)') && /Chrome\//.test(navigator.userAgent)
