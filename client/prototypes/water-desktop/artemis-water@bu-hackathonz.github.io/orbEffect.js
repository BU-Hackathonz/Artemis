// The Artemis agent orb: a soft, living glass blob, modelled on a reference clip of a liquid-glass
// orb (original-eda42fff….mp4). What the clip shows, and how each part is drawn here:
//   - the outline never holds still: it slowly morphs between oval, egg and rounded-triangle
//     shapes (a sum of low angular harmonics, ±~10% of the radius, ~2-3 s period)
//   - the body is clear: what is behind it shows through, pulled inward toward the rim like a
//     thick glass ball, slightly darkened and cooled
//   - a broad white "window" reflection sweeps across it as the orb tumbles (a procedural studio
//     environment, reflected off a fake-3D sphere normal that slowly rotates)
//   - strong rainbow fringes (red/green/blue/cyan) on the edges of every bright band, from
//     sampling the reflection and the refraction at several wavelengths
//   - liquid caustic filaments churning on one side, and a bright rim whose colour slides around
//     the edge
// Energy (from JS) makes it wobble harder: commands, keystrokes and landings kick it.

import Cogl from 'gi://Cogl';
import GObject from 'gi://GObject';
import Shell from 'gi://Shell';

const DECLARATIONS = `
uniform vec2 uView;     // target width, height (px)
uniform vec4 uOrb;      // centre x, y (px), radius (px), visibility 0..1
uniform vec4 uShape;    // motion direction x, y, stretch (0 = round), energy (0 = calm)
uniform vec4 uLight;    // highlights, dispersion, morph time (s), tumble angle (rad)

vec3 spectral(float t) {
  return vec3(exp(-pow((t - 0.15) / 0.2, 2.0)) + 0.18 * exp(-pow((t - 0.95) / 0.1, 2.0)),
              exp(-pow((t - 0.5) / 0.19, 2.0)),
              exp(-pow((t - 0.85) / 0.2, 2.0)));
}

// Studio lighting reflected in the glass (y points down the screen, z toward the viewer).
float env(vec3 r) {
  // A broad soft window: the bright band that sweeps across the orb.
  float band = 1.7 * exp(-pow((dot(r, normalize(vec3(0.85, -0.35, 0.4))) - 0.2) / 0.26, 2.0));
  band *= smoothstep(-0.15, 0.55, dot(r, normalize(vec3(-0.3, -0.8, 0.5))));
  // A second, dimmer panel on the other side.
  float band2 = 0.6 * exp(-pow((dot(r, normalize(vec3(-0.7, 0.5, 0.5))) - 0.35) / 0.18, 2.0));
  band2 *= smoothstep(0.0, 0.6, dot(r, normalize(vec3(0.2, 0.9, 0.3))));
  // A small hot key light.
  float key = 1.6 * pow(max(dot(r, normalize(vec3(-0.55, -0.55, 0.62))), 0.0), 60.0);
  // A dim bounce from below.
  float bounce = 0.25 * smoothstep(0.35, 0.95, r.y);
  return band + band2 + key + bounce;
}

mat3 tumble(float a) {
  float c = cos(a), s = sin(a);
  float b = a * 0.63 + 0.4;
  float cb = cos(b), sb = sin(b);
  mat3 ry = mat3(c, 0.0, -s,  0.0, 1.0, 0.0,  s, 0.0, c);
  mat3 rx = mat3(1.0, 0.0, 0.0,  0.0, cb, sb,  0.0, -sb, cb);
  return rx * ry;
}
`

