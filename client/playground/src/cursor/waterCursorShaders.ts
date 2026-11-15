// Shaders for the site-wide water cursor. The simulation step is shared with the Rain hero.
export { quadVS, simFS } from '../experiments/water/waterShaders'

// Normals from the height field (2-texel span, like the Rain hero).
const normal = `
vec3 surfaceNormal(sampler2D s, vec2 uv, vec2 texel, float gain) {
  vec2 tx = vec2(texel.x * 2.0, 0.0), ty = vec2(0.0, texel.y * 2.0);
  vec2 g = vec2(texture(s, uv + tx).r - texture(s, uv - tx).r,
                texture(s, uv + ty).r - texture(s, uv - ty).r) * 0.5 * gain;
  return normalize(vec3(-g, 1.0));
}
`

// Displacement map for the SVG filter, written straight in BMP byte order (B, G, R, A) so the
// pixels read back can be wrapped in a BMP header without any re-packing or compression.
// Offset (px) = refract · normal.xy; screen y points down, the simulation's y points up.
export const mapFS = `#version 300 es
precision highp float;
uniform sampler2D uState;
uniform vec2 uTexel;
uniform float uSlope;
uniform float uRefract;  // px per unit slope
uniform float uScale;    // the filter's scale (px): offset = scale · (channel − 0.5)
in vec2 vUv;
out vec4 o;
${normal}
void main() {
  vec3 n = surfaceNormal(uState, vUv, uTexel, uSlope);
  // Soft limit (see the wallpaper prototype): small ripples bend linearly, big waves cap at uRefract / 2.
  float s = length(n.xy);
  vec2 off = (s > 1e-5 ? vec2(n.x, -n.y) / s : vec2(0.0)) * (0.5 * tanh(2.0 * s)) * uRefract;
  vec2 enc = clamp(0.5 + off / uScale, 0.0, 1.0);
  o = vec4(0.5, enc.y, enc.x, 1.0); // B, G, R, A
}
`

// Light on the water, drawn on a transparent canvas above the page (premultiplied alpha):
// slopes facing the light get a little brighter, slopes facing away a little darker, and the
// crests catch sharp highlights. Flat water draws nothing at all.
export const shadeFS = `#version 300 es
precision highp float;
uniform sampler2D uState;
uniform vec2 uTexel;
uniform float uSlope;
uniform float uLight;    // overall strength of shading + highlights
in vec2 vUv;
out vec4 o;
${normal}
void main() {
  vec3 n = surfaceNormal(uState, vUv, uTexel, uSlope);
  float steep = 1.0 - n.z;
  vec2 L2 = normalize(vec2(-0.55, 0.85));
  float facing = clamp(dot(n.xy, -L2), -0.6, 0.6);
  vec3 L = normalize(vec3(-0.45, 0.65, 0.62));
  float spec = pow(max(reflect(-L, n).z, 0.0), 120.0) * smoothstep(0.002, 0.03, steep);
  float white = clamp((spec * 0.9 + max(facing, 0.0) * 0.22) * uLight, 0.0, 1.0);
  float black = clamp(max(-facing, 0.0) * 0.2 * uLight, 0.0, 1.0);
  o = vec4(vec3(white), white + black * (1.0 - white));
}
`
