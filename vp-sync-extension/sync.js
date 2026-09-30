// One sync run: read 317's people and their records from VP, post them to the
// SMS API. The API decides what it keeps (dataset allow-list, CINs on the SMS
// roster); this side just avoids fetching what it will throw away.

import { NoPermissionError, NotSignedInError, resetSession, rowsOf, vpGet, vpGetAll } from "./vp.js";

export const DEFAULT_SETTINGS = {
  apiBase: "https://smsapi.317atc.co.uk",
  siteBase: "https://sms.317atc.co.uk",
  // Matched against VP's unitName. A user whose VP role covers the wing sees
  // every squadron; this keeps the sync to ours before anything leaves VP.
  unitFilter: "317",
  // 0 turns scheduled syncs off. They only run while Chrome is open.
  intervalMinutes: 360,
};

// Per-person VP endpoints, keyed by the dataset name the API stores them under
// (routers/vp_sync.py DATASETS). {id} is VP's personnelWebId.
const PERSON_DATASETS = {
  learning: (id) => `api/person/${id}/learning/history`,
  agreements: (id) => `api/person/${id}/agreements`,
  whts: (id) => `api/shootingmanagement/${id}/whts`,
  shooting_log: (id) => `api/shootingmanagement/${id}/shootinglog`,
  fieldcraft: (id) => `api/fieldcraftmanagement/${id}/completions`,
  aviation: (id) => `api/person/${id}/aviation/history`,
};

// Roster fields sent to the API. It filters again on its side; trimming here too
// means date of birth and the like never leave the browser.
const PROFILE_FIELDS = [
  "personnelId", "givenName", "familyName", "rankAbbreviation", "unitName",
  "wing", "classification", "flight", "activeStatusType", "serviceJoinDate",
  "appointmentType", "appointmentTitle",
];

const CONCURRENCY = 3;
const UPLOAD_BATCH = 100;

export async function getSettings() {
  const stored = await chrome.storage.local.get("settings");
  return { ...DEFAULT_SETTINGS, ...(stored.settings || {}) };
}

/**
 * The Google ID token the SMS site's session already holds. The API accepts it
 * like any other SMS request (staff only), so the extension needs no secret of
 * its own and every sync is recorded against a real person.
 */
async function smsToken(siteBase) {
  const res = await fetch(`${siteBase}/api/auth/session`, { credentials: "include" });
  const session = res.ok ? await res.json() : null;
  if (!session?.id_token) throw new Error(`Not signed in to 317 SMS. Open ${siteBase}, sign in, then sync again.`);
  return session.id_token;
}

async function apiPost(settings, token, path, body) {
  const res = await fetch(`${settings.apiBase}${path}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) {
    let detail = text.slice(0, 200);
    try { detail = JSON.parse(text).detail ?? detail; } catch { /* not JSON */ }
    if (res.status === 403) throw new Error("The SMS API only accepts syncs from staff accounts.");
    throw new Error(`SMS API ${res.status} on ${path}: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
  }
  return JSON.parse(text);
}

/** Run tasks with a small concurrency limit, so VP sees a trickle, not a burst. */
async function pool(tasks, limit) {
  let next = 0;
  const workers = Array.from({ length: Math.min(limit, tasks.length) }, async () => {
    while (next < tasks.length) await tasks[next++]();
  });
  await Promise.all(workers);
}

function toRosterPerson(row, personType) {
  const cin = Number(String(row.computerNumber ?? "").trim());
  const personnelWebId = String(row.personnelWebId ?? "").trim();
  if (!personnelWebId || !Number.isSafeInteger(cin) || cin <= 0) return null;
  const profile = {};
  for (const k of PROFILE_FIELDS) if (row[k] !== undefined && row[k] !== null) profile[k] = row[k];
  return { personnelWebId, cin, personType, profile };
}

/** A VP unit-wide feed split into one record per person, or [] if VP says no. */
async function unitWide(label, fn, skipped) {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof NoPermissionError) {
      skipped.push(label);
      return [];
    }
    throw e;
  }
}

