// Albums, the home screen, as in flacmn: every album in the library folder on the left, A to Z, narrowed by the tag
// search; one album on the right with everything about it at once: its facts, its tracks (click one to play the album
// from there), what needs fixing and how (the library check and the Internet Archive's fixes, health.js), its cover, and
// its quality check (quality.js). From here an album opens in Album Search (a better copy) or Case Art.

import { api, url, on, jobs } from "./api.js";
import { h, clear, icon, fmtTime, fmtBytes, errorText, albumQualityChip } from "./ui.js";
import { now, onChange, playAlbum, toggle, playingPath } from "./player.js";
import { openTagEditor } from "./tagedit.js";
import { parseQuery, matchAlbum } from "./tagsearch.js";
import { compareTitleOrder } from "./rules.js";
import { addToPlaylist } from "./playlists.js";
import { openQuality } from "./quality.js";
import { mountHealth, onHealth, problemsOf, select as selectHealth, load as loadHealth } from "./health.js";

const state = { data: null, filter: "", selected: null, album: null, scanning: false, editing: false, progress: null };
let list = null, pane = null, hooks = {};
const els = {};

export function mountLibrary(listCard, paneEl, opts) {
  list = listCard;
  pane = paneEl;
  hooks = opts;
  onChange(renderTracks);
  on("library", () => load());
  on("progress", (e) => {
    if (e.stage === "library" && e.job_id === state.data?.scanning) { state.progress = e; renderScan(); }
  });
  mountHealth({
    openSettings: opts.openSettings, openSearch: opts.openSearch,
    libraryChanged: () => { load(); opts.libraryChanged && opts.libraryChanged(); },
    tagIssues: (folder) => (folder === state.album?.folder ? state.album.issues : []),
    editTags: (folder) => editTags(folder),
    coverOf: (folder) => (folder === state.album?.folder && state.album.cover
      ? { src: coverSrc(state.album), embedded: !/\.(jpe?g|png)$/i.test(state.album.cover) } : null),
    analyse: (folder) => checkQuality(folder),
  });
  onHealth(() => { renderHead(); renderRows(); });
  renderList();
  renderAlbum();
  loadHealth();
}

