// Album Search, beside the upload's header: the tags that will be written when it is saved. Filled from the upload's
// own tags and archive.org, then matched against MusicBrainz; every value shows where it came from. The album's fields
// are edited in place (saved as you leave each one); the track tags open in their own window.

import { api, waitJob } from "./api.js";
import { h, clear, icon, chip, fmtTime, toast, errorText, dismissOnOutside } from "./ui.js";

const FIELDS = [["albumartist", "Album artist"], ["album", "Album"], ["date", "Date"], ["genre", "Genre"],
  ["label", "Label"], ["catalog", "Catalog #"]];
const SOURCE = { musicbrainz: ["MB", "mb"], you: ["you", "you"], "archive.org": ["ia", ""], tags: ["file", ""] };

const state = { id: null, meta: null, folder: null, issues: [], matches: null, mbState: "", diff: [] };
let box = null;
let settings = () => ({});
let onMeta = () => {};

export function mountTags(el, opts) {
  box = el;
  settings = opts.settings;
  onMeta = opts.meta || (() => {});
  clear(box);
}

export async function loadTags(cand) {
  const id = cand.identifier;
  Object.assign(state, { id, meta: null, folder: null, issues: [], matches: null, mbState: "", diff: [] });
  clear(box, head(), h("div.muted", h("span.spinner"), " Reading the tags…"));
  try {
    const r = await api(`/items/${encodeURIComponent(id)}/meta`);
    if (state.id !== id) return;
    take(r);
    render();
    if (settings().auto_musicbrainz !== false) lookup();
  } catch (e) {
    if (state.id === id) clear(box, head(), h("div.muted", icon("alert"), ` Could not read the tags: ${errorText(e)}`));
  }
}

function take(r) {
  state.meta = r.meta;
  state.folder = r.folder;
  state.issues = r.issues || [];
  onMeta(state.meta);
}

async function lookup() {
  const id = state.id;
  state.mbState = "searching";
  renderMB();
  try {
    const { job_id } = await api(`/items/${encodeURIComponent(id)}/musicbrainz`, { method: "POST", body: {} });
    const res = await waitJob(job_id);
    if (state.id !== id) return;
    state.matches = res.matches || [];
    state.mbState = "";
    const best = state.matches[0];
    if (best && best.score >= 0.95 && state.meta.mb_release_id !== best.release_id) await apply(best.release_id, true);
    else render();
  } catch (e) {
    if (state.id !== id) return;
    state.mbState = "failed: " + errorText(e);
    renderMB();
  }
}

async function apply(releaseId, auto = false) {
  const id = state.id;
  try {
    const r = await api(`/items/${encodeURIComponent(id)}/musicbrainz/${releaseId}`, { method: "POST" });
    if (state.id !== id) return;
    take(r);
    state.diff = r.diff || [];
    for (const w of r.warnings || []) toast(w, "", 6000);
    render();
    if (!auto) toast(`Applied MusicBrainz release: ${state.diff.length} value(s) changed`);
  } catch (e) {
    toast(`MusicBrainz: ${errorText(e)}`, "bad");
  }
}

// ---- layout -----------------------------------------------------------------------------------------------------
let mbEl = null;
function head() {
  mbEl = h("span.tags-mb");
  return h("div.tags-head", h("h2", "Tags"), mbEl);
}

function render() {
  if (!state.meta) return;
  const m = state.meta;
  const fields = h("div.tag-fields", ...FIELDS.map(([k, label]) => {
    const [badge, cls] = SOURCE[m.sources?.[k]] || ["", ""];
    const input = h("input", { value: m[k] || "", "aria-label": label,
      onchange: (e) => saveField(k, e.target.value.trim()) });
    return h("label", label, h("span.field", input, badge ? h("span.src-badge" + (cls ? "." + cls : ""), { title: `from ${m.sources?.[k]}` }, badge) : null));
  }));
  const changed = state.diff.filter((d) => d.scope === "track").length;
  clear(box, head(), fields,
    h("div.tags-foot",
      h("button.btn.ghost.sm", { type: "button", onclick: openTracks }, icon("tag"), `Track tags · ${(m.tracks || []).length}`),
      changed ? chip(`${changed} changed by MusicBrainz`, "warn") : null,
      state.issues.length ? chip(state.issues.length === 1 ? state.issues[0] : `${state.issues.length} tag issues`, "warn", { icon: "alert", title: state.issues.join("\n") }) : null));
  renderMB();
}

