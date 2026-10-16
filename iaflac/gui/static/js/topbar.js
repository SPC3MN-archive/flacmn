// The top bar's middle: what is playing, from the library (player.js) or an Album Search preview (spectrogram.js),
// whichever started last. Just the album: its cover, the track, the artist and album, the progress and play/pause; no
// label. Clicking the cover or the names opens the Now playing card (art, scope, XY) for library playback.

import { h, clear, icon, fmtTime } from "./ui.js";

let cur = null;                    // {audio, title, sub, cover, toggle, card}
const subs = new Set();

const wired = new WeakSet();
/** Called by a player when it starts: what to show while its audio plays. */
export function setPlaying(info) {
  cur = info;
  if (!wired.has(info.audio)) {                     // each audio element once: only the current one is drawn
    wired.add(info.audio);
    info.audio.addEventListener("timeupdate", () => { if (cur?.audio === info.audio) tick(); });
    for (const ev of ["play", "pause", "ended", "loadedmetadata"]) info.audio.addEventListener(ev, () => { if (cur?.audio === info.audio) { paintPlay(); tick(); } });
  }
  for (const fn of subs) fn();
  render();
}
export function onPlaying(fn) { subs.add(fn); }

let el = null, pop = null, els = {};

export function mountTopbar() {
  el = document.getElementById("np-mini");
  pop = document.getElementById("np-pop");
  document.addEventListener("mousedown", (e) => {
    if (!pop.hidden && !pop.contains(e.target) && !el.contains(e.target)) pop.hidden = true;
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") pop.hidden = true; });
  render();
}

function render() {
  if (!el) return;
  el.hidden = false;
  el.classList.toggle("idle", !cur);
  if (!cur) { clear(el); return; }
  const open = cur.card ? () => { pop.hidden = !pop.hidden; } : null;
  const cover = cur.cover ? h("img.np-mini-cover", { src: cur.cover, alt: "" }) : h("span.np-mini-cover", icon("music"));
  els.now = h("span.num", "0:00");
  els.total = h("span.num", "0:00");
  els.fill = h("i");
  els.play = h("button.np-mini-play", { type: "button", onclick: () => cur.toggle(), "aria-label": "Play or pause" });
  const bar = h("span.np-mini-bar", { onclick: (e) => {
    const a = cur.audio, r = e.currentTarget.getBoundingClientRect();
    if (isFinite(a.duration)) a.currentTime = ((e.clientX - r.left) / r.width) * a.duration;
  } }, els.fill);
  clear(el,
    h("button.np-mini-open", { type: "button", onclick: open, disabled: !open, title: open ? "Now playing" : null },
      cover,
      h("span.np-mini-names", h("b.ellipsis", cur.title || ""), h("span.ellipsis", cur.sub || ""))),
    h("span.np-mini-progress", els.now, bar, els.total),
    els.play);
  paintPlay();
  tick();
}

function paintPlay() {
  if (!els.play || !cur) return;
  els.play.replaceChildren(icon(cur.audio.paused ? "play" : "pause"));
}

function tick() {
  if (!cur || !els.fill) return;
  const a = cur.audio, d = isFinite(a.duration) ? a.duration : 0;
  els.now.textContent = fmtTime(a.currentTime || 0);
  els.total.textContent = d ? fmtTime(d) : "—";
  els.fill.style.width = d ? `${(100 * a.currentTime) / d}%` : "0";
}
