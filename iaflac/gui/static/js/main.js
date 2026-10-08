// Wires the screens together: top-bar tabs, the selected upload shared by the Album Search cards, and Library → CD Art.

import { connect, on } from "./api.js";
import { h, hydrateIcons, toast } from "./ui.js";
import { mountSearch, updateCandidate as updateRow } from "./search.js";
import { mountDeepTest, loadCandidate, updateCandidate as updateDeep } from "./deeptest.js";
import { mountTags, loadTags } from "./tags.js";
import { mountLibrary, load as loadLibrary } from "./library.js";
import { mountCdArt, openAlbum, refresh as refreshArt } from "./cdart.js";
import { mountSettings, showSettings, loadSettings, getSettings } from "./settings.js";
import { mountQueue } from "./queue.js";

hydrateIcons();

function show(view) {
  for (const v of document.querySelectorAll(".view")) v.classList.toggle("active", v.id === `view-${view}`);
  for (const t of document.querySelectorAll("#tabs .tab")) t.classList.toggle("active", t.dataset.view === view);
  document.getElementById("settings-btn").classList.toggle("active", view === "settings");
  if (view === "settings") showSettings();
  if (view === "library") loadLibrary();
  if (view === "cdart") refreshArt();
}
for (const b of document.querySelectorAll("[data-view]")) b.addEventListener("click", () => show(b.dataset.view));

// ---- Album Search: one wide block on top, deep test and tags below --------------------------------------------------
const search = document.getElementById("view-search");
const searchCard = h("div.card", { id: "search-card" });
const deepCard = h("div.card.deep-card");
const tagsCard = h("div.card.tags-card");
search.append(searchCard, deepCard, tagsCard);

mountSearch(searchCard, { select: (c) => { loadCandidate(c); loadTags(c); } });
mountDeepTest(deepCard, { updated: (c) => updateRow(c), openSettings: () => show("settings") });
mountTags(tagsCard, { settings: getSettings });
on("candidate", (e) => updateDeep(e.candidate));

// ---- CD Art ----------------------------------------------------------------------------------------------------------
const art = document.getElementById("view-cdart");
const artLeft = h("div.card"), artRight = h("div.card.case-card");
art.append(artLeft, artRight);
mountCdArt(artLeft, artRight, { settings: getSettings });

// ---- Library ---------------------------------------------------------------------------------------------------------
const lib = document.getElementById("view-library");
const libTop = h("div.card"), libBottom = h("div.card");
lib.append(libTop, libBottom);
const badge = document.getElementById("lib-badge");
mountLibrary(libTop, libBottom, {
  openArt: (folder) => { show("cdart"); openAlbum(folder); },
  openSettings: () => show("settings"),
  count: (n) => { badge.hidden = !n; badge.textContent = n; },
});

// ---- Settings --------------------------------------------------------------------------------------------------------
mountSettings(document.getElementById("view-settings"), {
  changed: (s, values) => {
    if ("library" in values) loadLibrary();
    if ("paper" in values || "disc_hole_mm" in values || "bleed_mm" in values) refreshArt();
  },
});

// ---- start -----------------------------------------------------------------------------------------------------------
let wasDown = false;
on("disconnected", () => { if (!wasDown) { wasDown = true; } });
on("connected", () => { if (wasDown) { wasDown = false; toast("Reconnected to the iaflac engine"); } });
connect();
mountQueue();
loadSettings().catch(() => {});
loadLibrary();
