// Library → Edit tags: change an album's tags by hand, or from a MusicBrainz release, in the album's own card.
// Nothing is written until the changes have been reviewed: "Review changes" lists every value that would change
// (old → new), and only "Save tags" writes them. Only the tags shown here change in the files (retag.py).

import { api, waitJob } from "./api.js";
import { h, clear, icon, chip, fmtTime, toast, errorText } from "./ui.js";

const FIELDS = [["albumartist", "Album artist"], ["album", "Album"], ["date", "Date"], ["genre", "Genre"],
  ["label", "Label"], ["catalog", "Catalog #"]];
const LABEL = { albumartist: "Album artist", album: "Album", date: "Date", genre: "Genre", label: "Label", catalog: "Catalog #",
  title: "Title", track: "Track", disc: "Disc", artist: "Artist" };

let card = null, onDone = () => {};
let st = null;      // {folder, orig, draft, skipped, matches, mbState, release, warnings, review, rename, saving}

/** Open the editor for one album in `el` (the Library's album card). `done(saved)` is called when it closes. */
export async function openTagEditor(el, folder, done) {
  card = el;
  onDone = done || (() => {});
  st = { folder, orig: null, draft: null, skipped: [], matches: null, mbState: "", release: null, releaseInfo: null, warnings: [],
    review: null, rename: false, saving: false };
  clear(card, h("div.empty", h("span.spinner"), "Reading the tags…"));
  if (!card.dataset.tagEditor) {                   // any typing in the form after a release was used: "with your edits"
    card.dataset.tagEditor = "1";
    card.addEventListener("input", (e) => { if (st && !st.review && e.target.closest(".tag-fields, .tagedit-table")) st.touched = true; });
  }
  try {
    const r = await api("/library/tags", { query: { folder } });
    st.orig = r.meta;
    st.draft = structuredClone(r.meta);
    st.skipped = r.skipped || [];
    render();
  } catch (e) {
    clear(card, h("div.empty", icon("alert"), h("b", "Could not read the tags"), errorText(e),
      h("button.btn.secondary.sm", { onclick: () => onDone(false) }, "Back")));
  }
}

// ---- the editor ---------------------------------------------------------------------------------------------------
const els = {};

function render() {
  if (st.review) return renderReview();
  const d = st.draft, o = st.orig;
  els.mb = h("div.mb-bar.tagedit-mb");
  const fields = h("div.tag-fields", ...FIELDS.map(([k, label]) => h("label", label,
    h("div.field" + ((d[k] || "") !== (o[k] || "") ? ".changed" : ""),
      h("input", { value: d[k] || "", oninput: (e) => { d[k] = e.target.value; mark(e.target, (o[k] || "") !== e.target.value); } })))));
  const before = Object.fromEntries((o.tracks || []).map((t) => [t.source, t]));
  const rows = d.tracks.map((t) => {
    const was = before[t.source] || {};
    const cell = (k, w) => h("input", {
      value: t[k] ?? "", style: w ? { width: w } : null, class: String(t[k] ?? "") !== String(was[k] ?? "") ? "changed" : null,
      oninput: (e) => { t[k] = k === "track" || k === "disc" ? parseInt(e.target.value, 10) || 0 : e.target.value;
        e.target.classList.toggle("changed", String(t[k] ?? "") !== String(was[k] ?? "")); },
    });
    return h("tr", { title: t.source },
      h("td.num", cell("disc", "36px")), h("td.num", cell("track", "40px")),
      h("td", cell("title")), h("td", h("input", {
        value: t.artist || "", placeholder: d.albumartist || "", class: (t.artist || "") !== (was.artist || "") ? "changed" : null,
        oninput: (e) => { t.artist = e.target.value; e.target.classList.toggle("changed", (t.artist || "") !== (was.artist || "")); },
      })),
      h("td.dim.ellipsis", t.source.split("/").pop()), h("td.r.num", fmtTime(t.length)));
  });
  const table = h("div.tag-tracks.scroll.grow", h("table.grid.static.tagedit-table",
    h("thead", h("tr", h("th", "Disc"), h("th", "#"), h("th", "Title"), h("th", "Track artist"), h("th", "File"), h("th.r", "Length"))),
    h("tbody", ...rows)));
  clear(card,
    h("div.card-head", h("span.card-title", "Edit tags"), h("span.card-sub.grow.ellipsis", st.folder + "/"),
      h("button.btn.secondary.sm", { onclick: () => onDone(false) }, "Cancel"),
      h("button.btn.primary.sm", { onclick: review }, icon("check"), "Review changes")),
    els.mb, fields,
    st.warnings.length ? h("div.issue-line", ...st.warnings.map((w) => chip(w, "warn", { icon: "alert" }))) : null,
    table,
    st.skipped.length ? h("div.folder-line", icon("info"), h("span.ellipsis", `Not editable here (not FLAC or MP3, or unreadable): ${st.skipped.join(", ")}`)) : null);
  renderMB();
}

