// Album Search, bottom left: the deep test of the selected upload. Play any track (streamed from archive.org), see its
// 3D spectrogram with the playhead, and run the full test: every file decoded and checked against archive.org's
// checksums, and the spectrum checked for the ceiling an MP3 source leaves.

import { api, url, on, waitJob, cancelJob } from "./api.js";
import { h, clear, icon, chip, verdictChip, fmtTime, fmtBytes, kHz, toast, errorText } from "./ui.js";
import { Spectrum3D } from "./spectrum3d.js";

const player = () => document.getElementById("player");
const state = { runningFor: null, cand: null, detail: null, track: null, jobId: null, running: false, stage: "", spectrum: {}, loading: null };
let els = {};
let spec = null;
let hooks = {};

export function mountDeepTest(card, opts) {
  hooks = opts;
  els.card = card;
  renderEmpty();
  const a = player();
  a.addEventListener("timeupdate", tick);
  a.addEventListener("play", () => setPlayIcon(true));
  a.addEventListener("pause", () => setPlayIcon(false));
  a.addEventListener("ended", () => setPlayIcon(false));
  a.addEventListener("loadedmetadata", tick);
  a.addEventListener("error", () => {
    if (!state.track || a.dataset.wav === "1") return;
    // this web view cannot play the format: ask the server for WAV instead (decoded from a cached copy)
    a.dataset.wav = "1";
    a.src = url("/audio", { identifier: state.cand.identifier, file: state.track.name, wav: true });
    a.play().catch(() => {});
  });
  on("progress", (e) => {
    if (e.job_id !== state.jobId) return;
    if (e.stage === "decode" || e.stage === "decoded") {
      const pct = e.total_bytes ? ` · ${Math.floor((e.done_bytes / e.total_bytes) * 100)}%` : "";
      state.stage = `Decoding ${e.index} of ${e.total}${pct}`;
    }
    else if (e.stage === "spectral") state.stage = "Checking the spectrum";
    else if (e.stage === "inspecting") state.stage = "Reading the headers";
    renderHead();
  });
}

function renderEmpty() {
  clear(els.card,
    h("div.card-head", h("span.card-title", "Deep test")),
    h("div.empty", icon("playcircle"), h("b", "Choose an upload"),
      h("span", "Pick a result above to play its tracks, see the spectrogram and run the deep test.")));
}

export async function loadCandidate(cand) {
  const a = player();
  a.pause();
  a.removeAttribute("src");
  a.load();
  state.cand = cand;
  state.detail = null;
  state.track = null;
  state.running = state.runningFor === cand.identifier;
  render();
  try {
    state.detail = await api(`/items/${encodeURIComponent(cand.identifier)}`);
    if (state.cand.identifier !== cand.identifier) return;
    state.cand = { ...state.cand, ...pick(state.detail) };
    state.track = state.detail.track_list[0] || null;
    render();
  } catch (e) {
    toast(`Could not load ${cand.identifier}: ${errorText(e)}`, "bad");
  }
}

export function updateCandidate(c) {
  if (state.cand && c.identifier === state.cand.identifier) {
    state.cand = { ...state.cand, ...c };
    render();
  }
}

function pick(d) {
  const { track_list, description, other_files, logs, provenance, ...rest } = d;
  return rest;
}

// ---- layout -----------------------------------------------------------------------------------------------------
function render() {
  const c = state.cand;
  if (!c) return renderEmpty();
  els.head = h("div.card-head");
  els.player = h("div.player-row");
  els.canvas = h("canvas");
  els.overlay = h("div.overlay");
  els.specBox = h("div.spec-box", els.canvas,
    h("div.legend", h("i"), h("span", h("span", "quiet"), h("span", "loud"))), els.overlay);
  els.verdict = h("div.verdict");
  clear(els.card, els.head, els.player, h("div.deep-body", els.specBox, els.verdict));
  spec = new Spectrum3D(els.canvas);
  renderHead();
  renderPlayer();
  renderVerdict();
  showSpectrum();
}

function renderHead() {
  if (!els.head || !state.cand) return;
  const c = state.cand;
  let status;
  if (state.running) status = chip(state.stage || "Starting", "primary", {});
  else if (c.spectral || c.integrity) {
    const ig = c.integrity;
    status = ig && !ig.all_ok ? chip(`${ig.ok} of ${ig.of} decode cleanly`, "bad", { icon: "alert" })
      : chip(`Done${ig ? ` · ${ig.ok} of ${ig.of}` : ""}`, "good", { icon: "check" });
  } else status = chip("Not run yet", "", {});
  if (state.running) status.prepend(h("span.spinner", { style: { width: "10px", height: "10px", borderWidth: "1.5px" } }));
  const runBtn = state.running
    ? h("button.btn.secondary", { onclick: () => cancelJob(state.jobId) }, icon("x"), "Cancel")
    : h("button.btn.secondary", { onclick: runDeep }, icon("refresh"), c.spectral ? "Run again" : "Run deep test");
  const addBtn = h("button.btn.primary", { onclick: addToLibrary, disabled: c.tier === "D", title: c.tier === "D" ? "Failed uploads are not saved" : "" },
    icon("download"), "Add to library");
  clear(els.head, h("span.card-title", "Deep test"),
    h("span.card-sub.ellipsis.grow", `${c.identifier} · ${fmtBytes(c.bytes)}`),
    status, runBtn, addBtn);
}

