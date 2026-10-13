// Settings: saved as soon as anything changes (PUT /api/settings).

import { api } from "./api.js";
import { h, clear, icon, chip, toast, errorText } from "./ui.js";

let view = null;
let current = {};
let health = null;
let changed = () => {};

export function getSettings() { return current; }

export async function loadSettings() {
  current = await api("/settings");
  return current;
}

export function mountSettings(el, opts) {
  view = el;
  changed = opts.changed || changed;
}

export async function showSettings() {
  try { [current, health] = await Promise.all([api("/settings"), api("/health")]); }
  catch (e) { toast(errorText(e), "bad"); }
  render();
}

async function set(values) {
  try {
    const r = await api("/settings", { method: "PUT", body: values });
    current = r.settings;
    if (r.rejected?.length) toast(`Not saved: ${r.rejected.join(", ")}`, "bad");
    changed(current, values);
    if ("library" in values) health = await api("/health");
  } catch (e) {
    toast(errorText(e), "bad");
  }
  render();
}

// ---- controls -------------------------------------------------------------------------------------------------------
const row = (label, sub, control) => h("div.set-row", h("div.lbl", h("b", label), sub ? h("span", sub) : null), control);
const sw = (key) => h("input.switch", { type: "checkbox", checked: !!current[key], onchange: (e) => set({ [key]: e.target.checked }) });
const check = (key, label) => h("label.check", h("input", { type: "checkbox", checked: !!current[key], onchange: (e) => set({ [key]: e.target.checked }) }), label);
function seg(key, options) {
  return h("div.segmented", ...options.map(([v, t]) =>
    h("button" + (current[key] === v ? ".on" : ""), { onclick: () => set({ [key]: v }) }, t)));
}
function select(key, options, width = "150px") {
  return h("label.field.sm", { style: { width } }, h("select", { onchange: (e) => {
    const v = options.find(([val]) => String(val) === e.target.value)[0];
    set({ [key]: v });
  } }, ...options.map(([v, t]) => h("option", { value: v, selected: current[key] === v }, t))));
}
function stepper(key, min, max) {
  const v = current[key];
  return h("div.row", { style: { gap: 0 } },
    h("button.btn.secondary.sm.icon-only", { onclick: () => set({ [key]: Math.max(min, v - 1) }), disabled: v <= min, style: { borderRadius: "8px 0 0 8px" } }, icon("minus")),
    h("span.num", { style: { width: "40px", height: "30px", display: "grid", placeItems: "center", borderTop: "1px solid var(--border)", borderBottom: "1px solid var(--border)", fontWeight: 600 } }, v),
    h("button.btn.secondary.sm.icon-only", { onclick: () => set({ [key]: Math.min(max, v + 1) }), disabled: v >= max, style: { borderRadius: "0 8px 8px 0" } }, icon("plus")));
}
function folderField(key, placeholder) {
  const input = h("input", { value: current[key] || "", placeholder, spellcheck: false,
    onchange: (e) => set({ [key]: e.target.value.trim() }) });
  const browse = window.pywebview?.api?.choose_folder
    ? h("button.btn.secondary", { onclick: async () => {
      const p = await window.pywebview.api.choose_folder(input.value || "");
      if (p) set({ [key]: p });
    } }, "Browse…")
    : null;
  return h("div.row", h("label.field.grow", icon("folder"), input), browse);
}

