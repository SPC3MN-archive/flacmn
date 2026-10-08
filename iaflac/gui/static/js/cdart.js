// CD Art: find the case scans for an album you have, choose one per printed part, preview the true-size case
// pages and make the print PDF.

import { api, url, on, waitJob } from "./api.js";
import { h, clear, icon, chip, toast, errorText, dismissOnOutside } from "./ui.js";
import { albums, coverThumb } from "./library.js";

const PARTS = [
  ["front", "Front cover", "front"], ["back", "Back + spines", "back"], ["inside", "Inside of front insert", "booklet"],
  ["tray", "Under-tray", "tray"], ["disc", "Disc label", "disc"],
];
const SOURCE = { folder: "Album folder", "archive.org": "archive.org upload", coverartarchive: "Cover Art Archive",
  "coverartarchive-sibling": "Cover Art Archive · other pressing", "archive.org-search": "Other archive.org upload" };
const MISSING = { front: "a title-only cover", back: "a generated tracklist", inside: "left blank", tray: "left blank",
  disc: "cut from the front cover" };
const PAPER = { letter: [215.9, 279.4, "Letter"], a4: [210, 297, "A4"] };
const FRONT = 120, TRAY_W = 151, TRAY_H = 118, SPINE = 6.5, DISC = 118;

const state = { folder: null, art: null, finding: false, stage: "", progress: 0, page: "outside", printing: false, pdf: null, jobId: null };
let left = null, right = null, settings = () => ({});

export function mountCdArt(leftCard, rightCard, opts) {
  left = leftCard;
  right = rightCard;
  settings = opts.settings;
  new ResizeObserver(() => drawPage()).observe(right);
  on("progress", (e) => {
    if (e.job_id !== state.jobId) return;
    state.stage = e.step || e.message || e.stage || "";
    if (e.total) state.progress = e.index / e.total;
    renderStatus();
  });
  render();
}

export async function openAlbum(folder) {
  state.folder = folder;
  state.art = null;
  state.pdf = null;
  render();
  try {
    state.art = await api("/art", { query: { folder } });
    state.pdf = state.art.pdf;
  } catch (e) {
    if (e.status !== 404) toast(errorText(e), "bad");
  }
  render();
}

export function refresh() { render(); }

// ---- left: album, find, parts --------------------------------------------------------------------------------------
let statusEl = null;
function render() {
  renderLeft();
  renderRight();
}

function renderLeft() {
  const entry = albums().find((a) => a.folder === state.folder);
  const picker = h("div.album-picker",
    h("button", { onclick: (e) => openPicker(e.currentTarget.parentElement) },
      entry ? coverThumb(entry) : h("div.thumb", icon("disc")),
      h("div.grow", { style: { minWidth: 0 } },
        h("div.t.ellipsis", entry ? entry.album : "Choose an album"),
        h("div.s.ellipsis", entry ? [entry.artist, entry.year, "from your library"].filter(Boolean).join(" · ") : `${albums().length} albums in your library`)),
      icon("chevron")));
  statusEl = h("div.art-status");
  const find = h("button.btn.primary", { onclick: find_, disabled: !state.folder || state.finding }, icon("search"), state.art ? "Find again" : "Find art");
  const parts = h("div.parts.scroll");
  clear(left, h("div.row", { style: { gap: "16px", flex: "none" } }, picker, find, statusEl), notesBar(), parts);
  renderStatus();
  renderParts(parts);
}

function openPicker(anchor) {
  const filter = h("input", { placeholder: "Filter albums…" });
  const list = h("div");
  const fill = () => {
    const q = filter.value.trim().toLowerCase();
    clear(list, ...albums().filter((a) => !q || `${a.artist} ${a.album}`.toLowerCase().includes(q)).map((a) =>
      h("button", { onclick: () => { menu.remove(); openAlbum(a.folder); } }, coverThumb(a),
        h("div", { style: { minWidth: 0 } }, h("div.ellipsis", { style: { fontWeight: 600 } }, a.album),
          h("div.ellipsis.dim", { style: { fontSize: "12px" } }, [a.artist, a.year].filter(Boolean).join(" · "))))));
    if (!list.childElementCount) list.append(h("div.muted", { style: { padding: "10px" } }, albums().length ? "No match" : "The library is empty, or not chosen yet (Settings)."));
  };
  filter.addEventListener("input", fill);
  const menu = h("div.album-menu", h("label.field", icon("search"), filter), list);
  fill();
  anchor.append(menu);
  filter.focus();
  dismissOnOutside(menu, anchor.firstChild, () => menu.remove());
}

