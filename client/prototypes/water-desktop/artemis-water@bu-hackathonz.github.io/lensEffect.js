// A small liquid-glass lens around the pointer, rendered like a real glass drop:
//   - the centre gently magnifies what is underneath
//   - near the rim, thick-glass refraction pulls the view inward, and light splits into a smooth
//     spectrum: the image is sampled at 7 wavelengths, each bent by a slightly different amount,
//     and recombined with spectral weights (true rainbow fringes, not a 3-channel split)
//   - light: a warm caustic crescent where light focuses (lower right), a thin violet-blue rim line
//     where the edge catches the light, and an iridescent glint on the upper left whose hue slides
//     along the rim
//   - the outline breathes very slightly (an organic, liquid shape) and stretches along fast motion

import Cogl from 'gi://Cogl';
import GObject from 'gi://GObject';
import Shell from 'gi://Shell';

const DECLARATIONS = `
uniform vec2 uView;     // target width, height (px)
uniform vec4 uLens;     // centre x, y (px), radius (px), visibility 0..1
uniform vec4 uShape;    // motion direction x, y, stretch (0 = round), zoom at the centre
uniform vec3 uLight;    // highlights, dispersion, time (s)

vec3 spectral(float t) {
  return vec3(exp(-pow((t - 0.15) / 0.2, 2.0)) + 0.18 * exp(-pow((t - 0.95) / 0.1, 2.0)),
              exp(-pow((t - 0.5) / 0.19, 2.0)),
              exp(-pow((t - 0.85) / 0.2, 2.0)));
}
`

const CODE = `
vec2 uv = cogl_tex_coord_in[0].st;
vec4 base = texture2D(cogl_sampler0, uv);
vec3 col = base.rgb;
vec2 p = uv * uView;
vec2 q = p - uLens.xy;
float vis = uLens.w;
if (vis > 0.0 && dot(q, q) < uLens.z * uLens.z * 4.0) {
  // Lens space: stretched along the motion, slightly narrowed across it.
  vec2 d = uShape.xy;
  vec2 dp = vec2(-d.y, d.x);
  float st = uShape.z;
  vec2 ql = d * (dot(q, d) / (1.0 + st)) + dp * (dot(q, dp) * (1.0 + st * 0.4));
  float ang = atan(ql.y, ql.x);
  float time = uLight.z;
  // A liquid outline: breathes very slightly.
  float wob = 1.0 + 0.04 * sin(2.0 * ang + time * 1.3) + 0.025 * sin(3.0 * ang - time * 0.9 + 1.7);
  float rho = length(ql) / (uLens.z * wob);
  if (rho < 1.0) {
    vec2 n = ql / max(length(ql), 0.001);
    // Magnified centre; thick-glass refraction pulls the view inward near the rim.
    float t = smoothstep(0.45, 1.0, rho);
    float scale = mix(1.0 / uShape.w, 1.0, t);
    float rim = pow(smoothstep(0.55, 1.0, rho), 2.0);
    vec2 bend = -n * uLens.z * 0.35 * rim;
    // Spectral dispersion: 7 wavelengths, each bent a little differently, recombined.
    float spread = uLight.y * (0.25 + rim * 2.0);
    vec3 acc = vec3(0.0);
    vec3 wsum = vec3(0.0);
    for (int i = 0; i < 7; i++) {
      float w = (float(i) + 0.5) / 7.0;
      float f = 1.0 + (w - 0.5) * spread;
      vec3 sw = spectral(w);
      acc += texture2D(cogl_sampler0, (uLens.xy + (q * scale + bend) * f) / uView).rgb * sw;
      wsum += sw;
    }
    col = mix(col, acc / wsum, vis);

    float hl = uLight.x * vis;
    // Thin violet-blue line where the edge catches the light.
    float edge = smoothstep(0.86, 0.985, rho) * (1.0 - smoothstep(0.985, 1.0, rho));
    col += vec3(0.35, 0.42, 1.0) * edge * 0.5 * hl;
    // Warm caustic: light focused into a crescent on the lower right, inside the rim.
    float lr = max(dot(n, normalize(vec2(0.55, 0.83))), 0.0);
    float caus = pow(lr, 3.0) * smoothstep(0.55, 0.84, rho) * (1.0 - smoothstep(0.86, 0.99, rho));
    col += vec3(1.0, 0.93, 0.78) * caus * 0.55 * hl;
    // Iridescent glint on the upper left: its hue slides along the rim.
    float ul = max(dot(n, normalize(vec2(-0.6, -0.8))), 0.0);
    float glint = pow(ul, 2.5) * smoothstep(0.72, 0.92, rho) * (1.0 - smoothstep(0.92, 1.0, rho));
    col += spectral(fract(ang / 6.2832 + rho * 0.8 + time * 0.05)) * glint * 0.5 * hl;
  }
}
cogl_color_out = vec4(col, base.a) * cogl_color_in;
`

export const LensEffect = GObject.registerClass(
class LensEffect extends Shell.GLSLEffect {
    _init(params) {
        super._init(params);
        this._loc = {
            view: this.get_uniform_location('uView'),
            lens: this.get_uniform_location('uLens'),
            shape: this.get_uniform_location('uShape'),
            light: this.get_uniform_location('uLight'),
        };
    }

    vfunc_build_pipeline() {
        this.add_glsl_snippet(Cogl.SnippetHook.FRAGMENT, DECLARATIONS, CODE, true);
    }

    /**
     * @param {number[]} view   [width, height]
     * @param {number[]} lens   [centreX, centreY, radius, visibility]  (this actor's pixels)
     * @param {number[]} shape  [dirX, dirY, stretch, zoom]
     * @param {number[]} light  [highlights, dispersion, time]
     */
    update(view, lens, shape, light) {
        this.set_uniform_float(this._loc.view, 2, view);
        this.set_uniform_float(this._loc.lens, 4, lens);
        this.set_uniform_float(this._loc.shape, 4, shape);
        this.set_uniform_float(this._loc.light, 3, light);
        // Repaint the actor itself, not just the effect: queue_repaint() re-runs the shader on a
        // cached snapshot of the actor, which was seen to go stale (the wallpaper vanished).
        const actor = this.get_actor();
        if (actor) actor.queue_redraw();
        else this.queue_repaint();
    }
});
