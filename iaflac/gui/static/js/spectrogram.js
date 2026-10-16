// Album Search, "Listen and check": the selected upload's tracks on the left; pick one to play it (streamed from
// archive.org) and see its spectrogram on the right. The spectrogram is flat by default (time across, 0 to 25 kHz up
// the side) with a dashed line where the track's sound stops: at the file's own limit (22 kHz for a CD) for genuine
// lossless audio, lower for a lossy source. The 3D waterfall is one click away.

import { api, url } from "./api.js";
import { h, clear, icon, fmtTime, toast, errorText } from "./ui.js";
import { Spectrum3D } from "./spectrum3d.js";
import { resumeAudio } from "./scope.js";
import { setPlaying } from "./topbar.js";

const player = () => document.getElementById("player");
const state = { cand: null, detail: null, track: null, spectrum: {}, loading: null, view3d: false };
let els = {};
let spec3d = null;
let hooks = {};

export function mountSpectrogram(section, opts) {
  hooks = opts;
  els.section = section;
  render();
  const a = player();
  a.addEventListener("timeupdate", tick);
  a.addEventListener("play", () => { setPlayIcon(true); renderTracks(); });
  a.addEventListener("pause", () => { setPlayIcon(false); renderTracks(); });
  a.addEventListener("ended", () => { setPlayIcon(false); renderTracks(); });
  a.addEventListener("loadedmetadata", tick);
  a.addEventListener("error", () => {
    if (!state.track || a.dataset.wav === "1") return;
    // this web view cannot play the format: ask the server for WAV instead (decoded from a cached copy)
    a.dataset.wav = "1";
    a.src = url("/audio", { identifier: state.cand.identifier, file: state.track.name, wav: true });
    a.play().catch(() => {});
  });
  new ResizeObserver(() => draw2d()).observe(section);
}

export async function loadCandidate(cand) {
  const a = player();
  a.pause();
  a.removeAttribute("src");
  a.load();
  state.cand = cand;
  state.detail = null;
  state.track = null;
  render();
  try {
    state.detail = await api(`/items/${encodeURIComponent(cand.identifier)}`);
    if (state.cand.identifier !== cand.identifier) return;
    state.cand = { ...state.cand, ...pick(state.detail) };
    state.track = state.detail.track_list[0] || null;
    render();
    loadSpectrum();
    hooks.detail && hooks.detail(state.detail);
  } catch (e) {
    toast(`Could not load ${cand.identifier}: ${errorText(e)}`, "bad");
  }
}

export function updateCandidate(c) {
  if (state.cand && c.identifier === state.cand.identifier) {
    state.cand = { ...state.cand, ...c };
    renderChecks();
  }
}

function pick(d) {
  const { track_list, description, other_files, logs, provenance, ...rest } = d;
  return rest;
}

// ---- layout -----------------------------------------------------------------------------------------------------
function render() {
  els.checks = h("div.checks-slot");
  const head = h("div.section-head", h("h2", "Listen and check"), els.checks);
  if (!state.cand) {
    clear(els.section, head, h("div.listen.empty-box", h("div.empty", icon("playcircle"), h("b", "Choose an upload"),
      h("span", "Pick a result, then pick any of its tracks to play it and see its spectrogram."))));
    return;
  }
  els.tracks = h("ol.listen-tracks.scroll", { "aria-label": "Tracks" });
  els.player = h("div.listen-player");
  els.bar = h("span.listen-bar", { onclick: (e) => {
    const a = player(), r = e.currentTarget.getBoundingClientRect();
    if (a.duration) a.currentTime = ((e.clientX - r.left) / r.width) * a.duration;
  } }, h("i"));
  els.canvas = h("canvas.spec2d");
  els.canvas3d = h("canvas");
  els.axis = h("div.spec-axis", { "aria-hidden": "true" });
  els.band = h("div.spec-band", { hidden: true }, h("span"));
  els.overlay = h("div.overlay");
  els.plot = h("div.spec-plot", { role: "img" }, els.canvas, els.band, els.overlay);
  els.box3d = h("div.spec-box.spec-3d", { hidden: true }, els.canvas3d,
    h("div.legend", h("i"), h("span", h("span", "quiet"), h("span", "loud"))));
  els.result = h("p.spec-result");
  clear(els.section, head,
    h("div.listen",
      els.tracks,
      h("div.listen-main", els.player, els.bar,
        h("div.spec-figure", els.axis, els.plot, els.box3d),
        h("div.spec-time", { "aria-hidden": "true" }, h("span", "0:00"), h("span", "time"), h("span.spec-len", "")),
        els.result)));
  spec3d = new Spectrum3D(els.canvas3d);
  renderTracks();
  renderPlayer();
  renderChecks();
  showSpectrum();
}

