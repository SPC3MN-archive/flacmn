// Album Search, top block: the search fields, the format filters and the ranked results (best quality first).

import { api, on, waitJob } from "./api.js";
import { h, clear, icon, chip, qualityBadge, tierBadge, fmtTime, fmtBytes, toast, errorText } from "./ui.js";

const state = {
  searchId: null, jobId: null, busy: false, stage: "",
  results: [], notes: null, selected: null,
  filters: { flac: true, wav: true, mp3: true, losslessOnly: false, hideOwned: false },
  sort: "quality",
};
let els = {};
let onSelect = () => {};

export function mountSearch(card, { select }) {
  onSelect = select;
  els.artist = h("input", { placeholder: "", autocomplete: "off", spellcheck: false });
  els.album = h("input", { placeholder: "leave empty for all albums", autocomplete: "off", spellcheck: false });
  const submit = (e) => { e.preventDefault(); run(); };
  for (const i of [els.artist, els.album]) i.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(e); });
  els.go = h("button.btn.primary", { onclick: submit }, icon("search"), "Search");

  const toggle = (key, label) => {
    const b = h("button.toggle-chip" + (state.filters[key] ? ".on" : ""), {
      onclick: () => { state.filters[key] = !state.filters[key]; b.classList.toggle("on"); renderTable(); },
    }, icon("check"), label);
    return b;
  };
  const check = (key, label) => h("label.check", h("input", {
    type: "checkbox", checked: state.filters[key],
    onchange: (e) => { state.filters[key] = e.target.checked; renderTable(); },
  }), label);
  els.sort = h("select", { onchange: (e) => { state.sort = e.target.value; renderTable(); } },
    h("option", { value: "quality" }, "Quality"), h("option", { value: "match" }, "Match"),
    h("option", { value: "popularity" }, "Popularity"));

  els.notes = h("div.notes-line");
  els.results = h("div.results.scroll");
  clear(card,
    h("div.search-row",
      h("label.field.artist", icon("search"), h("span.prefix", "Artist"), els.artist),
      h("label.field.album", h("span.prefix", "Album"), els.album),
      els.go,
      h("div.filters",
        toggle("flac", "FLAC"), toggle("wav", "WAV"), toggle("mp3", "MP3"),
        check("losslessOnly", "Lossless only"), check("hideOwned", "Hide albums I have"),
        h("label.field.sm", { style: { width: "150px" } }, h("span.prefix", "Sort"), els.sort))),
    els.notes, els.results);
  renderNotes();
  renderTable();

  on("results", (e) => {
    if (e.search_id !== state.searchId) return;
    state.results = e.results;
    if (e.notes !== undefined) state.notes = e;
    state.busy = false;
    renderNotes();
    renderTable();
  });
  on("candidate", (e) => {
    const i = state.results.findIndex((r) => r.identifier === e.candidate.identifier);
    if (i >= 0) {
      state.results[i] = e.candidate;
      renderTable();
    }
  });
  on("progress", (e) => {
    if (e.job_id !== state.jobId) return;
    state.stage = describe(e);
    renderNotes();
  });
  setTimeout(() => els.artist.focus(), 50);
}

export function updateCandidate(c) {
  const i = state.results.findIndex((r) => r.identifier === c.identifier);
  if (i >= 0) { state.results[i] = c; renderTable(); }
}

function describe(e) {
  if (e.stage === "search") return `Searching archive.org · ${e.rung || ""}${e.hits ? ` · ${e.hits} hits` : ""}`;
  if (e.stage === "inspecting") return `Reading ${e.identifier}…`;
  if (e.stage === "found") return `Found ${e.count} upload(s), reading them…`;
  return "Searching…";
}

async function run() {
  const artist = els.artist.value.trim(), album = els.album.value.trim();
  if (!artist && !album) { els.artist.focus(); return; }
  state.busy = true;
  state.stage = "Searching archive.org…";
  state.results = [];
  state.notes = null;
  renderNotes();
  renderTable();
  els.go.disabled = true;
  try {
    const r = await api("/search", { method: "POST", body: { artist, album } });
    state.searchId = r.search_id;
    state.jobId = r.job_id;
    await waitJob(r.job_id);
    // the results event usually arrives first; fetch them in case it was missed
    const res = await api(`/search/${r.search_id}`);
    if (res.search_id === state.searchId) {
      state.results = res.results;
      state.notes = res;
    }
  } catch (e) {
    toast(`Search failed: ${errorText(e)}`, "bad");
  } finally {
    state.busy = false;
    els.go.disabled = false;
    renderNotes();
    renderTable();
  }
}

