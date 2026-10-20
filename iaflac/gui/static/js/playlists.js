// Playlists, as in flacmn on the phone (its cover: the first four albums' covers tiled, one album's cover, or a picture
// you upload, chosen by clicking the cover): your playlists on the left, the chosen one on the right (play it, rename it,
// put its songs in order, take songs out, delete it). Songs are added from Albums with Add to playlist (an album) or a
// track's + (one song): addToPlaylist below. Kept by the app (playlists.py), by each song's path in the music folder.

import { api, url, on } from "./api.js";
import { h, clear, icon, fmtTime, toast, errorText } from "./ui.js";
import { now, onChange, playAlbum, toggle, playingPath } from "./player.js";

const state = { lists: null, selected: null, pl: null, renaming: false, creating: false, confirm: false };
let list = null, pane = null;
const els = {};

export function mountPlaylists(listCard, paneEl) {
  list = listCard;
  pane = paneEl;
  on("playlists", () => load());
  onChange(renderTracks);
  load();
}

export async function load() {
  try {
    state.lists = (await api("/playlists")).playlists;
  } catch (e) {
    state.lists = [];
    toast(`Could not read the playlists: ${errorText(e)}`, "bad");
  }
  renderList();
  if (state.selected && !state.lists.some((p) => p.id === state.selected)) { state.selected = null; state.pl = null; }
  if (state.selected) await select(state.selected, true);
  else renderPlaylist();
}

// ---- the list ------------------------------------------------------------------------------------------------------
function renderList() {
  if (!list) return;
  const lists = state.lists || [];
  const create = state.creating ? nameForm("", async (name) => {
    const p = await api("/playlists", { method: "POST", body: { name } });
    state.creating = false;
    state.selected = p.id;
    await load();
  }, () => { state.creating = false; renderList(); }) : null;
  clear(list,
    h("div.list-head",
      h("div.row.list-meta", h("h2.list-count.grow", `${lists.length} playlist${lists.length === 1 ? "" : "s"}`),
        h("button.btn.secondary.sm", { type: "button", onclick: () => { state.creating = true; renderList(); } }, icon("plus"), "New playlist")),
      create),
    h("ul.list.scroll", { "aria-label": "Playlists" },
      !lists.length && !state.creating ? h("li.empty", icon("music"), h("b", "No playlists yet"),
        h("span", "Make one here, or add an album or a song to one from Albums.")) : null,
      ...lists.map((p) => h("li", h("button.list-row" + (p.id === state.selected ? ".on" : ""), {
        type: "button", "aria-current": p.id === state.selected ? "true" : null, onclick: () => select(p.id) },
        coverArt(p, "thumb.lg"),
        h("span.list-main", h("b.ellipsis", p.name), h("span.list-sub", p.count === 1 ? "1 song" : `${p.count} songs`)))))));
}

function nameForm(value, save, cancel) {
  const input = h("input", { value, placeholder: "Playlist name", "aria-label": "Playlist name", maxlength: 120 });
  const go = async (e) => {
    e.preventDefault();
    const name = input.value.trim();
    if (!name) return input.focus();
    try { await save(name); } catch (err) { toast(`Could not save the playlist: ${errorText(err)}`, "bad"); }
  };
  const form = h("form.row", { onsubmit: go, onkeydown: (e) => { if (e.key === "Escape") cancel(); } },
    h("label.field.grow", input), h("button.btn.primary.sm", { type: "submit" }, "Save"),
    h("button.btn.ghost.sm", { type: "button", onclick: cancel }, "Cancel"));
  setTimeout(() => { input.focus(); input.select(); });
  return form;
}

// ---- one playlist ----------------------------------------------------------------------------------------------------
async function select(id, keep = false) {
  if (!keep) { state.renaming = false; state.confirm = false; }
  state.selected = id;
  renderList();
  try {
    state.pl = await api(`/playlists/${id}`);
  } catch (e) {
    state.pl = null;
    clear(pane, h("div.empty", icon("alert"), h("b", "Could not read the playlist"), errorText(e)));
    return;
  }
  renderPlaylist();
}

