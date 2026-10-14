// Library Health Check: every album in the music folder that is missing songs, is in a low-quality format, has no
// cover art or is there twice (health.py). On the left the flagged albums; on the right the chosen one: what is wrong,
// a search of the Internet Archive for what it is missing (fetch the missing tracks, use a cover), and the merge of
// duplicates, which keeps the best copy of every song and moves the rest to the Trash after you have seen the plan.
// A cover or the whole album can also be replaced with one the search found; the old one goes to the Trash. Every
// replacement asks twice: the first click says what will happen, the second does it.

import { api, url, on, waitJob, cancelJob } from "./api.js";
import { h, clear, icon, chip, fmtBytes, fmtTime, toast, errorText } from "./ui.js";

// the album's cover file; the URL carries the album's signature (which covers its cover files), so a replaced cover
// is fetched anew instead of shown from the window's cache
function cover(a, cls = "thumb") {
  if (!a.cover_file) return h("div." + cls, icon("image"));
  return h("img." + cls, { src: url("/library/file", { path: `${a.folder}/${a.cover_file}`, v: (a.signature || "").slice(0, 12) }),
    alt: "", loading: "lazy" });
}

const KINDS = ["missing_tracks", "low_quality", "missing_art", "duplicates"];
const LABEL = { missing_tracks: "Missing songs", low_quality: "Low quality", missing_art: "Missing art", duplicates: "Duplicates" };
const ICON = { missing_tracks: "music", low_quality: "alert", missing_art: "image", duplicates: "library" };

const state = {
  data: null, kind: "all", filter: "", selected: null, album: null, musicbrainz: false,
  progress: null, searching: false, searchStage: "", plan: null, busy: "", picks: {}, confirm: "", showAll: false,
  replace: null,       // the album replacement running: {folder, identifier, jobId, step, done, total, index, count}
};
let left = null, right = null, hooks = {};
const els = {};

export function mountHealth(leftCard, rightCard, opts) {
  left = leftCard;
  right = rightCard;
  hooks = opts || {};
  on("health", () => load());
  on("progress", (e) => {
    if (e.stage === "health" && state.data?.running === e.job_id) {
      state.progress = e;
      renderHead();
    }
  });
  renderLeft();
  renderRight();
}

export async function load() {
  try {
    state.data = await api("/health-check");
  } catch (e) {
    state.data = { error: errorText(e), albums: [] };
  }
  if (!state.data.running) state.progress = null;
  hooks.count && hooks.count(flagged().length);
  renderLeft();
  if (state.selected) {
    if (state.data.albums?.some((a) => a.folder === state.selected)) await select(state.selected, true);
    else { state.selected = null; state.album = null; renderRight(); }
  }
}

function flagged() { return (state.data?.albums || []).filter((a) => a.findings.length); }

async function run() {
  try {
    const { job_id } = await api("/health-check/scan", { method: "POST", body: { musicbrainz: state.musicbrainz } });
    state.data = { ...(state.data || {}), running: job_id };
    renderLeft();
    await waitJob(job_id);
    await load();
    const c = state.data.counts || {};
    toast(`Checked ${c.albums} albums: ${c.albums - c.healthy} need attention`);
  } catch (e) {
    toast(`The health check failed: ${errorText(e)}`, "bad");
    await load();
  }
}

// ---- left: the flagged albums -----------------------------------------------------------------------------------
function renderLeft() {
  els.head = h("div");
  els.kinds = h("div.filters.health-kinds");
  els.rows = h("div.scroll.grow");
  clear(left, els.head, els.kinds, els.rows);
  renderHead();
  renderRows();
}

function renderHead() {
  const d = state.data;
  const running = !!d?.running;
  const c = d?.counts;
  const sub = running
    ? (state.progress ? `Checking… ${state.progress.index} / ${state.progress.total}${state.progress.step === "MusicBrainz" ? " · MusicBrainz" : ""}` : "Checking…")
    : c ? `${c.albums} albums · ${c.healthy} healthy · ${c.albums - c.healthy} need attention` : (d?.root || "");
  const mb = h("label.check", { title: "Albums whose files carry MusicBrainz IDs (tagged with Picard) are always compared with their "
      + "release. Tick this to also search MusicBrainz for albums without them: slower, about 5 s per album the first time." },
    h("input", { type: "checkbox", checked: state.musicbrainz, onchange: (e) => { state.musicbrainz = e.target.checked; } }),
    "Search MusicBrainz for untagged albums");
  clear(els.head, h("div.card-head",
    h("span.card-title", { style: { whiteSpace: "nowrap" } }, "Library Health Check"), h("span.card-sub.grow.ellipsis", sub), mb,
    h("button.btn.primary.sm", { onclick: run, disabled: running || !d?.library_ok },
      running ? h("span.spinner") : icon("refresh"), d?.checked ? "Check again" : "Run check")));
  const counts = d?.counts || {};
  const kindChip = (key, text) => h("button.toggle-chip" + (state.kind === key ? ".on" : ""),
    { onclick: () => { state.kind = key; renderLeft(); } }, icon("check"), text);
  clear(els.kinds, d?.checked ? [kindChip("all", `All problems (${counts.albums - counts.healthy || 0})`),
    ...KINDS.map((k) => kindChip(k, `${LABEL[k]} (${counts[k] || 0})`)),
    kindChip("every", `Every album (${counts.albums || 0})`),
    h("label.field.sm", { style: { width: "200px", marginLeft: "auto" } }, icon("search"),
      h("input", { placeholder: "Filter…", value: state.filter, oninput: (e) => { state.filter = e.target.value; renderRows(); } }))] : null);
}