async function mandatoryTraining(ids) {
  const rows = rowsOf(await vpGet("api/trackers/mandatorytraining"));
  return rows
    .filter((r) => ids.has(String(r.personnelWebId ?? "").trim()))
    .map((r) => ({ personnelWebId: String(r.personnelWebId).trim(), dataset: "mandatory_training", payload: r }));
}

async function examResults(ids) {
  const subjects = rowsOf(await vpGet("api/exams/management/subjects"));
  const byPerson = new Map();
  for (const s of subjects) {
    const examId = String(s.classificationExamId ?? "").trim();
    if (!examId) continue;
    const uri = String(s.resultsUri ?? "").trim() || `api/exams/management/subjects/${examId}/results`;
    const results = rowsOf(await vpGet(uri).catch((e) => {
      if (e instanceof NotSignedInError) throw e;
      return [];
    }));
    for (const r of results) {
      const id = String(r.personnelWebId ?? "").trim();
      if (!ids.has(id)) continue;
      if (!byPerson.has(id)) byPerson.set(id, []);
      byPerson.get(id).push({ ...r, classificationExamId: examId, cadetClassification: s.cadetClassification });
    }
  }
  // Everyone gets a row, even with no results, so an exam result VP later
  // removes doesn't linger here.
  return [...ids].map((id) => ({ personnelWebId: id, dataset: "exam_results", payload: byPerson.get(id) || [] }));
}

/**
 * Run a full sync. `progress` is called with a short status line as it goes;
 * the background worker writes it to storage, which also keeps the worker alive.
 */
export async function runSync(progress) {
  const settings = await getSettings();
  resetSession();

  progress("Checking your 317 SMS sign-in…");
  const token = await smsToken(settings.siteBase);

  progress("Reading the unit roster from VP…");
  const [cadets, staff] = await Promise.all([vpGetAll("api/persons/cadets"), vpGetAll("api/persons/staff")]);
  const inUnit = (r) => String(r.unitName ?? "").includes(settings.unitFilter);
  const people = [
    ...cadets.filter(inUnit).map((r) => toRosterPerson(r, "cadet")),
    ...staff.filter(inUnit).map((r) => toRosterPerson(r, "staff")),
  ].filter(Boolean);
  if (people.length === 0) {
    throw new Error(`VP shows nobody whose unit contains "${settings.unitFilter}". Check the unit filter.`);
  }

  const roster = await apiPost(settings, token, "/vp-sync/people", { people });
  const ids = new Set(roster.acceptedIds);

  const skipped = [];
  let pending = [];
  let stored = 0;
  const flush = async (force) => {
    while (pending.length >= UPLOAD_BATCH || (force && pending.length)) {
      const batch = pending.splice(0, UPLOAD_BATCH);
      const res = await apiPost(settings, token, "/vp-sync/records", { records: batch });
      stored += res.stored;
    }
  };

  progress("Reading mandatory training and exam results…");
  pending.push(...await unitWide("mandatory training", () => mandatoryTraining(ids), skipped));
  pending.push(...await unitWide("exam results", () => examResults(ids), skipped));
  await flush(false);

  // Per-person datasets. A 403 means this user's VP role can't see that
  // dataset at all, so stop asking for it rather than failing every person.
  const denied = new Set();
  const failed = [];
  let done = 0;
  const tasks = [...ids].map((id) => async () => {
    for (const [dataset, endpoint] of Object.entries(PERSON_DATASETS)) {
      if (denied.has(dataset)) continue;
      try {
        pending.push({ personnelWebId: id, dataset, payload: await vpGet(endpoint(id)) });
      } catch (e) {
        if (e instanceof NotSignedInError) throw e;
        if (e instanceof NoPermissionError) denied.add(dataset);
        // Anything else: leave the stored copy alone rather than overwrite it
        // with nothing, and report it at the end.
        else failed.push(`${dataset} for ${id}`);
      }
    }
    done += 1;
    progress(`Reading records from VP… ${done}/${ids.size} people`);
    await flush(false);
  });
  await pool(tasks, CONCURRENCY);
  await flush(true);

  skipped.push(...[...denied].map((d) => d.replace("_", " ")));
  return {
    people: roster.accepted,
    notOnSmsRoster: roster.skippedNotOnRoster,
    removed: roster.removed,
    recordsStored: stored,
    noPermission: skipped,
    failed: failed.length,
  };
}