function renderPlayer() {
  const d = state.detail;
  const tracks = d ? d.track_list : [];
  els.play = h("button.play-btn", { onclick: togglePlay, disabled: !state.track, title: "Play / pause" }, icon("play"));
  els.pick = h("select", { onchange: (e) => chooseTrack(tracks[+e.target.value]) },
    ...tracks.map((t, i) => h("option", { value: i, selected: t === state.track },
      `${String(i + 1).padStart(2, "0")} · ${t.title || t.name}${t.length ? " · " + fmtTime(t.length) : ""}`)));
  els.time = h("span.num.dim", { style: { fontWeight: 600, fontSize: "12.5px" } }, "0:00 / " + fmtTime(state.track?.length));
  els.seek = h("input.seek", { type: "range", min: 0, max: 1000, value: 0,
    oninput: (e) => { const a = player(); if (a.duration) a.currentTime = (e.target.value / 1000) * a.duration; } });
  const p = state.track?.probe;
  const fmt = p && p.sample_rate ? `${p.sample_rate / 1000} kHz · ${p.bits}-bit · ${p.channels === 1 ? "mono" : p.channels === 2 ? "stereo" : p.channels + " ch"}`
    : state.track?.format || "";
  clear(els.player, els.play,
    h("label.field.track-pick", icon("music"), d ? els.pick : h("span.muted", "Loading tracks…")),
    els.time, els.seek, fmt ? chip(fmt, "", {}) : null);
  setPlayIcon(!player().paused);
}

function setPlayIcon(playing) {
  if (!els.play) return;
  els.play.replaceChildren(icon(playing ? "pause" : "play"));
}

function chooseTrack(t) {
  const a = player();
  const wasPlaying = !a.paused;
  a.pause();
  state.track = t;
  delete a.dataset.wav;
  a.removeAttribute("src");
  renderPlayer();
  renderVerdict();
  showSpectrum();
  if (wasPlaying) togglePlay();
}

function togglePlay() {
  const a = player();
  if (!state.track) return;
  if (!a.getAttribute("src")) {
    delete a.dataset.wav;
    a.src = url("/audio", { identifier: state.cand.identifier, file: state.track.name });
    loadSpectrum();
  }
  if (a.paused) a.play().catch((e) => toast(`Cannot play: ${errorText(e)}`, "bad"));
  else a.pause();
}

function tick() {
  const a = player();
  const dur = a.duration || state.track?.length || 0;
  if (els.time) els.time.textContent = `${fmtTime(a.currentTime)} / ${fmtTime(dur)}`;
  if (els.seek && dur && document.activeElement !== els.seek) els.seek.value = Math.round((a.currentTime / dur) * 1000);
  if (spec && dur) spec.setPlayhead(a.currentTime / dur, a.currentTime);
}

// ---- spectrogram --------------------------------------------------------------------------------------------------
function key() { return state.cand && state.track ? `${state.cand.identifier}\n${state.track.name}` : ""; }

function showSpectrum() {
  if (!spec) return;
  const k = key();
  const data = state.spectrum[k];
  if (data && !(data instanceof Error)) {
    els.overlay.hidden = true;
    spec.set(data, trackCutoff(state.track)?.cutoff_hz);
    tick();
    return;
  }
  spec.set(null);
  els.overlay.hidden = false;
  if (!state.track) clear(els.overlay, "This upload has no playable tracks.");
  else if (state.loading === k) clear(els.overlay, h("span.spinner"), "Fetching the track and drawing its spectrum…");
  else if (data instanceof Error) clear(els.overlay, `No spectrum: ${data.message}`);
  else clear(els.overlay, h("div.stack", { style: { alignItems: "center" } },
    h("span", "Press play to stream the track. Its spectrum is drawn once it has downloaded."),
    h("button.btn.sm.secondary", { onclick: loadSpectrum }, "Draw the spectrum now")));
}

async function loadSpectrum() {
  const k = key();
  if (!k || state.spectrum[k] && !(state.spectrum[k] instanceof Error) || state.loading === k) return;
  state.loading = k;
  showSpectrum();
  try {
    state.spectrum[k] = await api("/track/spectrum3d", { query: { identifier: state.cand.identifier, file: state.track.name, slices: 40, bins: 128 } });
  } catch (e) {
    state.spectrum[k] = e;
  } finally {
    if (state.loading === k) state.loading = null;
  }
  if (key() === k) showSpectrum();
}