function renderStatus() {
  if (!statusEl) return;
  if (state.finding) {
    clear(statusEl, h("span.ellipsis", state.stage ? `Searching · ${state.stage}` : "Searching the folder, archive.org and the Cover Art Archive…"),
      h("div.progress" + (state.progress ? ".busy" : ".indeterminate"), h("i", { style: state.progress ? { width: `${state.progress * 100}%` } : null })));
  } else if (state.art) {
    const n = state.art.releases?.length || 0;
    clear(statusEl, h("span", `Done · ${n} pressing${n === 1 ? "" : "s"} searched · ${state.art.images.length} images`),
      h("div.progress", h("i", { style: { width: "100%" } })));
  } else clear(statusEl);
}

function notesBar() {
  const a = state.art;
  if (!a) return null;
  const notes = a.notes || [];
  return h("div.info-bar", { style: { marginTop: "12px" } }, icon("info"),
    a.mb_release_id ? h("span", "Matched ", h("b", [a.artist, a.album, a.year].filter(Boolean).join(" · "))) : h("span", "Not matched to a MusicBrainz release"),
    ...notes.slice(0, 3).map((n) => h("span", "·  " + n)));
}

async function find_() {
  if (!state.folder) return;
  state.finding = true;
  state.stage = "";
  state.progress = 0;
  renderLeft();
  try {
    const { job_id } = await api("/art/find", { method: "POST", body: { folder: state.folder } });
    state.jobId = job_id;
    state.art = await waitJob(job_id);
    state.pdf = state.art.pdf;
  } catch (e) {
    toast(`Finding art failed: ${errorText(e)}`, "bad");
  } finally {
    state.finding = false;
    render();
  }
}

function imagesOf(kind) {
  return (state.art?.images || []).filter((i) => i.kind === kind);
}

function img(id) { return state.art.images.find((i) => i.id === id); }

const imgUrl = (id, full = false) => url(`/art/image/${id}`, full ? { full: true } : undefined);

// an image that cannot be fetched shows an empty tile instead of a broken-image icon
document.addEventListener("error", (e) => {
  const t = e.target;
  if (t.tagName === "IMG" && t.src.includes("/api/art/image/")) t.style.visibility = "hidden";
}, true);

function dpiChip(im) {
  if (!im.dpi) return chip("size unknown", "", {});
  if (im.dpi < 200) return chip(`${im.dpi} dpi · below 200`, "solid-warn", { icon: "alert" });
  if (im.dpi < 300) return chip(`${im.dpi} dpi`, "", {});
  return chip(`${im.dpi} dpi`, "good", {});
}

