// Draws a Fluid as liquid glass over a background.
//   pass 1: every particle is splatted (soft kernel, additive) into a density field
//   pass 2: the liquid surface is where the field crosses a threshold; the field's gradient gives
//           a surface normal, used to refract the background (5 wavelengths, so edges get rainbow
//           fringes), reflect a soft studio light, light a caustic inside, and colour the rim.
// The container itself is invisible (an optional faint shell can be shown for reference).

export type Look = {
  /** px the background shifts at a steep surface */
  refraction: number
  /** spectral spread of that shift */
  dispersion: number
  /** body tint (multiplies what shows through) */
  tint: [number, number, number]
  /** reflections and rim brightness */
  highlights: number
  /** how thick (domed) the liquid looks */
  thickness: number
}

const SPLAT_VS = `#version 300 es
in vec2 aPos;
uniform vec2 uField;     // field size (px)
uniform float uSize;     // splat diameter (px)
void main() {
  // field row 0 = top of the canvas, matching how the background texture is sampled
  gl_Position = vec4(aPos / uField * 2.0 - 1.0, 0.0, 1.0);
  gl_PointSize = uSize;
}`

const SPLAT_FS = `#version 300 es
precision highp float;
uniform float uScale;
out vec4 o;
void main() {
  vec2 d = gl_PointCoord * 2.0 - 1.0;
  float r2 = dot(d, d);
  if (r2 >= 1.0) discard;
  float k = 1.0 - r2;
  o = vec4(k * k * k * uScale, 0.0, 0.0, 1.0);
}`

const QUAD_VS = `#version 300 es
in vec2 aPos;
out vec2 vUv;
void main() { vUv = aPos * 0.5 + 0.5; gl_Position = vec4(aPos, 0.0, 1.0); }`

const COMPOSITE_FS = `#version 300 es
precision highp float;
in vec2 vUv;
uniform sampler2D uBg;
uniform sampler2D uField;
uniform vec2 uRes;          // canvas size (CSS px)
uniform vec2 uFieldTexel;   // 1 / field size
uniform float uFieldScale;  // field units per stored unit
uniform float uThreshold;
uniform vec4 uShell;        // container centre x, y (CSS px), radius, shell opacity
uniform vec4 uLook;         // refraction (px), dispersion, highlights, thickness
uniform vec3 uTint;
uniform float uTime;
out vec4 o;

vec3 spectral(float t) {
  return vec3(exp(-pow((t - 0.15) / 0.2, 2.0)) + 0.18 * exp(-pow((t - 0.95) / 0.1, 2.0)),
              exp(-pow((t - 0.5) / 0.19, 2.0)),
              exp(-pow((t - 0.85) / 0.2, 2.0)));
}
float field(vec2 uv) { return texture(uField, uv).r * uFieldScale; }
// "height" of the liquid surface from the field: 0 at the edge, rising toward 1 inside
float height(float f) { return 1.0 - exp(-max(f - uThreshold, 0.0) / uLook.w); }

void main() {
  vec2 uv = vec2(vUv.x, 1.0 - vUv.y);
  vec3 bg = texture(uBg, uv).rgb;
  vec3 col = bg;
  vec2 p = uv * uRes;

  float f = field(uv);
  float aa = fwidth(f) * 0.75 + 1e-4;
  float inside = smoothstep(uThreshold - aa, uThreshold + aa, f);
  if (inside > 0.0) {
    // normals over a few texels, so individual particles don't show as bumps
    vec2 tx = vec2(uFieldTexel.x * 2.5, 0.0), ty = vec2(0.0, uFieldTexel.y * 2.5);
    vec2 g = vec2(height(field(uv + tx)) - height(field(uv - tx)),
                  height(field(uv + ty)) - height(field(uv - ty))) * 0.5;
    // at the very edge the height function is flat; use the field itself there
    vec2 gf = vec2(field(uv + tx) - field(uv - tx), field(uv + ty) - field(uv - ty)) * 0.5;
    g += gf * 0.08 * (1.0 - smoothstep(0.0, 0.25, height(f)));
    vec3 n = normalize(vec3(-g * 10.0, 1.0));
    float steep = 1.0 - n.z;

    // Refraction through the liquid, split into 5 wavelengths.
    vec2 off = n.xy * uLook.x / uRes;
    vec3 acc = vec3(0.0), ws = vec3(0.0);
    for (int i = 0; i < 5; i++) {
      float w = (float(i) + 0.5) / 5.0;
      vec3 sw = spectral(w);
      acc += texture(uBg, uv - off * (1.0 + (w - 0.5) * uLook.y * (1.0 + 6.0 * steep))).rgb * sw;
      ws += sw;
    }
    vec3 liq = acc / ws * uTint;

    float hl = uLook.z;
    // Soft studio window reflected in the surface, with rainbow edges.
    vec3 L = normalize(vec3(-0.5, -0.65, 0.58));
    vec3 refl = vec3(0.0);
    for (int i = 0; i < 3; i++) {
      float w = (float(i) + 0.5) / 3.0;
      vec3 nw = normalize(n + vec3(n.xy * (w - 0.5) * 0.6, 0.0));
      vec3 r = reflect(vec3(0.0, 0.0, -1.0), nw);
      float band = exp(-pow((dot(r, normalize(vec3(0.7, -0.45, 0.55))) - 0.25) / 0.2, 2.0))
                 * smoothstep(-0.2, 0.5, dot(r, normalize(vec3(-0.3, -0.8, 0.5))));
      float key = pow(max(dot(r, L), 0.0), 70.0) * 2.2;
      refl += (band + key) * spectral(w) * 2.4;
    }
    refl /= 3.0;
    float fres = 0.2 + 0.8 * steep;
    liq += refl * fres * hl;

    // Caustic: light gathered on the side away from the light, inside the liquid.
    float caus = pow(max(dot(n.xy, normalize(vec2(0.55, 0.8))), 0.0), 2.0) * smoothstep(0.02, 0.3, steep)
               * (1.0 - smoothstep(0.5, 0.9, steep));
    liq += vec3(1.0, 0.95, 0.85) * caus * 0.35 * hl;

    // Thin coloured rim where the surface turns away.
    float rim = smoothstep(0.2, 0.65, steep);
    float ang = atan(n.y, n.x);
    liq += mix(vec3(0.9), spectral(0.5 + 0.48 * sin(ang + uTime * 0.4)), 0.75) * rim * 0.7 * hl;
    // A thin bright line just inside the edge, like light caught in the surface of a drop.
    float hgt = height(f);
    float lip = smoothstep(0.0, 0.08, hgt) * (1.0 - smoothstep(0.08, 0.3, hgt));
    liq += vec3(0.85, 0.9, 1.0) * lip * 0.22 * hl;

    col = mix(bg, liq, inside);
  }

  // Optional reference: the invisible container's shell.
  if (uShell.w > 0.0) {
    float d = length(p - uShell.xy) - uShell.z;
    float line = exp(-d * d / 1.2);
    vec2 dir = normalize(p - uShell.xy + 1e-4);
    float lit = 0.35 + 0.65 * pow(max(dot(dir, normalize(vec2(-0.6, -0.8))), 0.0), 2.0);
    col += vec3(0.85, 0.9, 1.0) * line * lit * uShell.w;
  }
  o = vec4(col, 1.0);
}`

