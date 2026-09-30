# Volunteer Portal sync

RAFAC runs two systems. **SMS** (`sms.bader.mod.uk`) is the old one, and the
Playwright scrapers in `app/scripts/` already read it. The **Volunteer Portal
(VP)** (`volunteers.bader.mod.uk`) is the newer one. It holds data SMS doesn't
have, but it sits behind Microsoft (Entra) sign-in with MFA, so the scrapers
can't log in to it.

This doc covers what VP exposes, where that knowledge came from, and how the
VP sync gets VP data into the 317 SMS database without a server-side login.

## Where this came from

Everything here was worked out by reading **Dashy!**, a RAFAC Chrome
extension that builds dashboards from VP data using the signed-in user's
session. Only its built bundle was available, not its source. The TypeScript
source it refers to (`src/data/extensionFetch.ts` and the `import:events` /
`import:qualifications-map` scripts) is not public. None of this API is
documented by RAFAC, so it's internal and can change without notice.

## How VP's API works

- **Base URL:** `https://volunteers.bader.mod.uk/`. VP's own frontend calls a
  JSON API under `/api/`.
- **Auth:** the browser's VP session cookie. There's no API key or OAuth
  client, so anything calling it has to run in a browser that's signed in.
- **CSRF:** every request sends an `X-CSRF-TOKEN` header. It comes from
  `GET api/antiforgery/token`, which returns `{ "token": "…" }`. The fallback
  is the `<meta name="csrf-token">` tag on VP's page (Dashy expects the
  endpoint to be withdrawn). A 400 usually means the token is stale: fetch a
  new one and retry once.
- **Signed out:** you get a 401/403, or a 200 that is VP's HTML sign-in page.
  Check that the body starts with `{` or `[`.
- **Busy:** retry 429/502/503/504 with backoff.
- **IDs:** people are keyed on `personnelWebId`, which is what the per-person
  URLs use. The **CIN** is the `computerNumber` field on roster rows.
- **Paging:** list endpoints take
  `?pageNumber=N&pageSize=1000&sortBy=familyName&sortDesc=false`. Rows sit in
  `items` / `data` / `results` / `rows`, alongside `totalPages` or
  `totalRows`. Some endpoints return a bare array.

### Endpoints Dashy uses

| Endpoint | What | In SMS already? | Synced? |
|---|---|---|---|
| `api/persons/cadets`, `api/persons/staff` (paged) | Roster | Yes | Yes, as the roster (trimmed fields) |
| `api/person/{id}/learning/history` | E-learning / Learn courses | No | **Yes** (`learning`) |
| `api/person/{id}/agreements` | Code of conduct agreements | No | **Yes** (`agreements`) |
| `api/shootingmanagement/{id}/whts` | Weapon handling tests | No | **Yes** (`whts`) |
| `api/shootingmanagement/{id}/shootinglog` | Range / shooting log | No | **Yes** (`shooting_log`) |
| `api/fieldcraftmanagement/{id}/completions` | Fieldcraft lessons done | No | **Yes** (`fieldcraft`) |
| `api/fieldcraftmanagement/getLessons` | Fieldcraft lesson catalogue | No | Not yet (reference data) |
| `api/person/{id}/aviation/history` | Flying / gliding sorties | No | **Yes** (`aviation`) |
| `api/trackers/mandatorytraining` | Mandatory training per person (`passedAll`, `mandatoryTraining[]`) | No | **Yes** (`mandatory_training`), split per person |
| `api/exams/management/subjects` | Classification exam list (with `enrolmentsUri` / `resultsUri`) | No | Used to reach results |
| `api/exams/management/subjects/{examId}/results` | Exam results | No | **Yes** (`exam_results`), grouped per person |
| `api/exams/management/subjects/{examId}/enrolments` | Exam enrolments | No | No |
| `api/person/{id}/classification` | Classification | Yes (report scrape) | No |
| `api/person/{id}/qualifications/history` | Qualifications | Yes (quali scraper) | No |
| `api/person/{id}/events/history` | Events attended | Yes (event scraper) | No |
| `api/attendance/registers` (+ `/{id}/cadets`, `/{id}/staff`) | Registers | Yes (attendance scrape) | No |
| `api/person/{id}`, `/summary`, `/service/unithistory`, `/service/supernumeraryunithistory` | Profile, service history | Mostly | No |
| `api/person/{id}/contactdetails`, `/contacts` | Contact details, next of kin | Partly | **No, deliberately** |
| `api/person/{id}/security/dbs`, `/security/bpss` | DBS / BPSS vetting | No | **No, deliberately** |
| `api/securitymanagement/profile/assignedpermissions`, `/delegatepermissions` | The signed-in user's VP permissions | n/a | No |
| `POST api/exams/management/subjects/{examId}/enrolments` `{examId, actions: {personnelId: bool}}` | Enrol or unenrol for an exam | n/a | **Never**: the sync is read-only |

