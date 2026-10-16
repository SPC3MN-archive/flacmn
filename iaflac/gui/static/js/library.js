// Library, the home screen: every album in the library folder on the left, filtered by "Needs attention", lossy or
// no cover; one album on the right with everything about it at once: its facts, its tracks (click one to play the album
// from there), what needs fixing and how (the library check, health.js), and its cover and the others found for it.

import { api, url, on, waitJob } from "./api.js";
import { h, clear, icon, fmtTime, fmtBytes, toast, errorText, albumQualityChip } from "./ui.js";
import { now, onChange, playAlbum, toggle, playingPath } from "./player.js";
import { openTagEditor } from "./tagedit.js";
import { mountHealth, onHealth, healthData, problemsOf, select as selectHealth, load as loadHealth, run as runCheck,
  checking, progressText } from "./health.js";

const state = { data: null, filter: "", show: "all", selected: null, album: null, scanning: false, editing: false };
let list = null, pane = null, hooks = {};
const els = {};

export function mountLibrary(listCard, paneEl, opts) {
  list = listCard;
  pane = paneEl;
  hooks = opts;
  onChange(renderTracks);
  on("library", () => load());
  mountHealth({
    openSettings: opts.openSettings, openSearch: opts.openSearch,
    libraryChanged: () => { load(); opts.libraryChanged && opts.libraryChanged(); },
    tagIssues: (folder) => (folder === state.album?.folder ? state.album.issues : []),
    editTags: (folder) => editTags(folder),
    coverOf: (folder) => (folder === state.album?.folder && state.album.cover ? coverSrc(state.album) : null),
  });
  onHealth(() => { renderHead(); renderRows(); });
  renderList();
  renderAlbum();
  loadHealth();
}

export async function load() {
  try {
    state.data = await api("/library");
    state.data.albums = (state.data.albums || []).filter((a) => !a.opened);   // folders opened in Case Art aren't the library
  } catch (e) {
    state.data = { error: errorText(e), albums: [] };
  }
  hooks.count && hooks.count(state.data.albums?.length || 0);
  renderHead();
  renderRows();
  if (state.selected && !state.data.albums.some((a) => a.folder === state.selected)) {
    state.selected = null;
    state.album = null;
    renderAlbum();
  } else if (state.selected && state.data.albums.length && !state.editing) {
    // the open album may have changed (a cover or tracks added by a fix): show it as it is now
    api("/library/album", { query: { folder: state.selected } })
      .then((al) => { if (al.folder === state.selected) { state.album = al; renderAlbum(); } })
      .catch(() => {});
  }
}

export function albums() { return state.data?.albums || []; }

export function coverUrl(folder, name = "cover.jpg") { return url("/library/file", { path: `${folder}/${name}` }); }
const coverSrc = (al) => url("/library/file", { path: al.cover, v: String(Date.now()).slice(0, -4) });

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
  renderHead();
  try {
    const { job_id } = await api("/library/scan", { method: "POST" });
    await waitJob(job_id);
    await load();
    toast(`Library re-read: ${state.data.albums.length} albums`);
  } catch (e) {
    toast(`Rescan failed: ${errorText(e)}`, "bad");
  } finally {
    state.scanning = false;
    renderHead();
  }
}

// ---- the list ----------------------------------------------------------------------------------------------------
const FILTERS = [
  ["all", "All", () => true],
  ["attention", "Needs attention", (a) => !!problemsOf(a.folder) || (a.issues || []).length > 0],
  ["lossy", "Lossy", (a) => a.quality === "lossy"],
  ["nocover", "No cover", (a) => !a.has_cover],
];

function renderList() {
  els.filter = h("input", { id: "lib-filter", type: "search", placeholder: "Artist or album", value: state.filter,
    oninput: (e) => { state.filter = e.target.value; renderRows(); } });
  els.pills = h("div.pills", { role: "group", "aria-label": "Show" });
  els.meta = h("div.row.list-meta");
  els.rows = h("ul.list.scroll", { "aria-label": "Albums" });
  clear(list,
    h("div.list-head",
      h("label.sr-only", { for: "lib-filter" }, "Filter your library"),
      h("span.field", icon("search"), els.filter),
      els.pills, els.meta),
    els.rows);
  renderHead();
  renderRows();
}

