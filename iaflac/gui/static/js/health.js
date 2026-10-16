// Library Health Check, inside the Library (library.js): an album's problems (missing songs, low-quality files, no cover
// art, there twice: health.py) and their fixes, drawn in the album's "Needs attention" and "Cover" sections. The fixes:
// a search of the Internet Archive for what the album is missing (fetch the missing tracks, use a cover), replace the
// album with a better copy, and merge duplicates (keeping the best copy of every song; the rest goes to the Trash after
// you have seen the plan). Every replacement asks twice: the first click says what will happen, the second does it.

import { api, url, on, waitJob, cancelJob } from "./api.js";
import { h, clear, icon, chip, fmtBytes, fmtTime, toast, errorText } from "./ui.js";

const KINDS = ["missing_tracks", "low_quality", "missing_art", "duplicates"];
const LABEL = { missing_tracks: "Missing songs", low_quality: "Low quality", missing_art: "No cover", duplicates: "Duplicates" };

const state = {
  data: null, selected: null, album: null, musicbrainz: false, running: false, progress: null,
  searching: false, searchStage: "", plan: null, busy: "", picks: {}, confirm: "", showAll: false,
  replace: null,       // the album replacement running: {folder, identifier, jobId, step, done, total, index, count}
};
let needsEl = null, coverEl = null, hooks = {};
const listeners = new Set();
const changed = () => { for (const fn of listeners) fn(); };

/** hooks: openSettings, openSearch(artist, album), libraryChanged(), tagIssues(folder) -> [text], editTags(folder) */
export function mountHealth(opts) {
  hooks = opts || {};
  on("health", () => load());
  on("progress", (e) => {
    if (e.stage === "health" && state.data?.running === e.job_id) { state.progress = e; changed(); }
  });
}
export function onHealth(fn) { listeners.add(fn); }
export function healthData() { return state.data; }
export function checking() { return !!state.data?.running; }
export function progressText() {
  const p = state.progress;
  return p ? `Checking… ${p.index} of ${p.total}${p.step === "MusicBrainz" ? " · MusicBrainz" : ""}` : "Checking…";
}
export function setMusicBrainz(on) { state.musicbrainz = on; }

/** An album's problems in a few words, for the library list. */
export function problemsOf(folder) {
  const a = (state.data?.albums || []).find((x) => x.folder === folder);
  if (!a || !a.findings.length) return null;
  const kinds = KINDS.filter((k) => a.kinds.includes(k)).map((k) => LABEL[k]);
  return { text: kinds.join(", "), bad: a.findings.some((f) => f.severity === "bad"), kinds: a.kinds };
}

export async function load() {
  try {
    state.data = await api("/health-check");
  } catch (e) {
    state.data = { error: errorText(e), albums: [] };
  }
  if (!state.data.running) state.progress = null;
  changed();
  if (state.selected) await select(state.selected, needsEl, coverEl, true);
}

export async function run() {
  try {
    const { job_id } = await api("/health-check/scan", { method: "POST", body: { musicbrainz: state.musicbrainz } });
    state.data = { ...(state.data || {}), running: job_id };
    changed();
    renderRight();
    await waitJob(job_id);
    await load();
    const c = state.data.counts || {};
    toast(`Checked ${c.albums} albums: ${c.albums - c.healthy} need attention`);
  } catch (e) {
    toast(`The library check failed: ${errorText(e)}`, "bad");
    await load();
  }
}

/** Show one album's problems and covers in the two given sections. */
export async function select(folder, needs, cover, keep) {
  needsEl = needs;
  coverEl = cover;
  if (state.selected !== folder || !keep) { state.plan = null; state.picks = {}; state.confirm = ""; state.showAll = false; }
  state.selected = folder;
  state.album = null;
  if (state.data?.checked && (state.data.albums || []).some((a) => a.folder === folder)) {
    try { state.album = await api("/health-check/album", { query: { folder } }); }
    catch (e) { state.album = null; }
  }
  if (state.selected === folder) renderRight();
}

// ---- the two sections ----------------------------------------------------------------------------------------------
function renderRight() {
  if (!needsEl || !needsEl.isConnected) return;
  renderNeeds();
  renderCover();
}

function section(el, title, ...body) {
  clear(el, h("div.section-head", h("h2", title)), ...body);
}

