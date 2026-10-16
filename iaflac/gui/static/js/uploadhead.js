// Album Search, the top of the right side: the chosen upload's cover, what it is, its verdict, Add to library with its
// size, and a short note when you already have the album. The tags it will be saved with sit beside it (tags.js).

import { h, clear, icon, fmtTime, fmtBytes } from "./ui.js";
import { verdictOf, nameOf } from "./search.js";
import { addToLibrary } from "./spectrogram.js";

let el = null, cand = null, detail = null;

export function mountUploadHead(container) {
  el = container;
  render();
}

export function showUpload(c) { cand = c; detail = null; render(); }
export function updateUpload(c) { if (cand && c.identifier === cand.identifier) { cand = { ...cand, ...c }; render(); } }
export function setDetail(d) { detail = d; render(); }

// how your copy compares with this upload, in one line and no file details: lossless and hi-res count as the same
function owned(c) {
  const mine = c.in_library;
  if (!mine) return null;
  const rank = (lossless) => (lossless ? 1 : 0);
  const have = rank(mine.quality === "lossless" || mine.quality === "hires"), here = rank(!!c.quality.lossless);
  if (have > here) return "You already have this album in a higher quality.";
  if (have < here) return "You already have this album, in a lower quality.";
  return "You already have this album in the same quality.";
}

function render() {
  if (!el) return;
  if (!cand) { clear(el); el.hidden = true; return; }
  el.hidden = false;
  const c = cand;
  const [verdict, kind] = verdictOf(c);
  const cover = c.cover_url ? h("img.up-cover", { src: c.cover_url, alt: "", onerror: (e) => e.target.replaceWith(h("div.up-cover", icon("image"))) })
    : h("div.up-cover", icon("image"));
  const label = detail?.label || "";
  const note = owned(c);
  clear(el, cover,
    h("div.up-info",
      h("div.up-names",
        h("h1", nameOf(c)),
        h("p.up-artist", [c.artist || c.creator, c.year].filter(Boolean).join(" · ")),
        h("p.up-facts", [label, c.tracks ? `${c.tracks} tracks` : "", c.runtime_s ? fmtTime(c.runtime_s) : ""].filter(Boolean).join(" · "))),
      h("span.chip.lg." + kind, h("span.dot"), verdict),
      h("div.row", { style: { gap: "var(--sp-3)" } },
        h("button.btn.primary.lg", { type: "button", onclick: () => addToLibrary(c), disabled: c.tier === "D",
          title: c.tier === "D" ? "Failed uploads are not saved" : null }, "Add to library"),
        h("span.dim", fmtBytes(c.bytes))),
      note ? h("p.owned-note", note) : null));
}