export async function load() {
  try {
    state.data = await api("/library");
    state.data.albums = (state.data.albums || []).filter((a) => !a.opened).sort(compareTitleOrder);   // A to Z; folders opened in Case Art aren't the library
  } catch (e) {
    state.data = { error: errorText(e), albums: [] };
  }
  if (state.progress?.job_id !== state.data.scanning) state.progress = jobs.get(state.data.scanning)?.progress || null;
  hooks.count && hooks.count(state.data.albums?.length || 0);
  renderHead();
  renderRows();
  // an album is gone only once the folder has been read again: while it is being read (after a fix) the list is empty
  if (state.selected && state.data.scanned && !state.data.albums.some((a) => a.folder === state.selected)) {
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

// covers come from /library/cover: a cover file, or the picture embedded in the tracks when there is no file
export function coverUrl(folder) { return url("/library/cover", { folder }); }
const coverSrc = (al) => url("/library/cover", { path: al.cover, v: String(Date.now()).slice(0, -4) });

export function coverThumb(entry, cls = "thumb") {
  if (!entry.has_cover) return h("div." + cls, icon("image"));
  const img = h("img." + cls, { src: coverUrl(entry.folder), alt: "", loading: "lazy" });
  img.addEventListener("error", () => img.replaceWith(h("div." + cls, icon("image"))));
  return img;
}

// ---- the list ----------------------------------------------------------------------------------------------------
function renderList() {
  els.filter = h("input", { id: "lib-filter", type: "search", placeholder: "Search tags: artist, album, song, genre, label…",
    value: state.filter, title: "Any words match any tag. Narrow one with artist:, album:, song:, genre:, label:, year: or format:, "
      + "and keep words together in quotes: genre:jazz \"so what\"",
    oninput: (e) => { state.filter = e.target.value; renderRows(); } });
  els.meta = h("div.row.list-meta");
  els.rows = h("ul.list.scroll", { "aria-label": "Albums" });
  clear(list,
    h("div.list-head",
      h("label.sr-only", { for: "lib-filter" }, "Search your library's tags"),
      h("span.field", icon("search"), els.filter),
      els.meta),
    els.rows);
  renderHead();
  renderRows();
}

function renderHead() {
  if (!els.meta) return;
  const shown = visible().length;
  clear(els.meta, h("h2.list-count", `${shown} album${shown === 1 ? "" : "s"}`));
}

// reading a newly chosen library: how many albums the walk has found, then a bar as each one is read
function renderScan() {
  if (!els.scanBar?.isConnected) return;
  const p = state.progress;
  const reading = p?.step === "reading" && p.total;
  els.scanBar.parentNode.classList.toggle("indeterminate", !reading);
  els.scanBar.style.width = reading ? `${Math.round((100 * p.index) / p.total)}%` : "";
  els.scanText.textContent = reading ? `${p.index.toLocaleString()} of ${p.total.toLocaleString()} albums`
    : p?.found ? `Finding albums: ${p.found.toLocaleString()} so far` : "Finding albums…";
}

// the albums shown, A to Z as on the phone (rules.js), narrowed by the tag search (tagsearch.js); `found` remembers the
// song a search hit
const found = new Map();
function visible() {
  const terms = parseQuery(state.filter);
  found.clear();
  return albums().filter((a) => {
    if (!terms.length) return true;
    const m = matchAlbum(a, terms);
    if (m?.song) found.set(a.folder, m.song);
    return !!m;
  });
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
  if (d.scanning && !d.albums.length) {
    els.scanBar = h("i");
    els.scanText = h("span.small");
    clear(els.rows, h("li.empty.scan", h("b", "Reading the library…"), h("div.progress.scan-bar", els.scanBar), els.scanText));
    return renderScan();
  }
  if (!d.albums.length) return clear(els.rows, h("li.empty", icon("library"), h("b", "No albums found"), h("span", `Nothing in ${d.root} yet.`)));
  const rows = visible();
  if (!rows.length) return clear(els.rows, h("li.empty", icon("search"), h("b", "Nothing matches")));
  clear(els.rows, ...rows.map((a) => {
    return h("li", h("button.list-row" + (a.folder === state.selected ? ".on" : ""), {
      type: "button", "aria-current": a.folder === state.selected ? "true" : null, title: a.folder, onclick: () => select(a.folder) },
      coverThumb(a, "thumb.lg"),
      h("span.list-main", h("b.ellipsis", a.album || a.folder), h("span.list-sub.ellipsis", [a.artist, a.year].filter(Boolean).join(" · ")),
        found.has(a.folder) ? h("span.list-sub.ellipsis", icon("music"), " ", found.get(a.folder)) : null),
      h("span.list-side", albumQualityChip(a.quality))));
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

// Check quality: the album's page becomes the spectrum check (quality.js) until Back
async function checkQuality(folder) {
  if (state.album?.folder !== folder) await select(folder);
  if (!state.album) return;
  state.editing = true;                            // keeps the page from being redrawn underneath
  const box = h("div.card.tag-edit-card");
  clear(pane, box);
  openQuality(box, state.album, () => { state.editing = false; renderAlbum(); });
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

// the most common non-empty value
function majority(values) {
  const n = new Map();
  for (const v of values) if (v) n.set(String(v), (n.get(String(v)) || 0) + 1);
  return [...n].sort((a, b) => b[1] - a[1])[0]?.[0] || "";
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
  const lossy = entry.quality === "lossy", missing = problemsOf(al.folder)?.kinds.includes("missing_tracks");
  // the album's tags not already beside the cover (title, artist, year, tracks, format), as most of its tracks say
  const tag = (k) => majority(tracks.map((t) => t[k]));
  const albumArtist = tag("albumartist");
  const facts = [
    ["Album artist", albumArtist && albumArtist !== entry.artist ? albumArtist : entry.artist || "—"],
    ["Genre", tag("genre") || "—"], ["Released", tag("date") || "—"],
    ["Label", tag("label") || "—"], ["Catalog no.", tag("catalog") || "—"],
    ["Discs", String(Math.max(1, ...tracks.map((t) => t.disc || 1)))],
    ["MusicBrainz", tag("mb_release_id") ? "Tagged" : "Not tagged"], ["Size", fmtBytes(entry.size)],
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
            h("button.btn.secondary.lg", { type: "button", onclick: (e) => addToPlaylist(e.currentTarget, al.tracks.map((t) => t.path)) },
              icon("plus"), "Add to playlist"),
            h("button.btn.secondary.lg", { type: "button", onclick: () => editTags(al.folder) }, "Edit tags"),
            h("button.btn.secondary.lg", { type: "button", onclick: () => checkQuality(al.folder) }, icon("pulse"), "Check quality"),
            lossy || missing ? h("button.btn.secondary.lg", { type: "button", onclick: () => hooks.openSearch && hooks.openSearch(entry.artist, entry.album) },
              icon("search"), lossy ? "Find a lossless copy" : "Find the complete album") : null,
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
      h("span.track-len", fmtTime(t.length)),
      h("span.btn.ghost.sm.icon-only.row-act", { role: "button", tabindex: "0", title: "Add to playlist", "aria-label": "Add to playlist",
        onclick: (e) => { e.stopPropagation(); addToPlaylist(e.currentTarget, [t.path]); },
        onkeydown: (e) => { if (e.key === "Enter") { e.stopPropagation(); addToPlaylist(e.currentTarget, [t.path]); } } }, icon("plus"))));
  }));
}