function renderNeeds() {
  const a = state.album, folder = state.selected;
  const tags = hooks.tagIssues ? hooks.tagIssues(folder) : [];
  const d = state.data;
  if (!d?.checked || !a) {
    const running = !!d?.running;
    const items = tags.map((t) => issueRow("warn", "Tags", t, h("button.btn.secondary.sm", { type: "button", onclick: () => hooks.editTags(folder) }, "Edit tags")));
    section(needsEl, `Needs attention${tags.length ? ` · ${tags.length}` : ""}`,
      h("div.scroll.grow.needs-body", h("ul.issue-list", ...items,
        h("li.issue.info", h("span.issue-dot.wait"),
          h("span.issue-text", h("b", running ? progressText() : "Check your library"),
            h("span", "Finds missing songs, low-quality files, albums with no cover and albums you have twice, and offers the fixes.")),
          h("button.btn.primary.sm", { type: "button", onclick: run, disabled: running }, running ? h("span.spinner") : null, running ? "Checking" : "Check library")))));
    return;
  }
  const dupes = a.findings.filter((f) => f.kind === "duplicates");
  const rows = a.findings.map((f) => issueRow(f.severity, LABEL[f.kind], f.text, actionFor(a, f)));
  // the tag check repeats some of these in its own words: leave those out
  const said = (re) => tags.filter((t) => !re.test(t));
  let extra = tags;
  if (a.kinds.includes("missing_art")) extra = said(/cover/i);
  if (a.kinds.includes("missing_tracks")) extra = extra.filter((t) => !/missing track number/i.test(t));
  for (const t of extra) rows.push(issueRow("warn", "Tags", t, h("button.btn.secondary.sm", { type: "button", onclick: () => hooks.editTags(folder) }, "Edit tags")));
  const n = rows.length;
  section(needsEl, `Needs attention${n ? ` · ${n}` : ""}`,
    h("div.scroll.grow.needs-body",
      n ? h("ul.issue-list", ...rows) : h("p.muted", "Nothing to fix. You can still look for a better copy or another cover."),
      a.musicbrainz ? h("p.muted.small", `MusicBrainz: ${a.musicbrainz}`) : null,
      dupes.length ? mergeSection(a, dupes) : null,
      archiveSection(a)));
}

function issueRow(sev, kind, text, action) {
  return h("li.issue", h("span.issue-dot." + (sev === "bad" ? "bad" : "warn")),
    h("span.issue-text", h("b", kind), h("span", text)), action || null);
}

function actionFor(a, f) {
  if (f.kind === "missing_tracks") return h("button.btn.secondary.sm", { type: "button", onclick: () => search(a.folder), disabled: state.searching }, "Find the songs");
  if (f.kind === "low_quality") return h("button.btn.secondary.sm", { type: "button", onclick: () => search(a.folder), disabled: state.searching }, "Find a better copy");
  if (f.kind === "missing_art") return h("button.btn.secondary.sm", { type: "button", onclick: () => search(a.folder), disabled: state.searching }, "Find covers");
  return null;
}

function renderCover() {
  const a = state.album, folder = state.selected;
  const current = hooks.coverOf ? hooks.coverOf(folder) : null;
  const s = a?.search, art = s?.art || [];
  const tiles = [h("div.cover-tile.on", { "aria-current": "true" },
    current ? h("img", { src: current, alt: "" }) : h("div.cover-blank", icon("image")),
    h("b", current ? "In the folder" : "No cover"), h("span", current ? "In use" : "Find one below"))];
  for (const im of art.slice(0, 7)) tiles.push(artTile(a, im, !!current));
  if (!s) {
    tiles.push(h("button.cover-tile.find", { type: "button", disabled: state.searching || !a,
      onclick: () => search(folder), title: a ? "" : "Check your library first" },
      h("div.cover-blank", state.searching ? h("span.spinner") : icon("search")), h("b", "Find covers"),
      h("span", a ? "Cover Art Archive and archive.org" : "Check your library first")));
  }
  section(coverEl, "Cover",
    s?.cover_done ? coverDone(s.cover_done) : null,
    h("div.cover-grid", { role: "list" }, ...tiles));
}

