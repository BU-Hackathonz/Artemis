// Glass cursor shader. Everything on the stage is drawn here in one pass:
//   - the scene (a canvas-drawn page) with a soft glass shadow that has a caustic hot spot
//   - a background change that spreads from the click point, with a refracting ripple at its front
//   - the glass: a 2.5D shape (SDF + rounded-bevel height field). Light refracts through it at
//     several wavelengths (each with its own index of refraction), weighted back into RGB, which
//     gives real dispersion instead of a fixed three-channel split. Fresnel reflection, two
//     specular lights and a rim finish it.

export const SAMPLES = 12

export const vertexSrc = `
attribute vec2 aPos;
void main() { gl_Position = vec4(aPos, 0.0, 1.0); }
`

export const fragmentSrc = `
precision highp float;
uniform vec2 uRes;
uniform float uDpr;
uniform sampler2D uA;
uniform sampler2D uB;
uniform vec2 uClick;
uniform float uReveal;     // radius of the new background (px); < 0 when idle
uniform vec2 uPos;
uniform vec2 uVel;
uniform float uSize;
uniform float uShape;      // 0 sphere, 1 pill, 2 lens, 3 blob
uniform float uTime;
uniform float uIor;
uniform float uDisp;
uniform float uRefr;

vec2 toUv(vec2 p) { return vec2(p.x / uRes.x, 1.0 - p.y / uRes.y); }

vec3 scene(vec2 p) {
  if (uReveal < 0.0) return texture2D(uA, toUv(p)).rgb;
  vec2 d = p - uClick;
  float r = length(d);
  // Ripple at the reveal front: the scene is pushed outward there, like a wave of glass.
  float ring = exp(-pow((r - uReveal) / 22.0, 2.0));
  vec2 q = p - d / max(r, 1.0) * ring * 18.0;
  float m = smoothstep(uReveal + 2.0, uReveal - 10.0, r);
  vec3 c = mix(texture2D(uA, toUv(q)).rgb, texture2D(uB, toUv(q)).rgb, m);
  return c + ring * 0.08;
}

float sdShape(vec2 q) {
  float r = uSize;
  if (uShape < 0.5) return length(q) - r;
  if (uShape < 1.5) {
    float rad = r * 0.72;
    vec2 d = abs(q) - vec2(r * 1.45 - rad, 0.0);
    return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0) - rad;
  }
  if (uShape < 2.5) return (length(q / vec2(1.3, 0.78)) - r) * 0.8;
  float a = atan(q.y, q.x);
  float w = 0.07 * sin(3.0 * a + uTime * 1.2) + 0.05 * sin(5.0 * a - uTime * 1.7) + 0.03 * sin(7.0 * a + uTime * 2.3);
  return (length(q) - r * (1.0 + w)) * 0.9;
}

// Thickness profile: a rounded bevel (quarter circle) from the edge inward. The bevel depth must
// not exceed the shape's inner distance, or the top comes to a point (a starburst in the render).
float thick() {
  float k = uShape < 0.5 ? 1.0 : uShape < 1.5 ? 0.7 : uShape < 2.5 ? 0.76 : 0.7;
  return uSize * k;
}
float heightAt(vec2 q) {
  float x = clamp(-sdShape(q) / thick(), 0.0, 1.0);
  return sqrt(1.0 - (1.0 - x) * (1.0 - x));
}

vec3 spectral(float t) {
  return vec3(
    exp(-pow((t - 0.12) / 0.2, 2.0)) + 0.2 * exp(-pow((t - 0.95) / 0.1, 2.0)),
    exp(-pow((t - 0.5) / 0.19, 2.0)),
    exp(-pow((t - 0.88) / 0.2, 2.0)));
}

// Glass in local space: q relative to the centre, after squash-and-stretch.
vec2 local(vec2 p, vec2 at) {
  vec2 q = p - at;
  float s = clamp(length(uVel) / 28.0, 0.0, 1.0);
  if (s > 0.001) {
    vec2 v = normalize(uVel);
    float along = dot(q, v) / (1.0 + 0.4 * s);
    float across = dot(q, vec2(-v.y, v.x)) / (1.0 - 0.18 * s);
    q = v * along + vec2(-v.y, v.x) * across;
  }
  return q;
}

void main() {
  vec2 p = vec2(gl_FragCoord.x, uRes.y * uDpr - gl_FragCoord.y) / uDpr;
  vec3 col = scene(p);
  if (uSize < 1.0) { gl_FragColor = vec4(col, 1.0); return; }
  float T = thick();

  // Shadow on the page, offset away from the top-left light, with light focused in its middle.
  vec2 qs = local(p, uPos + vec2(10.0, 18.0) * (uSize / 70.0));
  float es = -sdShape(qs);
  float shadow = smoothstep(-24.0, 8.0, es);
  float focus = smoothstep(0.35, 0.9, clamp(es / T, 0.0, 1.0));
  col = col * (1.0 - shadow * 0.2) + focus * vec3(1.0, 0.96, 0.85) * 0.22;

  vec2 q = local(p, uPos);
  float e = -sdShape(q);
  float alpha = smoothstep(-1.0, 1.0, e);
  if (alpha <= 0.0) { gl_FragColor = vec4(col, 1.0); return; }

  float h = heightAt(q);
  vec2 g = vec2(heightAt(q + vec2(1.0, 0.0)) - heightAt(q - vec2(1.0, 0.0)),
                heightAt(q + vec2(0.0, 1.0)) - heightAt(q - vec2(0.0, 1.0))) * 0.5 * T;
  g = clamp(g, -8.0, 8.0);
  vec3 N = normalize(vec3(-g + uVel * 0.012, 1.0)); // velocity tilts the glass a little

  // Dispersion: refract each wavelength with its own index, sample the page, re-weight to RGB.
  vec3 I = vec3(0.0, 0.0, -1.0);
  float depth = T * (0.3 + 0.9 * h) * uRefr;
  vec3 acc = vec3(0.0), ws = vec3(0.0);
  for (int i = 0; i < ${SAMPLES}; i++) {
    float t = (float(i) + 0.5) / float(${SAMPLES});
    float ior = uIor * (1.0 + (t - 0.5) * uDisp);
    vec3 R = refract(I, N, 1.0 / ior);
    vec2 off = R.xy / max(-R.z, 0.25) * depth;
    vec3 w = spectral(t);
    acc += scene(p + off) * w;
    ws += w;
  }
  vec3 glass = acc / ws;

  // Fresnel: glancing angles reflect a soft bright environment.
  float F0 = pow((uIor - 1.0) / (uIor + 1.0), 2.0);
  float F = F0 + (1.0 - F0) * pow(1.0 - N.z, 5.0);
  vec3 env = mix(vec3(1.0), vec3(0.86, 0.9, 1.0), clamp(0.5 + N.y * 0.6, 0.0, 1.0));
  glass = mix(glass, env, clamp(F * 1.5, 0.0, 0.85));

  vec3 V = vec3(0.0, 0.0, 1.0);
  vec3 L1 = normalize(vec3(-0.45, -0.6, 0.75));
  vec3 L2 = normalize(vec3(0.6, 0.5, 0.6));
  float spec = pow(max(dot(N, normalize(L1 + V)), 0.0), 140.0) * 1.3
             + pow(max(dot(N, normalize(L1 + V)), 0.0), 14.0) * 0.07
             + pow(max(dot(N, normalize(L2 + V)), 0.0), 90.0) * 0.35;
  glass += spec;
  glass += smoothstep(1.8, 0.0, e) * 0.35;          // bright rim
  glass *= 1.0 - smoothstep(5.0, 1.5, e) * 0.08;     // faint dark line just inside it

  gl_FragColor = vec4(mix(col, glass, alpha), 1.0);
}
`