function renderRows() {
  const d = state.data;
  if (!d) return clear(els.rows, h("div.empty", h("span.spinner"), "Loading…"));
  if (d.error) return clear(els.rows, h("div.empty", icon("alert"), h("b", "Could not load the health check"), d.error));
  if (!d.root) {
    return clear(els.rows, h("div.empty", icon("folder"), h("b", "No library folder yet"),
      h("span", "Choose your music folder in Settings first."),
      h("button.btn.primary.sm", { onclick: () => hooks.openSettings && hooks.openSettings() }, "Open Settings")));
  }
  if (!d.checked) {
    return clear(els.rows, h("div.empty", icon("pulse"), h("b", d.running ? "Checking your library…" : "Check your library"),
      h("span", "Finds albums with missing songs, low-quality files or no cover art, and albums you have twice. "
        + "Then search the Internet Archive for what is missing, or merge the duplicates.")));
  }
  const q = state.filter.trim().toLowerCase();
  // "Every album" lists the healthy ones too, so any album can be searched for a better copy or another cover
  const pool = state.kind === "every" ? (d.albums || []) : flagged();
  const rows = pool.filter((a) => (state.kind === "all" || state.kind === "every" || a.kinds.includes(state.kind))
    && (!q || `${a.artist} ${a.album}`.toLowerCase().includes(q)));
  if (!rows.length) {
    return clear(els.rows, h("div.empty", icon("check"), h("b", flagged().length ? "Nothing matches" : "Every album is healthy"),
      flagged().length ? null : h("span", `${d.counts.albums} albums checked.`)));
  }
  clear(els.rows, h("table.grid.lib-table.health-table",
    h("thead", h("tr", h("th", ""), h("th", "Artist"), h("th", "Album"), h("th", "Problems"))),
    h("tbody", ...rows.map((a) => h("tr" + (a.folder === state.selected ? ".selected" : ""), { onclick: () => select(a.folder), title: a.folder },
      h("td", { style: { width: "56px" } }, cover(a)),
      h("td", a.artist), h("td.strong", a.album),
      h("td", h("div.issues", ...(a.findings.length ? problemChips(a)
        : [h("span.row", { style: { color: "var(--good-text)" } }, icon("check"), "Healthy")]))))))));
}

function problemChips(a) {
  return KINDS.filter((k) => a.kinds.includes(k)).map((k) => {
    const fs = a.findings.filter((f) => f.kind === k);
    const bad = fs.some((f) => f.severity === "bad");
    return chip(LABEL[k], bad ? "bad" : "warn", { icon: ICON[k], title: fs.map((f) => f.text).join("\n") });
  });
}

// ---- right: one album -------------------------------------------------------------------------------------------
async function select(folder, keep) {
  if (state.selected !== folder || !keep) { state.plan = null; state.picks = {}; state.confirm = ""; state.showAll = false; }
  state.selected = folder;
  renderRows();
  if (!keep) clear(right, h("div.empty", h("span.spinner"), "Reading the album…"));
  try {
    state.album = await api("/health-check/album", { query: { folder } });
  } catch (e) {
    state.album = null;
    clear(right, h("div.empty", icon("alert"), h("b", "Could not read the album"), errorText(e)));
    return;
  }
  renderRight();
}

