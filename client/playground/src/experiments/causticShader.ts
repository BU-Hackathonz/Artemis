// GLSL for the caustics cursor. One full-screen pass draws, per pixel:
//   1. a sunlit sand floor (the set in the reference render)
//   2. the liquid's shadow on that floor, with caustics: light focused through the liquid into
//      bright, rainbow-split ripples inside the shadow
//   3. the liquid itself: a metaball surface from the particle positions, which refracts the
//      floor, carries an oil-slick thin-film rainbow, and a specular highlight
// Particles come from JS as vec3(x, y, radius) in CSS pixels.

export const MAX_BLOBS = 32

export const vertexSrc = `
attribute vec2 aPos;
void main() { gl_Position = vec4(aPos, 0.0, 1.0); }
`

export const fragmentSrc = `
precision highp float;
uniform vec2 uRes;
uniform float uDpr;
uniform float uTime;
uniform vec3 uB[${MAX_BLOBS}];

// Metaball field + analytic gradient. Each blob uses a bounded falloff (1 - d^2/R^2)^3 with
// R = 2r: flat on top (clear centre), steep near the edge (where the colour and highlights live).
// A lone blob crosses the surface threshold T at exactly its radius r.
const float T = 0.4219;
vec3 field(vec2 p) {
  float f = 0.0; vec2 g = vec2(0.0);
  for (int i = 0; i < ${MAX_BLOBS}; i++) {
    vec3 b = uB[i];
    float R2 = 4.0 * b.z * b.z + 0.001;
    vec2 d = p - b.xy;
    float x = dot(d, d) / R2;
    if (x < 1.0) {
      float k = 1.0 - x;
      f += k * k * k;
      g -= 6.0 * k * k * d / R2;
    }
  }
  return vec3(f, g);
}

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }

// Water caustic pattern: an iterated sine warp that bunches up into bright, web-like lines.
float caustic(vec2 uv, float t) {
  vec2 p = mod(uv * 6.28318, 6.28318) - 250.0;
  vec2 i = p;
  float c = 1.0;
  for (int n = 0; n < 5; n++) {
    float tt = t * (1.0 - 3.5 / float(n + 1));
    i = p + vec2(cos(tt - i.x) + sin(tt + i.y), sin(tt - i.y) + cos(tt + i.x));
    c += 1.0 / length(vec2(p.x / (sin(i.x + tt) / 0.005), p.y / (cos(i.y + tt) / 0.005)));
  }
  c /= 5.0;
  c = 1.17 - pow(c, 1.4);
  return clamp(pow(abs(c), 8.0), 0.0, 1.5);
}

vec3 ground(vec2 p) {
  vec2 uv = p / uRes;
  float sun = length((uv - vec2(0.78, 0.12)) * vec2(1.0, 1.25));
  vec3 sand = vec3(0.91, 0.87, 0.79);
  vec3 col = sand * (0.86 + 0.22 * smoothstep(1.1, 0.0, sun));
  col = mix(col, vec3(0.17, 0.33, 0.35), smoothstep(0.6, 1.25, sun) * 0.6);
  col += (hash(floor(p * 0.9)) - 0.5) * 0.03;
  return col;
}

void main() {
  vec2 p = vec2(gl_FragCoord.x, uRes.y * uDpr - gl_FragCoord.y) / uDpr;
  float t = uTime;

  // Shadow: the liquid seen from the light, shifted down-right (light from the top-left).
  vec3 s = field(p - vec2(26.0, 38.0));
  float sh = smoothstep(T * 0.6, T * 1.3, s.x);
  // Light bends through the liquid, so the caustic ripples are pushed around by its slope.
  vec2 cuv = (p + s.yz * 260.0) / 210.0;
  float tc = t * 0.4;
  vec3 caus = vec3(
    caustic(cuv + vec2(0.006, 0.0), tc),
    caustic(cuv, tc),
    caustic(cuv - vec2(0.006, 0.0), tc));
  vec3 col = ground(p);
  col *= 1.0 - sh * 0.45;
  col += sh * caus * vec3(1.0, 0.86, 0.62) * 0.9;
  // Light also piles up along the inner rim of the shadow.
  col += vec3(1.0, 0.82, 0.55) * smoothstep(T * 0.9, T * 1.15, s.x) * smoothstep(T * 2.2, T * 1.2, s.x) * 0.35;

  // Liquid surface.
  vec3 l = field(p);
  float a = smoothstep(T * 0.94, T * 1.06, l.x);
  if (a > 0.0) {
    // Height profile like a droplet: steep at the rim (sqrt), perfectly flat once thick enough,
    // so the centre stays clear and all the colour and highlights sit around the edge.
    float u = clamp((l.x - T) / 2.2, 0.0, 1.0);
    float dh = u < 1.0 ? min(0.5 / sqrt(max(u, 0.0004)) / 2.2, 40.0) : 0.0;
    vec3 n = normalize(vec3(-l.yz * dh * 14.0, 1.0));
    float fres = pow(1.0 - n.z, 1.3);
    float thick = u;
    vec3 liquid = ground(p + n.xy * 40.0) * mix(vec3(0.97, 0.99, 1.0), vec3(0.9, 0.97, 0.98), thick);
    // Thin-film interference: rainbow bands that slide with slope, thickness and time.
    float phase = thick * 0.9 + fres * 1.6 + t * 0.12 + dot(p, vec2(0.0018, 0.0026));
    vec3 film = 0.72 + 0.28 * cos(6.28318 * (phase + vec3(0.0, 0.33, 0.67)));
    liquid = mix(liquid, film * ground(p + n.xy * 20.0) * 1.2, clamp(fres * 1.2, 0.0, 0.6));
    vec3 L = normalize(vec3(-0.5, -0.7, 0.8));
    float spec = pow(max(reflect(-L, n).z, 0.0), 70.0);
    liquid += spec * 1.1 + pow(fres, 3.0) * 0.18;
    // Broad soft sheen across the body, offset toward the light, so it reads as a rounded volume.
    liquid += 0.12 * smoothstep(0.2, 0.9, thick) * smoothstep(0.6, -0.2, dot(normalize(n.xy + 1e-4), vec2(0.6, 0.8)));
    liquid = mix(liquid * 0.78, liquid, smoothstep(T, T * 1.25, l.x));
    col = mix(col, liquid, a);
  }
  gl_FragColor = vec4(col, 1.0);
}
`