function renderParts(box) {
  if (!state.folder) {
    clear(box, h("div.empty", icon("disc"), h("b", "Choose an album from your library"),
      "Its case scans are looked up in the album folder, on archive.org and in the Cover Art Archive, including other pressings with the same tracklist."));
    return;
  }
  if (!state.art) {
    clear(box, state.finding ? h("div.empty", h("span.spinner"), "Looking for scans…")
      : h("div.empty", icon("search"), h("b", "No search yet for this album"), "Press Find art."));
    return;
  }
  const sel = state.art.selection || {};
  clear(box, ...PARTS.map(([part, title, kind]) => {
    const chosen = sel[part] ? img(sel[part]) : null;
    const alts = imagesOf(kind);
    const soft = chosen && chosen.dpi && chosen.dpi < 200;
    return h("div.part" + (soft ? ".warn" : ""),
      chosen ? h("img.main-img", { src: imgUrl(chosen.id), alt: title, loading: "lazy" })
        : h("div.main-img.missing", "Not found: " + MISSING[part]),
      h("div", { style: { minWidth: 0 } },
        h("h4", title),
        h("div.src.ellipsis", chosen ? chosen.label || SOURCE[chosen.source] || chosen.source : "No scan found"),
        chosen ? h("div.meta", dpiChip(chosen), chosen.width ? h("span", `${chosen.width} × ${chosen.height} px`) : null) : h("div.meta"),
        alts.length > 1 || (alts.length && !chosen) ? h("div.alts", h("span.section-label", "Other scans"),
          ...alts.slice(0, 10).map((a) => h("button" + (chosen && a.id === chosen.id ? ".on" : ""), {
            title: `${a.label || SOURCE[a.source] || a.source}${a.dpi ? ` · ${a.dpi} dpi` : ""}`,
            onclick: () => choose(part, a.id),
          }, h("img", { src: imgUrl(a.id), alt: "", loading: "lazy" }))),
          chosen ? h("button.btn.ghost.sm", { onclick: () => choose(part, null), title: "Print a generated piece instead" }, "None") : null) : null));
  }));
}

async function choose(part, id) {
  const selection = { ...(state.art.selection || {}), [part]: id };
  try {
    state.art = await api("/art/selection", { method: "PUT", body: { folder: state.folder, selection } });
    state.pdf = null;
    render();
  } catch (e) {
    toast(errorText(e), "bad");
  }
}

// ---- right: case preview and print -----------------------------------------------------------------------------------
let pageWrap = null;
function renderRight() {
  const s = settings();
  const [pw, ph, paperName] = PAPER[s.paper] || PAPER.letter;
  const seg = h("div.segmented", ...[["outside", "Outside"], ["inside", "Inside"], ["disc", "Disc"]].map(([k, t]) =>
    h("button" + (state.page === k ? ".on" : ""), { onclick: () => { state.page = k; renderRight(); } }, t)));
  pageWrap = h("div.page-wrap");
  const warnings = h("div.warnings", ...warningChips());
  const ready = !!state.art;
  clear(right,
    h("div.card-head", h("span.card-title.grow", "Case preview"), seg),
    pageWrap, warnings,
    h("hr.hr", { style: { marginTop: 0 } }),
    h("div.row", { style: { marginBottom: "10px", flex: "none" } }, h("span.section-label.grow", "Print"),
      h("span.muted", { style: { fontSize: "11.5px" } }, `${paperName} · ${s.bleed_mm ?? 1.5} mm bleed · 3 pages`)),
    h("div.print-row",
      h("button.btn.primary", { onclick: print, disabled: !ready || state.printing }, state.printing ? h("span.spinner") : icon("printer"), "Make PDF"),
      h("button.btn.secondary", { onclick: openPdf, disabled: !state.pdf }, icon("external"), "Open PDF"),
      h("button.btn.secondary.wide", { onclick: saveScans, disabled: !ready }, icon("folder"), "Save scans to album folder")));
  drawPage();
}

function warningChips() {
  if (!state.art) return [];
  const sel = state.art.selection || {};
  const out = [];
  for (const [part, title] of PARTS) {
    const im = sel[part] ? img(sel[part]) : null;
    if (!im) out.push(chip(`${title}: ${MISSING[part]}`, "", { icon: "info" }));
    else if (im.dpi && im.dpi < 200) out.push(chip(`${title} is soft (${im.dpi} dpi)`, "warn", { icon: "alert" }));
  }
  return out;
}

