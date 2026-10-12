// Library: every album in the library folder with what a player like Volumio would trip over, and one album's tracks.
// Below the list, the open album and its tracks on the left, and a live oscilloscope (waveform or stereo XY) of the
// playing track on the right.

import { api, url, on, waitJob } from "./api.js";
import { h, clear, icon, chip, fmtTime, fmtBytes, toast, errorText } from "./ui.js";
import { Scope, wireAudio } from "./scope.js";

const state = { data: null, filter: "", selected: null, album: null, scanning: false, playing: null, scopeView: "wave" };
let scope = null;
let top = null, bottom = null, scopeCard = null, hooks = {};
const audio = new Audio();

export function mountLibrary(topCard, bottomCard, opts) {
  top = topCard;
  bottom = bottomCard;
  hooks = opts;
  scopeCard = opts.scopeCard;
  audio.addEventListener("ended", () => { state.playing = null; renderAlbum(); renderScope(); });
  audio.addEventListener("pause", () => { renderAlbum(); renderScope(); });
  audio.addEventListener("play", () => { renderAlbum(); renderScope(); });
  on("library", () => load());
  renderTop();
  renderAlbum();
  renderScope();
}

// ---- the oscilloscope card: what is playing, a Scope / XY switch, and the live trace -------------------------------
function renderScope() {
  if (!scopeCard) return;
  const t = state.playing && state.album && state.album.tracks.find((x) => x.path === state.playing);
  const name = t ? (t.title || t.path.split("/").pop()) : state.playing ? state.playing.split("/").pop() : "";
  const seg = h("div.segmented", ...[["wave", "Scope"], ["xy", "XY"]].map(([v, label]) =>
    h("button", { class: state.scopeView === v ? "on" : "", onclick: () => { state.scopeView = v; renderScope(); } }, label)));
  const canvas = h("canvas");
  clear(scopeCard,
    h("div.card-head", h("span.card-title", "Oscilloscope"),
      h("span.card-sub.ellipsis.grow", name ? `${audio.paused ? "Paused" : "Playing"}: ${name}` : "Click a track to play it"), seg),
    h("div.spec-box.lib-scope", canvas));
  if (scope) scope.stop();
  scope = new Scope(canvas, audio);
  scope.mode = state.scopeView;
  if (state.playing && !audio.paused) scope.start();
  else requestAnimationFrame(() => scope && scope.draw());     // a still, flat trace
}

export async function load() {
  try {
    state.data = await api("/library");
  } catch (e) {
    state.data = { error: errorText(e), albums: [] };
  }
  hooks.count && hooks.count(state.data.albums?.length || 0);
  renderTop();
  if (state.selected && !state.data.albums.some((a) => a.folder === state.selected)) {
    state.selected = null;
    state.album = null;
    renderAlbum();
  }
}

export function albums() { return state.data?.albums || []; }

export function coverUrl(folder, name = "cover.jpg") { return url("/library/file", { path: `${folder}/${name}` }); }

export function coverThumb(entry, cls = "thumb") {
  if (!entry.has_cover) return h("div." + cls, icon("image"));
  const img = h("img." + cls, { src: coverUrl(entry.folder), alt: "", loading: "lazy" });
  const names = ["folder.jpg", "cover.png", "front.jpg"];
  img.addEventListener("error", () => {
    const next = names.shift();
    if (next) img.src = coverUrl(entry.folder, next);
    else img.replaceWith(h("div." + cls, icon("image")));
  });
  return img;
}

async function rescan() {
  state.scanning = true;
  renderTop();
  try {
    const { job_id } = await api("/library/scan", { method: "POST" });
    await waitJob(job_id);
    await load();
    toast(`Library re-read: ${state.data.albums.length} albums`);
  } catch (e) {
    toast(`Rescan failed: ${errorText(e)}`, "bad");
  } finally {
    state.scanning = false;
    renderTop();
  }
}

function issueChips(a) {
  const out = [];
  if (!a.lossless) out.push(chip("Lossy copy: a FLAC may exist", "bad", { dot: true }));
  for (const t of a.issues || []) out.push(chip(t.charAt(0).toUpperCase() + t.slice(1), "warn", { icon: "alert" }));
  return out;
}

function renderTop() {
  const d = state.data;
  const search = h("input", { placeholder: "Filter albums…", value: state.filter,
    oninput: (e) => { state.filter = e.target.value; renderRows(); } });
  const headSub = d && d.albums && d.albums.length
    ? `${d.albums.length} albums · ${d.albums.reduce((s, a) => s + a.tracks, 0)} tracks · ${fmtBytes(d.albums.reduce((s, a) => s + a.size, 0))} · ${d.root}`
    : d?.root || "";
  els.rows = h("div.scroll.grow");
  clear(top,
    h("div.card-head", h("span.card-title", "Library"), h("span.card-sub.grow.ellipsis", headSub),
      h("label.field", { style: { width: "280px", height: "30px" } }, icon("search"), search),
      h("button.btn.secondary.sm", { onclick: rescan, disabled: state.scanning || !d?.root },
        state.scanning ? h("span.spinner") : icon("refresh"), "Rescan")),
    els.rows);
  renderRows();
}

