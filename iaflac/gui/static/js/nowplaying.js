// The Now playing card, from the Pi library player (SPC3MN/piplayer): three tabs.
//   Art    the cover large, with the track, album and artist beside it (wrapped and shrunk to fit: never cut off),
//          a progress bar you can click to seek, and previous / play-pause / next
//   Scope  just the oscilloscope, the waveform, filling the card
//   XY     just the oscilloscope, the stereo figure
// The trace takes the album's colour, from its cover. Left alone while music plays (Settings: idle_scope seconds),
// the card turns to the Scope tab.

import { url } from "./api.js";
import { h, clear, icon, fmtTime, albumQualityChip } from "./ui.js";
import { Scope } from "./scope.js";
import { audio, now, track, onChange, toggle, next, prev, seekTo, idleFor } from "./player.js";
import { getSettings } from "./settings.js";

let card = null, scope = null, tab = "art", els = {};
const colours = new Map();                                       // cover URL -> trace colour

export function mountNowPlaying(el) {
  card = el;
  onChange(render);
  audio.addEventListener("timeupdate", progress);
  new ResizeObserver(() => fitInfo()).observe(card);
  setInterval(() => {
    const secs = +(getSettings().idle_scope ?? 30);
    if (secs > 0 && !audio.paused && track() && tab === "art" && idleFor() > secs) { tab = "wave"; render(); }
  }, 1000);
  render();
}

// the colour as it is, unless it is too light to read on the off-white card: then only its lightness is lowered
function readable([r, g, b]) {
  [r, g, b] = [r / 255, g / 255, b / 255];
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), dd = mx - mn, l = (mx + mn) / 2;
  if (l <= 0.55) return `rgb(${Math.round(r * 255)} ${Math.round(g * 255)} ${Math.round(b * 255)})`;
  let hue = 0;
  if (dd) hue = mx === r ? ((g - b) / dd) % 6 : mx === g ? (b - r) / dd + 2 : (r - g) / dd + 4;
  const sat = dd ? dd / (1 - Math.abs(mx + mn - 1)) : 0;
  return `hsl(${Math.round(hue * 60 + 360) % 360} ${Math.round(sat * 100)}% 50%)`;
}

function coverUrl() {
  const al = now.album;
  return al && al.cover ? url("/library/file", { path: al.cover }) : null;
}

// a colour taken from the cover itself, not an average of it: the cover's pixels are sorted into small colour boxes and
// the box that is both common and colourful wins; the trace is that box's colour (a light one is only darkened enough
// to read on the off-white card). A black-and-white cover gives its most common dark shade.
function traceColour(src) {
  if (!src) return Promise.resolve("");
  if (colours.has(src)) return Promise.resolve(colours.get(src));
  return new Promise((ok) => {
    const im = new Image();
    im.onload = () => {
      const c = document.createElement("canvas");
      c.width = c.height = 64;
      const x = c.getContext("2d", { willReadFrequently: true });
      x.drawImage(im, 0, 0, 64, 64);
      const d = x.getImageData(0, 0, 64, 64).data;
      const boxes = new Map();                                         // 8 levels a channel: [count, r, g, b]
      for (let i = 0; i < d.length; i += 4) {
        const k = (d[i] >> 5) * 64 + (d[i + 1] >> 5) * 8 + (d[i + 2] >> 5);
        const e = boxes.get(k) || [0, 0, 0, 0];
        e[0]++; e[1] += d[i]; e[2] += d[i + 1]; e[3] += d[i + 2];
        boxes.set(k, e);
      }
      let best = null, bestScore = -1, grey = null, greyCount = 0;
      for (const [n, r, g, b] of boxes.values()) {
        const rgb = [r / n, g / n, b / n], mx = Math.max(...rgb), mn = Math.min(...rgb);
        const sat = mx ? (mx - mn) / mx : 0;
        if (sat < 0.25 || mx < 60) { if (mx <= 150 && n > greyCount) { grey = rgb; greyCount = n; } continue; }
        const score = n * sat * sat * (mx / 255);
        if (score > bestScore) { bestScore = score; best = rgb; }
      }
      // a colourful box has to cover a little of the cover (0.5 %) to count; otherwise the cover is black and white
      const pick = best && bestScore >= 64 * 64 * 0.005 * 0.25 * 0.4 ? best : grey;
      const col = pick ? readable(pick) : "";
      colours.set(src, col);
      ok(col);
    };
    im.onerror = () => { colours.set(src, ""); ok(""); };
    im.src = src;
  });
}