const CODE = `
vec2 uv = cogl_tex_coord_in[0].st;
vec4 base = texture2D(cogl_sampler0, uv);
vec3 col = base.rgb;
vec2 p = uv * uView;
vec2 q = p - uOrb.xy;
float R = uOrb.z;
float vis = uOrb.w;
if (vis > 0.0 && dot(q, q) < R * R * 2.8) {
  // Orb space (radius 1): stretched along the motion, narrowed across it.
  vec2 d = uShape.xy;
  vec2 dp = vec2(-d.y, d.x);
  float st = uShape.z;
  vec2 ql = (d * (dot(q, d) / (1.0 + st)) + dp * (dot(q, dp) * (1.0 + st * 0.4))) / R;
  float ang = atan(ql.y, ql.x);
  float t = uLight.z;
  float e = uShape.w;
  // The living outline.
  float amp = 1.0 + e * 1.2;
  float outline = 1.0 + amp * (0.055 * sin(2.0 * ang + t * 0.83)
                             + 0.040 * sin(3.0 * ang - t * 0.61 + 1.3)
                             + 0.022 * sin(4.0 * ang + t * 1.17 + 2.1)
                             + 0.012 * sin(5.0 * ang - t * 1.60 + 0.4));
  float rho = length(ql) / outline;
  float aa = 1.3 / R;
  float inside = 1.0 - smoothstep(1.0 - aa, 1.0 + aa * 0.5, rho);
  if (inside > 0.0) {
    float r1 = min(rho, 1.0);
    vec2 dir = ql / max(length(ql), 1e-4);
    vec2 xy = dir * r1;
    float z = sqrt(max(1.0 - r1 * r1, 0.0));
    float edge = 1.0 - z;
    vec3 N = vec3(xy, z);

    // Refraction: the view through a thick glass ball, pulled inward near the rim, split into
    // 7 wavelengths that each bend a little differently.
    vec2 nq = q / max(length(q), 1e-4);
    vec2 through = q * 0.86 - nq * R * 0.55 * pow(edge, 1.4);
    float spread = uLight.y * (0.4 + 3.0 * edge);
    vec3 acc = vec3(0.0), wsum = vec3(0.0);
    for (int i = 0; i < 7; i++) {
      float w = (float(i) + 0.5) / 7.0;
      vec3 sw = spectral(w);
      acc += texture2D(cogl_sampler0, (uOrb.xy + through * (1.0 + (w - 0.5) * spread)) / uView).rgb * sw;
      wsum += sw;
    }
    vec3 orb = (acc / wsum) * vec3(0.84, 0.76, 0.9) + vec3(0.03, 0.012, 0.05);

    float hl = uLight.x;
    // Reflections of the studio, off the tumbling surface, with rainbow edges.
    mat3 M = tumble(uLight.w);
    vec3 Nr = M * N;
    vec3 V = M * vec3(0.0, 0.0, -1.0);
    vec3 refl = vec3(0.0);
    vec3 rsum = vec3(0.0);
    for (int i = 0; i < 5; i++) {
      float w = (float(i) + 0.5) / 5.0;
      vec3 sw = spectral(w);
      vec3 Nw = normalize(Nr + (M * vec3(xy, 0.0)) * (w - 0.5) * 0.7);
      refl += env(reflect(V, Nw)) * sw;
      rsum += sw;
    }
    refl /= rsum;
    float fres = 0.26 + 0.74 * pow(edge, 1.8);
    orb += refl * fres * 1.05 * hl;

    // Liquid caustic filaments churning on one side of the orb.
    vec2 w2 = xy * 2.6;
    w2 += 0.55 * vec2(sin(w2.y * 1.9 + t * 0.9), sin(w2.x * 1.5 - t * 0.7 + 1.0));
    w2 += 0.30 * vec2(sin(w2.y * 3.1 - t * 1.3 + 2.0), sin(w2.x * 2.7 + t * 1.1));
    float s = sin(w2.x * 2.3 + w2.y * 1.6 + t * 0.6);
    float fil = 1.0 - smoothstep(0.0, 0.5, abs(s));
    fil *= fil;
    vec2 side = vec2(cos(uLight.w * 0.7 + 1.9), sin(uLight.w * 0.7 + 1.9));
    float mask = smoothstep(0.0, 0.8, dot(xy, side)) * smoothstep(0.35, 0.8, r1);
    vec3 swirlCol = mix(vec3(0.85, 0.62, 1.0), spectral(0.5 + 0.48 * sin(w2.x * 1.1 + w2.y * 0.7 + t * 0.2)), 0.65);
    orb += swirlCol * fil * mask * 0.85 * hl;

    // A bright rim whose colour slides around the edge.
    float rim = smoothstep(0.8, 0.975, rho) * (1.0 - smoothstep(0.975, 1.0, rho));
    float rimGain = 0.35 + 0.65 * pow(0.5 + 0.5 * sin(ang * 2.0 + uLight.w * 1.3), 2.0);
    vec3 rimHue = spectral(0.5 + 0.48 * sin(ang + uLight.w * 0.5));
    orb += mix(vec3(0.78, 0.55, 1.0), rimHue, 0.45) * rim * rimGain * 0.8 * hl;

    col = mix(col, orb, inside * vis);
  }
}
cogl_color_out = vec4(col, base.a) * cogl_color_in;
`

export const OrbEffect = GObject.registerClass(
class OrbEffect extends Shell.GLSLEffect {
    _init(params) {
        super._init(params);
        this._loc = {
            view: this.get_uniform_location('uView'),
            orb: this.get_uniform_location('uOrb'),
            shape: this.get_uniform_location('uShape'),
            light: this.get_uniform_location('uLight'),
        };
    }

    vfunc_build_pipeline() {
        this.add_glsl_snippet(Cogl.SnippetHook.FRAGMENT, DECLARATIONS, CODE, true);
    }

    /**
     * @param {number[]} view   [width, height]
     * @param {number[]} orb    [centreX, centreY, radius, visibility]  (this actor's pixels)
     * @param {number[]} shape  [dirX, dirY, stretch, energy]
     * @param {number[]} light  [highlights, dispersion, morphTime, tumbleAngle]
     */
    update(view, orb, shape, light) {
        this.set_uniform_float(this._loc.view, 2, view);
        this.set_uniform_float(this._loc.orb, 4, orb);
        this.set_uniform_float(this._loc.shape, 4, shape);
        this.set_uniform_float(this._loc.light, 4, light);
        // Repaint the actor itself, not just the effect: queue_repaint() re-runs the shader on a
        // cached snapshot of the actor, which was seen to go stale (the wallpaper vanished).
        const actor = this.get_actor();
        if (actor) actor.queue_redraw();
        else this.queue_repaint();
    }
});
