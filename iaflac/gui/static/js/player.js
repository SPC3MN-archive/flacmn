// Playback: one <audio> element playing an album from a track onward, moving on to the next track by itself.
// From the Pi library player (SPC3MN/piplayer): album playback, keyboard control (Space, ←/→, +/-), and an idle
// timer the Now playing card uses to show the oscilloscope when nobody has touched anything for a while.

import { url } from "./api.js";
import { toast, errorText } from "./ui.js";
import { wireAudio } from "./scope.js";

export const audio = new Audio();
audio.preload = "auto";

// what is playing: the album as /api/library/album returned it, its library entry, and the track's index
export const now = { album: null, entry: null, index: -1 };

const listeners = new Set();
export function onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }
const changed = () => { for (const fn of listeners) fn(); };
for (const ev of ["play", "pause", "ended", "loadedmetadata"]) audio.addEventListener(ev, changed);

export const track = () => (now.album && now.index >= 0 ? now.album.tracks[now.index] : null);
export const playingPath = () => (track() && !audio.paused ? track().path : null);

// Call from a click or key press: browsers only start audio, and the scope's tap, from a user gesture.
export function playAlbum(album, entry, index = 0) {
  now.album = album;
  now.entry = entry;
  start(index);
}

const searchPlayer = () => document.getElementById("player");    // Album Search's preview player, if this page has one

function start(index) {
  now.index = index;
  const t = track();
  if (!t) return;
  searchPlayer()?.pause();
  audio.src = url("/audio", { path: t.path });
  wireAudio(audio);
  audio.play().catch((e) => toast(`Cannot play: ${errorText(e)}`, "bad"));
  changed();
}

export function toggle() {
  if (!track()) return;
  if (audio.paused) {
    searchPlayer()?.pause();
    wireAudio(audio);
    audio.play().catch((e) => toast(`Cannot play: ${errorText(e)}`, "bad"));
  } else audio.pause();
}

export function next() { if (now.album && now.index + 1 < now.album.tracks.length) start(now.index + 1); }
export function prev() {
  if (!now.album) return;
  if (audio.currentTime > 3 || now.index === 0) audio.currentTime = 0;      // as on most players: back to the start first
  else start(now.index - 1);
}
export function seekBy(s) { if (track() && isFinite(audio.duration)) audio.currentTime = Math.max(0, Math.min(audio.duration - 0.5, audio.currentTime + s)); }
export function seekTo(frac) { if (track() && isFinite(audio.duration)) audio.currentTime = frac * audio.duration; }
export function volumeBy(d) { audio.volume = Math.max(0, Math.min(1, audio.volume + d)); changed(); }

audio.addEventListener("ended", () => {
  if (now.album && now.index + 1 < now.album.tracks.length) start(now.index + 1);
  else { now.index = -1; changed(); }
});

// ---- the keyboard, and how long since anyone touched anything ---------------------------------------------------------
let touched = Date.now();
export const idleFor = () => (Date.now() - touched) / 1000;
for (const ev of ["pointerdown", "keydown", "wheel"]) document.addEventListener(ev, () => (touched = Date.now()), true);

document.addEventListener("keydown", (e) => {
  const t = e.target;
  if (e.metaKey || e.ctrlKey || e.altKey || (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)))) return;
  if (!track()) return;
  if (e.key === " ") toggle();
  else if (e.key === "ArrowRight") seekBy(10);
  else if (e.key === "ArrowLeft") seekBy(-10);
  else if (e.key === "+" || e.key === "=") volumeBy(0.04);
  else if (e.key === "-") volumeBy(-0.04);
  else return;
  e.preventDefault();
});
