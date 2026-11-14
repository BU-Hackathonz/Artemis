// The agent as a liquid-glass ring inside an invisible circle: the "Thinking swirl" mockup
// (client/src/experiments/BubbleMockups.tsx) in its ring state, drawn analytically instead of
// with a particle simulation.
//   - the ring is a tube of liquid around the inside of the circle with a round cross-section,
//     so it refracts like a glass torus
//   - smooth swells of liquid travel around it as it spins (faster while a command runs); it
//     keeps its round shape
//   - the whole ring turns into the flow: when a command starts it draws inward and thickens until
//     it is one droplet (uMorph 0 -> 1); the droplet flies the command (stretched along its
//     motion, leaving the trail) and, back home, opens out into the ring again
//   - the droplet glows brighter while it travels
//   - a soft halo around everything that slowly breathes, flaring when the ring is clicked
//   - shading matches the mockup: 5-wavelength refraction, a soft reflected studio window,
//     caustic, rainbow rim and a bright lip just inside the edge

import Cogl from 'gi://Cogl';
import GObject from 'gi://GObject';
import Shell from 'gi://Shell';

const DECLARATIONS = `
uniform vec2 uView;     // target width, height (px)
uniform vec4 uOrb;      // ring centre x, y (px), radius (px), visibility 0..1
uniform vec4 uShape;    // ring motion direction x, y, stretch (0 = still), energy (0 = calm)
uniform vec4 uLight;    // highlights, dispersion, morph time (s), spin angle (rad)
uniform float uGlow;    // halo strength (0 = none)
uniform float uMorph;   // 0 = ring, 1 = collapsed into one droplet (smoothed in JS)
uniform vec4 uPress;    // x: how wobbly (click, or held while its menu is open); y: random seed per click;
                        // z: splash (droplets flung out right after a click, fading to 0)

vec3 spectral(float t) {
  return vec3(exp(-pow((t - 0.15) / 0.2, 2.0)) + 0.18 * exp(-pow((t - 0.95) / 0.1, 2.0)),
              exp(-pow((t - 0.5) / 0.19, 2.0)),
              exp(-pow((t - 0.85) / 0.2, 2.0)));
}

float smin(float a, float b, float k) {
  float h = clamp(0.5 + 0.5 * (b - a) / k, 0.0, 1.0);
  return mix(b, a, h) - k * h * (1.0 - h);
}

// Half-width of the liquid at the middle of its cross-section (ring tube -> droplet radius).
float halfWidth() { return mix(0.2, 0.42, uMorph); }

// Signed distance (in ring radii) to the liquid: the ring tube, or, collapsing, one droplet.
float liquid(vec2 q) {
  vec2 dir = uShape.xy;
  float st = uShape.z;
  float t = uLight.z;
  float spin = uLight.w;
  float m = uMorph;
  // Pressed: the ring splashes. It jiggles off-centre, its outline and thickness break into
  // uneven, lopsided waves (whole-number harmonics with random phases per click, plus a warp of
  // the angle so they bunch up unevenly), and a few droplets fling out and get pulled back.
  float pr = sqrt(max(uPress.x, 0.0)) * (1.0 - m); // eases off slowly, then lets go
  float sd0 = uPress.y;
  q -= pr * 0.07 * vec2(sin(t * 11.0 + sd0), cos(t * 8.7 + sd0 * 1.7));
  q += dir * st * 0.18 * (1.0 - m);              // the ring's liquid lags behind its motion
  // a moving droplet stretches along its motion, a little narrower across it
  vec2 perp = vec2(-dir.y, dir.x);
  q = dir * (dot(q, dir) / (1.0 + st * m * 1.3)) + perp * (dot(q, perp) * (1.0 + st * m * 0.4));
  float d = length(q);
  float ang = atan(q.y, q.x);
  vec2 rd = q / max(d, 1e-4);
  float trail = max(-dot(rd, dir), 0.0);
  float wob = 1.0 + uShape.w * 0.6;
  float wa = ang + pr * 0.9 * sin(ang + sd0 * 2.3 + t * 3.0);   // uneven bunching
  float bend = 0.07 * sin(wa + sd0 + t * 4.3) + 0.06 * sin(2.0 * wa - sd0 * 1.9 - t * 6.1)
             + 0.045 * sin(3.0 * wa + sd0 * 3.1 + t * 7.7) + 0.025 * sin(5.0 * wa - sd0 * 0.7 + t * 10.3);
  float thick = 0.45 * sin(wa - sd0 * 2.7 - t * 5.2) + 0.3 * sin(3.0 * wa + sd0 * 1.3 + t * 7.4)
              + 0.18 * sin(4.0 * wa - sd0 * 4.1 - t * 9.6);
  float rc = (0.66 + wob * 0.012 * sin(2.0 * ang - t * 0.6) + pr * bend) * (1.0 - m);
  float swell = 1.0 + (0.14 * sin(ang - spin) + 0.07 * sin(2.0 * ang - spin * 1.3 + t * 0.4)) * (1.0 - m)
                    + pr * thick;
  float w = halfWidth() * max(swell, 0.25) * (1.0 + st * 2.2 * trail * trail * (1.0 - m));
  float sd = abs(d - rc) - w;
  // Splash droplets: fling out from the ring and fall back as the press fades.
  float sp = sqrt(max(uPress.z, 0.0)) * (1.0 - m);
  if (sp > 0.02) {
    for (int k = 0; k < 3; k++) {
      float fk = float(k);
      float a = sd0 * (1.7 + fk) + fk * 2.1 + t * (0.6 - fk * 0.35);
      float reach = 0.66 + 0.42 * sp * (0.7 + 0.3 * sin(fk * 3.7 + sd0));
      vec2 c = vec2(cos(a), sin(a)) * reach;
      sd = smin(sd, length(q - c) - 0.075 * sp * (1.0 + 0.4 * fk), 0.16 * sp);
    }
  }
  return sd;
}

// Height of the liquid surface (0 at the edge, 1 at the middle of the tube): a round section.
float surface(vec2 q) {
  float x = clamp(-liquid(q) / halfWidth(), 0.0, 1.0);
  return sqrt(1.0 - (1.0 - x) * (1.0 - x));
}
`

