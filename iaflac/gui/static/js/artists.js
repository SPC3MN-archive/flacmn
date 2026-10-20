// Artists, as in flacmn on the phone: every artist on the left (Bjork and Björk are one, named as most of the albums
// spell it: rules.js), and the chosen artist's albums on the right as covers. A cover opens the album in Albums.

import { h, clear, icon } from "./ui.js";
import { artistsOf, plainName } from "./rules.js";
import { albums, coverThumb } from "./library.js";

const state = { filter: "", selected: null };
let list = null, pane = null, hooks = {};
const els = {};

/** hooks: openAlbum(folder) */
export function mountArtists(listCard, paneEl, opts) {
  list = listCard;
  pane = paneEl;
  hooks = opts || {};
  els.filter = h("input", { id: "artist-filter", type: "search", placeholder: "Find an artist", value: state.filter,
    oninput: (e) => { state.filter = e.target.value; renderRows(); } });
  els.meta = h("div.row.list-meta");
  els.rows = h("ul.list.scroll", { "aria-label": "Artists" });
  clear(list,
    h("div.list-head", h("label.sr-only", { for: "artist-filter" }, "Find an artist"), h("span.field", icon("search"), els.filter), els.meta),
    els.rows);
  render();
}

/** Draw again from the library's albums (after it is read, or a fix changed it). */
export function render() {
  renderRows();
  renderArtist();
}

function all() { return artistsOf(albums()); }

function renderRows() {
  if (!els.rows) return;
  const q = plainName(state.filter);
  const shown = all().filter((a) => !q || plainName(a.name).includes(q));
  clear(els.meta, h("h2.list-count", `${shown.length} artist${shown.length === 1 ? "" : "s"}`));
  if (!albums().length) return clear(els.rows, h("li.empty", icon("library"), h("b", "No albums yet"), "Choose the music folder in Settings."));
  if (!shown.length) return clear(els.rows, h("li.empty", icon("search"), h("b", "No artist by that name")));
  clear(els.rows, ...shown.map((a) => h("li", h("button.list-row" + (plainName(a.name) === state.selected ? ".on" : ""), {
    type: "button", "aria-current": plainName(a.name) === state.selected ? "true" : null,
    onclick: () => { state.selected = plainName(a.name); renderRows(); renderArtist(); } },
    coverThumb(a.albums[0], "thumb.lg"),
    h("span.list-main", h("b.ellipsis", a.name), h("span.list-sub", a.albums.length === 1 ? "1 album" : `${a.albums.length} albums`))))));
}

function renderArtist() {
  if (!pane) return;
  const a = all().find((x) => plainName(x.name) === state.selected);
  if (!a) {
    clear(pane, h("div.empty", icon("library"), h("b", "Choose an artist"), "Their albums show here."));
    return;
  }
  clear(pane,
    h("div.section-head", h("h1", a.name), h("span.muted", a.albums.length === 1 ? "1 album" : `${a.albums.length} albums`)),
    h("div.artist-albums.scroll", ...a.albums.map((al) => h("button.album-tile", { type: "button", title: al.folder,
      onclick: () => hooks.openAlbum && hooks.openAlbum(al.folder) },
      coverThumb(al, "thumb-xl"),
      h("b.ellipsis", al.album || al.folder),
      h("span", [al.year, al.quality === "hires" ? "Hi-res" : al.quality === "lossy" ? "Lossy" : al.quality ? "Lossless" : ""].filter(Boolean).join(" · "))))));
}