function renderTracks() {
  if (!els.tracks) return;
  const tracks = state.detail ? state.detail.track_list : [];
  if (!state.detail) { clear(els.tracks, h("li.muted", { style: { padding: "12px" } }, "Loading the tracks…")); return; }
  const playing = !player().paused;
  clear(els.tracks, ...tracks.map((t, i) => {
    const on = t === state.track, band = bandOf(t);
    return h("li", h("button.track-row" + (on ? ".on" : ""), { type: "button", "aria-current": on ? "true" : null,
      onclick: () => chooseTrack(t, true), title: t.name },
      h("span.track-n", on && playing ? "▶" : String(i + 1)),
      h("span.ellipsis.grow", t.title || t.name),
      h("span.track-len", fmtTime(t.length)),
      h("span.track-dot." + (band ? (band.cut ? "bad" : "good") : "wait"), { title: band ? band.text : "Not checked yet" })));
  }));
}

function renderPlayer() {
  const t = state.track, p = t?.probe;
  els.play = h("button.play-btn.lg", { type: "button", onclick: togglePlay, disabled: !t, "aria-label": "Play or pause" }, icon("play"));
  els.time = h("span.num.muted", "0:00 / " + fmtTime(t?.length));
  const fmt = p && p.sample_rate ? `${(t.format || "").toUpperCase() || "FLAC"} ${p.bits}-bit / ${p.sample_rate / 1000} kHz · ${p.channels === 1 ? "mono" : p.channels === 2 ? "stereo" : p.channels + " ch"}`
    : t?.format || "";
  els.toggle3d = h("button.btn.ghost.sm", { type: "button", onclick: () => { state.view3d = !state.view3d; showSpectrum(); renderPlayer(); } },
    state.view3d ? "Flat view" : "3D view");
  const idx = state.detail ? state.detail.track_list.indexOf(t) : -1;
  clear(els.player, els.play,
    h("span.grow.listen-title", h("b.ellipsis", t ? `${String(idx + 1).padStart(2, "0")} · ${t.title || t.name}` : "No playable tracks"),
      h("span.muted", fmt)),
    els.time, els.toggle3d);
  setPlayIcon(!player().paused && !!player().getAttribute("src"));
  if (els.section.querySelector(".spec-len")) els.section.querySelector(".spec-len").textContent = fmtTime(t?.length);
}

function setPlayIcon(playing) {
  if (!els.play) return;
  els.play.replaceChildren(icon(playing ? "pause" : "play"));
}

function chooseTrack(t, play) {
  const a = player();
  if (t === state.track && a.getAttribute("src")) { togglePlay(); return; }
  a.pause();
  state.track = t;
  delete a.dataset.wav;
  a.removeAttribute("src");
  renderTracks();
  renderPlayer();
  showSpectrum();
  loadSpectrum();
  if (play) togglePlay();
}

function togglePlay() {
  const a = player();
  if (!state.track) return;
  resumeAudio();
  if (!a.getAttribute("src")) {
    delete a.dataset.wav;
    a.src = url("/audio", { identifier: state.cand.identifier, file: state.track.name });
  }
  if (a.paused) {
    a.play().catch((e) => toast(`Cannot play: ${errorText(e)}`, "bad"));
    const c = state.cand;
    setPlaying({ audio: a, toggle: togglePlay, title: state.track.title || state.track.name,
      sub: [c.artist, c.album || c.title].filter(Boolean).join(" · "), cover: c.cover_url || null });
  } else a.pause();
}

function tick() {
  const a = player();
  const dur = a.duration || state.track?.length || 0;
  if (els.time) els.time.textContent = `${fmtTime(a.currentTime)} / ${fmtTime(dur)}`;
  if (els.bar && dur) els.bar.firstChild.style.width = `${(100 * a.currentTime) / dur}%`;
  if (spec3d && dur && state.view3d) spec3d.setPlayhead(a.currentTime / dur, a.currentTime);
}