const CODE = `
vec2 uv = cogl_tex_coord_in[0].st;
vec4 base = texture2D(cogl_sampler0, uv);
vec3 col = base.rgb;
vec2 p = uv * uView;
float R = uOrb.z;
vec2 q = (p - uOrb.xy) / R;
if (uOrb.w > 0.0 && dot(q, q) < 2.0) {
  float t = uLight.z;
  float spin = uLight.w;
  float sd = liquid(q);
  float sdpx = sd * R;
  float inside = 1.0 - smoothstep(-0.7, 0.7, sdpx);
  float ang = atan(q.y, q.x);
  if (inside > 0.0) {
    float hgt = surface(q);
    float ep = 0.75 / R;                        // ~0.75 px
    vec2 gH = vec2(surface(q + vec2(ep, 0.0)) - surface(q - vec2(ep, 0.0)),
                   surface(q + vec2(0.0, ep)) - surface(q - vec2(0.0, ep))) / (2.0 * ep);
    vec3 n = normalize(vec3(-gH * 0.18, 1.0));
    float steep = 1.0 - n.z;

    // Refraction through the liquid, split into 5 wavelengths.
    vec2 off = n.xy * (R * 0.4) / uView;
    vec3 acc = vec3(0.0), ws = vec3(0.0);
    for (int i = 0; i < 5; i++) {
      float wl = (float(i) + 0.5) / 5.0;
      vec3 sw = spectral(wl);
      acc += texture2D(cogl_sampler0, uv - off * (1.0 + (wl - 0.5) * uLight.y * (1.0 + 6.0 * steep))).rgb * sw;
      ws += sw;
    }
    vec3 liq = acc / ws * vec3(0.93, 0.84, 1.0);   // purple-tinted glass

    float hl = uLight.x;
    // Soft studio window reflected in the surface, with rainbow edges.
    vec3 L = normalize(vec3(-0.5, -0.65, 0.58));
    vec3 refl = vec3(0.0);
    for (int i = 0; i < 3; i++) {
      float wl = (float(i) + 0.5) / 3.0;
      vec3 nw = normalize(n + vec3(n.xy * (wl - 0.5) * 0.6, 0.0));
      vec3 r = reflect(vec3(0.0, 0.0, -1.0), nw);
      float band = exp(-pow((dot(r, normalize(vec3(0.7, -0.45, 0.55))) - 0.25) / 0.2, 2.0))
                 * smoothstep(-0.2, 0.5, dot(r, normalize(vec3(-0.3, -0.8, 0.5))));
      float key = pow(max(dot(r, L), 0.0), 70.0) * 2.2;
      refl += (band + key) * spectral(wl) * 2.4;
    }
    refl /= 3.0;
    liq += refl * vec3(0.92, 0.8, 1.0) * (0.2 + 0.8 * steep) * hl;

    // Caustic: light gathered on the side away from the light.
    float caus = pow(max(dot(n.xy, normalize(vec2(0.55, 0.8))), 0.0), 2.0) * smoothstep(0.02, 0.3, steep)
               * (1.0 - smoothstep(0.5, 0.9, steep));
    liq += vec3(1.0, 0.8, 0.98) * caus * 0.35 * hl;

    // Coloured rim where the surface turns away, and a bright lip just inside the edge.
    float rim = smoothstep(0.2, 0.65, steep);
    float na = atan(n.y, n.x);
    liq += mix(vec3(0.78, 0.55, 1.0), spectral(0.5 + 0.48 * sin(na + t * 0.4)), 0.4) * rim * 0.75 * hl;
    float depth = -sd;
    float lip = smoothstep(0.0, 0.03, depth) * (1.0 - smoothstep(0.03, 0.09, depth));
    liq += vec3(0.9, 0.78, 1.0) * lip * 0.24 * hl;

    col = mix(col, liq, inside * uOrb.w);
  }
  // A soft halo around the liquid that slowly breathes (brighter as a travelling droplet).
  float g = uGlow * (1.0 + 0.8 * uMorph);
  if (g > 0.0) {
    float dpx = max(sd, 0.0) * R;
    float halo = (exp(-dpx / (R * 0.2)) * 0.75 + exp(-dpx / (R * 0.06)) * 0.5)
               * (1.0 - smoothstep(1.15, 1.41, length(q)));
    vec3 gcol = mix(vec3(0.7, 0.42, 1.0), spectral(0.5 + 0.45 * sin(ang - spin + t * 0.3)), 0.2);
    float breathe = 0.82 + 0.18 * sin(t * 1.4);
    col += gcol * halo * (1.0 - 0.5 * inside) * g * breathe * uOrb.w;
  }
}
cogl_color_out = vec4(col, base.a) * cogl_color_in;
`