const playable = (pl) => pl.tracks.filter((t) => !t.missing && !t.error);

// a playlist plays like an album whose songs come from anywhere: each song brings its own cover and names (player.js)
function play(index) {
  const pl = state.pl, songs = playable(pl), t = pl.tracks[index];
  const at = songs.indexOf(t);
  if (at < 0) return;
  if (now.album?.folder === `playlist:${pl.id}` && now.index === at) { toggle(); return; }
  playAlbum({ folder: `playlist:${pl.id}`, playlist: true, cover: t.cover, tracks: songs },
    { artist: t.artist || "", album: pl.name }, at);
}

async function saveTracks(tracks) {
  try {
    await api(`/playlists/${state.pl.id}`, { method: "PUT", body: { tracks: tracks.map((t) => t.path) } });
    state.pl.tracks = tracks;
    renderPlaylist();
    load();
  } catch (e) {
    toast(`Could not change the playlist: ${errorText(e)}`, "bad");
  }
}

function renderPlaylist() {
  if (!pane) return;
  const pl = state.pl;
  if (!pl) {
    clear(pane, h("div.empty", icon("music"), h("b", "Choose a playlist"), "Its songs show here. Add songs from Albums with Add to playlist or a track's +."));
    return;
  }
  const total = pl.tracks.reduce((s, t) => s + (t.length || 0), 0);
  const missing = pl.tracks.filter((t) => t.missing).length;
  const title = state.renaming
    ? nameForm(pl.name, async (name) => {
      await api(`/playlists/${pl.id}`, { method: "PUT", body: { name } });
      state.renaming = false;
      await load();
    }, () => { state.renaming = false; renderPlaylist(); })
    : h("h1", pl.name);
  els.tracks = h("ol.track-list.scroll", { "aria-label": "Songs" });
  clear(pane,
    h("div.up-row",
      h("div.up-head", h("button.up-cover.pl-cover-btn", { type: "button", title: "Change the cover", "aria-label": "Change the playlist's cover",
        onclick: (e) => coverMenu(e.currentTarget) }, coverArt(pl, "pl-cover"), h("span.pl-cover-edit", icon("edit"))),
        h("div.up-info",
          h("div.up-names", title,
            h("p.up-facts", `${pl.tracks.length === 1 ? "1 song" : `${pl.tracks.length} songs`} · ${fmtTime(total)}`
              + (missing ? ` · ${missing} not found` : ""))),
          h("div.row", { style: { gap: "var(--sp-2)", flexWrap: "wrap" } },
            h("button.btn.primary.lg", { type: "button", disabled: !playable(pl).length, onclick: () => play(pl.tracks.indexOf(playable(pl)[0])) },
              icon("play"), "Play"),
            h("button.btn.secondary.lg", { type: "button", onclick: () => { state.renaming = true; renderPlaylist(); } }, icon("edit"), "Rename"),
            h("button.btn.lg" + (state.confirm ? ".danger" : ".secondary"), { type: "button", onclick: remove },
              icon("trash"), state.confirm ? "Yes, delete it" : "Delete"),
            state.confirm ? h("button.btn.ghost.lg", { type: "button", onclick: () => { state.confirm = false; renderPlaylist(); } }, "Cancel") : null)))),
    h("div.album-body",
      h("section.tracks-section", { "aria-label": "Songs" }, h("div.section-head", h("h2", "Songs")), els.tracks)));
  renderTracks();
}

// deleting asks first; the songs themselves are never touched
async function remove() {
  if (!state.confirm) { state.confirm = true; renderPlaylist(); return; }
  const pl = state.pl;
  try {
    await api(`/playlists/${pl.id}`, { method: "DELETE" });
    toast(`Deleted the playlist ${pl.name}. Its songs are still in your library.`);
    state.selected = null;
    state.pl = null;
    state.confirm = false;
    await load();
  } catch (e) {
    toast(`Could not delete the playlist: ${errorText(e)}`, "bad");
  }
}