These are only the endpoints Dashy happens to call. VP almost certainly has
more. To find them, browse VP with DevTools' Network tab open and filter on
`api/`.

### Dashy's SharePoint feeds

Dashy also loads a few JSON exports that a wing staff member produces and keeps
on their OneDrive (`rafac-my.sharepoint.com`, with `&download=1` share links).
These are the qualifications map, and SMS "owned" and "open" unit events. It
also reads the Hants & IOW Wing Calendar SharePoint list. None of this is 317
data, and the event exports come from SMS, which we already scrape. It isn't
needed here.

## The design

The scrapers can't sign in to VP, so a staff member's browser does the VP part
and the API stores the result:

```
Chrome, signed in to VP (MFA done by a person) and to 317 SMS
  └─ vp-sync-extension/
       ├─ GET  sms.317atc.co.uk/api/auth/session   → Google id_token (the SMS login)
       ├─ GET  volunteers.bader.mod.uk/api/...     → session cookie + X-CSRF-TOKEN
       └─ POST smsapi.317atc.co.uk/vp-sync/...     → Authorization: Bearer <id_token>
                └─ k3s: Traefik on oracle → sms-api → CloudNativePG
                     VP_People, VP_Records
```

**Getting data into the cluster.** No infrastructure change is needed.
`smsapi.317atc.co.uk` is already public through Traefik on oracle, with a Let's
Encrypt certificate (`deploy/base/ingress.yaml`, `k3s-homelab`
`clusters/prod/traefik.yaml`). So the extension posts to the same host the
Vercel site uses. The new tables arrive through the normal migrate job
(`alembic upgrade head`) when this branch deploys. The dev stack is at
`smsapi-dev.317atc.co.uk` and is set in the extension's settings.

**Auth.** The extension reuses the Google `id_token` the SMS site's NextAuth
session already exposes (`auth.config.ts` puts it on the session). The API
checks it like any other request, and every endpoint needs **staff**. So the
extension has no secret of its own, and each sync is recorded against the
person who ran it (`synced_by`).

**Guards, enforced by the API:**

- **Dataset allow-list** (`DATASETS` in `app/routers/vp_sync.py`). Anything
  else gets a 422, so DBS, contact details and next of kin can't be stored,
  even by a modified extension.
- **317 only.** A person is accepted only if their CIN is already in `Cadets`
  or `Staff`, which the SMS scrapers maintain. If someone's VP role covers the
  whole wing, the wing still doesn't come in. Someone new in VP but not yet
  scraped from SMS is skipped until the next SMS scrape.
- **Roster fields are whitelisted** (`PROFILE_FIELDS`). Date of birth and
  similar fields are dropped. The extension trims them too, so they never
  leave the browser.
- **Pruning.** Anyone missing from a new roster is deleted along with their
  records. An empty roster, or one where nobody matches the SMS roster, is
  refused, so a bad run can't wipe the tables.

**Storage.** `VP_People` holds one row per person (keyed on `personnel_web_id`,
with `cin`). `VP_Records` holds one row per person per dataset, containing the
**raw JSON** VP returned. The response shapes are only partly known, so storing
the whole payload means nothing is lost while the parsing gets written. Once
real payloads have been looked at, the useful datasets can get typed tables or
parsing into the existing views, the same way scraped data does.

### API

All endpoints need staff.

| Method | Path | |
|---|---|---|
| `POST` | `/vp-sync/people` | `{people: [{personnelWebId, cin, personType: "cadet"\|"staff", profile}]}`. Replaces the roster and returns `acceptedIds` |
| `POST` | `/vp-sync/records` | `{records: [{personnelWebId, dataset, payload}]}`. Upserts, 1000 max per request |
| `GET` | `/vp-sync/status` | Head count, last roster sync, and per-dataset counts and times |
| `GET` | `/vp-sync/people` | Everyone synced, with which datasets each person has (no payloads) |
| `GET` | `/vp-sync/people/{cin}` | Everything synced for one person |

### The extension