// ---- verdict ------------------------------------------------------------------------------------------------------
function perTrack() {
  const s = state.cand?.spectral;
  if (!s) return [];
  const by = new Map();
  for (const w of s.windows) {
    const cur = by.get(w.track);
    if (w.flag === "unusable" && cur) continue;
    if (!cur || cur.flag === "unusable" || (w.flag !== "clean" && cur.flag === "clean") || (w.flag === cur.flag && w.cutoff_hz < cur.cutoff_hz)) by.set(w.track, w);
  }
  return [...by.values()];
}

function trackCutoff(t) {
  if (!t) return null;
  return perTrack().find((w) => w.track === t.name) || null;
}

const LEAD = {
  PASS: (k) => [`Full-band content to ${k} kHz:`, "a real lossless source, not an MP3 turned into FLAC."],
  LIKELY_LOSSY: (k) => [`A hard ceiling at ${k} kHz:`, "most likely made from an MP3 or AAC file."],
  SUSPECT: (k) => [`A step at ${k} kHz in some tracks:`, "listen and compare before saving."],
  UPSAMPLED: (k) => [`Content ends at ${k} kHz:`, "well below what the sample rate allows, so it was probably upsampled."],
};

function renderVerdict() {
  const c = state.cand;
  if (!els.verdict || !c) return;
  const s = c.spectral;
  if (!s) {
    clear(els.verdict,
      h("span.section-label", "Not tested yet"),
      h("div.detail", `The deep test downloads every file (${fmtBytes(c.bytes)}) temporarily, decodes it, checks it against archive.org's checksums, and looks for the ceiling a lossy source leaves in the spectrum.`),
      c.integrity ? integrityLine(c.integrity) : null);
    return;
  }
  const rows = perTrack();
  const usable = rows.filter((w) => w.flag !== "unusable");
  const top = s.verdict === "PASS" ? Math.max(...usable.map((w) => w.cutoff_hz), 0) : Math.min(...usable.map((w) => w.cutoff_hz), Infinity);
  const k = isFinite(top) && top > 0 ? kHz(top) : null;
  const lead = LEAD[s.verdict] && k ? LEAD[s.verdict](k) : [s.detail, ""];
  const names = state.detail ? state.detail.track_list : [];
  const nyq = (s.sample_rate || 44100) / 2;
  clear(els.verdict,
    verdictChip(s.verdict, k ? top : null),
    h("div.lead", lead[0]), lead[1] ? h("div.detail", lead[1]) : null,
    c.integrity ? integrityLine(c.integrity) : null,
    h("hr.hr"),
    h("span.section-label", "Cutoff per track"),
    h("div.cutoffs", ...rows.map((w) => {
      const i = names.findIndex((t) => t.name === w.track);
      const t = names[i];
      return h("div.cutoff" + (state.track && w.track === state.track.name ? ".current" : ""), { title: `${w.track}: ${w.flag}, ${w.step_db} dB step` },
        h("span.n", i >= 0 ? i + 1 : "·"), h("span.ellipsis", t ? t.title || t.name : w.track),
        h("span.bar." + w.flag, { style: { width: `${Math.max(10, Math.min(100, (w.cutoff_hz / nyq) * 100))}%` } }),
        h("span.num.r", { style: { textAlign: "right" } }, w.flag === "unusable" ? "—" : kHz(w.cutoff_hz)));
    })),
    h("div.detail.muted", { style: { marginTop: "6px", fontSize: "11px" } }, s.detail));
}

function integrityLine(ig) {
  if (ig.all_ok) return h("div.detail", `${ig.ok} of ${ig.of} files decode cleanly and match archive.org's checksums.`);
  return h("div.stack", { style: { gap: "4px" } },
    chip(`${ig.of - ig.ok} file(s) failed`, "bad", { icon: "alert" }),
    ...ig.failures.slice(0, 4).map((f) => h("div.detail", `${f.file}: ${f.message}`)));
}

// ---- actions -------------------------------------------------------------------------------------------------------
async function runDeep() {
  const c = state.cand;
  try {
    const r = await api(`/items/${encodeURIComponent(c.identifier)}/test`, { method: "POST", body: { level: "deep" } });
    state.jobId = r.job_id;
    state.runningFor = c.identifier;
    state.running = true;
    state.stage = "Starting";
    renderHead();
    const res = await waitJob(r.job_id);
    if (state.cand && state.cand.identifier === c.identifier) {
      state.cand = { ...state.cand, ...res };
      render();
    }
    hooks.updated && hooks.updated(res);
  } catch (e) {
    if (e.message !== "cancelled") toast(`Deep test failed: ${errorText(e)}`, "bad");
  } finally {
    state.runningFor = null;
    if (state.cand && state.cand.identifier === c.identifier) state.running = false;
    renderHead();
  }
}

async function addToLibrary() {
  const c = state.cand;
  try {
    const item = await api("/queue", { method: "POST", body: { identifier: c.identifier } });
    toast(`Added to the queue: ${item.folder}`, "good");
  } catch (e) {
    toast(errorText(e), "bad", 7000);
    if (/library folder/.test(errorText(e)) && hooks.openSettings) hooks.openSettings();
  }
}