function artTile(a, im, has) {
  const key = `cover:${im.id}`, asking = state.confirm === key;
  const from = im.source.startsWith("coverart") ? "Cover Art Archive" : "archive.org";
  const size = h("span", from);
  return h("div.cover-tile", { role: "listitem" },
    h("img", { src: url(`/health-check/image/${im.id}`), alt: "", loading: "lazy", title: im.label,
      onload: (e) => {                                     // its size, so a small thumbnail is not chosen by mistake
        const img = e.target, small = Math.min(img.naturalWidth, img.naturalHeight) < 500;
        size.textContent = `${img.naturalWidth} × ${img.naturalHeight}${small ? " · small" : ""}`;
        size.style.color = small ? "var(--warn-text)" : "";
      } }),
    h("b", from), size,
    h("button.btn.sm" + (has && asking ? ".danger" : ".secondary"), { type: "button",
      onclick: () => (has && !asking ? ask(key) : useCover(a.folder, im.id, has)), disabled: !!state.busy,
    }, state.busy === key ? h("span.spinner") : null, has ? (asking ? "Yes, replace" : "Use this") : "Use this"));
}

function tracksTable(a) {
  return h("table.grid.static",
    h("thead", h("tr", h("th", "#"), h("th", "Title"), h("th", "Format"), h("th.r", "Length"))),
    h("tbody", ...a.tracks.map((t) => h("tr" + (t.error ? ".changed" : ""), { title: t.name },
      h("td.num", (t.disc > 1 ? `${t.disc}-` : "") + (t.track || "–")),
      h("td.strong", h("span.ellipsis", t.title || t.name)),
      h("td.dim", t.error ? "unreadable" : fmtFormat(t)), h("td.r.num", fmtTime(t.length))))));
}

function fmtFormat(t) {
  if (["FLAC", "WAV", "AIFF", "WV", "APE"].includes(t.format) && t.bits && t.rate) return `${t.format} ${t.bits}/${t.rate / 1000}`;
  if (t.kbps) return `${t.format} ${t.kbps}`;
  return t.format;
}

// ---- duplicates: plan, then merge ---------------------------------------------------------------------------------
function mergeSection(a, dupes) {
  const folders = dupes.find((f) => f.folders.length)?.folders || [a.folder];
  const box = h("div.health-box");
  const p = state.plan && state.plan.folders.includes(a.folder) ? state.plan : null;
  if (!p) {
    clear(box, h("div.row",
      h("div.grow", h("b", folders.length > 1 ? `In ${folders.length} folders` : "Songs twice in this folder"),
        h("div.muted.small", "A merge keeps the best copy of every song in one folder: lossless over lossy, then the higher "
          + "resolution or bitrate. You see the plan first; the other copies go to the Trash, not deleted.")),
      h("button.btn.secondary.sm", { onclick: () => loadPlan(folders), disabled: !!state.busy },
        state.busy === "plan" ? h("span.spinner") : icon("library"), "Review merge")));
    return h("div", h("div.section-label", { style: { marginTop: "18px" } }, "Duplicates"), box);
  }
  const rows = p.songs.map((s) => h("tr",
    h("td.strong", h("span.ellipsis", s.title)),
    h("td", chip(s.keep.format, s.keep.quality[0] === 2 ? "good" : "warn"),
      s.action === "move" ? h("span.dim.small", ` from ${s.keep.folder}`) : null),
    h("td.dim.small", s.drop.length ? s.drop.map((d) => `${d.format}${d.folder !== p.target ? ` (${d.folder})` : ""}`).join(", ") : "—")));
  clear(box,
    h("div.small", "Everything ends up in ", h("b", p.target + "/"),
      `: ${p.moves} song${p.moves === 1 ? "" : "s"} move in, ${p.drops} worse cop${p.drops === 1 ? "y goes" : "ies go"} to the Trash`
      + (p.trash_folders.length ? `, then ${p.trash_folders.join(", ")} goes to the Trash (anything in it that is not audio is copied across first).` : ".")),
    h("table.grid.static", { style: { margin: "8px 0" } },
      h("thead", h("tr", h("th", "Song"), h("th", "Keep"), h("th", "To the Trash"))), h("tbody", ...rows)),
    h("div.row", h("div.grow"),
      h("button.btn.secondary.sm", { onclick: () => { state.plan = null; renderRight(); } }, "Cancel"),
      h("button.btn.danger.sm", { onclick: () => merge(p), disabled: !!state.busy || !(p.moves || p.drops || p.trash_folders.length) },
        state.busy === "merge" ? h("span.spinner") : icon("check"), "Merge")));
  return h("div", h("div.section-label", { style: { marginTop: "18px" } }, "Duplicates"), box);
}