function mark(input, changed) { input.closest(".field").classList.toggle("changed", changed); }

// ---- MusicBrainz ----------------------------------------------------------------------------------------------------
function renderMB() {
  const artist = h("input", { value: st.mbArtist ?? st.draft.albumartist ?? "", placeholder: "Artist", oninput: (e) => { st.mbArtist = e.target.value; } });
  const album = h("input", { value: st.mbAlbum ?? st.draft.album ?? "", placeholder: "Album", oninput: (e) => { st.mbAlbum = e.target.value; } });
  for (const i of [artist, album]) i.addEventListener("keydown", (e) => { if (e.key === "Enter") lookup(); });
  const head = h("div.row", { style: { width: "100%" } },
    h("span.src", "MusicBrainz"),
    h("label.field.sm.grow", artist), h("label.field.sm.grow", album),
    h("button.btn.secondary.sm", { onclick: lookup, disabled: st.mbState === "searching" },
      st.mbState === "searching" ? h("span.spinner") : icon("search"), "Search"));
  let body = null;
  if (st.mbState === "searching") body = h("div.dim.small", "Looking the album up on MusicBrainz (track count, lengths and names)…");
  else if (st.mbState) body = h("div.small", { style: { color: "var(--bad-text)" } }, st.mbState);
  else if (st.matches && !st.matches.length) body = h("div.dim.small", "No MusicBrainz release found. Try a shorter album name, or edit the tags by hand.");
  else if (st.matches) {
    body = h("div.tagedit-matches", ...st.matches.map((x) => h("div.tagedit-match" + (st.release === x.release_id ? ".on" : ""),
      { title: (x.reasons || []).join("\n") },
      h("span.score", Math.round(x.score * 100)),
      h("div.grow", { style: { minWidth: 0 } },
        h("div.ellipsis", h("b", x.title), " ", h("span.dim", x.artist)),
        h("div.dim.small.ellipsis", [x.date, x.country, x.formats, x.label, x.catalog,
          `${x.discs.length > 1 ? x.discs.join("+") : x.discs[0]} tracks`,
          x.length_delta != null ? `lengths ±${x.length_delta.toFixed(1)} s` : ""].filter(Boolean).join(" · "))),
      h("button.btn.sm" + (st.release === x.release_id ? ".primary" : ".secondary"), { onclick: () => useRelease(x) },
        st.release === x.release_id ? "In use" : "Use"))));
  } else if (st.orig.mb_release_id) {
    body = h("div.dim.small", `Tagged with MusicBrainz release ${st.orig.mb_release_id}. Search to choose it again or another.`);
  }
  clear(els.mb, h("div", { style: { display: "flex", flexDirection: "column", gap: "8px", width: "100%" } }, head, body));
}

async function lookup() {
  st.mbState = "searching";
  renderMB();
  try {
    const { job_id } = await api("/library/tags/musicbrainz", { method: "POST",
      body: { folder: st.folder, artist: st.mbArtist ?? st.draft.albumartist ?? "", album: st.mbAlbum ?? st.draft.album ?? "" } });
    const r = await waitJob(job_id);
    st.matches = r.matches || [];
    st.mbState = "";
  } catch (e) {
    st.mbState = `MusicBrainz: ${errorText(e)}`;
  }
  if (!st.review) renderMB();
}

// Use: fill the tags from the release and go straight to the review (Back to editing leaves them filled in)
async function useRelease(x) {
  try {
    const r = await api("/library/tags/preview", { method: "POST", body: { folder: st.folder, meta: st.draft, release_id: x.release_id } });
    st.draft = r.meta;
    st.release = x.release_id;
    st.releaseInfo = x;
    st.touched = false;
    st.warnings = r.warnings || [];
    if (!r.diff.length && !r.ids_added) {
      render();
      toast("Your tags already match this release: nothing would change", "", 5000);
      return;
    }
    st.review = r;
    render();
  } catch (e) {
    toast(`MusicBrainz: ${errorText(e)}`, "bad");
  }
}

// ---- review, then save ----------------------------------------------------------------------------------------------
async function review() {
  try {
    const r = await api("/library/tags/preview", { method: "POST", body: { folder: st.folder, meta: st.draft } });
    if (!r.diff.length && !r.ids_added && !r.renames.length) { toast("Nothing has changed yet"); return; }
    if (!r.diff.length && !r.ids_added) st.rename = true;   // the tags are right, only file names differ: that is the change
    r.warnings = st.warnings;
    st.review = r;
    render();
  } catch (e) {
    toast(`Could not check the changes: ${errorText(e)}`, "bad");
  }
}

const describeRelease = (x) => [x.title, x.date, x.country, x.formats,
  x.discs ? `${x.discs.length > 1 ? x.discs.join("+") : x.discs[0]} tracks` : ""].filter(Boolean).join(" · ");

