// Quality check, one album at a time (Library → Check quality): every track with its spectrum verdict on the left, the
// chosen track's spectrogram on the right, flat (time across, frequency up, with a legend) or as the 3D waterfall.
// The verdict is the same three-window measurement as spectral.py (gui/audio.py analyze): genuine lossless audio
// reaches the file's own limit (22 kHz for 44.1 kHz audio); a lossy source shows a shelf where its encoder cut off.

import { api, url } from "./api.js";
import { h, clear, icon, chip, errorText } from "./ui.js";
import { Spectrum3D } from "./spectrum3d.js";
import { traceColour } from "./nowplaying.js";

// spectral.py's verdicts, in plain words: [label, chip kind, what it means, the track list's short label]
const VERDICT = {
  PASS: ["Full spectrum", "good", "Sound reaches the file's own limit, as genuine lossless audio does.", "Full"],
  SUSPECT: ["Suspect", "warn", "A step in the top band: maybe a lossy source, maybe a band-limited master. Listen and compare.", "Suspect"],
  LIKELY_LOSSY: ["Likely from a lossy file", "bad", "The sound stops at a lossy encoder's cutoff: this was probably made from an MP3 or AAC.", "Lossy source"],
  UPSAMPLED: ["Upsampled", "warn", "Nothing above the lower rate it was made from: a hi-res file holding standard-resolution audio.", "Upsampled"],
  INCONCLUSIVE: ["Inconclusive", "", "Too quiet or too short to tell.", "Unclear"],
  UNAVAILABLE: ["Not checked", "", "The track could not be decoded.", "Unread"],
};
const LOSSY = new Set(["MP3", "AAC", "OGG", "OPUS"]);

let st = null, card = null, onDone = () => {}, spec3d = null;
const els = {};

/** Fill `el` with the album's quality check; `done()` is called on Back. */
export function openQuality(el, album, done) {
  card = el;
  onDone = done || (() => {});
  st = { album, index: 0, results: {}, running: false, view3d: false, spectrum: {}, colour: "" };
  st.colourReady = (album.cover ? traceColour(url("/library/cover", { path: album.cover })) : Promise.resolve(""))
    .then((c) => { st.colour = c || "#5C53A2"; });       // no cover: spcmn purple
  render();
  checkAll();
}

function render() {
  const al = st.album;
  els.tracks = h("ol.track-list.scroll", { "aria-label": "Tracks" });
  els.summary = h("div.q-summary");
  els.plotHead = h("div.row");
  els.plot = h("div.spec-box.q-plot");
  els.note = h("p.muted.small");
  clear(card,
    h("div.card-head",
      h("button.btn.ghost.sm", { type: "button", onclick: () => onDone() }, "← Back"),
      h("div.grow", h("div.card-title", "Quality check"), h("div.card-sub", al.folder)),
      els.summary),
    h("div.album-body",
      h("section.tracks-section.q-tracks", { "aria-label": "Tracks" }, h("div.section-head", h("h2", "Tracks")), els.tracks),
      h("section.q-spectrum", { "aria-label": "Spectrogram" }, els.plotHead, els.plot, els.note)));
  renderTracks();
  renderSummary();
  showTrack();
}

const pathOf = (i) => st.album.tracks[i]?.path;

function renderTracks() {
  if (!els.tracks?.isConnected) return;
  clear(els.tracks, ...st.album.tracks.map((t, i) => {
    const r = st.results[t.path];
    const v = r instanceof Error ? ["Error", "bad", "", "Error"] : r ? VERDICT[r.verdict] || [r.verdict, "", "", r.verdict] : null;
    return h("li", h("button.track-row" + (i === st.index ? ".on" : ""), { type: "button", title: t.path,
      "aria-current": i === st.index ? "true" : null, onclick: () => { st.index = i; renderTracks(); showTrack(); } },
      h("span.track-n", (t.disc > 1 ? `${t.disc}-` : "") + (t.track || "–")),
      h("span.ellipsis.grow", t.title || t.path.split("/").pop()),
      h("span.dim.small", fmtFormat(t)),
      v ? chip(v[3], v[1], { dot: true, title: v[0] }) : r === undefined && st.running ? h("span.spinner") : null));
  }));
}

