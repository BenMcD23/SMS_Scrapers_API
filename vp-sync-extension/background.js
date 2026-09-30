// Service worker: runs syncs on demand (popup) and on a timer (chrome.alarms),
// one at a time, and keeps the state the popup shows in chrome.storage.local.
//
// Writing progress to storage every step is also what keeps an MV3 worker
// alive through a run that takes a few minutes: each extension API call resets
// Chrome's idle timer.

import { getSettings, runSync } from "./sync.js";

const ALARM = "vp-sync";
let running = false;

async function setState(patch) {
  const { state = {} } = await chrome.storage.local.get("state");
  await chrome.storage.local.set({ state: { ...state, ...patch } });
}

async function sync(trigger) {
  if (running) return;
  running = true;
  await setState({ running: true, progress: "Starting…", startedAt: new Date().toISOString(), trigger });
  try {
    const summary = await runSync((progress) => setState({ progress }));
    await setState({ lastOk: { at: new Date().toISOString(), summary }, lastError: null });
  } catch (e) {
    await setState({ lastError: { at: new Date().toISOString(), message: String(e?.message || e) } });
  } finally {
    running = false;
    await setState({ running: false, progress: null });
  }
}

async function schedule() {
  const { intervalMinutes } = await getSettings();
  await chrome.alarms.clear(ALARM);
  if (intervalMinutes > 0) {
    chrome.alarms.create(ALARM, { delayInMinutes: 1, periodInMinutes: intervalMinutes });
  }
}

chrome.runtime.onInstalled.addListener(async () => {
  // A worker killed mid-run leaves running: true behind; clear it on update.
  await setState({ running: false, progress: null });
  await schedule();
});
chrome.runtime.onStartup.addListener(async () => {
  await setState({ running: false, progress: null });
  await schedule();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM) sync("scheduled");
});

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg?.action === "sync") {
    sync("manual");
    reply({ started: true });
  } else if (msg?.action === "reschedule") {
    schedule().then(() => reply({ ok: true }));
    return true;
  }
  return false;
});
