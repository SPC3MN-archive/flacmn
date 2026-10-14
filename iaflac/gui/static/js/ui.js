// Small DOM helpers, icons, formatting and the chips used on every screen.

export function h(tag, attrs, ...children) {
  const [name, ...classes] = tag.split(".");
  const el = document.createElement(name || "div");
  if (classes.length) el.className = classes.join(" ");
  if (attrs && (typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className += (el.className ? " " : "") + v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

// ---- icons (24x24 stroke paths) ------------------------------------------------------------------------------------
const PATHS = {
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
  disc: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2.5"/>',
  library: '<path d="M5 4v16M10 4v16M15 5l4 15"/>',
  sliders: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  alert: '<path d="M12 4 2.8 20h18.4z"/><path d="M12 10v4.5M12 17.6v.1"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.6v.1"/>',
  download: '<path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/>',
  refresh: '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3M19.5 4.5v4h-4"/>',
  play: '<path d="M7 4.5v15l12.5-7.5z"/>',
  pause: '<rect x="6" y="4.5" width="4" height="15" rx="1"/><rect x="14" y="4.5" width="4" height="15" rx="1"/>',
  music: '<path d="M9 18V6l11-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>',
  chevron: '<path d="m7 10 5 5 5-5"/>',
  tag: '<path d="M3.5 12.5V4.5a1 1 0 0 1 1-1h8l8 8-9 9z"/><circle cx="8.5" cy="8.5" r="1.5"/>',
  folder: '<path d="M3.5 6.5a1 1 0 0 1 1-1h5l2 2.5h8a1 1 0 0 1 1 1v9.5a1 1 0 0 1-1 1h-15a1 1 0 0 1-1-1z"/>',
  printer: '<path d="M7 9V3.5h10V9M7 17H4.5V10a1 1 0 0 1 1-1h13a1 1 0 0 1 1 1v7H17"/><rect x="7" y="14" width="10" height="6.5"/>',
  external: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  image: '<rect x="3.5" y="4.5" width="17" height="15" rx="1.5"/><circle cx="9" cy="10" r="1.6"/><path d="m4 18 5-5 4 4 3-3 4.5 4.5"/>',
  trash: '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13"/>',
  minus: '<path d="M6 12h12"/>',
  plus: '<path d="M12 6v12M6 12h12"/>',
  pausecircle: '<circle cx="12" cy="12" r="9"/><path d="M10 9v6M14 9v6"/>',
  playcircle: '<circle cx="12" cy="12" r="9"/><path d="m10 8.5 5.5 3.5-5.5 3.5z"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
  prev: '<path d="M19 5v14L9 12z"/><path d="M6 5v14"/>',
  next: '<path d="M5 5v14l10-7z"/><path d="M18 5v14"/>',
  pulse: '<path d="M3 12h4l2.5-6 4 12 2.5-6H21"/>',
};

export function icon(name, cls = "") {
  const span = document.createElement("span");
  span.innerHTML = `<svg class="icon ${cls}" viewBox="0 0 24 24" aria-hidden="true">${PATHS[name] || ""}</svg>`;
  return span.firstChild;
}

export function hydrateIcons(root = document) {
  for (const el of root.querySelectorAll("[data-icon]")) el.replaceWith(icon(el.dataset.icon));
}

// ---- formatting ----------------------------------------------------------------------------------------------------
export function fmtTime(s) {
  if (s === null || s === undefined || !isFinite(s)) return "—";
  s = Math.round(s);
  const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
  return (hh ? `${hh}:${String(mm).padStart(2, "0")}` : `${mm}`) + ":" + String(ss).padStart(2, "0");
}

export function fmtBytes(b) {
  if (!b) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (b >= 1000 && i < units.length - 1) { b /= 1000; i++; }
  return `${b >= 100 || i === 0 ? Math.round(b) : b.toFixed(1)} ${units[i]}`;
}

export const kHz = (hz) => (hz / 1000).toFixed(1);

// ---- chips -----------------------------------------------------------------------------------------------------------
export function chip(text, kind = "", opts = {}) {
  const c = h("span.chip" + (kind ? "." + kind.split(" ").join(".") : ""), { title: opts.title });
  if (opts.icon) c.append(icon(opts.icon));
  if (opts.dot) c.append(h("span.dot"));
  c.append(text);
  return c;
}

/** An album's quality, from its format: lossless or hi-res (both high quality, shown alike) or lossy. */
export function albumQualityChip(q) {
  if (q === "hires") return chip("Hi-res", "good", { dot: true });
  if (q === "lossless") return chip("Lossless", "good", { dot: true });
  if (q === "lossy") return chip("Lossy", "bad", { dot: true });
  return null;
}

/** The quality class of a result. q = candidate.quality. Lossless and Hi-res are one class, shown alike: the goal is any
 *  high-quality audio, from any source. */
export function qualityBadge(q) {
  const label = q.label.replace("Failed / suspect", "Failed");
  switch (q.rank) {
    case 0: return chip(label, "good", { dot: true });             // Lossless or Hi-res
    case 1: return chip(label, "outline-bad", { dot: true });      // Lossy, high bitrate
    case 2: return chip(label, "bad", { dot: true });              // Lossy
    default: return chip(label, "solid-bad", { icon: "x" });       // Failed / suspect
  }
}

export function tierBadge(tier) {
  const t = tier || "?";
  const titles = { A: "genuine lossless, strong evidence", B: "genuine lossless, likely", C: "lossless, little evidence either way",
    L: "lossy", D: "failed or fake", "?": "not tested yet" };
  return h("span.tier." + (t === "?" ? "q" : t), { title: titles[t] || "" }, t);
}

export const VERDICT = {
  PASS: ["PASS", "solid-good", "check"], SUSPECT: ["SUSPECT", "solid-warn", "alert"],
  LIKELY_LOSSY: ["LIKELY LOSSY", "solid-bad", "x"], UPSAMPLED: ["UPSAMPLED", "solid-bad", "alert"],
  INCONCLUSIVE: ["INCONCLUSIVE", "", "info"], UNAVAILABLE: ["NOT CHECKED", "", "info"],
};

export function verdictChip(verdict, cutoffHz) {
  const [label, kind, ic] = VERDICT[verdict] || [verdict, "", "info"];
  return chip(label + (cutoffHz ? ` · ${kHz(cutoffHz)} kHz` : ""), kind + " lg", { icon: ic });
}

// ---- toasts --------------------------------------------------------------------------------------------------------
export function toast(text, kind = "", ms = 4500) {
  const t = h("div.toast" + (kind ? "." + kind : ""), text);
  document.getElementById("toasts").append(t);
  setTimeout(() => t.remove(), ms);
}

export function errorText(e) {
  return e && e.message ? e.message : String(e);
}

/** Close a popup when clicking anywhere outside `el` (and outside `anchor`). Returns a function that unhooks it. */
export function dismissOnOutside(el, anchor, close) {
  const fn = (ev) => {
    if (!el.contains(ev.target) && !(anchor && anchor.contains(ev.target))) { close(); off(); }
  };
  const off = () => document.removeEventListener("mousedown", fn, true);
  setTimeout(() => document.addEventListener("mousedown", fn, true), 0);
  return off;
}

// ---- drop-down menus ----------------------------------------------------------------------------------------------
// Every <select> opens the app's own menu (charcoal, off-white lettering, like the album picker) instead of the
// system's: the select stays in the page and keeps its value, keyboard and change events; only the pop-up is ours.
let openMenu = null;

function closeMenu() {
  if (!openMenu) return;
  openMenu.menu.remove();
  openMenu.select.closest(".field")?.classList.remove("open");
  openMenu = null;
}

function showMenu(select) {
  closeMenu();
  const anchor = select.closest(".field") || select;
  const r = anchor.getBoundingClientRect();
  const pick = (opt) => {
    closeMenu();
    if (select.value !== opt.value) {
      select.value = opt.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
    }
    select.focus({ preventScroll: true });
  };
  const items = [...select.options].map((opt) => h("button.dd-item" + (opt.selected ? ".on" : ""), {
    type: "button", disabled: opt.disabled, onclick: () => pick(opt),
  }, h("span.ellipsis", opt.textContent), opt.selected ? icon("check") : null));
  const menu = h("div.dd-menu", { role: "listbox" }, ...items);
  document.body.append(menu);
  const below = window.innerHeight - r.bottom - 12, above = r.top - 12;
  const up = below < Math.min(menu.scrollHeight, 320) && above > below;
  Object.assign(menu.style, {
    left: `${Math.max(8, Math.min(r.left, window.innerWidth - Math.max(r.width, 180) - 8))}px`,
    minWidth: `${r.width}px`,
    maxHeight: `${Math.max(120, Math.min(360, up ? above : below))}px`,
    ...(up ? { bottom: `${window.innerHeight - r.top + 4}px` } : { top: `${r.bottom + 4}px` }),
  });
  anchor.classList.add("open");
  openMenu = { select, menu, items };
  const on = items[select.selectedIndex];
  if (on) { on.scrollIntoView({ block: "nearest" }); on.focus({ preventScroll: true }); }
}

document.addEventListener("mousedown", (e) => {
  const select = e.target.closest?.("select");
  if (select && !select.disabled && !select.multiple) {
    e.preventDefault();                                          // no system pop-up
    if (openMenu && openMenu.select === select) closeMenu(); else showMenu(select);
    return;
  }
  if (openMenu && !openMenu.menu.contains(e.target)) closeMenu();
}, true);
document.addEventListener("keydown", (e) => {
  if (!openMenu) {
    const select = e.target.closest?.("select");
    if (select && (e.key === " " || e.key === "Enter" || (e.altKey && e.key === "ArrowDown"))) { e.preventDefault(); showMenu(select); }
    return;
  }
  const { items } = openMenu, i = items.indexOf(document.activeElement);
  if (e.key === "Escape") { e.preventDefault(); const s = openMenu.select; closeMenu(); s.focus(); }
  else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const n = items[Math.max(0, Math.min(items.length - 1, (i < 0 ? openMenu.select.selectedIndex : i) + (e.key === "ArrowDown" ? 1 : -1)))];
    n?.focus();
  } else if (e.key === "Tab") closeMenu();
}, true);
window.addEventListener("resize", closeMenu);
document.addEventListener("scroll", (e) => { if (openMenu && !openMenu.menu.contains(e.target)) closeMenu(); }, true);