function fmtFormat(t) {
  if (t.bits && t.rate) return `${t.format} ${t.bits}/${t.rate / 1000}`;
  if (t.kbps) return `${t.format} ${t.kbps}`;
  return t.format || "";
}

function renderSummary() {
  const done = Object.values(st.results).filter((r) => r && !(r instanceof Error));
  const n = st.album.tracks.length;
  const count = (v) => done.filter((r) => r.verdict === v).length;
  const bad = count("LIKELY_LOSSY"), warn = count("SUSPECT") + count("UPSAMPLED"), pass = count("PASS");
  let text, kind;
  if (st.running) { text = `Checking ${done.length + 1} of ${n}…`; kind = ""; }
  else if (bad) { text = `${bad} of ${n} likely from a lossy file`; kind = "bad"; }
  else if (warn) { text = `${warn} of ${n} worth a listen`; kind = "warn"; }
  else if (pass) { text = `${pass === n ? "Every track" : `${pass} of ${n}`} has the full spectrum`; kind = "good"; }
  else { text = "No verdict"; kind = ""; }
  clear(els.summary, chip(text, kind, { dot: !!kind }));
}

// one track at a time (each is three ffmpeg decodes); results are cached by the server, so a second look is instant
async function checkAll() {
  st.running = true;
  const mine = st;
  for (const t of st.album.tracks) {
    if (st !== mine || !card?.isConnected) return;          // closed, or another album opened
    try { st.results[t.path] = await api("/track/analysis", { query: { path: t.path } }); }
    catch (e) { st.results[t.path] = new Error(errorText(e)); }
    renderTracks();
    renderSummary();
    if (t.path === pathOf(st.index)) showTrack();        // the note, and the cutoff line on the flat view
  }
  st.running = false;
  renderSummary();
}

function showTrack() {
  const path = pathOf(st.index);
  if (!path) { clear(els.plot, h("div.empty", "This album has no tracks to check.")); return; }
  clear(els.plotHead,
    h("h2.grow", "Spectrogram"),
    h("div.pills", { role: "group", "aria-label": "View" },
      h("button.pill", { type: "button", "aria-pressed": String(!st.view3d), onclick: () => { st.view3d = false; showTrack(); } }, "Flat"),
      h("button.pill", { type: "button", "aria-pressed": String(st.view3d), onclick: () => { st.view3d = true; showTrack(); } }, "3D")));
  const canvas = h("canvas", { role: "img", "aria-label": `Spectrogram of ${path.split("/").pop()}` });
  const wait = h("div.empty", h("span.spinner"), "Drawing the spectrogram…");
  clear(els.plot, canvas, wait);
  const key = `${path}\0${st.view3d ? "3d" : "flat"}`;
  const query = st.view3d ? { path, slices: 120, bins: 128 } : { path, slices: 360, bins: 256 };
  const draw = (d) => {
    wait.remove();
    if (st.view3d) {
      spec3d = new Spectrum3D(canvas);
      spec3d.setColour(st.colour);
      spec3d.set(d);
    } else {
      const redraw = () => drawFlat(canvas, d, st.colour, st.results[path]);
      new ResizeObserver(redraw).observe(canvas);
      redraw();
    }
  };
  Promise.all([st.spectrum[key] || api("/track/spectrum3d", { query }), st.colourReady])
    .then(([d]) => { st.spectrum[key] = d; if (pathOf(st.index) === path && canvas.isConnected) draw(d); })
    .catch((e) => { if (pathOf(st.index) === path) clear(wait, icon("alert"), `No spectrogram: ${errorText(e)}`); });
  showNote();
}

// ---- the flat spectrogram, in the album's colour: time across, 0 Hz to the file's limit up, quiet is the page's
// off-white and loud is the colour taken from the cover (as Now playing's scope); a dashed line where the sound stops
const PAPER = [246, 244, 238];
function rgbOf(colour) {
  const c = document.createElement("canvas").getContext("2d");
  c.fillStyle = colour;
  const hex = c.fillStyle;                                  // the browser writes any colour back as #rrggbb
  return /^#[0-9a-f]{6}$/i.test(hex) ? [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) : [92, 83, 162];
}

