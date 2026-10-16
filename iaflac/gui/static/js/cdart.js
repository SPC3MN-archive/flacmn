// CD Art: find the case scans for an album you have, choose one per printed part, preview the true-size case
// pages and make the print PDF.

import { api, url, on, waitJob } from "./api.js";
import { h, clear, icon, chip, toast, errorText, dismissOnOutside } from "./ui.js";
import { albums, coverThumb, rescan, scanning, libraryStatus, openFolder } from "./artlib.js";
import { CaseViewer } from "./case3d.js";
import { CassetteViewer } from "./cassette3d.js";

const CD_PARTS = [
  ["front", "Front cover", "front"], ["back", "Back + spines", "back"], ["inside", "Inside of front insert", "booklet"],
  ["tray", "Under-tray", "tray"], ["disc", "Disc label", "disc"],
];
// the cassette J-card uses three of the same choices: the cover, the colour of the spine and back flap, and the inside
const CASSETTE_PARTS = [
  ["front", "Front panel", "front"], ["back", "Spine + back flap", "back"], ["inside", "Inside", "booklet"],
];
const FORMATS = {
  cd: { label: "CD jewel case", parts: CD_PARTS, pages: 3, first: "3d",
    tabs: [["outside", "Outside"], ["inside", "Inside"], ["spine", "Spine"], ["disc", "Disc"], ["3d", "3D"]] },
  cassette: { label: "Cassette J-card", parts: CASSETTE_PARTS, pages: 2, first: "3d",
    tabs: [["outside", "Outside"], ["inside", "Inside"], ["spine", "Spine"], ["3d", "3D"]] },
};
const fmt = () => FORMATS[state.format];
const parts = () => fmt().parts;
// J-card panels (mm), as iaflac/cassette.py prints them
const J_H = 101.6, J_FLAP = 27.0, J_SPINE = 12.7, J_FRONT = 65.1, J_W = J_FLAP + J_SPINE + J_FRONT;
const CASSETTE_MISSING = { front: "the title on the album colour", back: "the album colour", inside: "the tracklist on white" };
const missing = (part) => (state.format === "cassette" ? CASSETTE_MISSING : MISSING)[part];
const CASSETTE_ON_COLOUR = { front: "title", back: "title and side A/B tracklist", inside: "tracklist" };
// designs for generated parts (iaflac/designs.py): the layouts a part offers in each format, the spcmn mark's sizes
// (large only where there is no tracklist), and what they are called
const DESIGN_NAME = { title: "Title", list: "List", centred: "Centred", columns: "Two columns", compact: "Compact", blank: "Blank" };
const LOGO_NAME = { small: "Small", medium: "Medium", large: "Large" };
const TRACKLISTS = ["list", "centred", "columns", "compact"];
const DESIGNS = {
  cd: { front: ["title", "blank"], back: [...TRACKLISTS, "blank"], inside: [...TRACKLISTS, "blank"], tray: ["blank"], disc: ["title"] },
  cassette: { front: ["title", "blank"], back: ["list", "blank"], inside: ["list", "blank"] },
};
const designsFor = (part) => DESIGNS[state.format][part] || [];
const designOf = (part) => state.art?.styles?.[part] || designsFor(part)[0];
// the spcmn mark (small unless chosen), in charcoal or off-white to suit its colour: on a CD the front, the back (and
// its spines) and the disc carry it, never the inside of the front insert or the under-tray image; on a cassette every
// generated part does
const marked = (part) => state.format !== "cd" || !["inside", "tray"].includes(part);
const logoOf = (part) => (marked(part) ? state.art?.styles?.[`${part}:logo`] || "small" : "none");
const logosFor = (part) => ["small", "medium", ...(["title", "blank"].includes(designOf(part)) ? ["large"] : [])];
// is this part drawn by flacmn (a colour, or nothing chosen) rather than printed from a scan or an upload?
function generated(part, value, chosen) {
  if (state.format === "cassette" && part === "back") return true;      // the spine and flap are always drawn
  if (part === "disc" && !colourOf(value)) return !value && !(state.art.selection?.front && !colourOf(state.art.selection.front));
  return !!colourOf(value) || !chosen;
}
const onColour = (part) => {
  const d = designOf(part), first = designsFor(part)[0];
  const base = d && d !== first ? DESIGN_NAME[d].toLowerCase() : (state.format === "cassette" ? CASSETTE_ON_COLOUR : ON_COLOUR)[part];
  return marked(part) ? `${base || "colour"}, ${logoOf(part)} spcmn logo` : base || "colour";
};
// the spcmn palette, offered as fill colours beside the album's own (lower case: the server's colour format)
const SPCMN_COLOURS = [["#2e2e33", "Charcoal"], ["#f6f4ee", "Off-white"], ["#5c53a2", "Purple"], ["#20a76b", "Green"],
  ["#f4b73e", "Amber"], ["#bc3843", "Red"], ["#dfd9e2", "Lavender"]];
