// Wires the screens together: top-bar tabs, the selected upload shared by the Album Search cards, and Library → CD Art.

import { connect, on } from "./api.js";
import { h, hydrateIcons, toast } from "./ui.js";
import { mountSearch, searchFor } from "./search.js";
import { mountUploadHead, showUpload, updateUpload, setDetail } from "./uploadhead.js";
import { mountTopbar } from "./topbar.js";
import { mountSpectrogram, loadCandidate, updateCandidate as updateSpec } from "./spectrogram.js";
import { mountTags, loadTags } from "./tags.js";
import { mountLibrary, load as loadLibrary, openAlbum as openLibraryAlbum } from "./library.js";
import { load as loadHealth } from "./health.js";
import { mountNowPlaying } from "./nowplaying.js";
import { mountArtLibrary, load as loadArtLibrary } from "./artlib.js";
import { mountCdArt, openAlbum, refresh as refreshArt, refreshPicker } from "./cdart.js";
import { mountSettings, showSettings, loadSettings, getSettings } from "./settings.js";
import { mountQueue } from "./queue.js";

hydrateIcons();
mountTopbar();

let current = "library", before = "library";
function show(view) {
  if (view !== current) { before = current; current = view; }
  for (const v of document.querySelectorAll(".view")) v.classList.toggle("active", v.id === `view-${view}`);
  for (const t of document.querySelectorAll("#tabs .tab")) t.classList.toggle("active", t.dataset.view === view);
  document.getElementById("settings-btn").classList.toggle("active", view === "settings");
  if (view === "settings") showSettings();
  if (view === "library") loadLibrary();
  if (view === "cdart") refreshArt();
}
for (const b of document.querySelectorAll("[data-view]")) b.addEventListener("click", () => show(b.dataset.view === "settings" && current === "settings" ? before : b.dataset.view));
// Escape leaves Settings for the screen you came from
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && current === "settings" && !document.querySelector("dialog[open]")) show(before); });

// ---- Album Search: the results on the left; the chosen upload on the right (header and tags, then Listen and check)
const search = document.getElementById("view-search");
const searchCard = h("div.card.list-card", { id: "search-card" });
const upHead = h("div.up-head"), tagsBox = h("div.up-tags");
const upRow = h("div.up-row", { hidden: true }, upHead, tagsBox);
const listen = h("section.listen-section", { "aria-label": "Listen and check" });
search.append(searchCard, h("div.pane", upRow, listen));

mountUploadHead(upHead);
mountSearch(searchCard, { select: (c) => { upRow.hidden = false; showUpload(c); loadCandidate(c); loadTags(c); } });
mountSpectrogram(listen, { openSettings: () => show("settings") });
mountTags(tagsBox, { settings: getSettings, meta: (m) => setDetail({ label: m.label }) });
on("candidate", (e) => { updateSpec(e.candidate); updateUpload(e.candidate); });

// ---- CD Art ----------------------------------------------------------------------------------------------------------
const art = document.getElementById("view-cdart");
const artLeft = h("div.card"), artRight = h("div.card.case-card");
art.append(artLeft, artRight);
mountCdArt(artLeft, artRight, { settings: getSettings });
mountArtLibrary({ changed: () => refreshPicker() });   // Case Art's album picker: the music folder, plus folders opened on their own

// ---- Library (home): the albums on the left, one album on the right; the library check lives inside it ----------
const lib = document.getElementById("view-library");
const libList = h("div.card.list-card"), libPane = h("div.pane.album-pane");
lib.append(libList, libPane);
const badge = document.getElementById("lib-badge");
mountLibrary(libList, libPane, {
  openArt: (folder) => { show("cdart"); openAlbum(folder); },
  openSettings: () => show("settings"),
  openSearch: (artist, album) => { show("search"); searchFor(artist, album); },
  libraryChanged: () => loadArtLibrary(),
  count: (n) => { badge.hidden = !n; badge.textContent = n; },
});
// the Now playing card (art, scope, XY) opens from the top bar's playing display
mountNowPlaying(document.getElementById("np-pop"));

// ---- Settings --------------------------------------------------------------------------------------------------------
mountSettings(document.getElementById("view-settings"), {
  changed: (s, values) => {
    if ("library" in values) { loadLibrary(); loadArtLibrary(); loadHealth(); }
    if ("paper" in values || "disc_hole_mm" in values || "bleed_mm" in values) refreshArt();
  },
});

// ---- start -----------------------------------------------------------------------------------------------------------
let wasDown = false;
on("disconnected", () => { if (!wasDown) { wasDown = true; } });
on("connected", () => { if (wasDown) { wasDown = false; toast("Reconnected to the flacmn engine"); } });
connect();
mountQueue();
const ready = Promise.allSettled([loadSettings(), loadLibrary(), loadArtLibrary()]);
window.flacmnSplash?.done(ready);   // the launch screen stays until the settings and library are in