// ---- layout ---------------------------------------------------------------------------------------------------------
function render() {
  if (!view) return;
  const tools = health?.tools || {};
  const libOk = health?.library_ok;
  const toolRow = (name, sub) => h("div.tool-row",
    h("span." + (tools[name] ? "ok" : "no"), icon(tools[name] ? "check" : "alert")),
    h("div.grow", { style: { minWidth: 0 } }, h("b", name), h("div.muted.ellipsis", { style: { fontSize: "11.5px" }, title: tools[name] || "" }, tools[name] || sub)),
    chip(tools[name] ? "found" : "not found", tools[name] ? "good" : "warn"));

  clear(view,
    h("div.set-col",
      h("div.card",
        h("div.card-head", h("span.card-title", "Library & files")),
        h("div.set-group",
          h("b", "Library folder"),
          folderField("library", "e.g. D:\\Music or \\\\volumio\\Internal Storage"),
          current.library && !libOk ? chip("This folder does not exist (is the drive or share connected?)", "warn", { icon: "alert" }) : null,
          h("span.muted", { style: { fontSize: "11.5px" } }, "Layout: Artist/Album (Year)/NN - Title.flac (Volumio)"),
          h("hr.hr"),
          row("Rip files", "Logs and cue sheets next to the audio", seg("rip_files", [["skip", "Skip"], ["keep", "Keep"]])),
          current.rip_files === "keep" ? chip("A .cue file can make Volumio list the album twice", "warn", { icon: "alert" }) : null,
          h("hr.hr"),
          row("Cover art", "Images saved with an album", select("art", [["cover", "Front cover only"], ["all", "Every image"], ["none", "None"]], "170px")),
          row("Embed the cover in the files", null, sw("embed_art")),
          row("Write cover.jpg", null, sw("cover_file")),
          row("Cover Art Archive", "When the upload has no cover", sw("online_cover")))),
      h("div.card",
        h("div.card-head", h("span.card-title", "Downloads & cache")),
        h("div.set-group",
          row("Simultaneous downloads", "Takes effect next start", stepper("download_concurrency", 1, 4)),
          h("hr.hr"),
          row("Audio cache", "Streamed tracks, reused by the player", select("audio_cache_mb", [[1024, "1 GB"], [2048, "2 GB"], [5120, "5 GB"], [10240, "10 GB"]], "110px")))),
      h("div.card",
        h("div.card-head", h("span.card-title", "Now playing")),
        h("div.set-group",
          row("Show the oscilloscope when idle", "While music plays and nothing is touched, Now playing turns to the Scope tab",
            select("idle_scope", [[0, "Never"], [15, "15 s"], [30, "30 s"], [60, "1 min"], [120, "2 min"]], "110px"))))),
    h("div.set-col",
      h("div.card",
        h("div.card-head", h("span.card-title", "Search & tests")),
        h("div.set-group",
          h("b", "Search options"),
          check("all_formats", "List WAV and MP3 uploads next to FLAC"),
          check("include_live", "Include live recordings"),
          check("free_only", "Free licences only"),
          check("rip_only", "Only uploads with a rip log or cue sheet"),
          row("Uploads read per format", null, select("max_candidates", [[5, "5"], [10, "10"], [20, "20"], [40, "40"]], "90px")),
          h("hr.hr"),
          row("Auto MusicBrainz", "Look the album up when it is chosen; apply a match of 95 or better", sw("auto_musicbrainz")))),
      h("div.card",
        h("div.card-head", h("span.card-title.grow", "Tools"),
          h("button.btn.secondary.sm", { onclick: showSettings }, icon("refresh"), "Check again")),
        toolRow("flac", "decoding and tag writing"), toolRow("metaflac", "tags and pictures"), toolRow("ffmpeg", "MP3/WAV, spectrum, playback"))),
    h("div.set-col",
      h("div.card",
        h("div.card-head", h("span.card-title", "CD art & print")),
        h("div.set-group",
          row("Search other archive.org uploads", "Slower, finds more scans", sw("art_search_archive")),
          h("hr.hr"),
          row("Paper", null, seg("paper", [["a4", "A4"], ["letter", "Letter"]])),
          row("Bleed", null, select("bleed_mm", [[0, "None"], [1.5, "1.5 mm"], [3, "3 mm"]], "110px")),
          row("Disc hole", "Cut line for the centre hole", seg("disc_hole_mm", [[41, "41 mm"], [23, "23 mm"], [15, "15 mm"]])),
          h("hr.hr"),
          h("b", "Output folder"),
          folderField("print_folder", "Documents/iaflac CD prints"),
          h("div.note-box", icon("info"), h("div", h("b", "Print at 100 % (actual size)."), h("br"),
            "Front 120 × 120 mm · tray card 151 × 118 mm", h("br"), "Disc label Ø 118 mm"))))));
}