const SOURCE = { folder: "Album folder", upload: "Your upload", "archive.org": "archive.org upload",
  coverartarchive: "Cover Art Archive", "coverartarchive-sibling": "Cover Art Archive · other pressing",
  "archive.org-search": "Other archive.org upload" };
const MISSING = { front: "a title-only cover", back: "a generated tracklist", inside: "a tracklist on white", tray: "left blank",
  disc: "cut from the front cover" };
// what gets generated on a part filled with a solid colour
const ON_COLOUR = { front: "title", back: "tracklist and spines", inside: "tracklist", tray: "", disc: "title" };
const KIND_NAME = { front: "Front cover", back: "Back cover", booklet: "Booklet page", disc: "Disc", tray: "Tray image",
  spine: "Spine", other: "Image" };
const colourOf = (v) => (typeof v === "string" && v.startsWith("colour:") ? v.slice(7) : null);
function inkOn(hex) {             // black or white text, whichever reads on the colour (as the PDF does)
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.55 ? "#000" : "#fff";
}
const PAPER = { letter: [215.9, 279.4, "Letter"], a4: [210, 297, "A4"] };
const FRONT = 120, TRAY_W = 151, TRAY_H = 118, SPINE = 6.5, DISC = 118;

const state = { folder: null, art: null, finding: false, stage: "", progress: 0, page: "3d", printing: false, pdf: null, jobId: null,
  view3d: { folder: null, version: 0, busy: false, open: false, discOut: false },
  format: (() => { try { return localStorage.getItem("flacmn.format") === "cassette" ? "cassette" : "cd"; } catch { return "cd"; } })(),
  jcard: { folder: null, version: 0, busy: false, failed: null, open: false, tapeOut: false },  // the cassette, as it prints
  expanded: new Set() };                                     // parts whose choices (scans, colours) are shown
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
    state.pdf = state.art.pdfs?.[state.format] || null;
  } catch (e) {
    if (e.status !== 404) toast(errorText(e), "bad");
  }
  render();
}

export function refresh() { render(); }

// the album list changed (read progress, a new read, an opened folder): redraw only the picker, and an open menu in place
let refillMenu = null;
export function refreshPicker() {
  if (refillMenu && left?.querySelector(".album-menu")) refillMenu();
  else renderLeft();
}

// ---- left: album, find, parts --------------------------------------------------------------------------------------
let statusEl = null, findStatusEl = null;      // beside the button, or under the loading logo while finding
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
        h("div.s.ellipsis", entry ? [entry.artist, entry.year, entry.opened ? "opened folder" : "from your music folder"].filter(Boolean).join(" · ")
          : libraryStatus() || `${albums().length} albums in your music folder`)),
      icon("chevron")));
  statusEl = h("div.art-status");
  const find = h("button.btn.secondary.sm", { type: "button", onclick: find_, disabled: !state.folder || state.finding }, icon("search"), state.art ? "Find again" : "Find art");
  const scrolled = left.querySelector(".parts")?.scrollTop || 0;      // keep your place when a choice redraws the list
  const parts = h("div.parts.scroll");
  const formats = h("div.segmented", { role: "radiogroup", "aria-label": "Case" }, ...Object.entries(FORMATS).map(([k, f]) =>
    h("button" + (state.format === k ? ".on" : ""), { type: "button", role: "radio", "aria-checked": String(state.format === k), onclick: () => setFormat(k) }, f.label)));
  clear(left, h("div.list-head", picker,
      h("div.row", { style: { justifyContent: "space-between", gap: "var(--sp-3)", flexWrap: "wrap" } }, formats, find),
      statusEl, notesBar()),
    parts);
  renderParts(parts);
  parts.scrollTop = scrolled;
  renderStatus();
}

