// The albums to choose from, for the album picker: any album folders opened on their own, then the music folder's
// (read in the background the first time, with progress), each with its cover thumbnail.

import { api, url, on, waitJob } from "./api.js";
import { h, icon, toast, errorText } from "./ui.js";

const state = { data: null, scanning: false, done: 0, total: 0 };
let changed = () => {};

export function mountArtLibrary(opts) {
  changed = opts.changed || changed;
  on("library", () => { state.done = state.total = 0; load(); });
  on("progress", (e) => {                                  // the music folder being read: "45 / 138"
    if (e.stage !== "library" || !state.data?.scanning || e.job_id !== state.data.scanning) return;
    state.done = e.index || 0;
    state.total = e.total || 0;
    changed(state.data);
  });
}

export async function load() {
  try {
    state.data = await api("/library");
  } catch (e) {
    state.data = { error: errorText(e), albums: [] };
  }
  changed(state.data);
}

export function albums() { return state.data?.albums || []; }
export function scanning() { return state.scanning || !!state.data?.scanning; }
// what the picker says while the music folder is read, or why it is empty
export function libraryStatus() {
  const d = state.data;
  if (!d) return "Loading…";
  if (d.scanning) return state.total ? `Reading your music folder… ${state.done} / ${state.total}` : "Reading your music folder…";
  if (!d.root) return "No music folder chosen yet (Settings), or open an album folder";
  if (!d.library_ok) return `${d.root} is not connected`;
  return "";
}

// an album folder opened on its own: a folder dialog in the app window, a typed path in a browser
export async function openFolder() {
  let path = null;
  if (window.pywebview?.api?.choose_folder) path = await window.pywebview.api.choose_folder("");
  else path = window.prompt("Path of an album folder (the folder that holds its tracks):");
  if (!path) return null;
  try {
    const entry = await api("/library/open", { method: "POST", body: { path: path.trim() } });
    await load();
    return entry;
  } catch (e) {
    toast(errorText(e), "bad");
    return null;
  }
}

export async function rescan() {
  state.scanning = true;
  changed(state.data);
  try {
    const { job_id } = await api("/library/scan", { method: "POST" });
    await waitJob(job_id);
    await load();
    toast(`Music folder re-read: ${albums().length} albums`);
  } catch (e) {
    toast(`Rescan failed: ${errorText(e)}`, "bad");
  } finally {
    state.scanning = false;
    changed(state.data);
  }
}

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