// one change: what it is, the value now (struck through) and the value after saving (highlighted)
function change(label, before, after) {
  const val = (v, cls) => h("span.rv-val." + cls, v === "" || v == null ? h("i", "empty") : String(v));
  return h("div.rv-row", h("span.rv-label", label), val(before, "old"), h("span.rv-arrow", "\u2192"), val(after, "new"));
}

function renderReview() {
  const r = st.review;
  const d = r.diff;
  const album = d.filter((x) => x.scope === "album");
  const byTrack = new Map();
  for (const x of d.filter((x) => x.scope === "track")) {
    if (!byTrack.has(x.source)) byTrack.set(x.source, []);
    byTrack.get(x.source).push(x);
  }
  const renames = st.rename ? new Map(r.renames.map(([o, n]) => [o, n])) : new Map();
  const tracks = [...st.draft.tracks].sort((a, b) => a.disc - b.disc || a.track - b.track);
  const changedTracks = tracks.filter((t) => byTrack.has(t.source) || renames.has(t.source));
  const unchanged = tracks.length - changedTracks.length;
  const files = album.length || r.ids_added ? tracks.length : changedTracks.length;
  const count = d.length + (r.ids_added ? 1 : 0) + renames.size;
  const multiDisc = tracks.some((t) => t.disc > 1);
  const fromMB = st.release && st.releaseInfo;

  const summary = h("div.rv-summary",
    h("span.rv-icon", icon(fromMB ? "tag" : "edit")),
    h("div.grow",
      h("b", fromMB ? `From MusicBrainz: ${describeRelease(st.releaseInfo)}${st.touched ? ", with your edits" : ""}` : "Your edits"),
      h("div.dim.small", `${count} change${count === 1 ? "" : "s"} in ${files} of ${tracks.length} file${tracks.length === 1 ? "" : "s"}. `
        + "Nothing is written until you choose Save tags.")));

  const albumSection = album.length || r.ids_added ? h("div.rv-section",
    h("div.rv-head", h("b", "Album"), h("span.dim.small", "written to every track")),
    ...album.map((x) => change(LABEL[x.field] || x.field, x.old, x.new)),
    r.ids_added ? change("MusicBrainz release", st.orig.mb_release_id ? "another release" : "", fromMB ? "this release's IDs" : "added") : null) : null;

  const trackSection = changedTracks.length ? h("div.rv-section",
    h("div.rv-head", h("b", "Tracks"), h("span.dim.small", unchanged ? `${unchanged} other track${unchanged === 1 ? "" : "s"} unchanged` : "")),
    ...changedTracks.map((t) => h("div.rv-track",
      h("div.rv-track-head", h("span.rv-num", (multiDisc ? `${t.disc}-` : "") + String(t.track || "?").padStart(2, "0")),
        h("b.ellipsis", t.title || "(no title)"), h("span.dim.small.ellipsis", t.source.split("/").pop())),
      ...(byTrack.get(t.source) || []).map((x) => change(LABEL[x.field] || x.field, x.old, x.new)),
      renames.has(t.source) ? change("File name", t.source.split("/").pop(), renames.get(t.source).split("/").pop()) : null))) : null;

  clear(card,
    h("div.card-head", h("span.card-title", "Review changes"), h("span.grow"),
      h("button.btn.secondary.sm", { onclick: () => { st.review = null; render(); } }, "Back to editing"),
      h("button.btn.primary.sm", { onclick: save, disabled: st.saving }, st.saving ? h("span.spinner") : icon("check"), "Save tags")),
    summary,
    ...(r.warnings || []).map((w) => h("div.finding.warn", icon("alert"), h("span", w))),
    h("div.scroll.grow.rv-body", albumSection, trackSection),
    h("label.check", { style: { marginTop: "10px" } },
      h("input", { type: "checkbox", checked: st.rename, onchange: (e) => { st.rename = e.target.checked; renderReview(); } }),
      `Also rename the files to match (\u201c03 - Title.flac\u201d)` + (r.renames.length ? `: ${r.renames.length} file${r.renames.length === 1 ? "" : "s"}` : ": none need it")),
    h("div.folder-line", icon("info"), h("span", "Only the tags listed here change. Every other tag, and the embedded cover, stay as they are.")));
}

async function save() {
  st.saving = true;
  renderReview();
  try {
    const r = await api("/library/tags", { method: "PUT", body: { folder: st.folder, meta: st.draft, rename: st.rename } });
    const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
    const parts = [];
    if (r.written.length) parts.push(`Saved tags in ${plural(r.written.length, "file")}`);
    if (r.renamed.length) parts.push(parts.length ? `renamed ${r.renamed.length}` : `Renamed ${plural(r.renamed.length, "file")}`);
    toast(parts.join(", ") || "Nothing needed changing", "good");
    for (const f of r.failures || []) toast(f, "bad", 8000);
    onDone(true);
  } catch (e) {
    st.saving = false;
    renderReview();
    toast(`Could not save the tags: ${errorText(e)}`, "bad", 8000);
  }
}