export const RingEffect = GObject.registerClass(
class RingEffect extends Shell.GLSLEffect {
    _init(params) {
        super._init(params);
        this._loc = {
            view: this.get_uniform_location('uView'),
            orb: this.get_uniform_location('uOrb'),
            shape: this.get_uniform_location('uShape'),
            light: this.get_uniform_location('uLight'),
            glow: this.get_uniform_location('uGlow'),
            morph: this.get_uniform_location('uMorph'),
            press: this.get_uniform_location('uPress'),
        };
    }

    vfunc_build_pipeline() {
        this.add_glsl_snippet(Cogl.SnippetHook.FRAGMENT, DECLARATIONS, CODE, true);
    }

    /**
     * Same arguments as OrbEffect.update(), plus:
     * @param {number} glow  halo strength
     * @param {number} morph  0 = ring .. 1 = one droplet
     * @param {number} press  1 just after a click, fading to 0
     * @param {number} seed  random per click (each splash looks different)
     * @param {number} splash  droplets flung out (1 right after a click, fading to 0)
     */
    update(view, orb, shape, light, glow = 0, morph = 0, press = 0, seed = 0, splash = 0) {
        this.set_uniform_float(this._loc.press, 4, [press, seed, splash, 0]);
        this.set_uniform_float(this._loc.view, 2, view);
        this.set_uniform_float(this._loc.orb, 4, orb);
        this.set_uniform_float(this._loc.shape, 4, shape);
        this.set_uniform_float(this._loc.light, 4, light);
        this.set_uniform_float(this._loc.glow, 1, [glow]);
        this.set_uniform_float(this._loc.morph, 1, [morph]);
        // Repaint the actor itself, not just the effect: queue_repaint() re-runs the shader on a
        // cached snapshot of the actor, which was seen to go stale (the wallpaper vanished).
        const actor = this.get_actor();
        if (actor) actor.queue_redraw();
        else this.queue_repaint();
    }
});