async function loadPlan(folders) {
  state.busy = "plan";
  renderRight();
  try {
    state.plan = await api("/health-check/merge-plan", { method: "POST", body: { folders } });
  } catch (e) {
    toast(`Could not plan the merge: ${errorText(e)}`, "bad");
  } finally {
    state.busy = "";
    renderRight();
  }
}

async function merge(p) {
  state.busy = "merge";
  renderRight();
  try {
    const { job_id } = await api("/health-check/merge", { method: "POST", body: { folders: p.folders, token: p.token } });
    const r = await waitJob(job_id);
    const gone = [r.trashed_folders.length && `${r.trashed_folders.length} old folder${r.trashed_folders.length === 1 ? "" : "s"}`,
      r.trashed_files.length && `${r.trashed_files.length} file${r.trashed_files.length === 1 ? "" : "s"}`].filter(Boolean).join(" and ");
    toast(`Merged into ${r.target}: ${r.moved.length} song${r.moved.length === 1 ? "" : "s"} moved in` + (gone ? `, ${gone} to the Trash` : ""), "good");
    state.plan = null;
    state.selected = r.target;
    hooks.libraryChanged && hooks.libraryChanged();
    await load();
  } catch (e) {
    toast(`Merge failed: ${errorText(e)}`, "bad", 8000);
    state.plan = null;
  } finally {
    state.busy = "";
    renderRight();
  }
}

// ---- the Internet Archive -----------------------------------------------------------------------------------------
function archiveSection(a) {
  const s = a.search;
  const head = h("div.row", { style: { marginTop: "var(--sp-2)" } },
    h("h3.sub-head.grow", "On the Internet Archive"),
    h("button.btn.primary.sm", { onclick: () => search(a.folder), disabled: state.searching },
      state.searching ? h("span.spinner") : icon("search"), s ? "Search again" : "Search the Internet Archive"));
  if (state.searching) return h("div", head, h("div.muted.small", h("span.spinner"), " ", state.searchStage || "Searching archive.org…"));
  if (!s) {
    if (a.findings.length) return null;                 // each problem has its own button that starts this search
    return h("p.muted.small", "Want a better copy or another cover? ",
      h("button.link", { type: "button", onclick: () => search(a.folder) }, "Search the Internet Archive"));
  }
  const all = s.suggestions, art = s.art || [];
  const better = all.filter((x) => x.upgrade).length, whole = all.filter((x) => x.complete).length;
  const shown = state.showAll ? all : all.slice(0, 8);
  return h("div", head,
    h("div.muted.small", `Searched for ${s.query}: ${all.length} upload${all.length === 1 ? "" : "s"}`
      + (whole ? `, ${whole} with the complete album` : "") + (better ? `, ${better} better than your copy` : "") + "."),
    all.length ? shown.map((x) => suggestionBox(a, x))
      : h("div.empty.small", icon("search"), h("b", "No uploads of this album on archive.org"),
        "Try Album Search with a shorter title."),
    all.length > shown.length ? h("button.btn.secondary.sm", { onclick: () => { state.showAll = true; renderRight(); } },
      `Show all ${all.length} uploads`) : null,
    s.notes?.length ? h("div.muted.small", s.notes.join(" · ")) : null);
}

function coverDone(c) {
  const what = [c.written, c.px ? `${c.px} px` : "", c.embedded ? `also embedded in ${c.embedded} track${c.embedded === 1 ? "" : "s"}` : ""]
    .filter(Boolean).join(" · ");
  return h("p.cover-done", icon("check"), h("b", c.replaced ? "Cover replaced" : "Cover saved"), h("span.muted", what));
}

// the first click of a replacement only asks; clicking another replacement asks about that one instead
function ask(key) {
  state.confirm = key;
  renderRight();
}

