// Library: every album in the library folder with its quality and what a player like Volumio would trip over, and one
// album's tracks. Below the list, the open album and its tracks on the left, and the Now playing card on the right
// (nowplaying.js). Playing a track plays the album on from there (player.js).

import { api, url, on, waitJob } from "./api.js";
import { h, clear, icon, chip, fmtTime, fmtBytes, toast, errorText, albumQualityChip } from "./ui.js";
import { now, onChange, playAlbum, toggle, playingPath } from "./player.js";

const state = { data: null, filter: "", selected: null, album: null, scanning: false, colw: null };
let top = null, bottom = null, hooks = {};

export function mountLibrary(topCard, bottomCard, opts) {
  top = topCard;
  bottom = bottomCard;
  hooks = opts;
  onChange(renderAlbum);
  on("library", () => load());
  renderTop();
  renderAlbum();
}

export async function load() {
  try {
    state.data = await api("/library");
    state.colw = null;                                           // measure the columns again for this library
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
    h("thead", h("tr", h("th", ""), h("th", "Artist"), h("th", "Album"), h("th", { "data-fit": "all" }, "Quality"),
      h("th", "Cover"), h("th", "Issues"))),
    h("tbody", ...rows.map((a) => h("tr" + (a.folder === state.selected ? ".selected" : ""), { onclick: () => select(a.folder), title: a.folder },
      h("td", { style: { width: "56px" } }, coverThumb(a)),
      h("td", a.artist), h("td.strong", a.album), h("td", albumQualityChip(a.quality)),
      h("td", a.has_cover ? icon("check", "ok-icon") : icon("x", "bad-icon")),
      h("td", h("div.issues", ...(issueChips(a).length ? issueChips(a) : [h("span.row", { style: { color: "var(--good-text)" } }, icon("check"), "No issues")]))))))));
  const table = els.rows.querySelector("table");
  if (!state.colw && !q && table.offsetParent) state.colw = measureColumns(table);   // over the whole library
  if (state.colw) fitColumns(table, state.colw);
  for (const el of els.rows.querySelectorAll(".ok-icon")) el.style.color = "var(--green)";
  for (const el of els.rows.querySelectorAll(".bad-icon")) el.style.color = "var(--red)";
}

