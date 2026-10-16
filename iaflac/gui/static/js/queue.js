// The queue chip in the top bar and its popover: downloads (pause, resume, retry, cancel, remove) and running jobs.

import { api, on, jobs, cancelJob } from "./api.js";
import { h, clear, icon, fmtBytes, toast, errorText, dismissOnOutside } from "./ui.js";

const items = new Map();
let chipEl, textEl, pop, offOutside = null;

export async function mountQueue() {
  chipEl = document.getElementById("queue-chip");
  textEl = document.getElementById("queue-text");
  pop = document.getElementById("queue-pop");
  chipEl.addEventListener("click", () => (pop.hidden ? open() : close()));
  on("queue", (e) => {
    const before = items.get(e.item.id);
    items.set(e.item.id, e.item);
    if (before && before.state !== "done" && e.item.state === "done") toast(`Saved: ${e.item.artist} / ${e.item.album}`, "good");
    if (before && before.state !== "failed" && e.item.state === "failed") toast(`Download failed: ${e.item.album}: ${e.item.error}`, "bad", 8000);
    update();
  });
  on("job", update);
  on("progress", () => { if (!pop.hidden) render(); });
  try {
    for (const it of await api("/queue")) items.set(it.id, it);
    for (const j of await api("/jobs")) jobs.set(j.id, j);
  } catch (e) { /* the server answers once it is up */ }
  update();
}

// album replacements from the Health Check: shown like downloads, with a bar, rather than as background tasks
const isReplace = (j) => j.kind === "health-replace" && (j.state === "running" || j.state === "queued");

function update() {
  const list = [...items.values()];
  const active = list.filter((i) => i.state === "downloading" || i.state === "tagging");
  const waiting = list.filter((i) => i.state === "queued");
  const replacing = [...jobs.values()].filter(isReplace);
  const running = [...jobs.values()].filter((j) => (j.state === "running" || j.state === "queued") && !isReplace(j));
  let t = "Downloads";
  if (replacing.length) t = `Downloads · replacing ${replacing.length} album${replacing.length === 1 ? "" : "s"}`;
  else if (active.length) t = `Downloads · ${active.length} downloading`;
  else if (waiting.length) t = `Downloads · ${waiting.length} waiting`;
  else if (list.some((i) => i.state === "paused")) t = "Downloads · paused";
  else if (running.length) t = `${running.length} task${running.length === 1 ? "" : "s"} running`;
  else if (list.length) t = `Downloads · ${list.filter((i) => i.state === "done").length} done`;
  textEl.textContent = t;
  chipEl.classList.toggle("busy", !!(active.length || running.length || replacing.length));
  if (!pop.hidden) render();
  if (running.length || replacing.length) recheckSoon();
}

// A job's "done" event can be missed (one that finished while the page was loading, or while the connection was
// re-made), which left the chip saying "1 task running" for good. While anything looks busy, ask the server again
// every few seconds.
let recheck = null;
function recheckSoon() {
  if (recheck) return;
  recheck = setTimeout(async () => {
    recheck = null;
    try {
      const now = new Map((await api("/jobs")).map((j) => [j.id, j]));
      for (const [id, j] of jobs) {
        const fresh = now.get(id);
        if (fresh) jobs.set(id, { ...j, ...fresh });
        else if (j.state === "running" || j.state === "queued") jobs.set(id, { ...j, state: "done" });
      }
    } catch (e) { /* the server answers again shortly */ }
    update();
  }, 4000);
}

function open() {
  pop.hidden = false;
  render();
  offOutside = dismissOnOutside(pop, chipEl, close);
}

function close() {
  pop.hidden = true;
  if (offOutside) offOutside();
}

async function act(id, action) {
  try {
    const it = action === "remove" ? (await api(`/queue/${id}`, { method: "DELETE" }), null) : await api(`/queue/${id}/${action}`, { method: "POST" });
    if (it) items.set(it.id, it); else items.delete(id);
    update();
  } catch (e) { toast(errorText(e), "bad"); }
}

const STATE = { queued: "Waiting", downloading: "Downloading", tagging: "Writing tags", paused: "Paused", failed: "Failed", done: "Saved", cancelled: "Cancelled" };

const REPLACE_STEP = { preparing: "Reading the upload", download: "Downloading", tagging: "Writing tags",
  swapping: "Swapping in: the old folder goes to the Trash" };

