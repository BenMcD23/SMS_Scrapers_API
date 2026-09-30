// Volunteer Portal client.
//
// VP's frontend talks to its own JSON API under /api/. There's no API key: it
// rides on the browser's VP session cookie, which is why this runs in an
// extension rather than on the server. Chrome attaches the cookie to an
// extension's fetch when the manifest has host permission for VP.
//
// Every request also needs an X-CSRF-TOKEN header. It comes from
// /api/antiforgery/token, with the csrf-token meta tag on VP's own page as the
// fallback (Dashy expects the endpoint to be withdrawn at some point).
// Endpoints and behaviour are documented in docs/vp-sync.md.

export const VP_ORIGIN = "https://volunteers.bader.mod.uk";

const RETRY_STATUSES = new Set([429, 502, 503, 504]);
const MAX_RETRIES = 4;
const PAGE_SIZE = 1000;

/** VP answered, but not as a signed-in user. Stops the whole sync. */
export class NotSignedInError extends Error {}

/** VP refused this endpoint for this user (their VP permissions don't cover it). */
export class NoPermissionError extends Error {}

let csrfToken = null;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function fetchToken() {
  const res = await fetch(`${VP_ORIGIN}/api/antiforgery/token`, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (res.status === 401 || res.status === 403) throw new NotSignedInError("Not signed in to the Volunteer Portal.");
  const body = await res.text();
  if (res.ok && /^\s*\{/.test(body)) {
    const token = JSON.parse(body).token;
    if (typeof token === "string" && token.trim()) return token.trim();
  }
  // Fall back to the token VP puts in its own page. A service worker has no
  // DOMParser, hence the regex.
  const page = await fetch(`${VP_ORIGIN}/`, { credentials: "include" });
  const html = await page.text();
  const meta = html.match(/<meta[^>]+name=["']csrf-token["'][^>]*content=["']([^"']+)["']/i)
    || html.match(/<meta[^>]+content=["']([^"']+)["'][^>]*name=["']csrf-token["']/i);
  if (meta) return meta[1];
  throw new NotSignedInError("Couldn't get a VP session token. Open volunteers.bader.mod.uk, sign in, then sync again.");
}

/**
 * GET a VP API path and parse the JSON. 404 means "nothing recorded" and gives
 * null. Busy/gateway errors are retried with backoff, since VP is shared and we
 * don't want to lean on it.
 */
export async function vpGet(path) {
  let refreshedToken = false;
  for (let attempt = 0; ; attempt += 1) {
    if (!csrfToken) csrfToken = await fetchToken();
    // Some VP responses carry their own links (e.g. an exam's resultsUri), which
    // may be absolute. Only VP's origin is ever fetched either way.
    const url = new URL(path, `${VP_ORIGIN}/`);
    if (url.origin !== VP_ORIGIN) throw new Error(`Refusing non-VP URL ${url}`);
    const res = await fetch(url, {
      credentials: "include",
      headers: { Accept: "application/json", "X-CSRF-TOKEN": csrfToken },
    });
    // A stale token comes back as 400. Get a fresh one, once.
    if (res.status === 400 && !refreshedToken) {
      csrfToken = null;
      refreshedToken = true;
      continue;
    }
    if (RETRY_STATUSES.has(res.status) && attempt < MAX_RETRIES) {
      await sleep(500 * 2 ** attempt + Math.random() * 250);
      continue;
    }
    if (res.status === 401) throw new NotSignedInError("Not signed in to the Volunteer Portal.");
    if (res.status === 403) throw new NoPermissionError(`VP denied ${path}`);
    if (res.status === 404) return null;
    const body = await res.text();
    if (!res.ok) throw new Error(`VP ${res.status} for ${path}`);
    // A lapsed session shows up as VP's sign-in page with a 200, not a 401.
    if (!/^\s*[[{]/.test(body)) throw new NotSignedInError("VP returned its sign-in page. Sign in again, then sync.");
    return JSON.parse(body);
  }
}

/** Pull the rows out of a VP list response: either a bare array or a page envelope. */
export function rowsOf(data) {
  if (Array.isArray(data)) return data.filter((r) => r && typeof r === "object");
  if (!data || typeof data !== "object") return [];
  for (const key of ["data", "items", "results", "rows", "registers"]) {
    if (Array.isArray(data[key])) return data[key].filter((r) => r && typeof r === "object");
  }
  return [];
}

/** Every row of a paged VP list (api/persons/cadets, api/persons/staff). */
export async function vpGetAll(path) {
  const rows = [];
  for (let page = 1; ; page += 1) {
    const data = await vpGet(`${path}?pageNumber=${page}&pageSize=${PAGE_SIZE}&sortBy=familyName&sortDesc=false`);
    const batch = rowsOf(data);
    rows.push(...batch);
    const totalPages = data && typeof data === "object" ? data.totalPages ?? data.pageCount : undefined;
    if (typeof totalPages === "number" ? page >= totalPages : batch.length < PAGE_SIZE) return rows;
  }
}

export function resetSession() {
  csrfToken = null;
}
