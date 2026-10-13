// The launch screen: spcmn's wordmark as ferrofluid (off-white on charcoal, the app's colours), shown from the first
// paint until the app has its settings and library, and at least 5 seconds; then it fades.
// A plain script (not a module), so it starts before three.js and the app's modules have loaded.
// main.js calls window.flacmnSplash.done(promise) once it has started loading.

(function splash() {
  const el = document.getElementById("splash");
  if (!el) return;
  const svg = el.querySelector("svg"), group = svg.querySelector("g.fluid"), blur = svg.querySelector("feGaussianBlur");
  const still = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const NS = "http://www.w3.org/2000/svg";
  const MIN_MS = still ? 600 : 5000, MAX_MS = 10000, FADE_MS = 450;
  const shown = performance.now();

  // spcmn's dot letters, one # per dot on a grid of 1 (the dots are 0.9 across): s p c, then m n
  const dots = [[0, ".####|#####|###..|..###|#####|####."], [6, "####.|#####|##.##|##.##|#####|####.|##...|##..."],
                [12, ".####|#####|##...|##...|#####|.####"], [18, "#.#####.|########|##.##.##|##.##.##|##.##.##|##.##.##"],
                [27, "#.##.|#####|##.##|##.##|##.##|##.##"]]
    .flatMap(([x0, rows]) => rows.split("|").flatMap((row, y) =>
      [...row].flatMap((ch, x) => (ch === "#" ? [{ x: x0 + x + 0.5, y: y + 0.5, r: 0.45 }] : []))));

  // each droplet's rim is the sum of slow ripples (2 to 6 lobes), each swelling and fading on its own beat, and its
  // centre wanders around its spot. Every 9.5 s the fluid dips towards the still grid and straight back out.
  const rand = (a, b) => a + Math.random() * (b - a), sign = () => (Math.random() < 0.5 ? -1 : 1);
  const drops = dots.map((d) => ({
    waves: [[2, 0.17], [3, 0.11], [4, 0.07], [5, 0.045], [6, 0.028]].map(([lobes, amp]) => ({
      lobes, amp: amp * rand(0.6, 1.3), speed: rand(2.6, 6.2) * sign(), phase: rand(0, 2 * Math.PI), surge: rand(0.4, 1.6), surgePhase: rand(0, 2 * Math.PI),
    })),
    drift: [rand(1, 2.4), rand(1, 2.4), rand(0, 2 * Math.PI), rand(0, 2 * Math.PI)],
    x: d.x, y: d.y, r: d.r,
  }));
  const CYCLE = 9.5, NEAR = 0.6, TURN = 8, HALF = 1.5;
  function fluidity(t) {
    const x = Math.abs((t % CYCLE) - TURN) / HALF;
    return 1 - (1 - NEAR) * (x < 1 ? (1 - x) * (1 - x) : 0);
  }
  const K = 12, f = (v) => v.toFixed(3);
  function shape(d, t, e) {
    const R = d.r * (1 - 0.067 * e);
    const cx = d.x + e * 0.14 * Math.sin(d.drift[0] * t + d.drift[2]);
    const cy = d.y + e * 0.14 * Math.sin(d.drift[1] * t + d.drift[3]);
    const pts = [], reach = 0.8;                                         // never further than this from its spot: holes stay open
    for (let k = 0; k < K; k++) {
      const ang = (k / K) * 2 * Math.PI;
      let m = 1;
      for (const w of d.waves) m += e * w.amp * (0.5 + 0.5 * Math.abs(Math.sin(w.surge * t + w.surgePhase))) * Math.sin(w.lobes * ang + w.speed * t + w.phase);
      let px = cx + R * m * Math.cos(ang) - d.x, py = cy + R * m * Math.sin(ang) - d.y;
      const r = Math.hypot(px, py);
      if (r > reach) { px *= reach / r; py *= reach / r; }
      pts.push([d.x + px, d.y + py]);
    }
    let out = `M${f(pts[0][0])},${f(pts[0][1])}`;                       // a closed Catmull-Rom spline, as cubic Beziers
    for (let i = 0; i < K; i++) {
      const p0 = pts[(i - 1 + K) % K], p1 = pts[i], p2 = pts[(i + 1) % K], p3 = pts[(i + 2) % K];
      out += `C${f(p1[0] + (p2[0] - p0[0]) / 6)},${f(p1[1] + (p2[1] - p0[1]) / 6)} ${f(p2[0] - (p3[0] - p1[0]) / 6)},${f(p2[1] - (p3[1] - p1[1]) / 6)} ${f(p2[0])},${f(p2[1])}`;
    }
    return out + "Z";
  }

  const paths = drops.map(() => group.appendChild(document.createElementNS(NS, "path")));
  // start part-way into the cycle, so the fluid is already moving freely when it first shows
  const t0 = shown - 1500;
  function draw(now) {
    const t = (now - t0) / 1000, e = still ? 0 : fluidity(t);
    blur.setAttribute("stdDeviation", (0.17 * (still ? 1 : e)).toFixed(3));
    drops.forEach((d, i) => paths[i].setAttribute("d", shape(d, t, e)));
  }
  let running = true, last = 0;
  function frame(now) {
    if (!running) return;
    if (now - last > 33) { last = now; draw(now); }                     // about 30 frames a second is plenty
    if (!still) requestAnimationFrame(frame);
  }
  draw(shown);
  requestAnimationFrame(frame);

  let closing = false;
  function close() {
    if (closing) return;
    closing = true;
    el.classList.add("leaving");
    setTimeout(() => { running = false; el.remove(); }, FADE_MS);
  }
  setTimeout(close, MAX_MS);                                            // never stay up, whatever happens to the loading
  window.flacmnSplash = {
    done(ready) {
      Promise.resolve(ready).catch(() => {}).then(() => {
        setTimeout(close, Math.max(0, MIN_MS - (performance.now() - shown)));
      });
    },
  };
})();