function renderHead() {
  if (!els.pills) return;
  const all = albums();
  clear(els.pills, ...FILTERS.map(([key, label, test]) => h("button.pill", { type: "button", "aria-pressed": String(state.show === key),
    onclick: () => { state.show = key; renderHead(); renderRows(); } }, `${label} · ${all.filter(test).length}`)));
  const d = state.data, hd = healthData();
  const shown = visible().length;
  const what = state.show === "attention" ? `${shown} album${shown === 1 ? "" : "s"} need${shown === 1 ? "s" : ""} attention`
    : `${shown} album${shown === 1 ? "" : "s"}`;
  const size = fmtBytes(all.reduce((s, a) => s + (a.size || 0), 0));
  clear(els.meta,
    h("h2.list-count", what), h("span.muted.ellipsis.grow", d?.root ? `${size} · ${d.root}` : ""),
    h("button.btn.ghost.sm", { type: "button", onclick: runCheck, disabled: checking() || !d?.root,
      title: hd?.checked ? "Check every album again for missing songs, low quality, covers and duplicates" : "Look for missing songs, low quality, missing covers and duplicates" },
      checking() ? h("span.spinner") : null, checking() ? progressText() : hd?.checked ? "Check again" : "Check library"),
    h("button.btn.ghost.sm", { type: "button", onclick: rescan, disabled: state.scanning || !d?.root, title: "Read the library folder again" },
      state.scanning ? h("span.spinner") : icon("refresh"), "Rescan"));
}

function visible() {
  const q = state.filter.trim().toLowerCase(), test = FILTERS.find((f) => f[0] === state.show)[2];
  return albums().filter((a) => test(a) && (!q || `${a.artist} ${a.album}`.toLowerCase().includes(q)));
}

function summary(a) {
  const p = problemsOf(a.folder), tags = (a.issues || []).length;
  if (p) return [p.text + (tags ? ` · ${tags} tag issue${tags === 1 ? "" : "s"}` : ""), p.bad ? "bad" : "warn"];
  if (tags) return [tags === 1 ? a.issues[0] : `${tags} tag issues`, "warn"];
  return ["", ""];
}

function renderRows() {
  if (!els.rows) return;
  const d = state.data;
  if (!d) return clear(els.rows, h("li.empty", h("span.spinner"), "Reading the library…"));
  if (d.error) return clear(els.rows, h("li.empty", icon("alert"), h("b", "Could not read the library"), d.error));
  if (!d.root) {
    return clear(els.rows, h("li.empty", icon("folder"), h("b", "No library folder yet"),
      h("span", "Choose where albums are saved: a local folder, a USB drive or your Volumio share."),
      h("button.btn.primary.sm", { type: "button", onclick: () => hooks.openSettings && hooks.openSettings() }, "Open Settings")));
  }
  if (d.scanning && !d.albums.length) return clear(els.rows, h("li.empty", h("span.spinner"), "Reading the library…"));
  if (!d.albums.length) return clear(els.rows, h("li.empty", icon("library"), h("b", "No albums found"), h("span", `Nothing in ${d.root} yet.`)));
  const rows = visible();
  if (!rows.length) return clear(els.rows, h("li.empty", icon("check"), h("b", state.show === "attention" ? "Nothing needs attention" : "Nothing matches")));
  clear(els.rows, ...rows.map((a) => {
    const [issue, sev] = summary(a);
    return h("li", h("button.list-row" + (a.folder === state.selected ? ".on" : ""), {
      type: "button", "aria-current": a.folder === state.selected ? "true" : null, title: a.folder, onclick: () => select(a.folder) },
      coverThumb(a, "thumb.md"),
      h("span.list-main", h("b.ellipsis", a.album || a.folder), h("span.list-sub.ellipsis", [a.artist, a.year].filter(Boolean).join(" · "))),
      h("span.list-side", albumQualityChip(a.quality), issue ? h("span.list-issue.ellipsis." + sev, issue) : null)));
  }));
}

// ---- one album -------------------------------------------------------------------------------------------------------
/** Show one album (from another screen). The list is loaded first if it isn't yet. */
export async function openAlbum(folder) {
  if (!state.data) await load();
  await select(folder);
}

// Edit tags: the album's page becomes the editor; when it closes, the album is read again if anything was saved
function editTags(folder) {
  state.editing = true;
  const box = h("div.card.tag-edit-card");
  clear(pane, box);
  openTagEditor(box, folder, async (saved) => {
    state.editing = false;
    if (saved) { await select(folder); load(); } else renderAlbum();
  });
}

