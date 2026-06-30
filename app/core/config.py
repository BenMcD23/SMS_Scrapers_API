"""Central place for env vars and constants shared across the API."""

import os
from dotenv import load_dotenv

load_dotenv()

# Google OAuth client used by both the SMS site and the cadet portal
GOOGLE_CLIENT_ID = "490734276503-9s44s89sdhgct8ismqnsm7s1d4v6e4uv.apps.googleusercontent.com"

# Google Workspace groups that decide roles
STAFF_GROUP = "staff@317atc.co.uk"
NCO_GROUP = "ncoteam@317atc.co.uk"

# Sole owner/maintainer — has access to developer-only views (e.g. API logs)
OWNER_EMAIL = "ci.mcdonald@317atc.co.uk"

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

# Staff/NCO photos — metadata lives in people.json, images under public/people/
PEOPLE_JSON_PATH = "src/data/people.json"

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

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
NOTIFY_API_KEY = os.getenv("NOTIFY_API_KEY")
NOTIFY_SMS_TEMPLATE_ID = os.getenv("NOTIFY_SMS_TEMPLATE_ID")
TEXTS_ALERT_EMAIL = os.getenv("TEXTS_ALERT_EMAIL", STAFF_GROUP)
