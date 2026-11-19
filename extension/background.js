// JobPilot browser button: a badge on application pages. It only reads the address of tabs on the job
// sites listed in the manifest, and asks the dashboard on this computer whether it knows that job.
// It never reads or changes the page, and never submits anything.
const SITES = /^https:\/\/([a-z0-9-]+\.)*(greenhouse\.io|lever\.co|ashbyhq\.com|myworkdayjobs\.com|icims\.com|smartrecruiters\.com|workable\.com|jobvite\.com|bamboohr\.com)\//i;

async function lookup(url) {
  const { key, base } = await chrome.storage.local.get(["key", "base"]);
  if (!key) return null;
  try {
    const r = await fetch((base || "http://127.0.0.1:8765").replace(/\/+$/, "") + "/api/ext/status", {
      method: "POST", headers: { "Content-Type": "application/json", "X-JobPilot-Key": key },
      body: JSON.stringify({ url }),
    });
    return r.ok ? await r.json() : null;
  } catch (e) { return null; }
}

async function badge(tabId, url) {
  if (!url || !SITES.test(url)) { chrome.action.setBadgeText({ tabId, text: "" }); return; }
  const got = await lookup(url);
  const st = got && got.job ? got.job.status : "";
  const ready = st === "approved" || st === "filled";
  chrome.action.setBadgeBackgroundColor({ tabId, color: ready ? "#1d9a5b" : "#2f6fdb" });
  chrome.action.setBadgeText({ tabId, text: ready ? "GO" : st === "submitted" ? "✓" : "FILL" });
  chrome.action.setTitle({ tabId, title: ready ? "JobPilot: you approved this one. Click (or Alt+Shift+J) to fill it."
    : st === "submitted" ? "JobPilot: you already applied here." : "JobPilot: click (or Alt+Shift+J) to fill this application." });
}

chrome.tabs.onUpdated.addListener((tabId, info, tab) => { if (info.status === "complete") badge(tabId, tab.url); });
chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (tab) badge(tabId, tab.url);
});