// Each column as wide as the median of its contents across the library (at least its heading), so a few long names
// don't push the rest of the table off the card; longer entries end in "…" and show in full on hover. A column marked
// data-fit="all" (Quality) is as wide as its widest entry, so every chip shows whole. The cover thumbnail keeps its
// size, and the last column (Issues) takes whatever is left.
function measureColumns(table) {
  const rows = [...table.tBodies[0].rows], heads = [...table.tHead.rows[0].cells];
  const ctx = document.createElement("canvas").getContext("2d");
  const textWidth = (el) => {                                   // as drawn: font, case and letter spacing
    const cs = getComputedStyle(el);                              // (cs.font is "" once numbers are tabular)
    ctx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    let s = el.textContent.trim();
    if (cs.textTransform === "uppercase") s = s.toUpperCase();
    return ctx.measureText(s).width + (parseFloat(cs.letterSpacing) || 0) * s.length;
  };
  const median = (a) => { const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
  const content = (td) => {
    const kids = [...td.children];
    return kids.length ? Math.max(...kids.map((k) => k.getBoundingClientRect().width)) : textWidth(td);
  };
  return heads.map((th, c) => {
    if (c === 0) return 56;
    if (c === heads.length - 1) return null;
    const pad = parseFloat(getComputedStyle(th).paddingLeft) + parseFloat(getComputedStyle(th).paddingRight);
    const widths = rows.map((r) => content(r.cells[c]));
    const w = th.dataset.fit === "all" ? Math.max(0, ...widths) : median(widths);
    return Math.ceil(Math.max(w, textWidth(th)) + pad + 1);
  });
}

function fitColumns(table, widths) {
  table.prepend(h("colgroup", ...widths.map((w) => h("col", w ? { style: { width: w + "px" } } : null))));
  table.classList.add("fitted");
  for (const td of table.querySelectorAll("tbody td")) {
    if (!td.children.length && td.scrollWidth > td.clientWidth) td.title = td.textContent;   // the whole name on hover
  }
}

async function select(folder) {
  state.selected = folder;
  renderRows();
  clear(bottom, h("div.empty", h("span.spinner"), "Reading the album…"));
  try {
    state.album = await api("/library/album", { query: { folder } });
    renderAlbum();
  } catch (e) {
    clear(bottom, h("div.empty", icon("alert"), h("b", "Could not read the album"), errorText(e)));
  }
}

// a click: the track plays, and the album plays on from it; clicking the playing track pauses it
function play(index) {
  if (now.album && now.album.folder === state.album.folder && now.index === index) { toggle(); return; }
  playAlbum(state.album, entryOf(state.album), index);
}

function entryOf(al) {
  return albums().find((a) => a.folder === al.folder) || { folder: al.folder, has_cover: !!al.cover, artist: "", album: al.folder };
}

function renderAlbum() {
  if (!bottom) return;
  const al = state.album;
  if (!al) {
    clear(bottom, h("div.empty", icon("library"), h("b", "Choose an album"), "Its tracks and tag issues show here. Click a track to play it."));
    return;
  }
  const entry = entryOf(al);
  const tracks = al.tracks;
  const total = tracks.reduce((s, t) => s + (t.length || 0), 0);
  const cover = al.cover ? h("img.thumb.lg", { src: url("/library/file", { path: al.cover }), alt: "" }) : h("div.thumb.lg", icon("image"));
  const table = (list, offset) => h("table.grid.two-col-table",
    h("thead", h("tr", h("th", "#"), h("th", "Title"), h("th", "Artist"), h("th.r", "Length"), h("th.r", "Tags"))),
    h("tbody", ...list.map((t, i) => {
      const bad = !t.title || !t.track || t.error;
      const playing = playingPath() === t.path;
      return h("tr" + (bad ? ".changed" : "") + (playing ? ".selected" : ""), { onclick: () => play(i + (offset || 0)), title: t.path },
        h("td.num", playing ? icon("pause") : (t.disc > 1 ? `${t.disc}-` : "") + (t.track || "–")),
        h("td.strong", h("div.row", h("span.ellipsis", t.title || t.path.split("/").pop()),
          bad ? chip(t.error ? "unreadable" : !t.title ? "no title" : "no track number", "solid-warn") : null)),
        h("td.dim", t.artist || entry.artist), h("td.r.num", fmtTime(t.length)),
        h("td.r", bad ? h("span", { style: { color: "var(--warn-text)" } }, icon("alert")) : h("span", { style: { color: "var(--green)" } }, icon("check"))));
    })));
  const fmtChip = entry.format_label ? chip(entry.format_label, "", {}) : null;
  const kind = albumQualityChip(entry.quality);
  const playBtn = h("button.btn.primary.sm", { onclick: () => playAlbum(al, entry, 0) }, icon("play"), "Play album");
  clear(bottom,
    h("div.album-head", cover,
      h("div.grow",
        h("h2", entry.album || al.folder, kind, fmtChip),
        h("div.s", [entry.artist, entry.year, `${tracks.length} tracks`, fmtTime(total), al.folder + "/"].filter(Boolean).join(" · "))),
      playBtn,
      h("button.btn.secondary", { onclick: () => hooks.openArt && hooks.openArt(al.folder) }, icon("disc"), "CD art")),
    al.issues.length ? h("div.issue-line", { style: { marginTop: 0, marginBottom: "10px" } }, ...al.issues.map((t) => chip(t, "warn", { icon: "alert" }))) : null,
    h("div.scroll.grow", table(tracks)));
}
