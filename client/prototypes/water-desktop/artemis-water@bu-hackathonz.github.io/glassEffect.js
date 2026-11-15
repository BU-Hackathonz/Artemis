// Liquid glass under the island's widget and its menu window, drawn on the wallpaper like the lens (lensEffect.js).
// What the reference shows (liquidglassdesign.com, "Weather Widget Liquid Glass"):
//   - the backdrop inside the panel is magnified, like a thick lens
//   - near the rim the view is pulled inward along the edge's normal (the glass bends it)
//   - at the rim the colours split slightly (blue/orange fringes)
// The menu's own Shell.BlurEffect then frosts it, and the CSS adds the top specular line and grain.

import Cogl from 'gi://Cogl';
import GObject from 'gi://GObject';
import Shell from 'gi://Shell';

const DECLARATIONS = `
uniform vec2 uView;    // target width, height (px)
uniform vec4 uRectA;   // pane A (the widget): x, y, width, height (px, this actor's space)
uniform vec4 uGlassA;  // corner radius (px), magnification, rim width (px), visibility 0..1
uniform vec4 uRectB;   // pane B (the menu window)
uniform vec4 uGlassB;
uniform float uDisp;   // colour split at the rim (0 = none)

float sdRound(vec2 p, vec2 b, float r) {
  vec2 q = abs(p) - b + r;
  return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}
`

// Both panes are the same glass: one material for the widget and its window.
const CODE = `
vec2 uv = cogl_tex_coord_in[0].st;
vec4 base = texture2D(cogl_sampler0, uv);
vec3 col = base.rgb;
vec2 p = uv * uView;
for (int i = 0; i < 2; i++) {
  vec4 R = (i == 0) ? uRectA : uRectB;
  vec4 G = (i == 0) ? uGlassA : uGlassB;
  if (G.w <= 0.0) continue;
  vec2 c = R.xy + R.zw * 0.5;
  vec2 hb = R.zw * 0.5;
  float d = sdRound(p - c, hb, G.x);
  if (d >= 0.0) continue;
  float rim = G.z;
  float k = clamp(1.0 + d / rim, 0.0, 1.0);          // 0 inside, 1 at the edge
  float e = 1.0;
  vec2 n = normalize(vec2(sdRound(p - c + vec2(e, 0.0), hb, G.x) - sdRound(p - c - vec2(e, 0.0), hb, G.x),
                          sdRound(p - c + vec2(0.0, e), hb, G.x) - sdRound(p - c - vec2(0.0, e), hb, G.x)) + 1e-5);
  vec2 m = c + (p - c) / G.y;                          // magnified view
  vec2 off = -n * (k * k * rim * 0.9);                 // the rim pulls the view inward
  float s = uDisp * k * k;
  vec3 g;
  g.r = texture2D(cogl_sampler0, (m + off * (1.0 + s)) / uView).r;
  g.g = texture2D(cogl_sampler0, (m + off) / uView).g;
  g.b = texture2D(cogl_sampler0, (m + off * (1.0 - s)) / uView).b;
  col = mix(col, g, G.w * clamp(-d, 0.0, 1.0));
}
cogl_color_out = vec4(col, base.a) * cogl_color_in;
`

export const GlassEffect = GObject.registerClass(
class GlassEffect extends Shell.GLSLEffect {
    _init(params) {
        super._init(params);
        this._loc = {
            view: this.get_uniform_location('uView'),
            rectA: this.get_uniform_location('uRectA'),
            glassA: this.get_uniform_location('uGlassA'),
            rectB: this.get_uniform_location('uRectB'),
            glassB: this.get_uniform_location('uGlassB'),
            disp: this.get_uniform_location('uDisp'),
        };
    }

    vfunc_build_pipeline() {
        this.add_glsl_snippet(Cogl.SnippetHook.FRAGMENT, DECLARATIONS, CODE, true);
    }

    /** view [w, h]; a/b = {rect: [x, y, w, h] in this actor's pixels, glass: [radius, magnification, rim, visibility]}; disp */
    update(view, a, b, disp) {
        this.set_uniform_float(this._loc.view, 2, view);
        this.set_uniform_float(this._loc.rectA, 4, a.rect);
        this.set_uniform_float(this._loc.glassA, 4, a.glass);
        this.set_uniform_float(this._loc.rectB, 4, b.rect);
        this.set_uniform_float(this._loc.glassB, 4, b.glass);
        this.set_uniform_float(this._loc.disp, 1, [disp]);
        const actor = this.get_actor();
        if (actor) actor.queue_redraw();
        else this.queue_repaint();
    }
});