function openPicker(anchor) {
  const filter = h("input", { placeholder: "Filter albums…" });
  const list = h("div");
  const fill = () => {
    const q = filter.value.trim().toLowerCase();
    clear(list, ...albums().filter((a) => !q || `${a.artist} ${a.album}`.toLowerCase().includes(q)).map((a) =>
      h("button", { onclick: () => { menu.remove(); openAlbum(a.folder); } }, coverThumb(a),
        h("div", { style: { minWidth: 0 } }, h("div.ellipsis", { style: { fontWeight: 600 } }, a.album),
          h("div.ellipsis.dim", { style: { fontSize: "12px" } }, [a.artist, a.year, a.opened ? "opened folder" : ""].filter(Boolean).join(" · "))))));
    const status = libraryStatus();
    if (status) list.append(h("div.muted.row", { style: { padding: "10px", gap: "8px" } }, scanning() ? h("span.spinner") : null, status));
    else if (!list.childElementCount) list.append(h("div.muted", { style: { padding: "10px" } }, albums().length ? "No match" : "No albums found in the music folder."));
  };
  filter.addEventListener("input", fill);
  const again = h("button.btn.secondary.sm", { onclick: async () => { again.disabled = true; await rescan(); fill(); again.disabled = false; },
    disabled: scanning(), title: "Read the music folder again" }, icon("refresh"), "Rescan");
  const browse = h("button.btn.secondary.sm.open-folder", { onclick: async () => {
    const entry = await openFolder();
    if (entry) { menu.remove(); openAlbum(entry.folder); }
  }, title: "Open one album's folder, from anywhere" }, icon("folder"), "Open album folder…");
  const menu = h("div.album-menu", h("div.row.menu-head", h("label.field.grow", icon("search"), filter), again), browse, list);
  refillMenu = fill;                                          // the list fills in while the music folder is read
  fill();
  anchor.append(menu);
  filter.focus();
  dismissOnOutside(menu, anchor.firstChild, () => menu.remove());
}

