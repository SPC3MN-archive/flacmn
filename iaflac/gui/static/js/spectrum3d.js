// The deep test's 3D spectrogram: a waterfall of spectrum slices across the whole track, drawn back to front on a
// canvas. Front edge = start of the track, back = end; the slice under the playhead is outlined in white.
// Data comes from /api/track/spectrum3d: levels[slice][bin] in 0..1 over -90..0 dB, bins linear from 0 Hz to Nyquist.

import { fmtTime } from "./ui.js";

const LOUD = [[0, "#23305A"], [0.35, "#5C53A2"], [0.62, "#BC3843"], [0.85, "#F4B73E"], [1, "#FADFA8"]];
const C = { text3: "#7B7782", border: "#CFC9DA", floor: "#F0EDF2", grid: "#E3DEE7", amber: "#F4B73E",
            amberTint: "#F5BD4C", text: "#0F0D1A", slice: "#9D98C7", white: "#FFFFFF" };
const FONT = '500 10px "Helvetica Neue", Helvetica, Arial, sans-serif';
const BOLD = '700 10px "Helvetica Neue", Helvetica, Arial, sans-serif';

export class Spectrum3D {
  constructor(canvas) {
    this.canvas = canvas;
    this.data = null;
    this.playhead = 0;          // 0..1 of the track
    this.cutoffHz = null;
    this.current = 0;           // seconds, for the playhead label
    new ResizeObserver(() => this.draw()).observe(canvas);
  }

  set(data, cutoffHz) {
    this.data = data;
    this.cutoffHz = cutoffHz || null;
    this.draw();
  }

  setPlayhead(fraction, seconds) {
    const n = this.data ? this.data.slices : 1;
    const before = Math.round(this.playhead * (n - 1));
    this.playhead = Math.max(0, Math.min(1, fraction || 0));
    this.current = seconds || 0;
    if (Math.round(this.playhead * (n - 1)) !== before || !this._labelDrawn) this.draw();
    else this._drawLabelOnly();
  }

  _geometry(w, h) {
    const depthX = w * 0.15, depthY = h * 0.30;
    const baseW = w - depthX - 110;
    const ampH = h * 0.56;
    const fx0 = 60, fy0 = h - 52;
    return { depthX, depthY, baseW, ampH, fx0, fy0 };
  }

  draw() {
    const cv = this.canvas;
    const dpr = window.devicePixelRatio || 1;
    const w = cv.clientWidth, h = cv.clientHeight;
    if (!w || !h) return;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
      cv.width = Math.round(w * dpr);
      cv.height = Math.round(h * dpr);
    }
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const d = this.data;
    if (!d || !d.levels || !d.levels.length) return;

    // leave room at the top for the legend
    const top = 40;
    const g = this._geometry(w, h - top);
    const nT = d.levels.length, nF = d.levels[0].length;
    const nyq = d.nyquist_hz / 1000;
    const pt = (ti, fj, a) => {
      const tt = nT > 1 ? ti / (nT - 1) : 0;
      return [g.fx0 + (fj / (nF - 1)) * g.baseW + tt * g.depthX, top + g.fy0 - tt * g.depthY - a * g.ampH];
    };

    // floor
    const corners = [pt(0, 0, 0), pt(0, nF - 1, 0), pt(nT - 1, nF - 1, 0), pt(nT - 1, 0, 0)];
    ctx.beginPath();
    corners.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.closePath();
    ctx.fillStyle = C.floor;
    ctx.fill();
    ctx.strokeStyle = C.border;
    ctx.lineWidth = 1;
    ctx.stroke();

