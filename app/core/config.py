"""Central place for env vars and constants shared across the API."""

import os

from dotenv import load_dotenv

load_dotenv()

# Which deployment this is. Only an explicit ENV=dev unlocks the dev-only
# surfaces (the interactive API docs); an unset, misspelt or unexpected value
# is treated as production, so a config slip can never publish them.
ENV = os.getenv("ENV", "prod").lower()
IS_DEV = ENV == "dev"
IS_PROD = not IS_DEV

# The fake-token bypass in core.security accepts the literal string
# "Bearer dev-fake-token" as the owner account. On a Funnel-published API that
# is a full authentication bypass for anyone who reads the source, so refuse to
# start rather than serve with it live. Raised at import so it catches every
# entrypoint — uvicorn, alembic and the standalone scripts alike.
if IS_PROD and os.getenv("DEV_FAKE_AUTH") == "1":
    raise RuntimeError(
        f"DEV_FAKE_AUTH=1 with ENV={ENV}. The dev auth bypass must never be "
        "enabled outside ENV=dev. Refusing to start."
    )

# Google OAuth client used by both the SMS site and the cadet portal. ID tokens
# are only accepted if they were issued to this client.
GOOGLE_CLIENT_ID = os.getenv(
    "GOOGLE_CLIENT_ID", "490734276503-9s44s89sdhgct8ismqnsm7s1d4v6e4uv.apps.googleusercontent.com"
)

# Only tokens from this Google Workspace are accepted — outside Google accounts
# (personal Gmail etc.) are rejected before any role check.
GOOGLE_DOMAIN = os.getenv("GOOGLE_DOMAIN", "317atc.co.uk")

# Google Workspace groups that decide roles. The SMS site resolves roles from
# the same groups (auth.ts), so change both together.
STAFF_GROUP = os.getenv("STAFF_GROUP", f"staff@{GOOGLE_DOMAIN}")
NOTIFY_GROUP = os.getenv("NOTIFY_GROUP", f"notifications@{GOOGLE_DOMAIN}")
SNCO_GROUP = os.getenv("SNCO_GROUP", f"snco@{GOOGLE_DOMAIN}")
NCO_GROUP = os.getenv("NCO_GROUP", f"ncoteam@{GOOGLE_DOMAIN}")

# Sole owner/maintainer — has access to developer-only views (e.g. backups, usage)
OWNER_EMAIL = os.getenv("OWNER_EMAIL", f"ci.mcdonald@{GOOGLE_DOMAIN}")

# Officer Commanding — gates the OC dashboard and the committee-request approval
# actions (send-to-committee / approve / reject / mark-paid). Identified by email
# rather than a Google group since there is only ever one OC.
OC_EMAIL = os.getenv("OC_EMAIL", "")
# Where committee purchase requests and payment requests are emailed.
COMMITTEE_EMAIL = os.getenv("COMMITTEE_EMAIL", "")

# Service account used for the admin directory lookups and sending email
SA_EMAIL = os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL")
SA_PRIVATE_KEY = os.getenv("GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY", "").replace("\\n", "\n").strip('"')
IMPERSONATE_EMAIL = os.getenv("GOOGLE_IMPERSONATE_EMAIL", OWNER_EMAIL)
NOREPLY_EMAIL = os.getenv("NOREPLY_EMAIL")

# Pre-shared key for the Google Form uniform order importer
UNIFORM_FORM_API_KEY = os.getenv("UNIFORM_FORM_API_KEY")

# GitHub repos we commit content into
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = "BenMcD23/cadet-website"
GITHUB_BRANCH = "master"

NEWSLETTER_REPO = "BenMcD23/317_Newsletter"
NEWSLETTER_BRANCH = "development"
NEWSLETTER_JSON_PATH = "317_newsletter/lib/newsletters.json"

PROGRAMME_APPS_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbyqQbEdYxu53ARNzfcxdcm9cRieRVBC3cJ_TtdGVbpPQaMfpzD3XkreSmNSnJaHe1pM/exec"

# Parade-night texts — programme doc lives in year subfolders of this Drive folder
PROGRAMME_DRIVE_FOLDER_ID = "1sg1yemPOD_P3GIj9lwy3ArJ3c2pRmFo6"