function renderStatus() {
  if (!statusEl) return;
  if (state.finding) {
    const under = findStatusEl && findStatusEl.isConnected;     // no loader when re-finding over shown scans
    clear(under ? statusEl : findStatusEl || statusEl);
    const text = h("span.ellipsis", state.stage ? `Searching · ${state.stage}` : "Searching the folder, archive.org and the Cover Art Archive…");
    if (under) clear(findStatusEl, text);                       // the animated logo above it is the progress indicator
    else clear(statusEl, text,
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
  return h("div.list-note.art-notes", icon("info"),
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
    stalePreviews();
    state.pdf = state.art.pdfs?.[state.format] || null;
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
    findStatusEl = state.finding ? h("div.loading-status") : null;
    clear(box, state.finding ? h("div.empty.loading", h("img.loading-logo", { src: "/static/logo-loading.svg", alt: "Looking for scans" }), findStatusEl)
      : h("div.empty", icon("search"), h("b", "No search yet for this album"), "Press Find art."));
    return;
  }
  const sel = state.art.selection || {};
  clear(box, ...parts().map(([part, title, kind]) => {
    const value = sel[part], colour = colourOf(value);
    const chosen = !colour && value ? img(value) : null;
    const borrowed = chosen && chosen.kind !== kind;          // another of the album's images filling this gap
    const alts = imagesOf(kind);
    const others = (state.art.images || []).filter((i) => i.kind !== kind && i.kind !== "spine").slice(0, 8);
    const soft = chosen && chosen.dpi && chosen.dpi < 200;
    const thumb = (a) => h("button" + (chosen && a.id === chosen.id ? ".on" : ""), {
      title: `${a.kind !== kind ? KIND_NAME[a.kind] + " · " : ""}${a.label || SOURCE[a.source] || a.source}${a.dpi ? ` · ${a.dpi} dpi` : ""}`,
      onclick: () => choose(part, a.id),
    }, h("img", { src: imgUrl(a.id), alt: "", loading: "lazy" }));
    const open = state.expanded.has(part);
    const isBlank = generated(part, value, chosen);         // drawn by flacmn: a colour, or nothing chosen
    const toggle = () => { open ? state.expanded.delete(part) : state.expanded.add(part); renderLeft(); };
    const count = alts.filter((a) => !chosen || a.id !== chosen.id).length + others.length;
    return h("div.part" + (soft ? ".warn" : "") + (open ? ".open" : ""),
      chosen ? h("img.main-img", { src: imgUrl(chosen.id), alt: title, loading: "lazy" })
        : colour ? h("div.main-img.swatch", { style: { background: colour, color: inkOn(colour) } }, onColour(part) ? `Generated ${onColour(part)}` : "")
        : h("div.main-img.missing", "Not found: " + missing(part)),
      h("div", { style: { minWidth: 0 } },
        h("h4", title),
        h("div.src", chosen ? (borrowed ? `${KIND_NAME[chosen.kind]} used here · ` : "") + (chosen.label || SOURCE[chosen.source] || chosen.source)
          : colour ? `Album colour ${colour}` + (onColour(part) ? ` · generated ${onColour(part)} on it` : "")
          : (alts.length ? "" : "No scan found · ") + "Generated: " + missing(part)),
        chosen ? h("div.meta", dpiChip(chosen), chosen.width ? h("span", `${chosen.width} × ${chosen.height} px`) : null) : h("div.meta"),
        h("button.btn.secondary.sm.part-toggle", { onclick: toggle, "aria-expanded": String(open) },
          open ? "Hide options" : `Change${count ? ` · ${count} image${count === 1 ? "" : "s"}` : ""}, colour or upload`, icon("chevron")),
        !open ? null : h("div.part-options",
        // a blank part: its colour (the cover's, or spcmn's), its layout and the spcmn mark's size
        isBlank ? h("div.alts.fill", h("span.section-label", "Cover colours"),
          ...(state.art.palette || []).map((hex) => h("button.swatch" + (colour === hex ? ".on" : ""), {
            style: { background: hex }, title: `${hex} · from the album cover`, onclick: () => choose(part, `colour:${hex}`),
          })),
          (state.art.palette || []).length ? null : h("span.muted", { style: { fontSize: "11.5px" } }, "none: no cover image found")) : null,
        isBlank ? h("div.alts.fill", h("span.section-label", "spcmn colours"),
          ...SPCMN_COLOURS.map(([hex, name]) => h("button.swatch" + (colour === hex ? ".on" : ""), {
            style: { background: hex }, title: `${name} ${hex.toUpperCase()} · spcmn palette`, onclick: () => choose(part, `colour:${hex}`),
          }))) : null,
        isBlank && designsFor(part).length > 1 ? h("div.alts.design-row", h("span.section-label", "Design"),
          ...designsFor(part).map((d) => h("button.design" + (designOf(part) === d ? ".on" : ""), {
            onclick: () => setDesign(part, { layout: d }),
          }, DESIGN_NAME[d]))) : null,
        isBlank && designsFor(part).length && marked(part) ? h("div.alts.design-row", h("span.section-label", "spcmn logo"),
          ...logosFor(part).map((size) => h("button.design" + (logoOf(part) === size ? ".on" : ""), {
            onclick: () => setDesign(part, { logo: size }),
            title: (part === "back" ? "The spines get a small mark too. " : "") + "Charcoal on light colours, off-white on dark ones",
          }, LOGO_NAME[size]))) : null,
        // a part printed from an image: switch it to a blank, or to another image
        !isBlank && designsFor(part).length ? h("div.alts.design-row", h("span.section-label", "Blank"),
          h("button.design", { onclick: () => choose(part, `colour:${(state.art.palette || [])[0] || "#f6f4ee"}`),
            title: marked(part) ? "Print this part in a colour with the spcmn logo instead of an image" : "Print this part in a colour instead of an image" }, "Use a blank")) : null,
        part === "disc" && value ? h("div.alts.design-row", h("span.section-label", "Disc"),
          h("button.design", { onclick: () => choose(part, null), title: "Cut the disc label from the front cover" }, "Cut from the cover")) : null,
        h("div.alts.upload-row", h("span.section-label", "Your own"),
          h("button.btn.secondary.sm", { onclick: () => uploadFor(part, title), title: `Use an image file of your own for the ${title.toLowerCase()}` },
            icon("folder"), "Upload image…")),
        alts.length > 1 || (alts.length && !chosen) ? h("div.alts", h("span.section-label", "Other scans"), ...alts.slice(0, 10).map(thumb)) : null,
        others.length ? h("div.alts.fill", h("span.section-label", "Or another image"), ...others.map(thumb)) : null)));
  }));
}

async function choose(part, id) {
  const selection = { ...(state.art.selection || {}), [part]: id };
  try {
    state.art = await api("/art/selection", { method: "PUT", body: { folder: state.folder, selection } });
    state.pdf = null;
    stalePreviews();                                        // the 3D textures and the J-card preview are out of date
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
  if (!fmt().tabs.some(([k]) => k === state.page)) state.page = fmt().first;
  const seg = h("div.segmented", ...fmt().tabs.map(([k, t]) =>
    h("button" + (state.page === k ? ".on" : ""), { onclick: () => { state.page = k; renderRight(); } }, t)));
  if (viewer && (state.page !== "3d" || state.format !== "cd")) { viewer.dispose(); viewer = null; }
  if (tapeViewer && (state.page !== "3d" || state.format !== "cassette")) { tapeViewer.dispose(); tapeViewer = null; }
  pageWrap = h("div.page-wrap");
  const warnings = h("div.warnings", ...warningChips());
  const ready = !!state.art;
  clear(right,
    h("div.section-head.case-head", h("h1", "Case preview"), seg),
    pageWrap, warnings,
    h("div.print-bar",
      h("div.print-what", h("b", "Print at true size"),
        h("span", `${fmt().label} · ${paperName} · ${s.bleed_mm ?? 1.5} mm bleed · ${fmt().pages} pages`)),
      h("button.btn.secondary.lg", { type: "button", onclick: saveScans, disabled: !ready }, "Save scans to album folder"),
      h("button.btn.secondary.lg", { type: "button", onclick: openPdf, disabled: !state.pdf }, "Open PDF"),
      h("button.btn.primary.lg", { type: "button", onclick: print, disabled: !ready || state.printing }, state.printing ? h("span.spinner") : icon("printer"), "Make PDF")));
  drawPage();
}

function warningChips() {
  if (!state.art) return [];
  const sel = state.art.selection || {};
  const out = [];
  for (const [part, title] of parts()) {
    if (colourOf(sel[part])) continue;                         // filled in an album colour: nothing to warn about
    const im = sel[part] ? img(sel[part]) : null;
    if (!im) out.push(chip(`${title}: ${missing(part)}`, "", { icon: "info" }));
    else if (im.dpi && im.dpi < 200) out.push(chip(`${title} is soft (${im.dpi} dpi)`, "warn", { icon: "alert" }));
  }
  return out;
}

function drawPage() {
  if (!pageWrap) return;
  if (state.format === "cassette") return state.page === "3d" ? drawTape3d() : drawJcard();
  if (state.page === "3d") return draw3d();
  if (state.page === "spine") return drawSpines();
  const [pw, ph] = PAPER[settings().paper] || PAPER.letter;
  const box = pageWrap.getBoundingClientRect();
  if (!box.width || !box.height) return;
  const k = Math.min((box.width - 24) / pw, (box.height - 36) / ph);      // px per mm
  const mm = (v) => `${v * k}px`;
  const page = h("div.case-page", { style: { width: mm(pw), height: mm(ph) } });
  const sel = state.art?.selection || {};
  const piece = (x, y, w, hgt, value, gen, part) => {
    const colour = colourOf(value);
    const p = h("div.piece", { style: { left: mm(x), top: mm(y), width: mm(w), height: mm(hgt) } },
      colour ? h("div.gen.filled", { style: { background: colour, color: inkOn(colour) } }, onColour(part) ? `(generated ${onColour(part)})` : "")
        : value ? h("img", { src: imgUrl(value), alt: "" }) : h("div.gen", gen));
    page.append(p);
    return p;
  };
  const fx = (pw - FRONT) / 2, fy = 12, tx = (pw - TRAY_W) / 2, ty = fy + FRONT + 10;
  if (state.page === "outside") {
    piece(fx, fy, FRONT, FRONT, sel.front, "Front cover\n(generated: title)", "front");
    const back = piece(tx, ty, TRAY_W, TRAY_H, sel.back, "Back tray card\n(generated tracklist)", "back");
    for (const x of [0, TRAY_W - SPINE]) back.append(h("div.spine", { style: { left: mm(x), width: mm(SPINE) } }));
    page.append(h("div.cap", { style: { left: mm(fx + FRONT + 2), top: mm(fy) } }, "Front 120 × 120"),
      h("div.cap", { style: { left: mm(tx), top: mm(ty + TRAY_H + 2) } }, "Tray card 151 × 118 mm · 2 spines"));
  } else if (state.page === "inside") {
    const inside = piece(fx, fy, FRONT, FRONT, sel.inside, "Inside of the insert\n(generated tracklist)", "inside");
    const im = sel.inside && !colourOf(sel.inside) ? img(sel.inside) : null;
    if (im && im.width / im.height >= 1.6) {          // an opened booklet: the left page prints, as in the PDF
      Object.assign(inside.firstChild.style, { width: "200%", maxWidth: "none", objectPosition: "left center" });
    }
    piece(tx, ty, TRAY_W, TRAY_H, sel.tray, "Under-tray\n(left blank)", "tray");
    page.append(h("div.cap", { style: { left: mm(fx + FRONT + 2), top: mm(fy) } }, "Print on the back of page 1"));
  } else {
    const dx = (pw - DISC) / 2, dy = 12;
    const discColour = colourOf(sel.disc), frontImg = sel.front && !colourOf(sel.front) ? sel.front : null;
    const disc = h("div.disc", { style: { left: mm(dx), top: mm(dy), width: mm(DISC), height: mm(DISC), ...(discColour ? { background: discColour } : {}) } },
      discColour ? null : sel.disc ? h("img", { src: imgUrl(sel.disc), alt: "" }) : frontImg ? h("img", { src: imgUrl(frontImg), alt: "", style: { opacity: 0.85 } }) : null);
    const hole = settings().disc_hole_mm || 41;
    page.append(disc, h("div.hole", { style: { left: mm(dx + (DISC - hole) / 2), top: mm(dy + (DISC - hole) / 2), width: mm(hole), height: mm(hole) } }),
      h("div.cap", { style: { left: mm(dx), top: mm(dy + DISC + 3) } }, `Disc label Ø ${DISC} mm · hole ${hole} mm${discColour ? ` · title on ${discColour}` : sel.disc ? "" : " · cut from the front cover"}`));
  }
  const n = { outside: 1, inside: 2, disc: 3 }[state.page];
  clear(pageWrap, page, h("div.page-no", `Page ${n} of 3`));
  if (!state.art) page.append(h("div.gen", { style: { position: "absolute", inset: 0, display: "flex", alignItems: "flex-end", justifyContent: "center", padding: "12px", color: "var(--text-3)", fontSize: "11px" } }, "Find art to fill the case"));
}

async function print() {
  state.printing = true;
  renderRight();
  try {
    const { job_id } = await api("/art/print", { method: "POST", body: { folder: state.folder, format: state.format } });
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
  try { await api("/art/open", { method: "POST", body: { folder: state.folder, format: state.format } }); }
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

// ---- 3D: the case with the parts exactly as they print ----------------------------------------------------------------
let viewer = null, stage3d = null;
function draw3d() {
  const v = state.view3d;
  if (!stage3d || !stage3d.isConnected || stage3d.parentElement !== pageWrap) {
    if (viewer) { viewer.dispose(); viewer = null; }
    stage3d = h("div.stage3d");
    const seg = (key, options) => h("div.segmented", { "data-key": key }, ...options.map(([val, label]) =>
      h("button" + (v[key] === val ? ".on" : ""), { "data-val": String(val), onclick: (e) => {
        v[key] = val;
        if (key === "discOut") v.open = true;               // the disc comes out (and goes back) through an open lid
        for (const b of e.currentTarget.parentElement.parentElement.querySelectorAll(".segmented")) {
          const k = b.dataset.key;
          for (const btn of b.children) btn.classList.toggle("on", String(v[k]) === btn.dataset.val);
        }
        apply3d();
      } }, label)));
    const tools = h("div.tools3d",
      seg("open", [[false, "Closed"], [true, "Open"]]),
      seg("discOut", [[false, "Disc in"], [true, "Disc out"]]));
    clear(pageWrap, stage3d, tools);
    try {
      viewer = new CaseViewer(stage3d, { discHole: settings().disc_hole_mm || 41 });
    } catch (e) {
      clear(pageWrap, h("div.muted", { style: { padding: "20px" } }, `3D preview unavailable: ${e.message}`));
      return;
    }
    v.loadedFor = null;
    apply3d();
  }
  if (viewer) viewer.setDiscHole(settings().disc_hole_mm || 41);
  if (!state.art) return;
  if ((v.folder !== state.folder || !v.version) && v.failed !== state.folder) refresh3d();
  else if (v.loadedFor !== `${v.folder}:${v.version}`) load3d();
}

function apply3d() {
  if (!viewer) return;
  const v = state.view3d;
  viewer.setOpen(v.open);
  viewer.setDiscOut(v.discOut);
}

async function refresh3d() {
  const v = state.view3d;
  if (v.busy) return;
  v.busy = true;
  const folder = state.folder;
  try {
    const { job_id } = await api("/art/preview3d", { method: "POST", body: { folder } });
    const r = await waitJob(job_id);
    if (folder !== state.folder) return;
    v.folder = folder;
    v.version = r.version;
    load3d();
  } catch (e) {
    v.failed = folder;                                      // no retry loop; choosing a part or Find again retries
    toast(`3D preview: ${errorText(e)}`, "bad");
  } finally {
    v.busy = false;
    if (state.page === "spine") drawPage();                  // the spine view shows the same printed cut-out
  }
}

function load3d() {
  const v = state.view3d;
  if (!viewer) return;
  const urls = {};
  for (const part of ["front", "inside", "back", "tray", "disc"]) urls[part] = url("/art/texture", { folder: v.folder, part, v: v.version });
  viewer.setTextures(urls);
  v.loadedFor = `${v.folder}:${v.version}`;
}

// ---- spine: both spines of the tray card, enlarged and turned to read left to right, exactly as they print ----------
function drawSpines() {
  const v = state.view3d;
  const box = pageWrap.getBoundingClientRect();
  if (!box.width || !box.height) return;
  if (!state.art) {
    clear(pageWrap, h("div.muted", "Find art to see the spines"));
    return;
  }
  if (v.folder !== state.folder || !v.version) {             // the printed parts are cut out by the same job as the 3D view
    clear(pageWrap, h("div.muted", h("span.spinner"), " Drawing the spines as they print…"));
    if (v.failed !== state.folder) refresh3d();
    return;
  }
  const back = url("/art/texture", { folder: v.folder, part: "back", v: v.version });
  const k = Math.min((box.width - 64) / TRAY_H, 9);           // px per mm: a spine as long as the panel allows
  const L = TRAY_H * k, T = SPINE * k;
  const strip = (offsetMM, turn) => h("div.spine-strip", { style: { width: `${L}px`, height: `${T}px` } },
    h("div", { style: {
      width: `${T}px`, height: `${L}px`, left: `${(L - T) / 2}px`, top: `${(T - L) / 2}px`, transform: `rotate(${turn}deg)`,
      backgroundImage: `url("${back}")`, backgroundSize: `${TRAY_W * k}px ${TRAY_H * k}px`, backgroundPosition: `${-offsetMM * k}px 0`,
    } }));
  clear(pageWrap, h("div.spine-view",
    h("div.spine-item", h("div.cap-row", h("b", "Left spine"), h("span", "open side (right, seen from the front) · reads bottom to top")), strip(0, 90)),
    h("div.spine-item", h("div.cap-row", h("b", "Right spine"), h("span", "hinge side (left, seen from the front) · reads top to bottom")), strip(TRAY_W - SPINE, -90)),
    h("div.muted.spine-note", `Each spine is ${SPINE} × ${TRAY_H} mm, folded back on the dashed marks of the tray card (page 1).`)));
}

// ---- format: the CD jewel case or the cassette J-card -------------------------------------------------------------
function setFormat(k) {
  if (k === state.format) return;
  state.format = k;
  try { localStorage.setItem("flacmn.format", k); } catch { /* a remembered choice is only a convenience */ }
  state.page = fmt().first;
  state.pdf = state.art?.pdfs?.[k] || null;
  render();
}

function stalePreviews() {
  for (const v of [state.view3d, state.jcard]) { v.version = 0; v.failed = null; }
}

// ---- cassette: the J-card exactly as it prints (cut out of its PDF), with its folds and panels marked ---------------
async function refreshJcard() {
  const v = state.jcard;
  if (v.busy) return;
  v.busy = true;
  const folder = state.folder;
  try {
    const { job_id } = await api("/art/preview3d", { method: "POST", body: { folder, format: "cassette" } });
    const r = await waitJob(job_id);
    if (folder !== state.folder) return;
    v.folder = folder;
    v.version = r.version;
  } catch (e) {
    v.failed = folder;
    toast(`J-card preview: ${errorText(e)}`, "bad");
  } finally {
    v.busy = false;
    if (state.format === "cassette") drawPage();
  }
}

function drawJcard() {
  const v = state.jcard;
  const box = pageWrap.getBoundingClientRect();
  if (!box.width || !box.height) return;
  if (!state.art) {
    clear(pageWrap, h("div.muted", "Find art to fill the J-card"));
    return;
  }
  if (v.folder !== state.folder || !v.version) {
    clear(pageWrap, h("div.muted", h("span.spinner"), " Drawing the J-card as it prints…"));
    if (v.failed !== state.folder) refreshJcard();
    return;
  }
  const tex = (part) => url("/art/texture", { folder: v.folder, part, format: "cassette", v: v.version });
  if (state.page === "spine") {
    const k = Math.min((box.width - 64) / J_H, 9);
    const L = J_H * k, T = J_SPINE * k;
    clear(pageWrap, h("div.spine-view",
      h("div.spine-item", h("div.cap-row", h("b", "Spine"), h("span", `${J_SPINE} × ${J_H} mm · reads top to bottom on the case`)),
        h("div.spine-strip", { style: { width: `${L}px`, height: `${T}px` } },
          h("div", { style: { width: `${T}px`, height: `${L}px`, left: `${(L - T) / 2}px`, top: `${(T - L) / 2}px`,
            transform: "rotate(-90deg)", backgroundImage: `url("${tex("spine")}")`, backgroundSize: "100% 100%" } }))),
      h("div.muted.spine-note", "The spine sits at the case's edge, between the front panel and the back flap (dashed folds on page 1).")));
    return;
  }
  const inside = state.page === "inside";
  // panels left to right as the sheet lies: outside = flap | spine | front; inside (the back of it) = front | spine | flap
  const panels = inside ? [["Inside of front", J_FRONT], ["spine", J_SPINE], ["flap", J_FLAP]]
    : [["Back flap · tracklist", J_FLAP], ["Spine", J_SPINE], ["Front", J_FRONT]];
  const k = Math.min((box.width - 40) / J_W, (box.height - 70) / J_H);
  const card = h("div.jcard", { style: { width: `${J_W * k}px`, height: `${J_H * k}px` } },
    h("img", { src: tex(inside ? "inside" : "outside"), alt: inside ? "J-card inside" : "J-card outside" }));
  let x = 0;
  const caps = h("div.jcard-caps", { style: { width: `${J_W * k}px` } });
  panels.forEach(([name, w], i) => {
    caps.append(h("span", { style: { width: `${w * k}px` } }, h("b", name), ` ${w} mm`));
    x += w;
    if (i < panels.length - 1) card.append(h("div.fold", { style: { left: `${x * k}px` } }));
  });
  clear(pageWrap, h("div.jcard-view", caps, card,
    h("div.muted.spine-note", inside ? "Page 2: printed on the back of page 1 (double-sided, flip on the long edge)."
      : `Page 1: ${J_W.toFixed(1)} × ${J_H} mm unfolded, folded on the dashed lines.`)));
}

// ---- cassette 3D: the case with the J-card folded inside, as it prints --------------------------------------------
let tapeViewer = null, tapeStage = null;
function drawTape3d() {
  const v = state.jcard;
  if (!tapeStage || !tapeStage.isConnected || tapeStage.parentElement !== pageWrap) {
    if (tapeViewer) { tapeViewer.dispose(); tapeViewer = null; }
    tapeStage = h("div.stage3d");
    const seg = (key, options) => h("div.segmented", { "data-key": key }, ...options.map(([val, label]) =>
      h("button" + (v[key] === val ? ".on" : ""), { "data-val": String(val), onclick: (e) => {
        v[key] = val;
        if (key === "tapeOut") v.open = true;              // the tape comes out (and goes back) through an open lid
        for (const b of e.currentTarget.parentElement.parentElement.querySelectorAll(".segmented")) {
          for (const btn of b.children) btn.classList.toggle("on", String(v[b.dataset.key]) === btn.dataset.val);
        }
        applyTape();
      } }, label)));
    clear(pageWrap, tapeStage, h("div.tools3d",
      seg("open", [[false, "Closed"], [true, "Open"]]),
      seg("tapeOut", [[false, "Tape in"], [true, "Tape out"]])));
    try {
      tapeViewer = new CassetteViewer(tapeStage);
    } catch (e) {
      clear(pageWrap, h("div.muted", { style: { padding: "20px" } }, `3D preview unavailable: ${e.message}`));
      return;
    }
    v.loadedFor = null;
    applyTape();
  }
  if (!state.art) return;
  tapeViewer.setLabel(state.art.artist, state.art.album);
  if ((v.folder !== state.folder || !v.version) && v.failed !== state.folder) refreshJcard();
  else if (v.version && v.loadedFor !== `${v.folder}:${v.version}`) {
    const tex = (part) => url("/art/texture", { folder: v.folder, part, format: "cassette", v: v.version });
    tapeViewer.setTextures({ front: tex("front"), spine: tex("spine"), flap: tex("flap"), inside: tex("inside") });
    v.loadedFor = `${v.folder}:${v.version}`;
  }
}

function applyTape() {
  if (!tapeViewer) return;
  tapeViewer.setOpen(state.jcard.open);
  tapeViewer.setTapeOut(state.jcard.tapeOut);
}

// ---- your own image for one part: read in the page, sent as base64, kept in flacmn's cache and chosen for the part ----
function uploadFor(part, title) {
  const input = h("input", { type: "file", accept: "image/jpeg,image/png,image/webp,image/tiff,image/bmp,image/gif", style: { display: "none" } });
  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    input.remove();
    if (!file) return;
    if (file.size > 40 * 2 ** 20) { toast(`${file.name} is over 40 MB`, "bad"); return; }
    try {
      const data = await new Promise((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(String(r.result).split(",", 2)[1] || "");
        r.onerror = () => reject(r.error);
        r.readAsDataURL(file);
      });
      state.art = await api("/art/upload", { method: "POST", body: { folder: state.folder, part, name: file.name, data } });
      state.pdf = null;
      stalePreviews();
      toast(`${title}: using ${file.name}`, "good");
      render();
    } catch (e) {
      toast(`Upload failed: ${errorText(e)}`, "bad");
    }
  });
  document.body.append(input);
  input.click();
}

// ---- the design of a generated part: a tracklist layout, blank, or the spcmn mark in a size ------------------------
async function setDesign(part, change) {                 // change: { layout } | { logo } | { ink }
  try {
    state.art = await api("/art/style", { method: "PUT", body: { folder: state.folder, part, ...change } });
    state.pdf = null;
    stalePreviews();
    render();
  } catch (e) {
    toast(errorText(e), "bad");
  }
}
