// The water shader: a Shell.GLSLEffect attached to a monitor's background actor.
//
// GNOME's effect snippets can't keep simulation state between frames, so the water is analytic:
// each disturbance ("drop") the pointer leaves is a short-lived source that grows into one crisp
// expanding ring (a crest and a trough) plus a small wake dent where it was made. Only their
// slope is needed: the wallpaper is sampled at an offset along the slope (refraction, with a soft
// cap and a faint colour split), and thin glossy lines are lit from an exaggerated copy of it.
// Everything is masked to a soft circle around the pointer, so the rest of the desktop stays still.

import Cogl from 'gi://Cogl';
import GObject from 'gi://GObject';
import Shell from 'gi://Shell';

export const MAX_DROPS = 24;

// One uniform per drop: Shell.GLSLEffect.set_uniform_float only delivers the first element of a
// uniform array, so an array would silently render a single drop.
// uDropN: x, y (px), age (s), amplitude.  uFormN: ring speed (px/s), ring width (px), wake gain,
// reach (px). Each drop carries its own form, set from how fast the pointer was moving.
const DROP_UNIFORMS = Array.from({length: MAX_DROPS}, (_, i) => `uniform vec4 uDrop${i};\nuniform vec4 uForm${i};`).join('\n')

const DECLARATIONS = `
${DROP_UNIFORMS}                   // each: x, y (px), age (s), amplitude
uniform vec4 uView;                // width, height (px), area centre x, y (px)
uniform vec4 uWave;                // ring speed (px/s), ring width (px), wake width (px), lifetime (s)
uniform vec4 uLook;                // bend gain (px per unit slope), active radius (px), highlights, dispersion
uniform float uCap;                // most the wallpaper can ever be shifted (px)
uniform vec4 uTrail;               // agent flow: tint rgb, glow on the slopes (0 = pointer mode: no tint)
uniform float uCount;

// tanh() is GLSL 1.30+; GNOME 46's Cogl compiles snippets as GLSL 1.10, where it doesn't exist.
float softCap(float x) {
  float e = exp(2.0 * clamp(x, -10.0, 10.0));
  return (e - 1.0) / (e + 1.0);
}

vec3 spectral(float t) {
  return vec3(exp(-pow((t - 0.15) / 0.2, 2.0)) + 0.18 * exp(-pow((t - 0.95) / 0.1, 2.0)),
              exp(-pow((t - 0.5) / 0.19, 2.0)),
              exp(-pow((t - 0.85) / 0.2, 2.0)));
}

vec2 dropSlope(vec4 d, vec4 f, vec4 prev, vec2 p) {
  vec2 rel = p - d.xy;
  float dist = length(rel);
  if (dist < 0.001 || d.w <= 0.0) return vec2(0.0);
  float age = d.z;
  // Each drop owns a small patch of water that shrinks as it ages, so the trail tapers behind
  // the pointer instead of being cut off by one circle around it.
  float reach = f.w * (1.0 - 0.5 * age / uWave.w);
  float patch = 1.0 - smoothstep(reach * 0.45, reach, dist);
  if (patch <= 0.0) return vec2(0.0);
  float life = clamp(1.0 - age / uWave.w, 0.0, 1.0);
  float fade = life * life * (3.0 - 2.0 * life);           // smooth fade to zero at end of life
  // Ring: derivative of a gaussian bump centred on the expanding front (one crest, one trough).
  float r = f.x * age;
  float s = f.y;
  float x = dist - r;
  float ring = -(1.0 - x * x / (s * s)) * exp(-x * x / (2.0 * s * s)) / s;
  ring *= inversesqrt(1.0 + r / 60.0);                      // spreads out as it grows
  ring *= smoothstep(0.0, s, dist);                         // a round ripple is flat at its centre (no seam)
  // Wake: a dent where the drop was made, gone in a fraction of the lifetime.
  // Wake: a channel along the segment back to the previous drop, so the trail is one continuous
  // groove rather than a row of separate dents (stroke breaks over 80 px are not bridged).
  float w = s * 1.3;
  vec2 c = d.xy;
  vec2 ab = prev.xy - d.xy;
  float segLen = length(ab);
  if (prev.w > 0.0 && segLen > 0.5 && segLen < 80.0) c = d.xy + ab * clamp(dot(p - d.xy, ab) / (segLen * segLen), 0.0, 1.0);
  vec2 relw = p - c;
  float dw = length(relw);
  float wake = f.z * dw / (w * w) * exp(-dw * dw / (2.0 * w * w)) * exp(-age * 4.0);
  return d.w * fade * patch * (ring * (rel / dist) + wake * (relw / max(dw, 0.001)));
}
`

const SUM = Array.from({length: MAX_DROPS}, (_, i) => `  if (uCount > ${i}.5) g += dropSlope(uDrop${i}, uForm${i}, ${i > 0 ? `uDrop${i - 1}` : 'vec4(0.0)'}, p);`).join('\n')