function renderTracks() {
  if (!els.tracks?.isConnected || !state.pl) return;
  const tracks = state.pl.tracks;
  if (!tracks.length) return clear(els.tracks, h("li.empty", icon("plus"), h("b", "No songs yet"), "In Albums, use Add to playlist or a track's +."));
  const move = (i, d) => { const t = [...tracks]; [t[i], t[i + d]] = [t[i + d], t[i]]; saveTracks(t); };
  const act = (label, ic, fn, disabled) => h("span.btn.ghost.sm.icon-only.row-act", { role: "button", tabindex: "0", title: label,
    "aria-label": label, "aria-disabled": disabled ? "true" : null, style: disabled ? { visibility: "hidden" } : null,
    onclick: (e) => { e.stopPropagation(); fn(); }, onkeydown: (e) => { if (e.key === "Enter") { e.stopPropagation(); fn(); } } }, icon(ic));
  clear(els.tracks, ...tracks.map((t, i) => {
    const on = playingPath() === t.path && now.album?.folder === `playlist:${state.pl.id}`;
    return h("li", h("button.track-row" + (on ? ".on" : "") + (t.missing ? ".missing" : ""), { type: "button", title: t.path,
      onclick: () => play(i), "aria-current": on ? "true" : null },
      h("span.track-n", on ? "▶" : String(i + 1)),
      h("span.ellipsis.grow", t.title || t.path.split("/").pop(),
        h("span.dim.small", " · " + [t.artist, t.album].filter(Boolean).join(" · "))),
      t.missing ? h("span.chip.warn", "Not found") : null,
      h("span.track-len", fmtTime(t.length)),
      act("Move up", "chevron", () => move(i, -1), i === 0),
      act("Move down", "chevron", () => move(i, 1), i === tracks.length - 1),
      act("Remove from the playlist", "minus", () => saveTracks(tracks.filter((_, j) => j !== i)))));
  }));
  // the first chevron points up
  for (const li of els.tracks.children) li.querySelector('[aria-label="Move up"] svg')?.style.setProperty("transform", "rotate(180deg)");
}

// ---- Add to playlist: a small menu at the button -----------------------------------------------------------------------
let pop = null;
function closePop() { pop?.remove(); pop = null; document.removeEventListener("mousedown", outside, true); }
function outside(e) { if (pop && !pop.contains(e.target)) closePop(); }

/** Offer the playlists (and a new one) for `paths`, songs by their path in the music folder. */
export async function addToPlaylist(anchor, paths) {
  closePop();
  if (!paths.length) return;
  let lists = [];
  try { lists = (await api("/playlists")).playlists; } catch (e) { /* offer a new one anyway */ }
  const what = paths.length === 1 ? "1 song" : `${paths.length} songs`;
  const add = async (p) => {
    try {
      await api(`/playlists/${p.id}/add`, { method: "POST", body: { tracks: paths } });
      toast(`Added ${what} to ${p.name}`, "good");
    } catch (e) { toast(`Could not add to the playlist: ${errorText(e)}`, "bad"); }
    closePop();
  };
  const body = h("div");
  const menu = () => clear(body,
    h("button", { type: "button", onclick: () => clear(body, nameForm("", async (name) => {
      const p = await api("/playlists", { method: "POST", body: { name, tracks: paths } });
      toast(`Made the playlist ${p.name} with ${what}`, "good");
      closePop();
    }, menu)) }, icon("plus"), "New playlist…"),
    ...lists.map((p) => h("button", { type: "button", onclick: () => add(p) }, icon("music"), h("span.ellipsis.grow", p.name),
      h("span.dim.small", String(p.count)))));
  menu();
  pop = h("div.pick-pop", { role: "menu", "aria-label": `Add ${what} to a playlist` }, body);
  document.body.append(pop);
  const r = anchor.getBoundingClientRect(), w = pop.offsetWidth, ht = pop.offsetHeight;
  pop.style.left = `${Math.max(8, Math.min(window.innerWidth - w - 8, r.right - w))}px`;
  pop.style.top = `${r.bottom + 6 + ht > window.innerHeight ? Math.max(8, r.top - ht - 6) : r.bottom + 6}px`;
  setTimeout(() => document.addEventListener("mousedown", outside, true));
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePop(); });