// one album replacement, drawn like a download: what it is, the step with files and sizes, a green bar, Cancel
function replaceItem(j) {
  const e = j.progress || {};
  const step = e.stage === "download" ? "download" : e.stage === "tagging" ? "tagging" : e.stage === "replace" ? e.step : "preparing";
  const pct = step === "download" && e.total_bytes ? Math.min(100, (100 * (e.done_bytes || 0)) / e.total_bytes)
    : ["tagging", "swapping"].includes(step) ? 100 : 0;
  const [album, from] = j.label.split(" \u2190 ");
  return h("div.q-item",
    h("div.row", h("div.grow", { style: { minWidth: 0 } },
      h("div.t.ellipsis", `Replacing ${album}`),
      h("div.s.ellipsis", (REPLACE_STEP[step] || "Starting") + (step === "download"
        ? `${e.total ? ` · file ${e.index} of ${e.total}` : ""} · ${fmtBytes(e.done_bytes)} of ${fmtBytes(e.total_bytes)}` : "")
        + (from ? ` · from ${from}` : ""))),
      h("button.btn.ghost.sm", { onclick: () => cancelJob(j.id) }, "Cancel")),
    h("div.progress", h("i", { style: { width: `${pct}%` } })));
}

function render() {
  const list = [...items.values()].sort((a, b) => a.order - b.order || a.added - b.added);
  const replacing = [...jobs.values()].filter(isReplace).sort((a, b) => a.created - b.created);
  const running = [...jobs.values()].filter((j) => (j.state === "running" || j.state === "queued") && !isReplace(j))
    .sort((a, b) => a.created - b.created);
  const finished = list.some((i) => ["done", "cancelled"].includes(i.state));
  clear(pop,
    h("div.card-head", h("span.card-title.grow", "Downloads"),
      finished ? h("button.btn.ghost.sm", { onclick: async () => { await api("/queue/clear", { method: "POST" }); items.clear(); for (const it of await api("/queue")) items.set(it.id, it); update(); } }, "Clear finished") : null),
    h("div.scroll", { style: { maxHeight: "50vh" } },
      replacing.map(replaceItem),
      list.length ? list.map((it) => {
        const pct = it.total_bytes ? Math.min(100, (it.done_bytes / it.total_bytes) * 100) : it.state === "done" ? 100 : 0;
        const btns = [];
        if (it.state === "downloading" || it.state === "queued") btns.push(["pause", "Pause"]);
        if (it.state === "paused") btns.push(["resume", "Resume"]);
        if (it.state === "failed" || it.state === "cancelled") btns.push(["retry", "Retry"]);
        if (["downloading", "queued", "paused", "tagging"].includes(it.state)) btns.push(["cancel", "Cancel"]);
        if (["done", "failed", "cancelled"].includes(it.state)) btns.push(["remove", "Remove"]);
        const problems = (it.result?.problems || []).concat(it.result?.issues || []);
        return h("div.q-item",
          h("div.row", h("div.grow", { style: { minWidth: 0 } }, h("div.t.ellipsis", `${it.artist} / ${it.album}`),
            h("div.s.ellipsis", `${STATE[it.state] || it.state}${it.total ? ` · file ${it.index} of ${it.total}` : ""} · ${fmtBytes(it.done_bytes)} of ${fmtBytes(it.total_bytes)}`)),
            ...btns.map(([a, t]) => h("button.btn.ghost.sm", { onclick: () => act(it.id, a) }, t))),
          h("div.progress" + (it.state === "done" ? "" : ".busy"), h("i", { style: { width: `${pct}%`, background: it.state === "failed" ? "var(--red)" : null } })),
          it.error ? h("div.s", { style: { color: "var(--bad-text)" } }, it.error) : null,
          problems.length ? h("div.s", { style: { color: "var(--warn-text)" } }, problems.slice(0, 3).join(" · ")) : null);
      }) : replacing.length ? null : h("div.muted", { style: { padding: "8px 0" } }, "Nothing queued. Use Add to library on Album Search.")),
    running.length ? h("div", h("hr.hr"), h("div.section-label", { style: { marginBottom: "6px" } }, "Working on"),
      ...running.map((j) => h("div.job-item", h("span.spinner"), h("span.grow.ellipsis", j.label),
        h("button.btn.ghost.sm", { onclick: () => cancelJob(j.id) }, icon("x"))))) : null);
}