// ---- the checks: what the upload passed, folded away under one line -------------------------------------------
function renderChecks() {
  if (!els.checks) return;
  const c = state.cand, d = state.detail;
  if (!c) { clear(els.checks); return; }
  const items = [];
  const reasons = d?.provenance?.reasons || [];
  for (const r of reasons) items.push(r);
  if (c.integrity?.checked) {
    items.push(c.integrity.all_ok ? `Every one of ${c.integrity.of} files decodes cleanly and matches archive.org's checksums.`
      : `${c.integrity.failures.length} file(s) failed the decode test.`);
  }
  if (c.spectral?.verdict) items.push(`Spectrum: ${c.spectral.detail || c.spectral.verdict}`);
  if (!items.length) { clear(els.checks, h("span.muted", c.tier === "?" ? "Not checked yet" : "")); return; }
  clear(els.checks, h("details.checks",
    h("summary", c.tier === "?" ? "Checks so far" : `${items.length} check${items.length === 1 ? "" : "s"}${["A", "B"].includes(c.tier) ? " passed" : ""}`),
    h("ul", ...items.map((t) => h("li", icon("check"), h("span", t))))));
}

// ---- the spectrogram ----------------------------------------------------------------------------------------------
function key(t = state.track) { return state.cand && t ? `${state.cand.identifier}\n${t.name}` : ""; }

/** Where a track's sound stops, from its spectrum: the highest band still above the noise floor on average. Real
 *  recordings fade out gently near the file's limit; an MP3 source stops at a hard line (a sharp drop), which is the
 *  only thing called a lossy cutoff here. */
function bandOf(t) {
  const d = state.spectrum[key(t)];
  if (!d || d instanceof Error || !d.levels?.length) return null;
  if (d.band) return d.band;
  const bins = d.levels[0].length, nyq = d.nyquist_hz || (d.sample_rate || 44100) / 2;
  const mean = [];
  for (let b = 0; b < bins; b++) mean.push(d.levels.reduce((s, x) => s + x[b], 0) / d.levels.length);
  let top = 0;
  for (let b = 0; b < bins; b++) if (mean[b] >= 0.03) top = b;          // about -87 dB: above the noise floor
  const hz = ((top + 1) / bins) * nyq, full = hz >= 0.93 * nyq;
  let drop = 0;
  for (let k = Math.max(0, top - 4); k <= top; k++) drop = Math.max(drop, mean[k] - mean[Math.min(bins - 1, k + 3)]);
  const cut = !full && drop >= 0.12 && mean[Math.max(0, top - 4)] >= 0.1;   // a cliff, not a slope
  const k = (hz / 1000).toFixed(1).replace(/\.0$/, ""), lim = String(Math.floor(nyq / 100) / 10).replace(/\.0$/, "");   // 22050 Hz reads "22"
  const limit = Math.abs(nyq - 22050) < 100 ? "the CD limit" : "the limit of this file";
  d.band = full ? { hz, nyq, full: true, cut: false, text: `Reaches ${lim} kHz, ${limit} · no lossy cutoff`,
      lead: "Full band on this track.", detail: "No hard ceiling near 16–20 kHz, so no sign of an MP3 source." }
    : cut ? { hz, nyq, full: false, cut: true, text: `Stops at ${k} kHz · hard cutoff`,
      lead: `Stops at ${k} kHz on this track.`, detail: "A hard ceiling below the file's limit usually means it was made from a lossy source such as an MP3." }
    : { hz, nyq, full: false, cut: false, text: `Fades out near ${k} kHz · no hard cutoff`,
      lead: "No hard ceiling on this track.", detail: `Its highest frequencies fade out gently near ${k} kHz, as real recordings do; an MP3 source would stop at a sharp line.` };
  return d.band;
}

const SCALE = [[0, [249, 248, 244]], [0.2, [228, 225, 240]], [0.42, [185, 180, 218]], [0.62, [127, 119, 188]],
  [0.8, [92, 83, 162]], [1, [52, 46, 104]]];                // quiet (the page) to loud (dark purple)
function colour(v) {
  for (let i = 1; i < SCALE.length; i++) {
    if (v <= SCALE[i][0]) {
      const [a, ca] = SCALE[i - 1], [b, cb] = SCALE[i], f = (v - a) / (b - a || 1);
      return ca.map((x, j) => Math.round(x + (cb[j] - x) * f));
    }
  }
  return SCALE[SCALE.length - 1][1];
}

