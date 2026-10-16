// Album Search, the left block: one search box, the format filters and the uploads found, each with a verdict in plain
// words. The order stays put while the automatic checks finish (rows are updated in place); it is re-sorted only when
// you ask, so a row never moves away under the pointer.

import { api, on, waitJob } from "./api.js";
import { h, clear, icon, fmtBytes, toast, errorText } from "./ui.js";

const state = {
  searchId: null, jobId: null, busy: false, stage: "",
  results: [], notes: null, selected: null, order: [], stale: false,
  filters: { flac: true, wav: true, m4a: true, mp3: true, other: true, hideOwned: false },
  showLeftOut: false,
  queued: new Set(),                 // results sent for a quick test and not back yet                // the uploads the search left out (live, singles), listed last when shown
  sort: "quality",
};
let els = {};
let stageEl = null;                // the status under the loading logo, while it is shown
let onSelect = () => {};

/** "Radiohead – OK Computer" (any dash with spaces round it) -> artist and album; no dash: the artist alone. */
export function splitQuery(q) {
  const m = q.trim().match(/^(.*?)\s+[-–—]\s+(.*)$/);
  return m ? { artist: m[1].trim(), album: m[2].trim() } : { artist: q.trim(), album: "" };
}

export function mountSearch(card, { select }) {
  onSelect = select;
  els.q = h("input", { id: "search-q", type: "search", autocomplete: "off", spellcheck: false,
    placeholder: "Artist – Album (or just an artist)" });
  els.q.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); run(); } });
  els.go = h("button.btn.primary", { type: "button", onclick: run }, "Search");

  const pill = (key, label) => {
    const b = h("button.pill", { type: "button", "aria-pressed": String(state.filters[key]),
      onclick: () => { state.filters[key] = !state.filters[key]; b.setAttribute("aria-pressed", String(state.filters[key])); renderNotes(); renderRows(); },
    }, label);
    return b;
  };
  els.sort = h("select.sort-select", { "aria-label": "Sort", onchange: (e) => { state.sort = e.target.value; resort(); } },
    h("option", { value: "quality" }, "Sorted by quality"), h("option", { value: "match" }, "Sorted by match"),
    h("option", { value: "popularity" }, "Sorted by popularity"));
  els.count = h("h2.list-count");
  els.resort = h("button.btn.ghost.sm", { type: "button", hidden: true, onclick: resort, title: "Checks have finished since this list was sorted" }, "Re-sort");
  els.notes = h("div.list-note");
  els.rows = h("ul.list.scroll", { "aria-label": "Uploads" });
  clear(card,
    h("div.list-head",
      h("div.row", h("label.sr-only", { for: "search-q" }, "Artist and album"),
        h("span.field.grow", icon("search"), els.q), els.go),
      h("div.pills", { role: "group", "aria-label": "Formats" },
        pill("flac", "FLAC"), pill("wav", "WAV"), pill("m4a", "M4A"), pill("mp3", "MP3"),
        h("span", { title: "AIFF, Monkey's Audio (APE) and WavPack" }, pill("other", "Other")), pill("hideOwned", "Hide albums I have")),
      h("div.row.list-meta", els.count, h("span.grow"), els.resort, els.sort),
      els.notes),
    els.rows);
  renderNotes();
  renderRows();

  on("results", (e) => {
    if (e.search_id !== state.searchId) return;
    take(e.results);
    if (e.notes !== undefined) state.notes = e;
    state.busy = false;
    renderNotes();
    renderRows();
  });
  on("candidate", (e) => {
    const i = state.results.findIndex((r) => r.identifier === e.candidate.identifier);
    if (i >= 0) {
      state.results[i] = e.candidate;
      if (e.candidate.tier !== "?") state.queued.delete(e.candidate.identifier);
      state.stale = state.sort === "quality";
      renderNotes();
      renderRows();
    }
  });
  on("progress", (e) => {
    if (e.job_id !== state.jobId) return;
    state.stage = describe(e);
    renderNotes();
  });
  setTimeout(() => els.q.focus(), 50);
}

// new results keep the rows already shown where they are; new uploads join the end
function take(results) {
  state.results = results;
  const ids = new Set(results.map((r) => r.identifier));
  const kept = state.order.filter((id) => ids.has(id));
  const known = new Set(kept);
  state.order = kept.concat(results.map((r) => r.identifier).filter((id) => !known.has(id)));
  if (kept.length) state.stale = state.sort === "quality";
}

function resort() {
  let rows = state.results;                                   // the server's order is best quality first
  if (state.sort === "match") rows = [...rows].sort((a, b) => (b.match?.score || 0) - (a.match?.score || 0));
  else if (state.sort === "popularity") rows = [...rows].sort((a, b) => (b.downloads || 0) - (a.downloads || 0));
  state.order = rows.map((r) => r.identifier);
  state.stale = false;
  renderNotes();
  renderRows();
}

function describe(e) {
  if (e.stage === "search") return `Searching archive.org · ${e.rung || ""}${e.hits ? ` · ${e.hits} hits` : ""}`;
  if (e.stage === "inspecting") return `Reading ${e.identifier}…`;
  if (e.stage === "found") return `Found ${e.count} upload(s), reading them…`;
  return "Searching…";
}

