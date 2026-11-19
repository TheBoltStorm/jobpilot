// JobPilot browser button popup. Talks only to the JobPilot dashboard on this computer.
const $ = (s) => document.querySelector(s);
const cfg = { base: "http://127.0.0.1:8765", key: "" };
let tab = null;
let frames = []; // [{frameId, fields}]
let plan = null;

function show(id, on = true) { $(id).hidden = !on; }
function say(text, kind = "") { const m = $("#msg"); m.textContent = text; m.className = kind; }

async function api(path, body) {
  let r;
  try {
    r = await fetch(cfg.base.replace(/\/+$/, "") + path, {
      method: body ? "POST" : "GET",
      headers: { "Content-Type": "application/json", "X-JobPilot-Key": cfg.key },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw new Error("Can't reach JobPilot. Start it with `jobpilot dashboard`, then try again.");
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || data.error || `JobPilot answered ${r.status}.`);
  return data;
}

async function inject() {
  await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, files: ["page.js"] }).catch(() => {});
}

async function connect() {
  try {
    const hi = await api("/api/ext/hello");
    $("#status").textContent = hi.profile ? "connected" : "connected · no profile yet";
    show("#setup", false); show("#main");
    if (hi.profile && tab && /^https?:/.test(tab.url || "")) $("#scan").click(); // read the form right away
  } catch (e) {
    $("#status").textContent = "not connected";
    show("#main", false); show("#setup");
    $("#key").value = cfg.key; $("#base").value = cfg.base;
    if (cfg.key) say(e.message, "err");
  }
}

function tagFor(f) {
  if (f.present) return "already filled";
  if (!f.value) return f.protected ? "only you" : "for you";
  return f.draft ? "DRAFT" : f.state === "human" ? "your answer" : "profile";
}

function renderPlan() {
  const list = $("#fields");
  list.replaceChildren();
  let fillable = 0;
  for (const f of plan.fields) {
    const li = document.createElement("li");
    if (!f.value && !f.present) li.className = "need";
    const box = document.createElement("input");
    box.type = "checkbox"; box.checked = f.fill; box.disabled = !f.can_fill; box.dataset.key = f.key;
    box.addEventListener("change", updateFillButton);
    const q = document.createElement("div"); q.className = "q";
    const label = document.createElement("div"); label.textContent = f.label;
    const v = document.createElement("div"); v.className = "v";
    const tag = document.createElement("span"); tag.className = "tag"; tag.textContent = tagFor(f);
    v.append(tag, " ", f.value ? f.value.slice(0, 160) : f.source);
    q.append(label, v); li.append(box, q); list.append(li);
    if (f.can_fill) fillable++;
  }
  const left = plan.fields.filter((f) => !f.value && !f.optional && !f.present).length;
  $("#plan-head").textContent = (plan.job ? `${plan.job.title} at ${plan.job.company}. ` : "") +
    `${fillable} fields JobPilot can fill, ${left} only you can answer. DRAFT answers are unticked: tick them only after reading.`;
  show("#plan"); updateFillButton();
  if (!$("#fill").disabled) $("#fill").focus(); // Enter fills the ticked fields
}

function updateFillButton() {
  const n = [...document.querySelectorAll("#fields input:checked")].length;
  $("#fill").textContent = `Fill ${n} ticked field${n === 1 ? "" : "s"}`;
  $("#fill").disabled = n === 0;
}

