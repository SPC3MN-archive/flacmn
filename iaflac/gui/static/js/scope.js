// A live oscilloscope of an <audio> element. Each element is routed through Web Audio once, from a click, the first
// time it needs a scope (it then plays through that graph for good: a media element can only be wired up once).
// A channel splitter feeds one analyser per channel, and each Scope draws the samples of its own element.
//
// Two views:
//   wave  about 20 ms of the left channel, triggered on a rising zero crossing so the trace holds still
//   xy    a stereo figure that keeps its shape open: x is the mid (L+R) a quarter-cycle later plus some of the side
//         (L-R), y is the mid now; the delay follows the dominant pitch, so a tone draws a loop and chords draw
//         rosettes. The figure is turned 45° clockwise so its long diagonal lies horizontal. Each axis has its own
//         automatic gain, so the figure fills the box, and the older part of the trace fades like phosphor so the
//         newest line reads clearly.
//
// Nothing clips: the wave's gain backs off at once on loud passages (recovering slowly), and the XY figure is never
// drawn past this frame's fit. The trace can take an album's colour (Scope.colour), as on the Pi library player.

let ctx = null;
const taps = new Map();                                          // audio element -> its two analysers

// Call from a click (browsers only start audio from a user gesture).
export function wireAudio(audioEl) {
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return false;
  if (!ctx) ctx = new AC();
  if (!taps.has(audioEl)) {
    const src = ctx.createMediaElementSource(audioEl);
    const split = ctx.createChannelSplitter(2);
    const l = ctx.createAnalyser(), r = ctx.createAnalyser();
    l.fftSize = r.fftSize = 2048;
    src.connect(ctx.destination);
    src.connect(split); split.connect(l, 0); split.connect(r, 1);
    taps.set(audioEl, { l, r });
  }
  if (ctx.state === "suspended") ctx.resume();
  return true;
}

export function resumeAudio() { if (ctx && ctx.state === "suspended") ctx.resume(); }

export class Scope {
  constructor(canvas, audioEl) {
    this.canvas = canvas;
    this.audio = audioEl;
    this.mode = "wave";
    this.gx = 2; this.gy = 2; this.gw = 1.6;
    this.colour = "";                                             // "" for the theme's light indigo
    this.l = new Float32Array(2048); this.r = new Float32Array(2048);
    this.running = false;
    this.frame = this.frame.bind(this);
  }

  start() { if (!this.running) { this.running = true; requestAnimationFrame(this.frame); } }
  stop() { this.running = false; }

  frame() {
    if (!this.running || !this.canvas.isConnected) { this.running = false; return; }
    this.draw();
    requestAnimationFrame(this.frame);
  }

  draw() {
    const c = this.canvas, dpr = window.devicePixelRatio || 1;
    const W = Math.round(c.clientWidth * dpr), H = Math.round(c.clientHeight * dpr);
    if (!W || !H) return;
    if (c.width !== W || c.height !== H) { c.width = W; c.height = H; }
    const x = c.getContext("2d");
    x.clearRect(0, 0, W, H);
    const l = this.l, r = this.r;
    const tap = taps.get(this.audio);
    if (tap) { tap.l.getFloatTimeDomainData(l); tap.r.getFloatTimeDomainData(r); } else { l.fill(0); r.fill(0); }
    const css = getComputedStyle(document.documentElement);
    x.strokeStyle = this.colour || css.getPropertyValue("--primary-text").trim() || "#BDB6F0";
    x.lineJoin = "round"; x.lineCap = "round";
    if (this.mode === "xy") this.drawXY(x, W, H, dpr);
    else this.drawWave(x, W, H, dpr);
  }

  drawWave(x, W, H, dpr) {
    const l = this.l, n = 882;                                  // 20 ms at 44.1 kHz
    let s0 = 0;
    for (let i = 1; i < 1100; i++) if (l[i - 1] < 0 && l[i] >= 0) { s0 = i; break; }
    let peak = 1e-4;
    for (let i = 0; i <= n; i++) peak = Math.max(peak, Math.abs(l[s0 + i] || 0));
    const fit = Math.min(1.6, 0.95 / (0.9 * peak));            // never past 95% of the half-height
    this.gw = fit < this.gw ? fit : this.gw + (fit - this.gw) * 0.08;
    const pad = x.lineWidth = 2 * dpr;
    x.beginPath();
    for (let i = 0; i <= n; i++) {
      const px = pad + (i / n) * (W - 2 * pad), py = H / 2 - (l[s0 + i] || 0) * (H / 2 - pad) * 0.9 * this.gw;
      i ? x.lineTo(px, py) : x.moveTo(px, py);
    }
    x.stroke();
  }

  drawXY(x, W, H, dpr) {
    const l = this.l, r = this.r, n = 360, STEP = 3;
    let cross = 0;
    for (let i = 1; i < l.length; i++) if (l[i - 1] + r[i - 1] < 0 && l[i] + r[i] >= 0) cross++;
    const rate = ctx ? ctx.sampleRate : 44100;
    const f0 = Math.max(30, (cross * rate) / l.length), D = Math.max(6, Math.min(200, Math.round(rate / (4 * f0))));
    const at = (a, i) => { i = Math.min(a.length - 2, i); return (a[i] + a[i + 1]) / 2; };
    const xs = new Float32Array(n), ys = new Float32Array(n);
    let mx = 1e-4, my = 1e-4;
    for (let j = 0; j < n; j++) {
      const i = j * STEP, k = i + D, mid = (at(l, k) + at(r, k)) / 2, side = (at(l, k) - at(r, k)) / 2;
      const fx = mid + side * 0.9, fy = (at(l, i) + at(r, i)) / 2 - side * 0.5;
      xs[j] = (fx + fy) * Math.SQRT1_2; ys[j] = (fy - fx) * Math.SQRT1_2;  // turned 45° clockwise: lies horizontal
      mx = Math.max(mx, Math.abs(xs[j])); my = Math.max(my, Math.abs(ys[j]));
    }
    const tx = Math.min(40, 0.95 / mx), ty = Math.min(40, 0.95 / my);
    this.gx += (tx - this.gx) * (tx < this.gx ? 0.5 : 0.08);  // shrink fast on peaks, grow back slowly
    this.gy += (ty - this.gy) * (ty < this.gy ? 0.5 : 0.08);
    const ux = Math.min(this.gx, tx), uy = Math.min(this.gy, ty);  // never past this frame's fit: no clipping
    const P = (i) => [W / 2 + xs[i] * ux * W * 0.47, H / 2 - ys[i] * uy * H * 0.45];
    const parts = 6, per = Math.ceil(n / parts);
    x.lineWidth = 2.5 * dpr;
    for (let p = 0; p < parts; p++) {
      x.globalAlpha = 0.15 + 0.85 * ((p + 1) / parts) ** 2;
      x.beginPath();
      for (let i = p * per; i <= Math.min(n - 1, (p + 1) * per); i++) {
        const [px, py] = P(i);
        i === p * per ? x.moveTo(px, py) : x.lineTo(px, py);
      }
      x.stroke();
    }
    x.globalAlpha = 1;
  }
}
