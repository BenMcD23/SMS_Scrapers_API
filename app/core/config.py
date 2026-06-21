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

# Dedicated OAuth credentials for the noreply mailbox, used to send email *as that
# mailbox only* (no domain-wide delegation). The refresh token is minted once by
# consenting as noreply@ — see app/google_admin_api/mint_noreply_token.py.
GMAIL_OAUTH_CLIENT_ID = os.getenv("GMAIL_OAUTH_CLIENT_ID")
GMAIL_OAUTH_CLIENT_SECRET = os.getenv("GMAIL_OAUTH_CLIENT_SECRET")
GMAIL_NOREPLY_REFRESH_TOKEN = os.getenv("GMAIL_NOREPLY_REFRESH_TOKEN")

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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
NOTIFY_API_KEY = os.getenv("NOTIFY_API_KEY")
NOTIFY_SMS_TEMPLATE_ID = os.getenv("NOTIFY_SMS_TEMPLATE_ID")
TEXTS_ALERT_EMAIL = os.getenv("TEXTS_ALERT_EMAIL", STAFF_GROUP)
