# Volunteer Portal (Bader) JSON API

The newer Bader front end at `https://volunteers.bader.mod.uk` is backed by a
JSON API under `/api/`. Our scrapers in `app/scripts/` drive the old
`sms.bader.mod.uk` ASP.NET pages with Playwright and parse HTML. Much of the
same data, and some that SMS doesn't show, can be read straight from this API.

This list comes from reading the **RAFAC Dashy!** Chrome extension (v0.1.27, a
third-party staff dashboard), which calls these routes from the signed-in
user's browser. Nobody publishes or documents this API, so:

- Routes and field names are what Dashy calls and reads. Responses probably
  carry more fields than are listed here.
- Field lists are taken from the parsing code. Where Dashy accepts several
  spellings (`registerId` / `id`), the first one is probably the real name.
  Check against a live response before you rely on it.
- Bader can change any of this without warning. Any code built on it should
  fail loudly when that happens.

## Calling it

All paths are relative to `https://volunteers.bader.mod.uk/`.

**Auth is the browser session cookie.** Dashy has no token flow of its own. It
sends every request with `credentials: "include"` from a content script running
on a `volunteers.bader.mod.uk` tab, so the user's Bader sign-in cookie goes with
it. A server-side client would have to sign in (e.g. with Playwright, as
`scraper_utils.py` does for SMS) and reuse that context's cookies.

**Every request needs `X-CSRF-TOKEN`.** Get it from:

```
GET /api/antiforgery/token      ->  { "token": "..." }
```

If that endpoint doesn't return a token, fall back to the page's
`<meta name="csrf-token" content="...">`. Dashy's comments expect access to the
endpoint to be withdrawn at some point. Dashy caches the token and fetches a
new one after the first `400`.

**Mutations (POST) also need** `RequestVerificationToken` (the ASP.NET
antiforgery token from `<meta name="RequestVerificationToken">` or the hidden
`<input name="__RequestVerificationToken">` on a portal page such as `/exams`),
plus `X-Requested-With: XMLHttpRequest` and
`Content-Type: application/json`.

**Signed-out responses** come back as `401`/`403`, or as `200` with an HTML
sign-in page. Treat any body that isn't JSON as "not authenticated".

**Retries.** Dashy retries `429`, `502`, `503` and `504` up to 4 times with
exponential backoff (500 ms × 2^n plus jitter). It runs per-person fetches
through a small concurrency pool.