`vp-sync-extension/` is plain MV3 with no build step.

- `vp.js`: the VP client (CSRF, retries, paging, sign-in detection).
- `sync.js`: a single run. It reads the roster, filters it to `unitFilter`
  (default `317`) and posts it. It then reads mandatory training and exam
  results, then the six per-person datasets for the accepted people, three at
  a time, and uploads in batches of 100.
  - If VP returns 403 for a dataset, the user's VP role can't see it, so the
    run stops asking for it.
  - Any other failure keeps the stored copy rather than overwriting it with
    nothing.
- `background.js`: "Sync now" from the popup, plus a `chrome.alarms` schedule
  (default every 6 hours) while Chrome is open. Only one run happens at a
  time.
- `popup.html` / `popup.js`: status, the last run's summary, and settings.

**Installing (for now):** go to `chrome://extensions`, turn on Developer mode,
choose **Load unpacked**, and pick `vp-sync-extension/`. Sign in to VP and to
317 SMS in the same Chrome profile, then click **Sync now**. For the dev stack,
set the API to `https://smsapi-dev.317atc.co.uk` and the site to the dev
site's URL in Settings.

**Freshness.** Data is only as fresh as the last time someone with the
extension had Chrome open and was signed in to both. One or two staff running
it keeps it current. `/vp-sync/status` shows when and by whom.

### The bookmarklet (no install)

The alternative to the extension is a bookmarklet, which lives in the SMS site
(`317_SMS_Site`, branch `vp-sync`). The SMS site can't call VP itself: VP
answers cross-origin preflights with no `Access-Control-Allow-*` headers
(checked 2026-09-30), and its session cookie isn't sent cross-site. Code
running on VP's own page is same-origin, though, so:

1. On the SMS site's **VP Sync** page (`/tools/vp-sync`, staff only), drag the
   **317 VP Sync** bookmark to the bookmarks bar.
2. On a signed-in VP tab, click it. The bookmark opens a popup of
   `/tools/vp-sync?receive=1` (it opens during the click, so pop-up blockers
   allow it), then loads `public/vp-sync-collector.js` from the SMS site into
   the VP tab.
3. The collector reads VP the same way `sync.js` does and sends each batch to
   the popup with `postMessage`. Messages go only to the SMS origin, and the
   popup only accepts them from VP's origin and from the window that opened
   it. The popup is signed in to SMS, so it posts to `/vp-sync/*` as the user.

The same page shows sync status and lets you browse the raw synced payloads for
each person (`GET /vp-sync/people`, `GET /vp-sync/people/{cin}`).

**Trade-offs compared with the extension:**
- Nothing to install, and it works in any desktop browser.
- It's manual only, with no schedule.
- VP could block it by adding a Content-Security-Policy. VP had none on
  2026-09-30; a manual `fetch('/api/antiforgery/token')` from VP's console
  returned the token.
- The VP-reading logic is copied between `sync.js`/`vp.js` and the collector.
  Pick one route and drop the other, or keep both in step.

**Checked 2026-09-30:** on the first account tested `api/persons/cadets` returned **403**,
so that account lacks *Cadet Details – View*. Both routes now report the
missing permission by name.

## Before relying on this

1. **Get it agreed.** Copying VP personal data into our own database outside
   MOD systems is a different situation from Dashy, which only displays it in
   the browser. Get OC (and probably wing) agreement, and keep the allow-list
   to what's actually used.
2. **Capture real payloads.** Nobody has seen these responses from 317's
   account yet. Run a sync against dev and look at `/vp-sync/people/{cin}` for
   a few people. Then decide what to parse and show in the SMS site: the next
   piece of work is a VP section on the cadet and staff pages.
3. **Check the endpoints hold up.** Some VP endpoints may be permission-gated
   for your role. The popup lists anything VP refused.
4. **Consider an official route.** It's worth asking the RAFAC/VP team whether
   an export or API exists, and Dashy's author how they got theirs cleared.

## Not done yet

- Nothing in the SMS site reads this data yet (the frontend work is item 2
  above).
- Sensitive fields (DBS/BPSS expiry, contacts) could be shown **live** without
  storing them: the SMS site asks the extension (`externally_connectable`),
  and the extension fetches from VP on demand.
- `api/fieldcraftmanagement/getLessons` would give readable names for the
  fieldcraft completions.
- No automated tests for the extension beyond a mocked smoke run. The API
  side is covered by `app/routers/test_vp_sync.py`.
