// Album Search, bottom right: the tags that will be written when the selected upload is saved. Filled from the
// upload's own tags and archive.org, then matched against MusicBrainz; every value shows where it came from.

import { api, waitJob } from "./api.js";
import { h, clear, icon, chip, fmtTime, toast, errorText, dismissOnOutside } from "./ui.js";

const FIELDS = [["albumartist", "Album artist"], ["album", "Album"], ["date", "Date"], ["genre", "Genre"],
  ["label", "Label"], ["catalog", "Catalog #"]];
const SOURCE = { musicbrainz: ["MB", "mb"], you: ["you", "you"], "archive.org": ["ia", ""], tags: ["file", ""] };

const state = { id: null, meta: null, folder: null, issues: [], matches: null, mbState: "", diff: [], editing: false, draft: null };
let card = null;
let settings = () => ({});

export function mountTags(el, opts) {
  card = el;
  settings = opts.settings;
  renderEmpty();
}

function renderEmpty() {
  clear(card, h("div.card-head", h("span.card-title", "Tags"), h("span.card-sub", "written when saved")),
    h("div.empty", icon("tag"), h("b", "No upload chosen"), h("span", "The album and track tags appear here, checked against MusicBrainz.")));
}

export async function loadTags(cand) {
  const id = cand.identifier;
  Object.assign(state, { id, meta: null, folder: null, issues: [], matches: null, mbState: "", diff: [], editing: false, draft: null });
  clear(card, head(), h("div.empty", h("span.spinner"), h("span", "Reading the tags…")));
  try {
    const r = await api(`/items/${encodeURIComponent(id)}/meta`);
    if (state.id !== id) return;
    take(r);
    render();
    if (settings().auto_musicbrainz !== false) lookup();
  } catch (e) {
    if (state.id === id) clear(card, head(), h("div.empty", icon("alert"), h("b", "Could not read the tags"), h("span", errorText(e))));
  }
}