function renderRight() {
  if (!right) return;
  const a = state.album;
  if (!a) {
    clear(right, h("div.empty", icon("pulse"), h("b", "Choose an album"),
      "What is wrong with it shows here, with a search of the Internet Archive for what is missing."));
    return;
  }
  const dupes = a.findings.filter((f) => f.kind === "duplicates");
  clear(right,
    h("div.album-head", cover(a, "thumb.lg"),
      h("div.grow",
        h("h2", a.album),
        h("div.s", [a.artist, a.year, `${a.track_count} tracks`, a.folder + "/"].filter(Boolean).join(" · "))),
      h("button.btn.secondary.sm", { onclick: () => hooks.openLibrary && hooks.openLibrary(a.folder) }, icon("library"), "Library")),
    h("div.scroll.grow.health-body",
      h("div.section-label", "Problems"),
      h("div.findings", ...(a.findings.length ? a.findings.map((f) => h("div.finding." + f.severity,
        icon(f.severity === "bad" ? "x" : "alert"), h("b", LABEL[f.kind]), h("span", f.text)))
        : [h("div.muted.small", "None found. You can still search for a better copy or another cover.")])),
      a.musicbrainz ? h("div.muted.small", icon("info"), ` MusicBrainz: ${a.musicbrainz}`) : null,
      a.kinds.includes("missing_tracks") ? h("div.row", { style: { gap: "8px" } },
        h("span.muted.small.grow", "Get the songs that are missing, or download the complete album again in place of this one."),
        h("button.btn.secondary.sm", { onclick: () => search(a.folder), disabled: state.searching },
          icon("search"), a.search ? "Search again" : "Find the complete album")) : null,
      dupes.length ? mergeSection(a, dupes) : null,
      archiveSection(a),
      h("div.section-label", { style: { marginTop: "18px" } }, "Tracks"),
      tracksTable(a)));
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
  const head = h("div.row", { style: { marginTop: "18px" } },
    h("div.section-label.grow", "Internet Archive"),
    h("button.btn.primary.sm", { onclick: () => search(a.folder), disabled: state.searching },
      state.searching ? h("span.spinner") : icon("search"), s ? "Search again" : "Search the Internet Archive"));
  if (state.searching) return h("div", head, h("div.muted.small", state.searchStage || "Searching archive.org…"));
  if (!s) {
    return h("div", head, h("div.muted.small", "Lists every upload of this album, best audio first: the ones with the "
      + "missing songs, better quality to replace it with, or a cover. Only their listings are read; nothing is "
      + "downloaded until you choose."));
  }
  const all = s.suggestions, art = s.art || [];
  const better = all.filter((x) => x.upgrade).length, whole = all.filter((x) => x.complete).length;
  const shown = state.showAll ? all : all.slice(0, 8);
  return h("div", head,
    h("div.muted.small", `Searched for ${s.query}: ${all.length} upload${all.length === 1 ? "" : "s"}`
      + (whole ? `, ${whole} with the complete album` : "") + (better ? `, ${better} better than your copy` : "") + "."),
    s.cover_done ? coverDone(s.cover_done) : art.length ? artPicker(a, art) : null,
    all.length ? shown.map((x) => suggestionBox(a, x))
      : h("div.empty.small", icon("search"), h("b", "No uploads of this album on archive.org"),
        "Try Album Search with a shorter title."),
    all.length > shown.length ? h("button.btn.secondary.sm", { onclick: () => { state.showAll = true; renderRight(); } },
      `Show all ${all.length} uploads`) : null,
    s.notes?.length ? h("div.muted.small", s.notes.join(" · ")) : null);
}

function artPicker(a, art) {
  const has = !!a.cover || a.has_art;
  return h("div.health-box",
    h("b", has ? "Covers found: replace yours with one" : "Covers found"),
    has ? h("div.muted.small", "Replacing moves your cover to the Trash and embeds the new one in tracks that had one embedded.") : null,
    h("div.art-row", ...art.slice(0, 8).map((im) => {
      const key = `cover:${im.id}`, asking = state.confirm === key;
      return h("div.art-pick",
        h("img", { src: url(`/health-check/image/${im.id}`), alt: "", loading: "lazy", title: im.label,
          onload: (e) => {                                  // its size, so a small thumbnail is not chosen by mistake
            const img = e.target, small = Math.min(img.naturalWidth, img.naturalHeight) < 500;
            img.nextSibling.textContent = `${img.naturalWidth}×${img.naturalHeight}${small ? " · small" : ""} · ${img.nextSibling.dataset.from}`;
            img.nextSibling.style.color = small ? "var(--warn-text)" : "";
          } }),
        h("div.muted.small.ellipsis", { "data-from": im.source.startsWith("coverart") ? "Cover Art Archive" : im.identifier },
          im.source.startsWith("coverart") ? "Cover Art Archive" : im.identifier),
        h("button.btn.sm" + (has && asking ? ".danger" : ".secondary"), {
          onclick: () => (has && !asking ? ask(key) : useCover(a.folder, im.id, has)), disabled: !!state.busy,
        }, state.busy === key ? h("span.spinner") : null, has ? (asking ? "Yes, replace it" : "Replace cover") : "Use as cover"));
    })));
}

function coverDone(c) {
  const what = [c.written, c.px ? `${c.px} px` : "", c.embedded ? `also embedded in ${c.embedded} track${c.embedded === 1 ? "" : "s"}` : ""]
    .filter(Boolean).join(" · ");
  return h("div.health-box", h("div.row", h("span", { style: { color: "var(--green)" } }, icon("check")),
    h("div.grow", h("b", c.replaced ? "Cover replaced" : "Cover saved"), h("div.muted.small", what)),
    h("span.muted.small", "Search again to choose a different one.")));
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