function suggestionBox(a, x) {
  // the whole album, when songs are missing: download it again; a better copy (or any other one) replaces it
  const verb = x.complete && !x.upgrade ? "Redownload" : "Replace";
  const fixes = x.fixes.map((k) => chip(k === "missing_tracks" ? (x.complete ? `Complete album: all ${x.album_tracks} songs`
      : x.provides_all ? "Has every missing song" : `Has ${x.provides.length} missing song(s)`)
    : k === "low_quality" ? (x.lossless ? "Better than yours: lossless" : `Better than yours: ~${x.kbps} kbps`)
    : "Has a cover", "good", { icon: "check" }));
  const qkind = x.lossless ? "good" : x.upgrade ? "warn" : "bad";
  const picks = state.picks[x.identifier] || (state.picks[x.identifier] = new Set(x.provides.map((p) => p.file)));
  const tracks = x.provides.length ? h("div.provides", ...x.provides.map((p) => h("label.check",
    h("input", { type: "checkbox", checked: picks.has(p.file), onchange: (e) => { e.target.checked ? picks.add(p.file) : picks.delete(p.file); } }),
    h("span", `${p.disc > 1 ? p.disc + "-" : ""}${p.track || "?"}. ${p.title}`), h("span.muted.small", ` ${p.format} · ${fmtBytes(p.size)}`)))) : null;
  const rkey = `album:${x.identifier}`, asking = state.confirm === rkey;
  const r = state.replace, running = !!r && r.folder === a.folder && r.identifier === x.identifier;
  const have = Object.entries(a.formats || {}).map(([f, n]) => `${n} ${f}`).join(", ");
  return h("div.health-box",
    h("div.row", h("div.grow",
      h("div.strong.ellipsis", x.title || x.identifier),
      h("div.muted.small", [x.identifier, x.format, `${x.tracks} tracks`, fmtBytes(x.bytes), x.year, x.match && `${x.match} match`].filter(Boolean).join(" · "))),
      chip(x.quality, qkind, { dot: true, title: x.kbps ? `about ${x.kbps} kbps on average, from the file sizes` : "" })),
    fixes.length ? h("div.issue-line", ...fixes) : null,
    tracks,
    asking ? h("div.finding.warn", icon("alert"), h("span",
      `${verb === "Redownload" ? "Download the complete album again: replace" : "Replace"} your ${a.track_count} tracks (${have}) `
      + `with this upload's ${x.tracks} (${x.format}, ${fmtBytes(x.bytes)})? `
      + "It is downloaded and checked first; only then does your folder go to the Trash, with its booklets and other "
      + "files copied across. Your artist and album names are kept."
      + (x.tracks < a.track_count ? ` This upload has ${a.track_count - x.tracks} fewer track(s) than yours.` : ""))) : null,
    running ? replaceProgress() : null,
    h("div.row", { style: { marginTop: "8px" } }, h("div.grow"),
      h("button.btn.ghost.sm", {
        title: "Look at it (spectrogram, tags) in Album Search first",
        onclick: () => hooks.openSearch && hooks.openSearch(a.artist, (a.search?.titles || [a.album]).slice(-1)[0]),
      }, "Open in Album Search"),
      asking ? h("button.btn.secondary.sm", { onclick: () => ask("") }, "Cancel") : null,
      // never offered for an upload with fewer tracks than yours (a single-file rip, a few songs): it is not the album
      (x.upgrade || x.redownload || (x.lossless && x.tracks >= a.track_count)) && !running ? h("button.btn.sm" + (asking ? ".danger" : x.complete ? ".primary" : ".secondary"), {
        onclick: () => (asking ? replaceAlbum(a.folder, x) : ask(rkey)), disabled: !!state.busy || !!state.replace,
      }, icon(verb === "Redownload" ? "download" : "refresh"),
        asking ? `Yes, ${verb.toLowerCase()} the album` : `${verb} album`) : null,
      x.provides.length ? h("button.btn.primary.sm", { onclick: () => fetchTracks(a.folder, x), disabled: !!state.busy },
        state.busy === `fetch:${x.identifier}` ? h("span.spinner") : icon("download"), "Add to album") : null));
}

async function search(folder) {
  state.searching = true;
  state.searchStage = "";
  renderRight();
  try {
    const { job_id } = await api("/health-check/search", { method: "POST", body: { folder } });
    await waitJob(job_id, (e) => {
      if (e.stage === "search") state.searchStage = `Searching archive.org · ${e.rung || ""}`;
      else if (e.stage === "found") state.searchStage = `Found ${e.count} upload(s)`;
      if (state.selected === folder) renderRight();
    });
    if (state.selected === folder) state.album = await api("/health-check/album", { query: { folder } });
  } catch (e) {
    toast(`The search failed: ${errorText(e)}`, "bad");
  } finally {
    state.searching = false;
    renderRight();
  }
}