    // frequency grid and labels
    ctx.font = FONT;
    ctx.fillStyle = C.text3;
    ctx.textAlign = "center";
    const step = nyq > 30 ? 10 : 5;
    for (let khz = 0; khz <= nyq; khz += step) {
      const fj = (khz / nyq) * (nF - 1);
      const [x0, y0] = pt(0, fj, 0), [x1, y1] = pt(nT - 1, fj, 0);
      ctx.strokeStyle = C.grid;
      ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
      ctx.fillText(khz ? `${khz} kHz` : "0", x0, y0 + 16);
    }
    // time labels along the right edge
    ctx.textAlign = "left";
    const [rx0, ry0] = pt(0, nF - 1, 0), [rx1, ry1] = pt(nT - 1, nF - 1, 0);
    ctx.fillText("0:00", rx0 + 10, ry0 + 4);
    ctx.fillText(fmtTime(d.duration), rx1 + 10, ry1 + 4);
    // loudness axis
    const [bx, by] = pt(0, 0, 0);
    ctx.strokeStyle = C.border;
    ctx.beginPath(); ctx.moveTo(bx - 10, by); ctx.lineTo(bx - 10, by - g.ampH); ctx.stroke();
    ctx.textAlign = "right";
    for (const [frac, lab] of [[0, "−90 dB"], [0.5, "−45"], [1, "0 dB"]]) {
      const yy = by - frac * g.ampH;
      ctx.strokeStyle = C.text3;
      ctx.beginPath(); ctx.moveTo(bx - 14, yy); ctx.lineTo(bx - 10, yy); ctx.stroke();
      ctx.fillText(lab, bx - 17, yy + 3.5);
    }

    // cutoff marker on the floor
    const cutoff = this.cutoffHz ? this.cutoffHz / 1000 : null;
    if (cutoff && cutoff < nyq) {
      const fj = (cutoff / nyq) * (nF - 1);
      const [x0, y0] = pt(0, fj, 0), [x1, y1] = pt(nT - 1, fj, 0);
      ctx.save();
      ctx.strokeStyle = C.amber; ctx.lineWidth = 2; ctx.setLineDash([5, 3]);
      ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
      ctx.restore();
    }

    // slices, back to front so nearer ones cover farther ones
    const playI = Math.round(this.playhead * (nT - 1));
    for (let ti = nT - 1; ti >= 0; ti--) {
      const row = d.levels[ti];
      const [lx, ly] = pt(ti, 0, 0), [rx, ry] = pt(ti, nF - 1, 0);
      ctx.beginPath();
      ctx.moveTo(lx, ly);
      for (let fj = 0; fj < nF; fj++) {
        const [x, y] = pt(ti, fj, row[fj]);
        ctx.lineTo(x, y);
      }
      ctx.lineTo(rx, ry);
      ctx.closePath();
      const grad = ctx.createLinearGradient(0, ly, 0, ly - g.ampH);
      for (const [o, c] of LOUD) grad.addColorStop(o, c);
      ctx.globalAlpha = ti <= playI ? 0.96 : 0.82;
      ctx.fillStyle = grad;
      ctx.fill();
      ctx.globalAlpha = 1;
      ctx.lineJoin = "round";
      if (ti === playI) { ctx.strokeStyle = C.white; ctx.lineWidth = 1.6; }
      else { ctx.strokeStyle = C.slice; ctx.lineWidth = 0.5; }
      ctx.stroke();
    }

    // cutoff label
    if (cutoff && cutoff < nyq) {
      const fj = (cutoff / nyq) * (nF - 1);
      const [px, py] = pt(0, fj, 0);
      const t = `cutoff ${cutoff.toFixed(1)} kHz`;
      ctx.font = BOLD;
      const tw = ctx.measureText(t).width + 12;
      ctx.fillStyle = C.amberTint;
      roundRect(ctx, px - tw + 18, py + 22, tw, 18, 5);
      ctx.fill();
      ctx.fillStyle = C.text;
      ctx.textAlign = "right";
      ctx.fillText(t, px + 12, py + 34.5);
    }
    this._pt = pt;
    this._nT = nT;
    this._nF = nF;
    this._label(ctx);
  }

  _label(ctx) {
    if (!this._pt) return;
    const playI = Math.round(this.playhead * (this._nT - 1));
    const [px, py] = this._pt(playI, this._nF - 1, 0);
    ctx.fillStyle = C.text;
    roundRect(ctx, px + 8, py - 10, 44, 20, 5);
    ctx.fill();
    ctx.fillStyle = C.white;
    ctx.font = '700 10.5px "Helvetica Neue", Helvetica, Arial, sans-serif';
    ctx.textAlign = "center";
    ctx.fillText(fmtTime(this.current), px + 30, py + 4);
    this._labelDrawn = true;
  }

  _drawLabelOnly() {
    const ctx = this.canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this._label(ctx);
  }
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