const CODE = `
vec2 uv = cogl_tex_coord_in[0].st;
vec2 p = uv * uView.xy;
vec2 g = vec2(0.0);
${SUM}
vec4 base = texture2D(cogl_sampler0, uv);
vec3 col = base.rgb;
float gl2 = dot(g, g);
if (gl2 > 1e-10) {
  // Refraction along the slope: gentle slopes bend almost linearly (a slow drift still shows),
  // and no amount of overlapping ripples can push the picture more than uCap pixels.
  float sl = sqrt(gl2);
  vec2 off = (g / sl) * uCap * softCap(sl * uLook.x / uCap) / uView.xy;
  // Spectral dispersion: 7 wavelengths, each bent a little differently, recombined with spectral
  // weights. The spread grows with the slope, so steep fronts get real rainbow fringes.
  vec3 nl = normalize(vec3(-g * 7.0, 1.0));
  float steep = 1.0 - nl.z;
  float spread = uLook.w * (0.3 + smoothstep(0.0, 0.25, steep) * 1.7);
  vec3 acc = vec3(0.0);
  vec3 wsum = vec3(0.0);
  for (int k = 0; k < 7; k++) {
    float w = (float(k) + 0.5) / 7.0;
    vec3 sw = spectral(w);
    acc += texture2D(cogl_sampler0, uv + off * (1.0 + (w - 0.5) * spread)).rgb * sw;
    wsum += sw;
  }
  col = acc / wsum;
  // Light like real glass: a thin violet-blue line on fronts facing away from the light, warm
  // caustic glints where the surface focuses light (lower right of a crest) and a white specular
  // glint toward the light. Colour only on the steepest fronts, so it reads as glass, not haze.
  vec3 L = normalize(vec3(-0.45, -0.65, 0.62));             // light from the top-left (y points down)
  float spec = pow(max(reflect(-L, nl).z, 0.0), 220.0) * smoothstep(0.004, 0.04, steep);
  float away = clamp(dot(nl.xy, normalize(vec2(0.55, 0.85))), 0.0, 1.0);
  float toward = clamp(dot(nl.xy, normalize(vec2(-0.55, -0.85))), 0.0, 1.0);
  // Only the steepest part of a front lights up, so these read as thin lines, not a haze.
  float front = smoothstep(0.15, 0.4, steep);
  vec3 violet = vec3(0.35, 0.42, 1.0) * pow(toward, 4.0) * front * 0.14;
  vec3 caustic = vec3(1.0, 0.93, 0.78) * pow(away, 6.0) * front * 0.16;
  col = col * (1.0 - pow(away, 2.0) * front * 0.06 * uLook.z)
      + (vec3(1.0, 0.98, 0.95) * spec * 0.6 + violet + caustic) * uLook.z
        * (uTrail.a > 0.0 ? mix(vec3(1.0), uTrail.rgb * 1.25, 0.7) : vec3(1.0));
  if (uTrail.a > 0.0) {
    float on = smoothstep(0.03, 0.35, sl);
    col *= mix(vec3(1.0), vec3(0.95, 0.88, 1.0), on);  // the moving water looks faintly purple
    vec3 gc = mix(uTrail.rgb, spectral(fract(atan(g.y, g.x) / 6.2832 + 0.5)), 0.25);
    col += gc * on * uTrail.a;
  }
}
cogl_color_out = vec4(col, base.a) * cogl_color_in;
`

export const WaterEffect = GObject.registerClass(
class WaterEffect extends Shell.GLSLEffect {
    _init(params) {
        super._init(params);
        this._dropLoc = Array.from({length: MAX_DROPS}, (_, i) => this.get_uniform_location(`uDrop${i}`));
        this._formLoc = Array.from({length: MAX_DROPS}, (_, i) => this.get_uniform_location(`uForm${i}`));
        this._loc = {
            view: this.get_uniform_location('uView'),
            wave: this.get_uniform_location('uWave'),
            look: this.get_uniform_location('uLook'),
            count: this.get_uniform_location('uCount'),
            cap: this.get_uniform_location('uCap'),
            trail: this.get_uniform_location('uTrail'),
        };
        this._drops = Array.from({length: MAX_DROPS}, () => [0, 0, 0, 0]);
    }

    vfunc_build_pipeline() {
        this.add_glsl_snippet(Cogl.SnippetHook.FRAGMENT, DECLARATIONS, CODE, true);
    }

    /**
     * @param {{x:number,y:number,age:number,amp:number,form:number[]}[]} drops  in this actor's pixels;
     *        form = [ringSpeed, ringWidth, wakeGain, reach]
     * @param {number[]} view  [width, height, areaX, areaY]
     * @param {number[]} wave  [ringSpeed, ringWidth, wakeWidth, lifetime]
     * @param {number[]} look  [bendGain, activeRadius, highlights, dispersion]
     * @param {number} cap  largest shift (px)
     * @param {number[]} trail  [r, g, b, glow]: the agent flow's tint and glow (glow 0 = no tint)
     */
    update(drops, view, wave, look, cap, trail = [1, 1, 1, 0]) {
        this.set_uniform_float(this._loc.trail, 4, trail);
        const n = Math.min(drops.length, MAX_DROPS);
        for (let i = 0; i < MAX_DROPS; i++) {
            const d = i < n ? drops[drops.length - n + i] : null;
            this._drops[i] = d ? [d.x, d.y, d.age, d.amp] : [0, 0, 0, 0];
            this.set_uniform_float(this._dropLoc[i], 4, this._drops[i]);
            this.set_uniform_float(this._formLoc[i], 4, d ? d.form : [1, 1, 0, 1]);
        }
        this.set_uniform_float(this._loc.view, 4, view);
        this.set_uniform_float(this._loc.wave, 4, wave);
        this.set_uniform_float(this._loc.look, 4, look);
        this.set_uniform_float(this._loc.count, 1, [n]);
        this.set_uniform_float(this._loc.cap, 1, [Math.max(cap, 0.01)]);
        // Repaint the actor itself, not just the effect: queue_repaint() re-runs the shader on a
        // cached snapshot of the actor, which was seen to go stale (the wallpaper vanished).
        const actor = this.get_actor();
        if (actor) actor.queue_redraw();
        else this.queue_repaint();
    }
});
