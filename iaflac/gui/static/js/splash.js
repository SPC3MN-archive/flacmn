// The launch screen: spcmn's wordmark as ferrofluid (off-white on charcoal, the app's colours), shown from the first
// paint until the app has its settings and library, and at least 5 seconds; then it fades.
// The fluid is a video (splash.mp4, 60 fps, rendered by design/render_splash.py): the browser plays it off the page's
// thread, so the app loading meanwhile cannot make it stutter. It fades in once it is playing, and it is 10 s long,
// as long as the screen can stay up. With reduced motion, the still poster is shown instead.
// A plain script (not a module): it starts the app's modules itself (below). main.js calls window.flacmnSplash.done(promise).

(function splash() {
  const el = document.getElementById("splash");
  if (!el) return;
  // made here, not in the HTML: WebKit only autoplays a video that is already muted when its source is set
  const poster = el.querySelector("img");
  const video = document.createElement("video");
  Object.assign(video, { muted: true, defaultMuted: true, playsInline: true, autoplay: true, loop: false, preload: "auto" });
  video.setAttribute("aria-hidden", "true");
  video.src = "/static/splash.mp4";
  el.append(video);
  const still = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const MIN_MS = still ? 600 : 5000, MAX_MS = 10000, FADE_MS = 450;
  const shown = performance.now();
  const appear = () => el.classList.add("drawn");
  // the poster is the video's first frame: it fades in at once, and the video takes over from it without a seam
  if (poster.complete) requestAnimationFrame(appear); else poster.addEventListener("load", appear, { once: true });
  let closing = false;

  // the app's own code starts once the video is playing (or after 1.5 s): it would otherwise take the connections and
  // the page's thread while the video loads
  const tryPlay = () => { if (video.paused && !closing) video.play()?.catch?.(() => {}); };
  let started = false;
  const startApp = () => { if (!started) { started = true; import("/static/js/main.js"); } };
  if (still) { video.removeAttribute("autoplay"); video.pause(); appear(); startApp(); }
  else {
    video.addEventListener("playing", () => { el.classList.add("playing"); appear(); startApp(); }, { once: true });
    // a browser plays it on its own; the desktop window's WebKit turns down any play() the page makes by itself, so
    // the app asks for it from outside as soon as the page has loaded (app.py: flacmnSplash.play()). Until then, or if
    // it never plays, the poster shows
    tryPlay();
    requestAnimationFrame(() => requestAnimationFrame(tryPlay));
    video.addEventListener("canplay", tryPlay);
    for (const ms of [250, 600, 1000]) setTimeout(tryPlay, ms);
  }
  setTimeout(() => { appear(); startApp(); }, 1500);                    // shown, and the app started, even if the video is slow

  function close() {
    if (closing) return;
    closing = true;
    el.classList.add("leaving");
    setTimeout(() => { video.pause(); el.remove(); }, FADE_MS);
  }
  setTimeout(close, MAX_MS);                                            // never stay up, whatever happens to the loading
  window.flacmnSplash = {
    play() { if (!still) tryPlay(); },
    done(ready) {
      Promise.resolve(ready).catch(() => {}).then(() => {
        setTimeout(close, Math.max(0, MIN_MS - (performance.now() - shown)));
      });
    },
  };
})();