function render() {
  if (!card) return;
  const t = track(), al = now.album, e = now.entry || {};
  const seg = h("div.segmented", ...[["art", "Art"], ["wave", "Scope"], ["xy", "XY"]].map(([v, label]) =>
    h("button", { class: tab === v ? "on" : "", onclick: () => { tab = v; render(); } }, label)));
  const head = h("div.card-head", h("span.card-title.grow", "Now playing"), seg);
  if (scope) { scope.stop(); scope = null; }
  if (!t) {
    clear(card, head, h("div.empty", icon("playcircle"), h("b", "Nothing playing"),
      "Choose an album, then Play album or click a track. Space plays and pauses; ←/→ seek; +/- volume."));
    return;
  }
  if (tab !== "art") {                                            // just the oscilloscope
    const canvas = h("canvas");
    clear(card, head, h("div.spec-box.np-scope", canvas));
    scope = new Scope(canvas, audio);
    scope.mode = tab;
    traceColour(coverUrl()).then((c) => { if (scope) scope.colour = c; });
    if (!audio.paused) scope.start(); else requestAnimationFrame(() => scope && scope.draw());
    return;
  }
  const cover = coverUrl() ? h("img.np-cover", { src: coverUrl(), alt: "" }) : h("div.np-cover.thumb", icon("image"));
  els.info = h("div.np-info",
    h("div.np-track", { "data-size": 26 }, t.title || t.path.split("/").pop()),
    h("div.np-album", { "data-size": 16 }, e.album || al.folder),
    h("div.np-artist", { "data-size": 16 }, [t.artist || e.artist, e.year].filter(Boolean).join(" · ")),
    h("div.np-chips", albumQualityChip(e.quality), e.format_label ? h("span.muted", e.format_label) : null));
  els.bar = h("div.np-bar-fill");
  els.el = h("span");
  els.dur = h("span");
  const bar = h("div.np-bar", { title: "Click to seek", onclick: (ev) => {
    const r = bar.getBoundingClientRect();
    seekTo(Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width)));
  } }, els.bar);
  const btn = (name, fn, title) => h("button.icon-btn", { onclick: fn, title }, icon(name));
  clear(card, head,
    h("div.np-art", cover, els.info),
    h("div.np-foot", bar,
      h("div.np-times", els.el,
        h("div.np-buttons", btn("prev", prev, "Previous"), btn(audio.paused ? "play" : "pause", toggle, audio.paused ? "Play" : "Pause"), btn("next", next, "Next")),
        els.dur)));
  progress();
  fitInfo();                                                     // now (the card is in the page), and again once it's laid out
  requestAnimationFrame(fitInfo);
}

function progress() {
  if (!els.bar || !els.bar.isConnected) return;
  const d = isFinite(audio.duration) ? audio.duration : (track()?.length || 0);
  els.bar.style.width = (d ? (audio.currentTime / d) * 100 : 0) + "%";
  els.el.textContent = fmtTime(audio.currentTime);
  els.dur.textContent = fmtTime(d);
}

// no clipped titles: the track, album and artist wrap, and shrink together until they fit beside the cover
function fitInfo() {
  const box = els.info;
  if (!box || !box.isConnected) return;
  const parts = [...box.querySelectorAll("[data-size]")];
  for (let s = 1; s >= 0.45; s -= 0.04) {
    parts.forEach((el) => (el.style.fontSize = (+el.dataset.size * s).toFixed(1) + "px"));
    if (box.scrollHeight <= box.clientHeight + 1) return;
  }
}