function compile(gl: WebGL2RenderingContext, vs: string, fs: string) {
  const prog = gl.createProgram()!
  for (const [type, src] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]] as const) {
    const sh = gl.createShader(type)!
    gl.shaderSource(sh, src)
    gl.compileShader(sh)
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh) ?? 'shader')
    gl.attachShader(prog, sh)
  }
  gl.bindAttribLocation(prog, 0, 'aPos')
  gl.linkProgram(prog)
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog) ?? 'link')
  return prog
}

export class LiquidRenderer {
  private gl: WebGL2RenderingContext
  private splat: WebGLProgram
  private comp: WebGLProgram
  private quad: WebGLBuffer
  private points: WebGLBuffer
  private pts = new Float32Array(0)
  private fieldTex: WebGLTexture
  private fbo: WebGLFramebuffer
  private bgTex: WebGLTexture
  private fieldW = 0
  private fieldH = 0
  private floatField: boolean
  private uni: Record<string, WebGLUniformLocation | null> = {}
  private w = 0
  private h = 0
  private canvas: HTMLCanvasElement

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas
    const gl = canvas.getContext('webgl2', { antialias: false, premultipliedAlpha: false })
    if (!gl) throw new Error('WebGL2 is not available')
    this.gl = gl
    this.floatField = !!gl.getExtension('EXT_color_buffer_float')
    this.splat = compile(gl, SPLAT_VS, SPLAT_FS)
    this.comp = compile(gl, QUAD_VS, COMPOSITE_FS)
    for (const name of ['uBg', 'uField', 'uRes', 'uFieldTexel', 'uFieldScale', 'uThreshold', 'uShell', 'uLook', 'uTint', 'uTime'])
      this.uni[name] = gl.getUniformLocation(this.comp, name)
    this.quad = gl.createBuffer()!
    gl.bindBuffer(gl.ARRAY_BUFFER, this.quad)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW)
    this.points = gl.createBuffer()!
    this.fieldTex = gl.createTexture()!
    this.fbo = gl.createFramebuffer()!
    this.bgTex = gl.createTexture()!
  }

  /** Size in CSS px; the canvas itself is drawn at device resolution. */
  resize(w: number, h: number, background: HTMLCanvasElement) {
    const gl = this.gl
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    this.w = w
    this.h = h
    this.canvas.width = Math.round(w * dpr)
    this.canvas.height = Math.round(h * dpr)
    // the field is smooth: CSS resolution is plenty
    this.fieldW = Math.round(w)
    this.fieldH = Math.round(h)
    gl.bindTexture(gl.TEXTURE_2D, this.fieldTex)
    if (this.floatField) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA16F, this.fieldW, this.fieldH, 0, gl.RGBA, gl.HALF_FLOAT, null)
    else gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, this.fieldW, this.fieldH, 0, gl.RGBA, gl.UNSIGNED_BYTE, null)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.fbo)
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, this.fieldTex, 0)
    gl.bindFramebuffer(gl.FRAMEBUFFER, null)

    gl.bindTexture(gl.TEXTURE_2D, this.bgTex)
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, background)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
  }

  /**
   * xs, ys: particle positions in canvas CSS px. splat: kernel radius (px).
   * shell: [cx, cy, radius, opacity].
   */
  draw(xs: Float32Array, ys: Float32Array, splat: number, threshold: number, look: Look, shell: [number, number, number, number], time: number) {
    const gl = this.gl
    const n = xs.length
    if (this.pts.length !== n * 2) this.pts = new Float32Array(n * 2)
    for (let i = 0; i < n; i++) { this.pts[i * 2] = xs[i]; this.pts[i * 2 + 1] = ys[i] }

    // pass 1: density field
    const scale = this.floatField ? 1 : 0.25 // 8-bit fallback: keep sums under 1
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.fbo)
    gl.viewport(0, 0, this.fieldW, this.fieldH)
    gl.clearColor(0, 0, 0, 0)
    gl.clear(gl.COLOR_BUFFER_BIT)
    gl.enable(gl.BLEND)
    gl.blendFunc(gl.ONE, gl.ONE)
    gl.useProgram(this.splat)
    gl.uniform2f(gl.getUniformLocation(this.splat, 'uField'), this.fieldW, this.fieldH)
    gl.uniform1f(gl.getUniformLocation(this.splat, 'uSize'), splat * 2)
    gl.uniform1f(gl.getUniformLocation(this.splat, 'uScale'), scale)
    gl.bindBuffer(gl.ARRAY_BUFFER, this.points)
    gl.bufferData(gl.ARRAY_BUFFER, this.pts, gl.DYNAMIC_DRAW)
    gl.enableVertexAttribArray(0)
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0)
    gl.drawArrays(gl.POINTS, 0, n)
    gl.disable(gl.BLEND)

    // pass 2: composite
    gl.bindFramebuffer(gl.FRAMEBUFFER, null)
    gl.viewport(0, 0, this.canvas.width, this.canvas.height)
    gl.useProgram(this.comp)
    gl.activeTexture(gl.TEXTURE0)
    gl.bindTexture(gl.TEXTURE_2D, this.bgTex)
    gl.uniform1i(this.uni.uBg, 0)
    gl.activeTexture(gl.TEXTURE1)
    gl.bindTexture(gl.TEXTURE_2D, this.fieldTex)
    gl.uniform1i(this.uni.uField, 1)
    gl.uniform2f(this.uni.uRes, this.w, this.h)
    gl.uniform2f(this.uni.uFieldTexel, 1 / this.fieldW, 1 / this.fieldH)
    gl.uniform1f(this.uni.uFieldScale, 1 / scale)
    gl.uniform1f(this.uni.uThreshold, threshold)
    gl.uniform4f(this.uni.uShell, ...shell)
    gl.uniform4f(this.uni.uLook, look.refraction, look.dispersion, look.highlights, look.thickness)
    gl.uniform3f(this.uni.uTint, ...look.tint)
    gl.uniform1f(this.uni.uTime, time)
    gl.bindBuffer(gl.ARRAY_BUFFER, this.quad)
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0)
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
  }

  // Free GPU resources. (Not loseContext(): under React StrictMode the same canvas is reused.)
  dispose() {
    const gl = this.gl
    gl.deleteProgram(this.splat)
    gl.deleteProgram(this.comp)
    gl.deleteBuffer(this.quad)
    gl.deleteBuffer(this.points)
    gl.deleteTexture(this.fieldTex)
    gl.deleteTexture(this.bgTex)
    gl.deleteFramebuffer(this.fbo)
  }
}