// ---- the playlist's cover ---------------------------------------------------------------------------------------------
/** The cover as playlists.py describes it: a picture you uploaded, one album's cover, or up to four albums' covers tiled. */
export function coverArt(pl, cls) {
  const c = pl.cover || { kind: "tiles", tiles: [] };
  const img = (src) => h("img", { src, alt: "", loading: "lazy" });
  if (c.kind === "image") return h("div." + cls + ".pl-art", img(url(`/playlists/${pl.id}/image`, { v: c.version })));
  if (c.kind === "album") return h("div." + cls + ".pl-art", img(url("/library/cover", { path: c.cover })));
  const tiles = c.tiles || [];
  if (!tiles.length) return h("div." + cls + ".pl-art.empty-art", icon("music"));
  if (tiles.length === 1) return h("div." + cls + ".pl-art", img(url("/library/cover", { path: tiles[0] })));
  return h("div." + cls + ".pl-art.pl-tiles", ...[0, 1, 2, 3].map((i) => (tiles[i] ? img(url("/library/cover", { path: tiles[i] })) : h("span"))));
}

const picker = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp,image/gif", hidden: true });
document.body.append(picker);

function coverMenu(anchor) {
  const pl = state.pl;
  closePop();
  const kind = pl.cover?.kind || "tiles";
  const choose = async (cover) => {
    closePop();
    try {
      await api(`/playlists/${pl.id}`, { method: "PUT", body: { cover } });
      await select(pl.id, true);
      load();
    } catch (e) { toast(`Could not change the cover: ${errorText(e)}`, "bad"); }
  };
  const tick = (on) => (on ? icon("check") : h("span", { style: { width: "16px", flex: "none" } }));
  pop = h("div.pick-pop.cover-pop", { role: "menu", "aria-label": "Playlist cover" },
    h("button", { type: "button", onclick: () => { closePop(); picker.click(); } }, tick(kind === "image"), icon("image"), "Upload a picture…"),
    h("button", { type: "button", onclick: () => choose({}) }, tick(kind === "tiles"), icon("library"), "The first four albums, tiled"),
    pl.albums?.length ? h("div.cover-pop-head", "Or one album's cover") : null,
    h("div.cover-pop-grid", ...(pl.albums || []).map((a) => h("button.cover-pop-album" + (kind === "album" && pl.cover.album === a.folder ? ".on" : ""), {
      type: "button", title: a.folder, onclick: () => choose({ album: a.folder }) },
      h("img", { src: url("/library/cover", { path: a.cover }), alt: a.folder.split("/").pop() })))));
  document.body.append(pop);
  const r = anchor.getBoundingClientRect(), ht = pop.offsetHeight;
  pop.style.left = `${Math.max(8, Math.min(window.innerWidth - pop.offsetWidth - 8, r.left))}px`;
  pop.style.top = `${r.bottom + 6 + ht > window.innerHeight ? Math.max(8, r.top - ht - 6) : r.bottom + 6}px`;
  setTimeout(() => document.addEventListener("mousedown", outside, true));
}

picker.addEventListener("change", () => {
  const file = picker.files?.[0], pl = state.pl;
  picker.value = "";
  if (!file || !pl) return;
  if (file.size > 15 * 1024 * 1024) { toast("That picture is over 15 MB", "bad"); return; }
  const reader = new FileReader();
  reader.onload = async () => {
    try {
      await api(`/playlists/${pl.id}/image`, { method: "POST", body: { data: reader.result } });
      await select(pl.id, true);
      load();
    } catch (e) { toast(`Could not use that picture: ${errorText(e)}`, "bad"); }
  };
  reader.readAsDataURL(file);
});
