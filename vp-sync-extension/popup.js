import { DEFAULT_SETTINGS, getSettings } from "./sync.js";

const $ = (id) => document.getElementById(id);
const FIELDS = Object.keys(DEFAULT_SETTINGS);

function ago(iso) {
  const mins = Math.round((Date.now() - new Date(iso)) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  return hours < 48 ? `${hours} h ago` : `${Math.round(hours / 24)} days ago`;
}

function render(state = {}) {
  $("sync").disabled = Boolean(state.running);
  const status = $("status");
  const summary = $("summary");
  summary.replaceChildren();

  if (state.running) {
    status.className = "muted";
    status.textContent = state.progress || "Syncing…";
    return;
  }
  const ok = state.lastOk;
  const err = state.lastError;
  if (err && (!ok || err.at > ok.at)) {
    status.className = "bad";
    status.textContent = `Last sync failed (${ago(err.at)}): ${err.message}`;
  } else if (ok) {
    status.className = "good";
    status.textContent = `Last synced ${ago(ok.at)}.`;
  } else {
    status.className = "muted";
    status.textContent = "Not synced yet.";
  }
  if (!ok) return;

  const s = ok.summary;
  const lines = [
    `${s.people} people, ${s.recordsStored} records stored`,
    s.notOnSmsRoster ? `${s.notOnSmsRoster} in VP but not on the SMS roster yet (skipped)` : null,
    s.removed ? `${s.removed} removed (no longer in VP)` : null,
    s.noPermission.length ? `Your VP role can't see: ${s.noPermission.join(", ")}` : null,
    s.failed ? `${s.failed} lookups failed; previous copies kept` : null,
  ].filter(Boolean);
  const ul = document.createElement("ul");
  for (const line of lines) {
    const li = document.createElement("li");
    li.textContent = line;
    ul.append(li);
  }
  summary.append(ul);
}

async function load() {
  const settings = await getSettings();
  for (const k of FIELDS) $(k).value = settings[k];
  const { state } = await chrome.storage.local.get("state");
  render(state);
}

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.state) render(changes.state.newValue);
});

$("sync").addEventListener("click", () => chrome.runtime.sendMessage({ action: "sync" }));

$("save").addEventListener("click", async () => {
  const settings = {
    apiBase: $("apiBase").value.trim().replace(/\/+$/, ""),
    siteBase: $("siteBase").value.trim().replace(/\/+$/, ""),
    unitFilter: $("unitFilter").value.trim() || DEFAULT_SETTINGS.unitFilter,
    intervalMinutes: Math.max(0, Number($("intervalMinutes").value) || 0),
  };
  // A dev API or site outside the manifest's hosts needs permission granted
  // now, while there's a click to attach the prompt to.
  const origins = [settings.apiBase, settings.siteBase].map((u) => `${new URL(u).origin}/*`);
  const granted = await chrome.permissions.request({ origins }).catch(() => false);
  if (!granted) {
    $("saved").textContent = "Not saved: permission for those addresses was refused.";
    return;
  }
  await chrome.storage.local.set({ settings });
  await chrome.runtime.sendMessage({ action: "reschedule" });
  $("saved").textContent = "Saved.";
});

load();