async function select(folder) {
  state.editing = false;
  state.selected = folder;
  renderRows();
  clear(pane, h("div.empty", h("span.spinner"), "Reading the album…"));
  try {
    state.album = await api("/library/album", { query: { folder } });
    renderAlbum();
  } catch (e) {
    clear(pane, h("div.empty", icon("alert"), h("b", "Could not read the album"), errorText(e)));
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
  if (!pane || state.editing) return;
  const al = state.album;
  if (!al) {
    clear(pane, h("div.empty", icon("library"), h("b", "Choose an album"), "Its tracks, what needs fixing and its cover show here."));
    return;
  }
  const entry = entryOf(al), tracks = al.tracks;
  const total = tracks.reduce((s, t) => s + (t.length || 0), 0);
  const cover = al.cover ? h("img.up-cover", { src: coverSrc(al), alt: "" }) : h("div.up-cover", icon("image"));
  const lossy = entry.quality === "lossy", p = problemsOf(al.folder);
  const better = lossy || p?.kinds.includes("missing_tracks");
  const facts = [
    ["Format", entry.format_label || "—"], ["Size", fmtBytes(entry.size)],
    ["Tracks", `${tracks.length} · ${fmtTime(total)}`], ["Cover", al.cover ? al.cover.split("/").pop() : "None"],
    ["Folder", al.folder + "/"], ["Tags", al.issues.length ? `${al.issues.length} issue${al.issues.length === 1 ? "" : "s"}` : "No issues"],
  ];
  els.tracks = h("ol.track-list.scroll", { "aria-label": "Tracks" });
  els.needs = h("section.needs-section", { "aria-label": "Needs attention" });
  els.cover = h("section.cover-section", { "aria-label": "Cover" });
  clear(pane,
    h("div.up-row",
      h("div.up-head", cover,
        h("div.up-info",
          h("div.up-names", h("h1", entry.album || al.folder),
            h("p.up-artist", [entry.artist, entry.year].filter(Boolean).join(" · ")),
            h("p.up-facts", `${tracks.length} tracks · ${fmtTime(total)}`)),
          h("span.chip.lg." + (lossy ? "bad" : "good"), h("span.dot"),
            [lossy ? "Lossy" : entry.quality === "hires" ? "Hi-res" : "Lossless", entry.format_label].filter(Boolean).join(" · ")),
          h("div.row", { style: { gap: "var(--sp-2)", flexWrap: "wrap" } },
            h("button.btn.primary.lg", { type: "button", onclick: () => playAlbum(al, entry, 0) }, icon("play"), "Play album"),
            h("button.btn.secondary.lg", { type: "button", onclick: () => editTags(al.folder) }, "Edit tags"),
            better ? h("button.btn.secondary.lg", { type: "button", onclick: () => hooks.openSearch && hooks.openSearch(entry.artist, entry.album) },
              lossy ? "Find a lossless copy" : "Find the complete album") : null,
            h("button.btn.secondary.lg", { type: "button", onclick: () => hooks.openArt && hooks.openArt(al.folder) }, "Case art")))),
      h("dl.facts", ...facts.map(([k, v]) => h("div", h("dt", k), h("dd.ellipsis", { title: v }, v))))),
    h("div.album-body",
      h("section.tracks-section", { "aria-label": "Tracks" }, h("div.section-head", h("h2", "Tracks")), els.tracks),
      h("div.album-side", els.needs, els.cover)));
  renderTracks();
  selectHealth(al.folder, els.needs, els.cover);
}

function renderTracks() {
  if (!els.tracks || !els.tracks.isConnected || !state.album) return;
  const al = state.album;
  clear(els.tracks, ...al.tracks.map((t, i) => {
    const flag = t.error ? "Unreadable" : !t.title ? "No title" : !t.track ? "No track number" : "";
    const playing = playingPath() === t.path;
    return h("li", h("button.track-row" + (playing ? ".on" : ""), { type: "button", onclick: () => play(i), title: t.path,
      "aria-current": playing ? "true" : null },
      h("span.track-n", playing ? "▶" : (t.disc > 1 ? `${t.disc}-` : "") + (t.track || "–")),
      h("span.ellipsis.grow", t.title || t.path.split("/").pop()),
      flag ? h("span.chip.warn", flag) : null,
      h("span.track-len", fmtTime(t.length))));
  }));
}