/** Search for an album from another screen (Library's "Find a better copy"). */
export function searchFor(artist, album) {
  els.q.value = album ? `${artist} – ${album}` : artist || "";
  run();
}

async function run() {
  const { artist, album } = splitQuery(els.q.value);
  if (!artist && !album) { els.q.focus(); return; }
  Object.assign(state, { busy: true, stage: "Searching archive.org…", results: [], notes: null, order: [], stale: false, queued: new Set() });
  renderRows();                      // first: it puts up the loading logo the stage text goes under
  renderNotes();
  els.go.disabled = true;
  try {
    const r = await api("/search", { method: "POST", body: { artist, album } });
    state.searchId = r.search_id;
    state.jobId = r.job_id;
    await waitJob(r.job_id);
    // the results event usually arrives first; fetch them in case it was missed
    const res = await api(`/search/${r.search_id}`);
    if (res.search_id === state.searchId) {
      if (!state.order.length) { state.results = res.results; resort(); } else take(res.results);
      state.notes = res;
    }
  } catch (e) {
    toast(`Search failed: ${errorText(e)}`, "bad");
  } finally {
    state.busy = false;
    els.go.disabled = false;
    renderNotes();
    renderRows();
  }
}

/** Quick-test the visible results not checked yet, best first: the search checks every result it keeps, so this is
 *  for the left-out ones once they are shown. */
async function checkVisible() {
  const ids = visible().filter((r) => r.tier === "?" && !state.queued.has(r.identifier)).map((r) => r.identifier);
  if (!ids.length || !state.searchId) return;
  ids.forEach((id) => state.queued.add(id));
  renderNotes();
  try {
    const r = await api(`/search/${state.searchId}/test`, { method: "POST", body: { identifiers: ids } });
    if (r.job_id) await waitJob(r.job_id);
  } catch (e) {
    toast(`Checking failed: ${errorText(e)}`, "bad");
  } finally {
    ids.forEach((id) => state.queued.delete(id));
    renderNotes();
    renderRows();
  }
}

/** Opening an unchecked result checks it straight away. */
function checkOne(r) {
  if (r.tier !== "?" || state.queued.has(r.identifier)) return;
  state.queued.add(r.identifier);
  renderNotes();
  api(`/items/${encodeURIComponent(r.identifier)}/test`, { method: "POST", body: { level: "quick" } })
    .then((j) => j.job_id && waitJob(j.job_id))
    .catch(() => {})
    .finally(() => { state.queued.delete(r.identifier); renderNotes(); });
}

function count(x) {
  if (!x) return 0;
  if (Array.isArray(x)) return x.length;
  if (typeof x === "object") return Object.values(x).reduce((a, v) => a + (typeof v === "number" ? v : Array.isArray(v) ? v.length : 1), 0);
  return Number(x) || 0;
}

function renderNotes() {
  const kept = state.results.filter((r) => !r.left_out);
  const all = kept.length, shown = visible().filter((r) => !r.left_out).length, checked = kept.filter((r) => r.tier !== "?").length;
  els.count.textContent = state.busy ? "Searching" : all ? `${all} upload${all === 1 ? "" : "s"} · ${checked} checked` : "Uploads";
  els.resort.hidden = !state.stale;
  const n = els.notes;
  if (state.busy) {
    const text = state.stage || "Searching…";
    if (stageEl && stageEl.isConnected) { stageEl.lastChild.textContent = text; clear(n); }
    else clear(n, h("span", text));
    return;
  }
  if (!state.notes) { clear(n); return; }
  const r = state.notes, parts = [];
  const near = count(r.near_misses), other = count(r.lossless_other), out = count(r.excluded);
  if (shown !== all) parts.push(`${all - shown} hidden by the filters`);
  if (other) parts.push(`${other} in Shorten (SHN), which isn't searched`);
  if (near) parts.push(`${near} near miss${near === 1 ? "" : "es"}`);
  const title = [...(r.notes || []), ...(r.near_misses || []).map((m) => (typeof m === "string" ? m : JSON.stringify(m)))].join("\n");
  const why = out ? leftOut(r.excluded) : "";
  const listed = state.results.some((x) => x.left_out);
  clear(n, parts.length ? h("span", { title }, parts.join(" · ")) : null,
    out ? h("span", parts.length ? " · " : null, `${out} left out${why ? ` (${why})` : ""}`,
      listed ? h("button.btn.ghost.sm", { type: "button", "aria-pressed": String(state.showLeftOut),
        onclick: () => { state.showLeftOut = !state.showLeftOut; renderNotes(); renderRows(); if (state.showLeftOut) checkVisible(); } },
        state.showLeftOut ? "Hide" : "Show") : null) : null);
}

// why an upload was left out, in a word or two
function leftOutWord(reason) {
  if (!reason) return "";
  return /^live/.test(reason) ? "Live" : /album-shaped|fewer than/.test(reason) ? "Single or EP"
    : /licence/.test(reason) ? "Not freely licensed" : /^no /.test(reason) ? "No audio files" : reason;
}

