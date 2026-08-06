"""Central place for env vars and constants shared across the API."""

import os
from dotenv import load_dotenv

load_dotenv()

# Google OAuth client used by both the SMS site and the cadet portal
GOOGLE_CLIENT_ID = "490734276503-9s44s89sdhgct8ismqnsm7s1d4v6e4uv.apps.googleusercontent.com"

# Only tokens from this Google Workspace are accepted — outside Google accounts
# (personal Gmail etc.) are rejected before any role check.
GOOGLE_DOMAIN = os.getenv("GOOGLE_DOMAIN", "317atc.co.uk")

# Google Workspace groups that decide roles
STAFF_GROUP = "staff@317atc.co.uk"
NOTIFY_GROUP = "notifications@317atc.co.uk"
SNCO_GROUP = "snco@317atc.co.uk"
NCO_GROUP = "ncoteam@317atc.co.uk"

# Sole owner/maintainer — has access to developer-only views (e.g. API logs)
OWNER_EMAIL = "ci.mcdonald@317atc.co.uk"

# Officer Commanding — gates the OC dashboard and the committee-request approval
# actions (send-to-committee / approve / reject / mark-paid). Identified by email
# rather than a Google group since there is only ever one OC.
OC_EMAIL = os.getenv("OC_EMAIL", "")
# Where committee purchase requests and payment requests are emailed.
COMMITTEE_EMAIL = os.getenv("COMMITTEE_EMAIL", "")

# Service account used for the admin directory lookups and sending email
SA_EMAIL = os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL")
SA_PRIVATE_KEY = os.getenv("GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY", "").replace("\\n", "\n").strip('"')
IMPERSONATE_EMAIL = os.getenv("GOOGLE_IMPERSONATE_EMAIL", "ci.mcdonald@317atc.co.uk")
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

# Shared "NCO Holidays" Google Calendar — booked NCO holidays are written here
# as all-day events so staff can overlay them on the squadron's other calendars.
# Share the calendar with the service account (or with IMPERSONATE_EMAIL, who it
# acts as) with "Make changes to events" before setting this. Left unset, the
# bookings still save; they just aren't pushed to Calendar.
NCO_HOLIDAY_CALENDAR_ID = os.getenv("NCO_HOLIDAY_CALENDAR_ID", "")

# Where the SMS site is served from — used to link straight to a page from an
# email (e.g. the session plan awaiting review) instead of "log in and find it".
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "https://sms.317atc.co.uk").rstrip("/")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
NOTIFY_API_KEY = os.getenv("NOTIFY_API_KEY")
NOTIFY_SMS_TEMPLATE_ID = os.getenv("NOTIFY_SMS_TEMPLATE_ID")
ALERT_EMAIL = os.getenv("ALERT_EMAIL", NOTIFY_GROUP)
QUALI_EXPIRY_ALERT_EMAIL = os.getenv("QUALI_EXPIRY_ALERT_EMAIL", NOTIFY_GROUP)
BAN_ALERT_EMAIL = os.getenv("BAN_ALERT_EMAIL", NOTIFY_GROUP)
# Who gets told when an NCO submits a session plan for approval. Any staff
# member can then review it, so this defaults to the shared notifications group
# rather than one person.
SESSION_PLAN_ALERT_EMAIL = os.getenv("SESSION_PLAN_ALERT_EMAIL", NOTIFY_GROUP)