async function saveField(k, value) {
  if ((state.meta[k] || "") === value) return;
  const body = structuredClone(state.meta);
  body[k] = value;
  await save(body, false);
}

async function save(body, quiet) {
  try {
    const r = await api(`/items/${encodeURIComponent(state.id)}/meta`, { method: "PUT", body });
    take(r);
    state.diff = [];
    render();
    if (!quiet) toast("Saved. These tags are written when you add the album to your library.", "good");
    return true;
  } catch (e) {
    toast(`Could not save the tags: ${errorText(e)}`, "bad");
    return false;
  }
}

function renderMB() {
  if (!mbEl) return;
  const m = state.meta;
  if (state.mbState === "searching") { clear(mbEl, h("span.spinner"), "Looking it up on MusicBrainz…"); return; }
  if (state.mbState.startsWith("failed")) {
    clear(mbEl, "MusicBrainz " + state.mbState, " · ", h("button.link", { type: "button", onclick: lookup }, "Retry"));
    return;
  }
  if (!m) { clear(mbEl); return; }
  const matches = state.matches || [];
  const cur = matches.find((x) => x.release_id === m.mb_release_id);
  const change = matches.length ? h("button.link", { type: "button", onclick: (e) => openMenu(e.currentTarget) }, "Change") : null;
  if (cur) clear(mbEl, `MusicBrainz · ${Math.round(cur.score * 100)}% match`, change ? " · " : "", change);
  else if (m.mb_release_id) clear(mbEl, "MusicBrainz", change ? " · " : "", change);
  else if (state.matches === null) clear(mbEl, "Not matched yet · ", h("button.link", { type: "button", onclick: lookup }, "Look up"));
  else if (!matches.length) clear(mbEl, "No MusicBrainz match · ", h("button.link", { type: "button", onclick: lookup }, "Retry"));
  else {
    const best = matches[0];
    clear(mbEl, `Best match ${Math.round(best.score * 100)}% · `, h("button.link", { type: "button", onclick: () => apply(best.release_id) }, "Apply"), " · ", change);
  }
}

function describe(x) {
  return [x.title, x.date, x.country, x.formats, x.catalog].filter(Boolean).join(" · ");
}

function openMenu(anchor) {
  const menu = h("div.mb-menu", ...(state.matches || []).map((x) =>
    h("button", { type: "button", onclick: () => { menu.remove(); apply(x.release_id); }, title: (x.reasons || []).join("\n") },
      h("span.s", Math.round(x.score * 100)),
      h("span.ellipsis", h("b", x.title), " ", h("span.dim", describe({ ...x, title: "" }))),
      h("span.muted", x.length_delta != null ? `±${x.length_delta.toFixed(1)} s` : ""))));
  box.append(menu);
  dismissOnOutside(menu, anchor, () => menu.remove());
}

// ---- the track tags, in their own window -------------------------------------------------------------------------
function openTracks() {
  const draft = structuredClone(state.meta);
  const tracks = draft.tracks || [];
  const multiDisc = tracks.some((t) => t.disc > 1);
  const changed = new Set(state.diff.filter((d) => d.scope === "track").map((d) => d.source));
  const cell = (t, k, w) => h("input", { value: t[k] ?? "", style: w ? { width: w } : null, "aria-label": k,
    oninput: (e) => { t[k] = k === "track" || k === "disc" ? parseInt(e.target.value, 10) || 0 : e.target.value; } });
  const dlg = h("dialog.tag-dialog", { "aria-label": "Track tags" },
    h("div.dialog-head", h("h2", "Track tags"), h("span.muted.grow", draft.album || "")),
    h("div.scroll.tag-tracks", h("table.grid.static",
      h("thead", h("tr", h("th", "Disc"), h("th", "#"), h("th", "Title"), h("th", "Artist"), h("th.r", "Length"))),
      h("tbody", ...tracks.map((t) => h("tr" + (changed.has(t.source) ? ".changed" : ""), { title: t.source },
        h("td", multiDisc ? cell(t, "disc", "40px") : cell(t, "disc", "40px")), h("td", cell(t, "track", "44px")),
        h("td", cell(t, "title")), h("td", cell(t, "artist")), h("td.r.num", fmtTime(t.length))))))),
    h("div.dialog-foot",
      h("button.btn.secondary", { type: "button", onclick: () => dlg.close() }, "Cancel"),
      h("button.btn.primary", { type: "button", onclick: async () => { if (await save(draft)) dlg.close(); } }, "Save track tags")));
  dlg.addEventListener("close", () => dlg.remove());
  document.body.append(dlg);
  dlg.showModal();
}