// why uploads were left out, in a few words: "15 live, 3 singles or EPs"
function leftOut(excluded) {
  if (!excluded || typeof excluded !== "object" || Array.isArray(excluded)) return "";
  const word = (reason) => { const w = leftOutWord(reason); return w === "Live" ? "live" : w === "Single or EP" ? "singles or EPs" : w.toLowerCase(); };
  const by = {};
  for (const [reason, n] of Object.entries(excluded)) by[word(reason)] = (by[word(reason)] || 0) + n;
  return Object.entries(by).sort((a, b) => b[1] - a[1]).map(([w, n]) => `${n} ${w}`).join(", ");
}

// which format filter each upload's format answers to: AIFF, APE and WavPack are "Other"
const FILTER_OF = { flac: "flac", wav: "wav", m4a: "m4a", mp3: "mp3", aiff: "other", ape: "other", wv: "other" };

function visible() {
  const f = state.filters, at = new Map(state.order.map((id, i) => [id, i]));
  return state.results
    .filter((r) => (f[FILTER_OF[r.media] || "other"]) && (!f.hideOwned || !r.in_library) && (state.showLeftOut || !r.left_out))
    .sort((a, b) => (!!a.left_out - !!b.left_out) || (at.get(a.identifier) ?? 1e9) - (at.get(b.identifier) ?? 1e9));
}

/** A result's verdict in plain words, with its chip colour. */
export function verdictOf(r) {
  const q = r.quality;
  if (q.rank >= 3 || r.tier === "D") return ["Failed", "bad"];
  if (r.media === "m4a" && (r.tier === "?" || !r.tier)) return ["Not checked yet", "wait"];   // ALAC or AAC: not known until read
  if (!q.lossless || r.tier === "L") return ["Lossy", "bad"];
  const base = /hi-?res/i.test(q.label) ? "Hi-res" : "Lossless";
  if (r.tier === "?" || !r.tier) return ["Not checked yet", "wait"];
  if (r.tier === "A") return [`Genuine ${base.toLowerCase()}`, "good"];
  if (r.tier === "B") return [`${base}, likely`, "good"];
  return [base, "good"];
}

/** The album's name as best we know it: the guessed album, unless that is only a format word ("FLAC"). */
export function nameOf(r) {
  const a = (r.album || "").trim();
  return a && !/^(flac|wav|mp3|cd rip|lossless|album)$/i.test(a) ? a : r.title || r.identifier;
}

function sourceShort(s) {
  if (!s) return "";
  if (s.startsWith("CD rip")) return "CD rip";
  if (s.startsWith("vinyl")) return "vinyl rip";
  if (s.startsWith("plain")) return "";
  return s.split(" (")[0].replace(" source", "");
}

function meta(r) {
  const src = sourceShort(r.source), log = (r.rip_files || []).some((f) => /log/i.test(f));
  return [leftOutWord(r.left_out), r.quality.format || r.media.toUpperCase(), src ? src + (log ? " with log" : "") : log ? "rip log" : "",
    r.tracks ? `${r.tracks} tracks` : "", r.year, r.in_library ? "In your library" : ""].filter(Boolean).join(" · ");
}

function renderRows() {
  const rows = visible();
  if (!rows.length) {
    stageEl = state.busy ? h("div.loading-status", h("span.ellipsis", state.stage || "Searching…")) : null;
    clear(els.rows, state.busy ? h("li.empty.loading", h("img.loading-logo", { src: "/static/logo-loading.svg", alt: "Searching" }), stageEl)
      : h("li.empty", icon(state.notes ? "search" : "music"),
        h("b", state.notes ? "No uploads to show" : "Search the Internet Archive"),
        h("span", state.notes ? "Try a shorter album title, or turn the format filters back on."
          : "Type an artist and album, like Radiohead – OK Computer. Every format (FLAC, WAV, AIFF, ALAC, APE, WavPack, AAC, MP3) is ranked together, best quality first; lossless ones are saved as FLAC.")));
    return;
  }
  const best = state.sort === "quality" && !rows[0].left_out && rows[0].quality.lossless && ["A", "B"].includes(rows[0].tier) ? rows[0].identifier : null;
  clear(els.rows, ...rows.map((r) => {
    const [verdict, kind] = verdictOf(r);
    return h("li", h("button.list-row" + (state.selected === r.identifier ? ".on" : "") + (r.left_out ? ".left-out" : ""), {
      type: "button", "aria-current": state.selected === r.identifier ? "true" : null, title: r.identifier,
      onclick: () => { state.selected = r.identifier; renderRows(); onSelect(r); checkOne(r); },
    },
      h("span.list-main",
        h("span.list-title", r.identifier === best ? h("span.best", "Best match") : null, h("b.ellipsis", nameOf(r))),
        h("span.list-sub.ellipsis", meta(r))),
      h("span.list-side", h("span.chip." + kind, h("span.dot"), verdict), h("span.list-size", fmtBytes(r.bytes)))));
  }));
}