function drawPage() {
  if (!pageWrap) return;
  const [pw, ph] = PAPER[settings().paper] || PAPER.letter;
  const box = pageWrap.getBoundingClientRect();
  if (!box.width || !box.height) return;
  const k = Math.min((box.width - 24) / pw, (box.height - 36) / ph);      // px per mm
  const mm = (v) => `${v * k}px`;
  const page = h("div.case-page", { style: { width: mm(pw), height: mm(ph) } });
  const sel = state.art?.selection || {};
  const piece = (x, y, w, hgt, id, gen) => {
    const p = h("div.piece", { style: { left: mm(x), top: mm(y), width: mm(w), height: mm(hgt) } },
      id ? h("img", { src: imgUrl(id), alt: "" }) : h("div.gen", gen));
    page.append(p);
    return p;
  };
  const fx = (pw - FRONT) / 2, fy = 12, tx = (pw - TRAY_W) / 2, ty = fy + FRONT + 10;
  if (state.page === "outside") {
    piece(fx, fy, FRONT, FRONT, sel.front, "Front cover\n(generated: title)");
    const back = piece(tx, ty, TRAY_W, TRAY_H, sel.back, "Back tray card\n(generated tracklist)");
    for (const x of [0, TRAY_W - SPINE]) back.append(h("div.spine", { style: { left: mm(x), width: mm(SPINE) } }));
    page.append(h("div.cap", { style: { left: mm(fx + FRONT + 2), top: mm(fy) } }, "Front 120 × 120"),
      h("div.cap", { style: { left: mm(tx), top: mm(ty + TRAY_H + 2) } }, "Tray card 151 × 118 mm · 2 spines"));
  } else if (state.page === "inside") {
    const inside = piece(fx, fy, FRONT, FRONT, sel.inside, "Inside of the insert\n(left blank)");
    const im = sel.inside ? img(sel.inside) : null;
    if (im && im.width / im.height >= 1.6) {          // an opened booklet: the left page prints, as in the PDF
      Object.assign(inside.firstChild.style, { width: "200%", maxWidth: "none", objectPosition: "left center" });
    }
    piece(tx, ty, TRAY_W, TRAY_H, sel.tray, "Under-tray\n(left blank)");
    page.append(h("div.cap", { style: { left: mm(fx + FRONT + 2), top: mm(fy) } }, "Print on the back of page 1"));
  } else {
    const dx = (pw - DISC) / 2, dy = 12;
    const disc = h("div.disc", { style: { left: mm(dx), top: mm(dy), width: mm(DISC), height: mm(DISC) } },
      sel.disc ? h("img", { src: imgUrl(sel.disc), alt: "" }) : sel.front ? h("img", { src: imgUrl(sel.front), alt: "", style: { opacity: 0.85 } }) : null);
    const hole = settings().disc_hole_mm || 41;
    page.append(disc, h("div.hole", { style: { left: mm(dx + (DISC - hole) / 2), top: mm(dy + (DISC - hole) / 2), width: mm(hole), height: mm(hole) } }),
      h("div.cap", { style: { left: mm(dx), top: mm(dy + DISC + 3) } }, `Disc label Ø ${DISC} mm · hole ${hole} mm${sel.disc ? "" : " · cut from the front cover"}`));
  }
  const n = { outside: 1, inside: 2, disc: 3 }[state.page];
  clear(pageWrap, page, h("div.page-no", `Page ${n} of 3`));
  if (!state.art) page.append(h("div.gen", { style: { position: "absolute", inset: 0, display: "flex", alignItems: "flex-end", justifyContent: "center", padding: "12px", color: "var(--text-3)", fontSize: "11px" } }, "Find art to fill the case"));
}

async function print() {
  state.printing = true;
  renderRight();
  try {
    const { job_id } = await api("/art/print", { method: "POST", body: { folder: state.folder } });
    const r = await waitJob(job_id);
    state.pdf = r.pdf;
    toast(`PDF ready: ${r.pdf}`, "good", 6000);
  } catch (e) {
    toast(`Could not make the PDF: ${errorText(e)}`, "bad");
  } finally {
    state.printing = false;
    renderRight();
  }
}

async function openPdf() {
  try { await api("/art/open", { method: "POST", body: { folder: state.folder } }); }
  catch (e) { toast(errorText(e), "bad"); }
}

async function saveScans() {
  try {
    const { job_id } = await api("/art/save", { method: "POST", body: { folder: state.folder } });
    const r = await waitJob(job_id);
    toast(`Saved ${r.written.length} scan(s) to ${state.folder}/Artwork`, "good");
  } catch (e) {
    toast(`Could not save the scans: ${errorText(e)}`, "bad");
  }
}