function take(r) {
  state.meta = r.meta;
  state.folder = r.folder;
  state.issues = r.issues || [];
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
function head() {
  const right = [];
  if (state.meta) {
    if (state.editing) {
      right.push(h("button.btn.secondary.sm", { onclick: () => { state.editing = false; state.draft = null; render(); } }, "Cancel"),
        h("button.btn.primary.sm", { onclick: saveEdits }, icon("check"), "Save tags"));
    } else {
      right.push(h("button.btn.secondary", { onclick: () => { state.editing = true; state.draft = structuredClone(state.meta); render(); } },
        icon("tag"), "Edit tags"));
    }
  }
  return h("div.card-head", h("span.card-title", "Tags"), h("span.card-sub.grow", "written when saved"), ...right);
}

let mbEl = null;
function render() {
  if (!state.meta) return;
  const m = state.editing ? state.draft : state.meta;
  mbEl = h("div.mb-bar");
  const fields = h("div.tag-fields", ...FIELDS.map(([k, label]) => {
    const [badge, cls] = SOURCE[state.meta.sources?.[k]] || ["", ""];
    const input = h("input", { value: m[k] || "", readOnly: !state.editing, oninput: (e) => { state.draft[k] = e.target.value; } });
    return h("label", label, h("div.field", input, badge ? h("span.src-badge" + (cls ? "." + cls : ""), badge) : null));
  }));
  const changed = new Set(state.diff.filter((d) => d.scope === "track").map((d) => d.source));
  const tracks = m.tracks || [];
  const multiDisc = tracks.some((t) => t.disc > 1);
  const rows = tracks.map((t, i) => {
    const cell = (k, w) => state.editing
      ? h("input", { value: t[k] ?? "", style: w ? { width: w } : null,
        oninput: (e) => { state.draft.tracks[i][k] = k === "track" || k === "disc" ? parseInt(e.target.value, 10) || 0 : e.target.value; } })
      : (t[k] || (k === "artist" ? m.albumartist : "—"));
    return h("tr" + (changed.has(t.source) && !state.editing ? ".changed" : ""), { title: t.source },
      h("td.num", multiDisc || state.editing ? cell("disc", "36px") : t.disc || 1),
      h("td.num", cell("track", "40px")),
      h("td.strong", h("div.row", state.editing ? cell("title") : h("span.ellipsis", t.title || "—"),
        changed.has(t.source) && !state.editing ? chip("changed", "solid-warn") : null)),
      h("td.dim", state.editing ? cell("artist") : h("span.ellipsis", t.artist || m.albumartist)),
      h("td.r.num", fmtTime(t.length)));
  });
  const table = h("div.tag-tracks.scroll", h("table.grid.static",
    h("thead", h("tr", h("th", "Disc"), h("th", "#"), h("th", "Title"), h("th", "Artist"), h("th.r", "Length"))),
    h("tbody", ...rows)));
  clear(card, head(), mbEl, fields, table,
    state.issues.length ? h("div.issue-line", ...state.issues.map((t) => chip(t, "warn", { icon: "alert" }))) : null,
    state.folder ? h("div.folder-line", icon("folder"), h("span.ellipsis", { title: state.folder }, state.folder)) : null);
  renderMB();
}

function renderMB() {
  if (!mbEl) return;
  const m = state.meta;
  if (state.mbState === "searching") {
    clear(mbEl, h("span.spinner"), h("span.dim", "Looking the album up on MusicBrainz…"));
    return;
  }
  if (state.mbState.startsWith("failed")) {
    clear(mbEl, icon("alert"), h("span.dim.grow", "MusicBrainz " + state.mbState),
      h("button.btn.ghost.sm", { onclick: lookup }, "Retry"));
    return;
  }
  const matches = state.matches || [];
  const cur = matches.find((x) => x.release_id === m.mb_release_id);
  const change = matches.length ? h("button.btn.ghost.sm", { onclick: (e) => openMenu(e.currentTarget) }, "Change") : null;
  if (cur) {
    clear(mbEl, h("span.score", Math.round(cur.score * 100)), h("span.src", "MusicBrainz"),
      h("span.ellipsis.grow", describe(cur)), change);
  } else if (m.mb_release_id) {
    clear(mbEl, h("span.src", "MusicBrainz"), h("span.ellipsis.grow.dim", `release ${m.mb_release_id}`), change);
  } else if (state.matches === null) {
    clear(mbEl, h("span.dim.grow", "Not matched to MusicBrainz yet"), h("button.btn.ghost.sm", { onclick: lookup }, "Look up"));
  } else if (!matches.length) {
    clear(mbEl, icon("info"), h("span.dim.grow", "No MusicBrainz release matches these tracks"), h("button.btn.ghost.sm", { onclick: lookup }, "Retry"));
  } else {
    const best = matches[0];
    clear(mbEl, h("span.score", Math.round(best.score * 100)), h("span.src", "Best match"), h("span.ellipsis.grow", describe(best)),
      h("button.btn.ghost.sm", { onclick: () => apply(best.release_id) }, "Apply"), change);
  }
}

function describe(x) {
  return [x.title, x.date, x.country, x.formats, x.catalog].filter(Boolean).join(" · ");
}

function openMenu(anchor) {
  const menu = h("div.mb-menu", ...(state.matches || []).map((x) =>
    h("button", { onclick: () => { menu.remove(); apply(x.release_id); }, title: (x.reasons || []).join("\n") },
      h("span.s", Math.round(x.score * 100)),
      h("span.ellipsis", h("b", x.title), " ", h("span.dim", describe({ ...x, title: "" }))),
      h("span.muted", x.length_delta != null ? `±${x.length_delta.toFixed(1)} s` : ""))));
  mbEl.append(menu);
  dismissOnOutside(menu, anchor, () => menu.remove());
}

async function saveEdits() {
  try {
    const r = await api(`/items/${encodeURIComponent(state.id)}/meta`, { method: "PUT", body: state.draft });
    take(r);
    state.editing = false;
    state.draft = null;
    state.diff = [];
    render();
    toast("Tags saved for this upload. They are written when you add it to the library.", "good");
  } catch (e) {
    toast(`Could not save the tags: ${errorText(e)}`, "bad");
  }
}