**Permissions.** Some routes need specific Bader permissions (see
[Security / permissions](#security--permissions)). Without them you get
`401`/`403`, and Dashy stops calling that route for the rest of the session.

**Response envelopes vary.** List endpoints return either a bare array or an
object that wraps the rows in one of `data` / `items` / `results` / `rows` /
`registers`. Paging metadata, when present, is in `totalRows` (or `totalCount` /
`total` / `count`), `totalPages` (or `pageCount`), `pageNumber` (or `page`) and
`pageSize` (or `perPage`). Parse defensively.

**IDs.** Two identifiers show up:

- `personnelWebId`: string id used in every `api/person/{id}/...` path.
- `personnelId`: numeric id used in exam enrolment and permissions.

People also have a `computerNumber` (service number).

## Endpoint index

| Method | Path | What it gives |
| --- | --- | --- |
| GET | `api/antiforgery/token` | CSRF token |
| GET | `api/securitymanagement/profile/assignedpermissions` | Your own permission ids + your `personnelId` |
| GET | `api/securitymanagement/profile/delegatepermissions` | Delegated permission ids |
| GET | `api/persons/cadets` | Every cadet you can see (paged) |
| GET | `api/persons/staff` | Every staff member / CFAV you can see (paged) |
| GET | `api/person/{id}` | Core personal details |
| GET | `api/person/{id}/summary` | Profile header: name, wing, region, avatar |
| GET | `api/person/{id}/contactdetails` | Phone and email |
| GET | `api/person/{id}/contacts` | Contacts, including next of kin |
| GET | `api/person/{id}/service/unithistory` | Primary unit postings |
| GET | `api/person/{id}/service/supernumeraryunithistory` | Supernumerary postings |
| GET | `api/person/{id}/qualifications/history` | Qualifications |
| GET | `api/person/{id}/classification` | Cadet classification dates |
| GET | `api/person/{id}/learning/history` | e-learning modules (mandatory training) |
| GET | `api/person/{id}/agreements` | Code of conduct / agreements |
| GET | `api/person/{id}/events/history` | Events applied for / attended |
| GET | `api/person/{id}/aviation/history` | Flying sorties (powered + gliding) |
| GET | `api/person/{id}/security/dbs` | DBS checks |
| GET | `api/person/{id}/security/bpss` | BPSS checks |
| GET | `api/shootingmanagement/{id}/whts` | Weapon handling tests |
| GET | `api/shootingmanagement/{id}/shootinglog` | Range shooting record |
| GET | `api/fieldcraftmanagement/getLessons` | Fieldcraft lesson catalogue |
| GET | `api/fieldcraftmanagement/{id}/completions` | Fieldcraft lessons a person completed |
| GET | `api/trackers/mandatorytraining` | Unit-wide mandatory training tracker |
| GET | `api/exams/management/subjects` | Classification exam subjects |
| GET | `api/exams/management/subjects/{examId}/enrolments` | Who is enrolled in an exam |
| GET | `api/exams/management/subjects/{examId}/results` | Exam results |
| POST | `api/exams/management/subjects/{examId}/enrolments` | Enrol / un-enrol cadets |
| GET | `api/attendance/registers` | Parade-night registers (paged) |
| GET | `api/attendance/registers/{registerId}/cadets` | Cadet marks for one register |
| GET | `api/attendance/registers/{registerId}/staff` | Staff marks for one register |

`{id}` is a `personnelWebId` unless stated otherwise.

## People

### `GET api/persons/cadets` and `GET api/persons/staff`

Everyone in the units the signed-in user can see. Paged. Dashy requests

```
?pageNumber={n}&pageSize=1000&sortBy=familyName&sortDesc=false
```

and keeps going until `totalPages` (or until a page comes back short).

Row fields Dashy uses: `personnelWebId`, `computerNumber`, `familyName`,
`givenName`, `rankAbbreviation`, `unitName`, `sectorName`, `activeStatusType`
(or `status`), `emailAddress`, `wing`. Someone holding appointments in two
units appears once per unit. Dashy merges those duplicates.

### `GET api/person/{id}`

Fields used: `gender`, `nationality`, `dateOfBirth`, `serviceJoinDate`.

### `GET api/person/{id}/summary`

Fields used: `fullName`, `serviceNumber`, `activeStatusType`, `wing`, `region`,
`phoneNumber`, `avatarUrl`.

### `GET api/person/{id}/contactdetails`

Object. Fields used: `primaryPhone`, `primaryEmail`, `baderEmail`.

### `GET api/person/{id}/contacts`

Array of contacts. Dashy takes the first with `isNextOfKin: true` and reads
`fullName` (or `title` + `givenName` + `familyName`), `relationship`, and
`contactDetails.primaryPhone` / `contactDetails.primaryEmail`.

### `GET api/person/{id}/service/unithistory` and `.../supernumeraryunithistory`

Array (or `{ items: [...] }`) of postings: `unitName`, `post` (appointment
title), `isPrimaryUnit`, `startDate`, `endDate` (empty means current). Dashy
uses the earliest `startDate` in the primary history as the join date.

## Training and qualifications

### `GET api/person/{id}/qualifications/history`

Array of qualifications: `name`, `status` (`"Current"` / expired …),
`dateAwarded`, `dateExpires`, `weaponCategory` on shooting quals.

### `GET api/person/{id}/classification`

Object of classification milestone dates: `probationerDate`,
`firstClassPart1Passed`, `firstClassPart2Passed`, `firstClassPart3Passed`,
`leadingCadetPassed`, `seniorCadetPassed`, `masterAirCadetPassed`.

### `GET api/person/{id}/learning/history`

Array (or wrapped) of e-learning modules: `title`, `satisfied` (bool),
`dateLastAccessed`, `validity` (years). Dashy builds mandatory-training
compliance (safeguarding, heat/cold injury, first aid, etc.) from these.

### `GET api/person/{id}/agreements`

Array: `agreementType`, `title`, `name`, `status`, and a signed flag under one
of several names (`satisfied`, `signed`, `accepted`, `isSigned`, `isAccepted`,
`agreed`). The signed date likewise appears as one of `dateAccepted`,
`signedDate`, `dateSigned`, `acceptedDate`, `dateAgreed` or `date`. Used for
code of conduct compliance.

### `GET api/trackers/mandatorytraining`

One call that returns mandatory training for the whole unit, much cheaper than
fetching learning history person by person. Array (or `{ rows }`) of people:

- `personnelWebId`, `serviceNumber`, `familyName`, `givenName`, `rank`,
  `personnelType`, `unitName`
- `passedAll`, `passedAllMandatory` (bools)
- `mandatoryTraining[]`: `criteriaId`, `displayName`, `statusValue` (number),
  `mandatory`, `dateAchieved`, `dateExpires`

### `GET api/fieldcraftmanagement/getLessons`

Catalogue array: `lessonId`, `badgeLevel`, `sortOrder`, `reference`, `title`,
`isSubLesson`.

### `GET api/fieldcraftmanagement/{id}/completions`

Array (or `{ completions: [...] }`): `lessonCompletionId`, `lessonId`,
`badgeLevel`, `reference`, `title`, `completedDate`, `deliveredBy`,
`recordedBy`, `recordedAt`.

## Shooting

### `GET api/shootingmanagement/{id}/whts`

Object whose `whTs` array holds weapon handling tests: `weaponCategory`,
`status`, `whtDateExpires` (`0001-01-01…` means none). Needs Shooting
Management permission, and Dashy shows a banner when it's denied.

### `GET api/shootingmanagement/{id}/shootinglog`

Array of range shoots: `weaponCategory`, `shootingPractice`,
`shootingRecordOutcomeType` (pass/fail), `rangeDate`, `actualScore`,
`highestPossibleScore`, `roundsFired`, `rangeOfficer`, `location`,
`rangeDistance`.

## Events and flying

### `GET api/person/{id}/events/history`

Array of events: `eventId`, `eventReference`, `title`, `startDate`, `endDate`,
`category`, `eventLocation`, `attendanceStatus` (`"Attended"` counts as
attended). Dashy uses it for squadron event stats and to flag parade nights
marked absent when the cadet was away on an event.

### `GET api/person/{id}/aviation/history`

Object `{ powered: [...], gliding: [...] }`. Each is an array of groups
`{ groupName, entries: [...] }`, and each entry has `date`, `isPowered`,
`aefCategory`, `type` (aircraft), `tailNumber`, `duty`, `sortie`, `pilot`,
`flyingUnit`, `durationMinutes`. Needs the aviation logs permission.

## Attendance

### `GET api/attendance/registers`

Parade registers, newest first:

```
?currentPage={n}&perPage=25&sortBy=registerDate&sortDirection=D
```

Row fields: `registerId`, `registerDate`, `unitId`, `unitName`,
`unitRegisterType`, `cadetStrength`, `cadetsPresent`, `staffStrength`,
`staffPresent`, plus `totalRows` for paging. Dashy pages back until it passes
its history window (months), then filters to the selected units by `unitId`.
Needs the unit registers permission.

### `GET api/attendance/registers/{registerId}/cadets` and `.../staff`

One row per person on that register: `personnelId`, `personnelWebId`,
`fullNameRank` (or `familyName` / `givenName` / `rankAbbreviation`), `flight`,
`attendanceCode` (e.g. `A`, `AA`, …), `attendanceStatus`, `absenceReason`.

## Exams (classification)

### `GET api/exams/management/subjects`

Array (or wrapped) of subjects: `classificationExamId`, `cadetClassification`,
`courseName`, `enrolmentsUri`, `resultsUri`. When the two `*Uri` fields are
present, they are the paths to call for that subject's enrolments and results.

### `GET api/exams/management/subjects/{examId}/enrolments`

Rows: `learnClassificationExamId`, `personnelId`, `computerNumber`,
`rankAbbreviation`, `familyNameGivenName`, `unitName`, `enrolled` (bool),
`dateEnrolled` (`0001-01-01…` means never).

### `GET api/exams/management/subjects/{examId}/results`

Rows: `learnCourseId`, `personnelWebId`, `rankAbbreviation`,
`familyNameGivenName`, `courseName`, `status` (number), `updatedDate`.

### `POST api/exams/management/subjects/{examId}/enrolments`

The only write Dashy makes. The body maps `personnelId` to enrol (`true`) or
un-enrol (`false`):

```json
{ "examId": "<examId>", "actions": { "123456": true } }
```

Needs the mutation headers described in [Calling it](#calling-it). The response
lists the outcome per person, and Dashy checks it to confirm the change landed.

## Security / permissions

### `GET api/person/{id}/security/dbs` and `.../security/bpss`

Shape varies: an object, an array, or an array wrapped in
`items` / `dbs` / `bpss` / `records` / `results` / `data` / `value`. Dashy keeps
the newest record and reads:

- DBS: the disclosure number, from one of `reference`, `disclosureNumber`,
  `certificateNumber`, `dbsNumber`, …, and the issue date, from one of
  `dateValidFrom`, `dateOfCheck`, `issueDate`, `dateIssued`, ….
- BPSS: the check date, from the same set of date fields.

Id fields seen: `vettingDBSId`, `vettingDBSTypesId`, `vettingBpssId`.

### `GET api/securitymanagement/profile/assignedpermissions` and `.../delegatepermissions`

Arrays of `{ permissionId, personnelId }`. Dashy combines the two into the
signed-in user's permission id set and `personnelId`, then skips any route the
user can't access:

| Route | Permission (Dashy's name) |
| --- | --- |
| `api/person/{id}/aviation/history` | `AVIATION_LOGS_VIEW` |
| `api/exams/management/subjects/{id}/results` | `LEARN_RESULTS_VIEW` |
| `api/exams/management/subjects/{id}/enrolments` | `LEARN_ENROLMENTS_VIEW` |
| `api/attendance/registers` (+ `/cadets`, `/staff`) | `UNIT_REGISTERS` |

Dashy tells users the portal permissions "Cadet Details – View" and "Staff
Details – View" are needed for most of the data. Users manage these at
`https://volunteers.bader.mod.uk/UserPermissions`.

## Non-Bader sources Dashy also uses

- `https://rafac.sharepoint.com/sites/HIOW/_api/web/lists/getbytitle('Wing%20Calendar')/items`:
  the HIOW wing calendar SharePoint list, fetched the same way through a tab on
  that origin.
- `https://www.gov.uk/bank-holidays.json`: UK bank holidays, public, no auth.

## Portal web pages (not API)

These are useful for linking users into the portal:

- `https://volunteers.bader.mod.uk/person/{personnelWebId}/classification`
- `https://volunteers.bader.mod.uk/exams`
- `https://volunteers.bader.mod.uk/UserPermissions`