async function fetchTracks(folder, x) {
  const files = [...(state.picks[x.identifier] || [])];
  if (!files.length) { toast("Tick the tracks to add first"); return; }
  state.busy = `fetch:${x.identifier}`;
  renderRight();
  try {
    const { job_id } = await api("/health-check/fetch", { method: "POST", body: { folder, identifier: x.identifier, files } });
    const r = await waitJob(job_id);
    if (r.added.length) toast(`Added ${r.added.length} track(s) to ${folder}`, "good");
    for (const f of r.failures) toast(f, "bad", 8000);
    hooks.libraryChanged && hooks.libraryChanged();
    await load();
  } catch (e) {
    toast(`Could not add the tracks: ${errorText(e)}`, "bad", 8000);
  } finally {
    state.busy = "";
    renderRight();
  }
}

// ---- the album replacement's progress, drawn like a download in the queue ------------------------------------------
const STEP = { preparing: "Reading the upload", download: "Downloading", tagging: "Writing tags",
  swapping: "Swapping in: your old folder goes to the Trash", cancelling: "Cancelling" };
let rp = null;          // the bar and its line, while shown: updated in place, so the page is not redrawn per event

function replaceProgress() {
  rp = { text: h("div.s.ellipsis"), bar: h("i") };
  const box = h("div.q-item", { style: { padding: "6px 0 0", borderBottom: 0 } },
    h("div.row", h("div.grow", { style: { minWidth: 0 } }, rp.text),
      h("button.btn.ghost.sm", { onclick: cancelReplace, disabled: state.replace?.step === "cancelling" }, "Cancel")),
    h("div.progress", rp.bar));                        // green, like the queue's
  paintReplace(true);                                  // drawn at once, before the box is on the page
  return box;
}

function paintReplace(fresh = false) {
  const r = state.replace;
  if (!r || !rp || (!fresh && !rp.bar.isConnected)) return;
  const pct = r.step === "download" && r.total ? Math.min(100, (100 * r.done) / r.total)
    : ["tagging", "swapping"].includes(r.step) ? 100 : 0;
  rp.bar.style.width = `${pct}%`;
  rp.text.textContent = (STEP[r.step] || "Starting") + (r.step === "download"
    ? `${r.count ? ` · file ${r.index} of ${r.count}` : ""} · ${fmtBytes(r.done)} of ${fmtBytes(r.total)}` : "");
}

async function cancelReplace() {
  const r = state.replace;
  if (!r) return;
  r.step = "cancelling";
  paintReplace();
  try { await cancelJob(r.jobId); } catch (e) { /* it may have just finished */ }
}

async function replaceAlbum(folder, x) {
  state.confirm = "";
  state.replace = { folder, identifier: x.identifier, jobId: null, step: "preparing", done: 0, total: x.bytes, index: 0, count: x.tracks };
  renderRight();
  try {
    const { job_id } = await api("/health-check/replace", { method: "POST", body: { folder, identifier: x.identifier } });
    state.replace.jobId = job_id;
    const r = await waitJob(job_id, (e) => {
      const st = state.replace;
      if (!st || st.step === "cancelling") return;
      if (e.stage === "download") Object.assign(st, { step: "download", done: e.done_bytes || 0, total: e.total_bytes || st.total,
        index: e.index || st.index, count: e.total || st.count });
      else if (e.stage === "tagging") st.step = "tagging";
      else if (e.stage === "replace" && e.step) st.step = e.step;
      paintReplace();
    });
    if (r.ok) toast(`Replaced ${folder} with ${x.identifier}. Your old folder is in ${r.trash}`, "good", 9000);
    else for (const f of r.failures || []) toast(`${f} (${r.note})`, "bad", 9000);
  } catch (e) {
    if (errorText(e) === "cancelled") toast("Replacement cancelled. Your album was not touched; replacing it again resumes the download.", "", 7000);
    else toast(`Could not replace the album: ${errorText(e)}`, "bad", 9000);
  } finally {
    state.replace = null;
    rp = null;
    hooks.libraryChanged && hooks.libraryChanged();
    await load();
    renderRight();
  }
}

async function useCover(folder, imageId, replace) {
  state.busy = `cover:${imageId}`;
  state.confirm = "";
  renderRight();
  try {
    const r = await api("/health-check/cover", { method: "POST", body: { folder, image_id: imageId, replace } });
    toast(replace ? `Replaced the cover of ${folder}` + (r.embedded ? ` (and in ${r.embedded} tracks)` : "") + `. The old one is in ${r.trash}`
      : `Saved ${r.written} in ${folder}`, "good", 7000);
    hooks.libraryChanged && hooks.libraryChanged();
    await load();
  } catch (e) {
    toast(`Could not save the cover: ${errorText(e)}`, "bad");
  } finally {
    state.busy = "";
    renderRight();
  }
}