const els = {};
function renderRows() {
  const d = state.data;
  if (!d) return clear(els.rows, h("div.empty", h("span.spinner"), "Reading the library…"));
  if (d.error) return clear(els.rows, h("div.empty", icon("alert"), h("b", "Could not read the library"), d.error));
  if (!d.root) {
    return clear(els.rows, h("div.empty", icon("folder"), h("b", "No library folder yet"),
      h("span", "Choose where albums are saved: a local folder, a USB drive or your Volumio share."),
      h("button.btn.primary.sm", { onclick: () => hooks.openSettings && hooks.openSettings() }, "Open Settings")));
  }
  if (!d.albums.length) {
    return clear(els.rows, h("div.empty", icon("library"), h("b", "No albums found"), h("span", `Nothing in ${d.root} yet.`)));
  }
  const q = state.filter.trim().toLowerCase();
  const rows = d.albums.filter((a) => !q || `${a.artist} ${a.album}`.toLowerCase().includes(q));
  clear(els.rows, h("table.grid.lib-table",
    h("thead", h("tr", h("th", ""), h("th", "Artist"), h("th", "Album"), h("th", "Year"), h("th", "Format"),
      h("th.r", "Tracks"), h("th", "Cover"), h("th", "Issues"))),
    h("tbody", ...rows.map((a) => h("tr" + (a.folder === state.selected ? ".selected" : ""), { onclick: () => select(a.folder), title: a.folder },
      h("td", { style: { width: "56px" } }, coverThumb(a)),
      h("td", a.artist), h("td.strong", a.album), h("td.num", a.year || "—"), h("td.dim", a.format_label),
      h("td.r.num", a.tracks),
      h("td", a.has_cover ? icon("check", "ok-icon") : icon("x", "bad-icon")),
      h("td", h("div.issues", ...(issueChips(a).length ? issueChips(a) : [h("span.row", { style: { color: "var(--good-text)" } }, icon("check"), "No issues")]))))))));
  for (const el of els.rows.querySelectorAll(".ok-icon")) el.style.color = "var(--green)";
  for (const el of els.rows.querySelectorAll(".bad-icon")) el.style.color = "var(--red)";
}

async function select(folder) {
  state.selected = folder;
  renderRows();
  clear(bottom, h("div.empty", h("span.spinner"), "Reading the album…"));
  try {
    state.album = await api("/library/album", { query: { folder } });
    renderAlbum();
    renderScope();
  } catch (e) {
    clear(bottom, h("div.empty", icon("alert"), h("b", "Could not read the album"), errorText(e)));
  }
}

function play(t) {
  if (state.playing === t.path && !audio.paused) { audio.pause(); return; }
  if (state.playing !== t.path) {
    state.playing = t.path;
    audio.src = url("/audio", { path: t.path });
  }
  document.getElementById("player").pause();
  wireAudio(audio);                                               // from this click, so the scope can tap the sound
  audio.play().catch((e) => toast(`Cannot play: ${errorText(e)}`, "bad"));
}

function renderAlbum() {
  if (!bottom) return;
  const al = state.album;
  if (!al) {
    clear(bottom, h("div.empty", icon("library"), h("b", "Choose an album"), "Its tracks and tag issues show here. Click a track to play it."));
    return;
  }
  const entry = albums().find((a) => a.folder === al.folder) || { folder: al.folder, has_cover: !!al.cover, artist: "", album: al.folder };
  const tracks = al.tracks;
  const total = tracks.reduce((s, t) => s + (t.length || 0), 0);
  const cover = al.cover ? h("img.thumb.lg", { src: url("/library/file", { path: al.cover }), alt: "" }) : h("div.thumb.lg", icon("image"));
  const table = (list, offset) => h("table.grid.two-col-table",
    h("thead", h("tr", h("th", "#"), h("th", "Title"), h("th", "Artist"), h("th.r", "Length"), h("th.r", "Tags"))),
    h("tbody", ...list.map((t) => {
      const bad = !t.title || !t.track || t.error;
      const playing = state.playing === t.path && !audio.paused;
      return h("tr" + (bad ? ".changed" : "") + (playing ? ".selected" : ""), { onclick: () => play(t), title: t.path },
        h("td.num", playing ? icon("pause") : (t.disc > 1 ? `${t.disc}-` : "") + (t.track || "–")),
        h("td.strong", h("div.row", h("span.ellipsis", t.title || t.path.split("/").pop()),
          bad ? chip(t.error ? "unreadable" : !t.title ? "no title" : "no track number", "solid-warn") : null)),
        h("td.dim", t.artist || entry.artist), h("td.r.num", fmtTime(t.length)),
        h("td.r", bad ? h("span", { style: { color: "var(--warn-text)" } }, icon("alert")) : h("span", { style: { color: "var(--green)" } }, icon("check"))));
    })));
  const fmtChip = entry.format_label ? chip(entry.format_label, "", {}) : null;
  const kind = entry.lossless === false ? chip("Lossy", "bad", { dot: true }) : entry.lossless ? chip("Lossless", "good", { dot: true }) : null;
  clear(bottom,
    h("div.album-head", cover,
      h("div.grow",
        h("h2", entry.album || al.folder, kind, fmtChip),
        h("div.s", [entry.artist, entry.year, `${tracks.length} tracks`, fmtTime(total), al.folder + "/"].filter(Boolean).join(" · "))),
      h("button.btn.secondary", { onclick: () => hooks.openArt && hooks.openArt(al.folder) }, icon("disc"), "CD art")),
    al.issues.length ? h("div.issue-line", { style: { marginTop: 0, marginBottom: "10px" } }, ...al.issues.map((t) => chip(t, "warn", { icon: "alert" }))) : null,
    h("div.scroll.grow", table(tracks)));
}
