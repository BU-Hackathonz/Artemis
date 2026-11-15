// WebGL2 shaders for the rain hero: a height-field water surface (wave equation) driven by the
// pointer, rendered as refraction of the hero artwork with highlights and slope shading.

export const quadVS = `#version 300 es
in vec2 aPos;
out vec2 vUv;
void main() { vUv = aPos * 0.5 + 0.5; gl_Position = vec4(aPos, 0.0, 1.0); }
`

// One simulation step. State texture: r = height, g = vertical velocity.
// The pointer's path this step (segment A→B) pushes the surface down, like a finger dragged
// through shallow water: that leaves a wake behind it and sends rings outward.
export const simFS = `#version 300 es
precision highp float;
uniform sampler2D uState;
uniform vec2 uTexel;
uniform vec2 uA;
uniform vec2 uB;
uniform float uAspect;
uniform float uRadius;
uniform float uStrength;
uniform float uDamp;
uniform float uRelax;   // height kept per step: pulls dents that stopped moving back to flat
uniform float uSponge;  // width (texels) of an absorbing border; 0 = walls reflect
uniform vec2 uCalmAt;   // centre (uv) of the active area
uniform float uCalmR;   // radius of the active area (in units of height); 0 = whole surface active
in vec2 vUv;
out vec4 o;

float segDist(vec2 p, vec2 a, vec2 b) {
  vec2 pa = p - a, ba = b - a;
  float h = clamp(dot(pa, ba) / max(dot(ba, ba), 1e-9), 0.0, 1.0);
  return length(pa - ba * h);
}

void main() {
  vec4 s = texture(uState, vUv);
  float h = s.r, v = s.g;
  float avg = 0.25 * (
    texture(uState, vUv + vec2(uTexel.x, 0.0)).r + texture(uState, vUv - vec2(uTexel.x, 0.0)).r +
    texture(uState, vUv + vec2(0.0, uTexel.y)).r + texture(uState, vUv - vec2(0.0, uTexel.y)).r);
  v += (avg - h) * 1.9;   // just under the stability limit (2.0)
  v *= uDamp;
  h += v;
  h = mix(h, avg, 0.06);  // a touch of diffusion kills grid-scale noise, keeps the broad waves
  h *= uRelax;
  // Absorption: near the screen edges (uSponge) and outside the active area around the cursor
  // (uCalmR). One absorber using the stronger of the two; stacking them made the scheme unstable.
  float absorb = 0.0;
  if (uSponge > 0.0) {
    vec2 px = vUv / uTexel;
    vec2 toEdge = min(px, 1.0 / uTexel - px);
    absorb = 1.0 - smoothstep(0.0, uSponge, min(toEdge.x, toEdge.y));
  }
  if (uCalmR > 0.0) {
    float r = length((vUv - uCalmAt) * vec2(uAspect, 1.0));
    absorb = max(absorb, smoothstep(uCalmR, uCalmR * 1.25, r));
  }
  v *= mix(1.0, 0.88, absorb);
  h *= mix(1.0, 0.96, absorb);

  if (uStrength > 0.0) {
    vec2 sc = vec2(uAspect, 1.0);
    float d = segDist(vUv * sc, uA * sc, uB * sc);
    float k = smoothstep(uRadius, 0.0, d);
    h -= uStrength * k * k;
  }
  o = vec4(h, v, 0.0, 1.0);
}
`

// Render: refract the artwork through the surface.
export const renderFS = `#version 300 es
precision highp float;
uniform sampler2D uState;
uniform sampler2D uArt;
uniform vec2 uTexel;
uniform float uRefract;   // UV offset per unit slope
uniform float uDisp;      // colour split
uniform float uSlope;     // height-to-normal gain
in vec2 vUv;
out vec4 o;

void main() {
  // Slope over a 2-texel span (smoother normals than nearest neighbours).
  vec2 tx = vec2(uTexel.x * 2.0, 0.0), ty = vec2(0.0, uTexel.y * 2.0);
  float hl = texture(uState, vUv - tx).r, hr = texture(uState, vUv + tx).r;
  float hd = texture(uState, vUv - ty).r, hu = texture(uState, vUv + ty).r;
  vec2 g = vec2(hr - hl, hu - hd) * 0.5 * uSlope;
  vec3 n = normalize(vec3(-g, 1.0));

  // Refraction with a little dispersion: red bends least, blue most.
  vec2 off = n.xy * uRefract;
  vec3 col = vec3(
    texture(uArt, vUv + off * (1.0 - uDisp)).r,
    texture(uArt, vUv + off).g,
    texture(uArt, vUv + off * (1.0 + uDisp)).b);

  // Slopes facing the light (top-left) brighten, slopes facing away darken: reads as volume.
  vec2 L2 = normalize(vec2(-0.55, 0.85));
  float facing = dot(n.xy, -L2);
  col *= 1.0 + clamp(facing, -0.6, 0.6) * 0.55;

  // Sharp highlights on the crests, plus a broad soft sheen.
  vec3 L = normalize(vec3(-0.45, 0.65, 0.62));
  vec3 R = reflect(-L, n);
  float spec = pow(max(R.z, 0.0), 120.0);
  float sheen = pow(max(R.z, 0.0), 14.0);
  float steep = 1.0 - n.z;
  col += vec3(1.0, 0.98, 0.93) * (spec * 1.1 * smoothstep(0.002, 0.03, steep) + sheen * 0.05 * smoothstep(0.0, 0.05, steep));
  o = vec4(col, 1.0);
}
`
