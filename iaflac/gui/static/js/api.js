// Talking to the local server: JSON calls, the event WebSocket, and waiting for background jobs.
// The page was opened with ?token=..., which set an HttpOnly cookie, so fetch, <audio>, <img> and the WebSocket all carry it.

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

export async function api(path, { method = "GET", body, query } = {}) {
  let url = "/api" + path;
  if (query) url += "?" + new URLSearchParams(query).toString();
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["content-type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(url, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(data.error || data.detail || `${r.status} ${r.statusText}`, r.status);
  return data;
}

export const url = (path, query) => "/api" + path + (query ? "?" + new URLSearchParams(query).toString() : "");

// ---- events ------------------------------------------------------------------------------------------------------
const handlers = new Map();          // type -> Set(fn)
export function on(type, fn) {
  if (!handlers.has(type)) handlers.set(type, new Set());
  handlers.get(type).add(fn);
  return () => handlers.get(type).delete(fn);
}
function emit(event) {
  for (const fn of handlers.get(event.type) || []) {
    try { fn(event); } catch (e) { console.error(e); }
  }
  for (const fn of handlers.get("*") || []) {
    try { fn(event); } catch (e) { console.error(e); }
  }
}

let socket = null;
let retry = 500;
export function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${proto}://${location.host}/api/events`);
  socket.onopen = () => { retry = 500; emit({ type: "connected" }); };
  socket.onmessage = (m) => { try { emit(JSON.parse(m.data)); } catch (e) { console.error(e); } };
  socket.onclose = () => {
    emit({ type: "disconnected" });
    setTimeout(connect, retry);
    retry = Math.min(retry * 2, 8000);
  };
}

// ---- jobs ----------------------------------------------------------------------------------------------------------
export const jobs = new Map();       // id -> latest job summary
on("job", (e) => jobs.set(e.job.id, { ...(jobs.get(e.job.id) || {}), ...e.job }));
on("progress", (e) => {
  const j = jobs.get(e.job_id);
  if (j) j.progress = e;
});

/** Resolve with the job's result once it is done; reject if it fails or is cancelled. onProgress gets progress events. */
export function waitJob(jobId, onProgress) {
  return new Promise((resolve, reject) => {
    let finished = false;
    const finish = (job) => {
      if (finished) return;
      if (job.state === "done") { finished = true; stop(); resolve(job.result); }
      else if (job.state === "failed" || job.state === "cancelled") {
        finished = true; stop();
        reject(new ApiError(job.state === "cancelled" ? "cancelled" : (job.error || "failed"), 0));
      }
    };
    const offJob = on("job", (e) => { if (e.job.id === jobId) finish(e.job); });
    const offProg = on("progress", (e) => { if (e.job_id === jobId && onProgress) onProgress(e); });
    const stop = () => { offJob(); offProg(); clearInterval(poll); };
    // the job may have finished before we subscribed, and events can be missed while the socket reconnects
    const check = () => api(`/jobs/${jobId}`).then(finish).catch(() => {});
    const poll = setInterval(check, 3000);
    check();
  });
}

export const cancelJob = (jobId) => api(`/jobs/${jobId}/cancel`, { method: "POST" });