$("#scan").addEventListener("click", async () => {
  say("Reading the form…"); show("#plan", false); show("#job-form", false);
  await inject();
  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id, allFrames: true }, func: () => (window.__jobpilot ? window.__jobpilot.scan() : null),
  }).catch((e) => { say("Can't read this page: " + e.message, "err"); return []; });
  frames = results.filter((r) => r.result).map((r) => ({ frameId: r.frameId, ...r.result }));
  if (frames.some((f) => f.hasPassword)) {
    say("This looks like a sign-in or sign-up page. JobPilot doesn't fill those or create accounts. Sign in yourself, then open the application form.", "err");
    return;
  }
  const fields = frames.flatMap((fr) => fr.fields.map((f) => ({ ...f, key: `${fr.frameId}|${f.key}` })));
  if (!fields.length) { say("No form fields on this page. Open the application form itself and try again.", "err"); return; }
  try {
    plan = await api("/api/ext/plan", { url: tab.url, title: tab.title, fields });
  } catch (e) { say(e.message, "err"); return; }
  if (plan.refused) { say(plan.refused, "err"); return; }
  say(""); renderPlan();
});

$("#fill").addEventListener("click", async () => {
  const ticked = new Set([...document.querySelectorAll("#fields input:checked")].map((b) => b.dataset.key));
  const byFrame = new Map();
  for (const f of plan.fields) {
    const [frameId, key] = [Number(f.key.split("|")[0]), f.key.split("|").slice(1).join("|")];
    if (!byFrame.has(frameId)) byFrame.set(frameId, { answers: [], left: [] });
    const fr = byFrame.get(frameId);
    if (ticked.has(f.key) && f.can_fill) fr.answers.push({ key, value: f.value, kind: f.kind });
    else if (!f.optional && !f.present) fr.left.push(key);
  }
  let filled = 0, left = 0, failed = 0;
  for (const [frameId, fr] of byFrame) {
    const [res] = await chrome.scripting.executeScript({
      target: { tabId: tab.id, frameIds: [frameId] },
      func: (a, l) => window.__jobpilot.fill(a, l), args: [fr.answers, fr.left],
    }).catch(() => [{ result: { filled: 0, failed: fr.answers.map((a) => a.key), left: 0 } }]);
    filled += res.result.filled; left += res.result.left; failed += res.result.failed.length;
  }
  say(`Filled ${filled} field${filled === 1 ? "" : "s"}` + (failed ? `, ${failed} couldn't be filled` : "") +
      `. ${left} left for you (orange). Check everything, then click Submit yourself.`, "ok");
  api("/api/ext/filled", { url: tab.url, filled, left }).catch(() => {});
});

$("#save-job").addEventListener("click", async () => {
  show("#plan", false); say("");
  await inject();
  const [res] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: () => window.__jobpilot.pageInfo() })
    .catch(() => [{ result: { title: tab.title, company: "" } }]);
  $("#j-title").value = res.result.title || ""; $("#j-company").value = res.result.company || "";
  $("#job-form").dataset.description = res.result.description || "";
  show("#job-form");
});

$("#job-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const r = await api("/api/ext/job", { url: tab.url, title: $("#j-title").value, company: $("#j-company").value,
      description: $("#job-form").dataset.description || "" });
    show("#job-form", false);
    say(r.new ? "Saved to JobPilot. " : "Already in JobPilot. ", "ok");
    const a = document.createElement("a"); a.href = "#"; a.textContent = "Open it";
    a.addEventListener("click", () => chrome.tabs.create({ url: cfg.base.replace(/\/+$/, "") + r.path }));
    $("#msg").append(a);
  } catch (err) { say(err.message, "err"); }
});

$("#save-setup").addEventListener("click", async () => {
  cfg.key = $("#key").value.trim(); cfg.base = $("#base").value.trim() || cfg.base;
  if (!/^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?\/?$/.test(cfg.base)) { say("The address must be on this computer (127.0.0.1 or localhost).", "err"); return; }
  await chrome.storage.local.set({ key: cfg.key, base: cfg.base });
  say(""); connect();
});

$("#change-key").addEventListener("click", (e) => { e.preventDefault(); show("#main", false); show("#setup"); $("#key").value = cfg.key; });

(async () => {
  Object.assign(cfg, await chrome.storage.local.get(["key", "base"]));
  [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  $("#page-title").textContent = tab ? tab.title : "";
  if (!cfg.key) { $("#status").textContent = "set up"; show("#setup"); return; }
  connect();
})();