function count(x) {
  if (!x) return 0;
  if (Array.isArray(x)) return x.length;
  if (typeof x === "object") return Object.values(x).reduce((a, v) => a + (typeof v === "number" ? v : Array.isArray(v) ? v.length : 1), 0);
  return Number(x) || 0;
}

function renderNotes() {
  const n = els.notes;
  if (state.busy) {
    clear(n, h("span.spinner"), h("span", state.stage || "Searching…"));
    return;
  }
  if (!state.notes) {
    clear(n, icon("info"), h("span.muted", "Search archive.org by artist, album or both. FLAC, WAV and MP3 uploads are ranked together, best quality first."));
    return;
  }
  const r = state.notes;
  const shown = visible().length;
  const parts = [];
  const near = count(r.near_misses), other = count(r.lossless_other), out = count(r.excluded);
  if (near) parts.push(`${near} near miss${near === 1 ? "" : "es"}`);
  if (other) parts.push(`${other} in other lossless formats (ALAC, APE)`);
  if (out) parts.push(`${out} left out`);
  if (shown !== state.results.length) parts.push(`${state.results.length - shown} hidden by filters`);
  const title = [...(r.notes || []), ...(r.near_misses || []).map((m) => (typeof m === "string" ? m : JSON.stringify(m)))].join("\n");
  clear(n, icon("info"), h("b", `${state.results.length} upload${state.results.length === 1 ? "" : "s"}`),
    parts.length ? h("span", { title }, "  ·  " + parts.join("  ·  ")) : null);
}

function visible() {
  const f = state.filters;
  let rows = state.results.filter((r) =>
    ((r.media === "flac" && f.flac) || (r.media === "wav" && f.wav) || (r.media === "mp3" && f.mp3) ||
      !["flac", "wav", "mp3"].includes(r.media)) &&
    (!f.losslessOnly || r.quality.lossless) && (!f.hideOwned || !r.in_library));
  if (state.sort === "match") rows = [...rows].sort((a, b) => (b.match?.score || 0) - (a.match?.score || 0));
  else if (state.sort === "popularity") rows = [...rows].sort((a, b) => (b.downloads || 0) - (a.downloads || 0));
  return rows;
}

function sourceShort(s) {
  if (!s) return "—";
  if (s.startsWith("CD rip")) return "CD rip";
  if (s.startsWith("vinyl")) return "vinyl rip";
  if (s.startsWith("plain")) return "plain";
  return s.split(" (")[0].replace(" source", "");
}

function renderTable() {
  const rows = visible();
  if (!rows.length) {
    clear(els.results, state.busy ? null : h("div.empty",
      icon(state.notes ? "search" : "music"),
      state.notes ? h("b", "No uploads to show") : h("b", "No search yet"),
      state.notes ? h("span", "Try a shorter album title, or turn the format filters back on.") : null));
    return;
  }
  const head = h("tr", ...["Quality", "Tier", "Format", "Upload", "Source", "Match", "Tracks", "Length", "Size", "Year",
    "Rip files", "Tested", "Library"].map((t, i) => h("th" + ([6, 7, 8].includes(i) ? ".r" : ""), t)));
  const body = rows.map((r) => {
    const tr = h("tr" + (state.selected === r.identifier ? ".selected" : ""), {
      onclick: () => { state.selected = r.identifier; renderTable(); onSelect(r); },
      title: r.title || r.identifier,
    },
      h("td", qualityBadge(r.quality)),
      h("td", tierBadge(r.tier)),
      h("td.strong", r.quality.format || r.media.toUpperCase()),
      h("td", h("span.ellipsis", { style: { display: "inline-block", maxWidth: "320px", verticalAlign: "middle" } }, r.identifier)),
      h("td.dim", sourceShort(r.source)),
      h("td.dim", r.match ? (r.match.bucket === 0 ? "exact" : "close") : "—"),
      h("td.r.num", r.tracks || "—"),
      h("td.r.num", fmtTime(r.runtime_s)),
      h("td.r.num", fmtBytes(r.bytes)),
      h("td.num", r.year || "—"),
      h("td", h("span.rip-files", ...(r.rip_files || []).map((f) => chip(f, "mono")))),
      h("td", chip(r.levels[r.levels.length - 1] || "V0", "primary")),
      h("td", r.in_library ? chip("Have it", "good", { icon: "check", title: r.in_library.folder }) : null));
    return tr;
  });
  clear(els.results, h("table.grid", h("thead", head), h("tbody", ...body)));
}
