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

function update() {
  const list = [...items.values()];
  const active = list.filter((i) => i.state === "downloading" || i.state === "tagging");
  const waiting = list.filter((i) => i.state === "queued");
  const running = [...jobs.values()].filter((j) => j.state === "running" || j.state === "queued");
  let t = "Queue · empty";
  if (active.length) t = `Queue · ${active.length} downloading`;
  else if (waiting.length) t = `Queue · ${waiting.length} waiting`;
  else if (list.some((i) => i.state === "paused")) t = "Queue · paused";
  else if (running.length) t = `${running.length} task${running.length === 1 ? "" : "s"} running`;
  else if (list.length) t = `Queue · ${list.filter((i) => i.state === "done").length} done`;
  textEl.textContent = t;
  chipEl.classList.toggle("busy", !!(active.length || running.length));
  if (!pop.hidden) render();
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

function render() {
  const list = [...items.values()].sort((a, b) => a.order - b.order || a.added - b.added);
  const running = [...jobs.values()].filter((j) => j.state === "running" || j.state === "queued").sort((a, b) => a.created - b.created);
  const finished = list.some((i) => ["done", "cancelled"].includes(i.state));
  clear(pop,
    h("div.card-head", h("span.card-title.grow", "Downloads"),
      finished ? h("button.btn.ghost.sm", { onclick: async () => { await api("/queue/clear", { method: "POST" }); items.clear(); for (const it of await api("/queue")) items.set(it.id, it); update(); } }, "Clear finished") : null),
    h("div.scroll", { style: { maxHeight: "50vh" } },
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
      }) : h("div.muted", { style: { padding: "8px 0" } }, "Nothing queued. Use Add to library on Album Search.")),
    running.length ? h("div", h("hr.hr"), h("div.section-label", { style: { marginBottom: "6px" } }, "Working on"),
      ...running.map((j) => h("div.job-item", h("span.spinner"), h("span.grow.ellipsis", j.label),
        h("button.btn.ghost.sm", { onclick: () => cancelJob(j.id) }, icon("x"))))) : null);
}