function draw2d() {
  const d = state.spectrum[key()], cv = els.canvas;
  if (!cv || !cv.isConnected || state.view3d) return;
  const w = cv.clientWidth, hgt = cv.clientHeight;
  if (!w || !hgt) return;
  const dpr = window.devicePixelRatio || 1;
  cv.width = Math.round(w * dpr);
  cv.height = Math.round(hgt * dpr);
  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, cv.width, cv.height);
  if (!d || d instanceof Error || !d.levels?.length) return;
  const slices = d.levels.length, bins = d.levels[0].length, nyq = d.nyquist_hz || (d.sample_rate || 44100) / 2;
  const fmax = Math.max(25000, Math.ceil(nyq / 5000) * 5000);
  // the picture at its own resolution, then stretched smoothly to the canvas
  const img = new ImageData(slices, bins);
  for (let s = 0; s < slices; s++) for (let b = 0; b < bins; b++) {
    const [r, g, bl] = colour(d.levels[s][b]), i = ((bins - 1 - b) * slices + s) * 4;
    img.data[i] = r; img.data[i + 1] = g; img.data[i + 2] = bl; img.data[i + 3] = 255;
  }
  const tmp = document.createElement("canvas");
  tmp.width = slices; tmp.height = bins;
  tmp.getContext("2d").putImageData(img, 0, 0);
  ctx.imageSmoothingEnabled = true;
  const top = cv.height * (1 - nyq / fmax);                  // nothing above the file's own limit
  ctx.fillStyle = "#F9F8F4";
  ctx.fillRect(0, 0, cv.width, top);
  ctx.drawImage(tmp, 0, top, cv.width, cv.height - top);
  // the frequency scale, and the band line
  const ticks = [];
  for (let f = fmax; f >= 0; f -= 5000) ticks.push(h("span", f === fmax ? `${f / 1000} kHz` : String(f / 1000)));
  clear(els.axis, ...ticks);
  const band = bandOf(state.track);
  els.band.hidden = !band;
  if (band) {
    els.band.style.top = `${(1 - band.hz / fmax) * 100}%`;
    els.band.classList.toggle("cut", band.cut);
    els.band.firstChild.textContent = band.text;
    els.plot.setAttribute("aria-label", `Spectrogram: ${band.text}`);
  }
}

function showSpectrum() {
  if (!els.plot) return;
  els.plot.hidden = state.view3d;
  els.axis.hidden = state.view3d;
  els.box3d.hidden = !state.view3d;
  const d = state.spectrum[key()];
  if (d && !(d instanceof Error)) {
    els.overlay.hidden = true;
    if (state.view3d) spec3d.set(d); else draw2d();
    const band = bandOf(state.track);
    clear(els.result, band ? icon(band.cut ? "alert" : "check", band.cut ? "warn" : "ok") : null,
      band ? h("span", h("b", band.lead + " "), h("span.dim", band.detail)) : null);
    renderTracks();
    tick();
    return;
  }
  if (state.view3d) spec3d.set(null); else draw2d();
  els.band.hidden = true;                                   // no line until this track's spectrum is in
  clear(els.result);
  els.overlay.hidden = false;
  if (!state.detail) clear(els.overlay, h("span.spinner"), "Loading the tracks…");
  else if (!state.track) clear(els.overlay, "This upload has no playable tracks.");
  else if (d instanceof Error) clear(els.overlay, h("div.stack", { style: { alignItems: "center" } },
    h("span", `No spectrogram: ${d.message}`),
    h("button.btn.sm.secondary", { type: "button", onclick: () => { delete state.spectrum[key()]; loadSpectrum(); } }, "Try again")));
  else clear(els.overlay, h("span.spinner"), "Fetching the track and drawing its spectrogram…");
}

async function loadSpectrum() {
  const k = key();
  if (!k || state.spectrum[k] && !(state.spectrum[k] instanceof Error) || state.loading === k) return;
  state.loading = k;
  showSpectrum();
  try {
    state.spectrum[k] = await api("/track/spectrum3d", { query: { identifier: state.cand.identifier, file: state.track.name, slices: 120, bins: 128 } });
  } catch (e) {
    state.spectrum[k] = e;
  } finally {
    if (state.loading === k) state.loading = null;
  }
  if (key() === k) showSpectrum();
}

// ---- actions -------------------------------------------------------------------------------------------------------
export async function addToLibrary(c = state.cand) {
  try {
    const item = await api("/queue", { method: "POST", body: { identifier: c.identifier } });
    toast(`Added to the downloads: ${item.folder}`, "good");
  } catch (e) {
    toast(errorText(e), "bad", 7000);
    if (/library folder/.test(errorText(e)) && hooks.openSettings) hooks.openSettings();
  }
}