# Database backups — gzipped pg_dump files are uploaded to this Shared Drive
# folder. The scheduled job only runs when DB_BACKUP_ENABLED is true (set in the
# prod .env), but the owner-only /backups endpoints work whenever the folder is
# configured.
DB_BACKUP_ENABLED = os.getenv("DB_BACKUP_ENABLED", "false").lower() == "true"
DB_BACKUP_DRIVE_FOLDER_ID = os.getenv(
    "DB_BACKUP_DRIVE_FOLDER_ID", "1Bi5CmjUVObZfarx2FUqECNJvBg3R1MeQ"
)
DB_BACKUP_RETENTION = int(os.getenv("DB_BACKUP_RETENTION", "14"))

# How long audit rows are kept. Deliberately much longer than the 182-day
# order/assessment cleanups — a safeguarding trail should outlive the records it
# describes. Squadron policy call, so it's overridable.
AUDIT_RETENTION_DAYS = int(os.getenv("AUDIT_RETENTION_DAYS", "365"))

# Shared "NCO Holidays" Google Calendar — booked NCO holidays are written here
# as all-day events so staff can overlay them on the squadron's other calendars.
# Share the calendar with the service account (or with IMPERSONATE_EMAIL, who it
# acts as) with "Make changes to events" before setting this. Left unset, the
# bookings still save; they just aren't pushed to Calendar.
NCO_HOLIDAY_CALENDAR_ID = os.getenv("NCO_HOLIDAY_CALENDAR_ID", "")

# Where the SMS site is served from — used to link straight to a page from an
# email (e.g. the session plan awaiting review) instead of "log in and find it".
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "https://sms.317atc.co.uk").rstrip("/")

# The public joining form on the squadron website. A recruit's link is this plus
# their token; parents fill it in at home before their first parade night.
RECRUIT_FORM_URL = os.getenv("RECRUIT_FORM_URL", "https://www.317atc.co.uk/enrol").rstrip("/")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NOTIFY_API_KEY = os.getenv("NOTIFY_API_KEY")
NOTIFY_SMS_TEMPLATE_ID = os.getenv("NOTIFY_SMS_TEMPLATE_ID")
ALERT_EMAIL = os.getenv("ALERT_EMAIL", NOTIFY_GROUP)
QUALI_EXPIRY_ALERT_EMAIL = os.getenv("QUALI_EXPIRY_ALERT_EMAIL", NOTIFY_GROUP)
BAN_ALERT_EMAIL = os.getenv("BAN_ALERT_EMAIL", NOTIFY_GROUP)
# Who gets told when an NCO submits a session plan for approval. Any staff
# member can then review it, so this defaults to the shared notifications group
# rather than one person.
SESSION_PLAN_ALERT_EMAIL = os.getenv("SESSION_PLAN_ALERT_EMAIL", NOTIFY_GROUP)
# Who gets a cadet's DofE activity log PDF when they finish a section.
ACTIVITY_LOG_ALERT_EMAIL = os.getenv("ACTIVITY_LOG_ALERT_EMAIL", NOTIFY_GROUP)
# The adjutant, who removes a cadet from the squadron. The OC dashboard's
# "Request removal" button emails this address. Unset = the button refuses
# rather than silently dropping the request.
ADJUTANT_EMAIL = os.getenv("ADJUTANT_EMAIL", "")

# Browser origins allowed to call the API directly (the SMS site does, and the
# squadron website's joining form; the cadet portal proxies through its own
# server). Comma-separated; localhost is added for local development when
# CORS_ALLOW_LOCALHOST is true (3000 the SMS site, 5173 the website).
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "CORS_ORIGINS",
        "https://sms.317atc.co.uk,https://317-sms-site.vercel.app,https://www.317atc.co.uk,https://317atc.co.uk",
    ).split(",")
    if o.strip()
]
if os.getenv("CORS_ALLOW_LOCALHOST", "").lower() == "true":
    CORS_ORIGINS += ["http://localhost:3000", "http://localhost:5173"]
# Vercel preview deploys of the SMS site. Anchored to this project's slug — an
# unanchored pattern would also match attacker-registered *.vercel.app hosts.
CORS_ORIGIN_REGEX = os.getenv("CORS_ORIGIN_REGEX", r"^https://317-sms-site-[a-z0-9-]+\.vercel\.app$")

# Whether this process runs the background scheduler (cleanups, parade-night
# texts, scheduled scrapers, backups). Exactly one process may have it on.
SCHEDULER_ENABLED = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"

# Docs assistant service (routers/chat.py). Cluster-internal only, no auth of its own.
CHATBOT_URL = os.getenv("CHATBOT_URL", "http://chatbot.chatbot.svc.cluster.local")
