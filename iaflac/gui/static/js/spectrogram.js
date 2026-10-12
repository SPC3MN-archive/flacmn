// Album Search, bottom left: the selected upload's tracks. Pick a track to see its 3D spectrogram (drawn as soon as
// it is picked), play it (streamed from archive.org) with the playhead on the spectrogram, and add the album to the
// library. The same box can show a live oscilloscope of the track instead: a waveform, or a stereo XY figure.

import { api, url } from "./api.js";
import { h, clear, icon, chip, fmtTime, fmtBytes, toast, errorText } from "./ui.js";
import { Spectrum3D } from "./spectrum3d.js";
import { Scope, wireAudio, resumeAudio } from "./scope.js";

const player = () => document.getElementById("player");
const state = { cand: null, detail: null, track: null, spectrum: {}, loading: null, view: "spectrum" };
const VIEWS = [["spectrum", "Spectrogram"], ["wave", "Scope"], ["xy", "XY"]];
let els = {};
let spec = null;
let scope = null;
let hooks = {};

export function mountSpectrogram(card, opts) {
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
}

function renderEmpty() {
  clear(els.card,
    h("div.card-head", h("span.card-title", "Spectrogram")),
    h("div.empty", icon("playcircle"), h("b", "Choose an upload"),
      h("span", "Pick a result above, then pick a track to see its spectrogram and play it.")));
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
  } catch (e) {
    toast(`Could not load ${cand.identifier}: ${errorText(e)}`, "bad");
  }
}

export function updateCandidate(c) {
  if (state.cand && c.identifier === state.cand.identifier) {
    state.cand = { ...state.cand, ...c };
    renderHead();
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
  els.scope = h("canvas", { hidden: true });
  els.overlay = h("div.overlay");
  els.legend = h("div.legend", h("i"), h("span", h("span", "quiet"), h("span", "loud")));
  els.specBox = h("div.spec-box", els.canvas, els.scope, els.legend, els.overlay);
  clear(els.card, els.head, els.player, h("div.spec-body", els.specBox));
  spec = new Spectrum3D(els.canvas);
  if (scope) scope.stop();
  scope = new Scope(els.scope, player());
  renderHead();
  renderPlayer();
  showSpectrum();
}

function renderHead() {
  if (!els.head || !state.cand) return;
  const c = state.cand;
  const addBtn = h("button.btn.primary", { onclick: addToLibrary, disabled: c.tier === "D", title: c.tier === "D" ? "Failed uploads are not saved" : "" },
    icon("download"), "Add to library");
  els.views = h("div.segmented", ...VIEWS.map(([v, t]) =>
    h("button", { class: state.view === v ? "on" : "", onclick: () => setView(v), title: v === "spectrum" ? "3D spectrogram" : v === "wave" ? "Live oscilloscope" : "Live stereo XY figure" }, t)));
  clear(els.head, h("span.card-title", state.view === "spectrum" ? "Spectrogram" : "Oscilloscope"), els.views,
    h("span.card-sub.ellipsis.grow", `${c.identifier} · ${fmtBytes(c.bytes)}`), addBtn);
  applyView();
}

// ---- views: the 3D spectrogram, or a live oscilloscope (waveform or XY) of what is playing --------------------------
function setView(v) {
  if (v !== "spectrum") wireAudio(player());                   // from this click: browsers only start audio on a gesture
  state.view = v;
  renderHead();
}

function applyView() {
  if (!els.scope) return;
  const live = state.view !== "spectrum";
  els.scope.hidden = !live;
  els.canvas.hidden = live;
  els.legend.hidden = live;
  if (live) { els.overlay.hidden = true; scope.mode = state.view; scope.start(); }
  else { scope.stop(); if (els.shown !== "spectrum") showSpectrum(); }
  els.shown = state.view;
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
  showSpectrum();
  loadSpectrum();
  if (wasPlaying) togglePlay();
}

function togglePlay() {
  const a = player();
  if (!state.track) return;
  resumeAudio();
  if (!a.getAttribute("src")) {
    delete a.dataset.wav;
    a.src = url("/audio", { identifier: state.cand.identifier, file: state.track.name });
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
  if (!spec || state.view !== "spectrum") return;
  const k = key();
  const data = state.spectrum[k];
  if (data && !(data instanceof Error)) {
    els.overlay.hidden = true;
    spec.set(data);
    tick();
    return;
  }
  spec.set(null);
  els.overlay.hidden = false;
  if (!state.detail) clear(els.overlay, h("span.spinner"), "Loading the tracks…");
  else if (!state.track) clear(els.overlay, "This upload has no playable tracks.");
  else if (data instanceof Error) clear(els.overlay, h("div.stack", { style: { alignItems: "center" } },
    h("span", `No spectrogram: ${data.message}`),
    h("button.btn.sm.secondary", { onclick: () => { delete state.spectrum[k]; loadSpectrum(); } }, "Try again")));
  else clear(els.overlay, h("span.spinner"), "Fetching the track and drawing its spectrogram…");
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

// ---- actions -------------------------------------------------------------------------------------------------------
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