function drawFlat(canvas, d, colour, result) {
  const dpr = window.devicePixelRatio || 1, W = canvas.clientWidth, H = canvas.clientHeight;
  if (!W || !H) return;
  canvas.width = W * dpr;
  canvas.height = H * dpr;
  const x = canvas.getContext("2d");
  x.scale(dpr, dpr);
  const L = 52, R = 12, T = 10, B = 28, w = Math.max(10, W - L - R), ht = Math.max(10, H - T - B);
  // the levels as an image (slices × bins), stretched smoothly over the plot
  // each track scaled to its own range (its quietest 2 % is paper, its loudest is the full colour), so the colour reads
  const img = new ImageData(d.slices, d.bins), ink = rgbOf(colour);
  const all = d.levels.flat().sort((a, b) => a - b);
  const lo = all[Math.floor(all.length * 0.02)] || 0, hi = all[all.length - 1] || 1, span = Math.max(1e-6, hi - lo);
  for (let s = 0; s < d.slices; s++) {
    for (let b = 0; b < d.bins; b++) {
      const v = Math.pow(Math.max(0, Math.min(1, (d.levels[s][b] - lo) / span)), 0.9), o = ((d.bins - 1 - b) * d.slices + s) * 4;
      for (let k = 0; k < 3; k++) img.data[o + k] = PAPER[k] + (ink[k] - PAPER[k]) * v;
      img.data[o + 3] = 255;
    }
  }
  const off = document.createElement("canvas");
  off.width = d.slices;
  off.height = d.bins;
  off.getContext("2d").putImageData(img, 0, 0);
  x.imageSmoothingEnabled = true;
  x.imageSmoothingQuality = "high";
  x.drawImage(off, L, T, w, ht);
  // axes: kHz up the side, minutes across the bottom, in the page's grey
  x.font = '500 10px "Heros", "Helvetica Neue", Helvetica, Arial, sans-serif';
  x.fillStyle = "#6B6A70";
  x.strokeStyle = "rgba(46, 46, 51, .18)";
  x.textAlign = "right";
  x.textBaseline = "middle";
  const top = d.nyquist_hz / 1000, step = top > 30 ? 10 : 5;
  for (let k = 0; k <= top; k += step) {
    const y = T + ht - (k / top) * ht;
    x.fillText(`${k} kHz`, L - 6, y);
    x.beginPath(); x.moveTo(L, y + 0.5); x.lineTo(L + w, y + 0.5); x.stroke();
  }
  x.textAlign = "center";
  x.textBaseline = "top";
  // about six to ten marks across, at a round number of seconds
  const tick = [1, 2, 5, 10, 15, 30, 60, 120, 300].find((s) => d.duration / s <= 10) || 600;
  for (let s = 0; s <= d.duration + 0.01; s += tick) {
    x.fillText(`${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`, L + (s / d.duration) * w, T + ht + 8);
  }
  if (result?.cutoff_hz) {                                  // where a lossy encoder stopped the sound
    const y = T + ht - (result.cutoff_hz / d.nyquist_hz) * ht;
    x.setLineDash([5, 4]);
    x.strokeStyle = "#2E2E33";
    x.beginPath(); x.moveTo(L, y); x.lineTo(L + w, y); x.stroke();
    x.setLineDash([]);
    x.textAlign = "left";
    x.textBaseline = "bottom";
    x.fillStyle = "#2E2E33";
    x.fillText(`sound stops near ${(result.cutoff_hz / 1000).toFixed(1)} kHz`, L + 6, y - 3);
  }
}

function showNote() {
  const t = st.album.tracks[st.index], r = st.results[t?.path];
  if (!t) return clear(els.note);
  if (r instanceof Error) return clear(els.note, `Could not check this track: ${r.message}`);
  if (!r) return clear(els.note, h("span.spinner"), " Measuring the top of the spectrum…");
  const [label, , meaning] = VERDICT[r.verdict] || [r.verdict, "", ""];
  const facts = [`${r.sample_rate / 1000} kHz file, limit ${Math.round(r.nyquist_hz / 100) / 10} kHz`,
    r.cutoff_hz ? `sound stops near ${Math.round(r.cutoff_hz / 100) / 10} kHz (a ${r.step_db} dB step)` : ""].filter(Boolean);
  clear(els.note, h("b", label + ". "), meaning, " ", h("span.dim", facts.join(" · ") + "."),
    LOSSY.has(t.format) ? h("span.dim", " This is a lossy file, so a cutoff is expected.") : null,
    r.detail ? h("div.dim", { style: { marginTop: "4px" } }, r.detail) : null);
}
