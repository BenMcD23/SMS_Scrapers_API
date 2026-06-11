"""Central place for env vars and constants shared across the API."""

import os
from dotenv import load_dotenv

load_dotenv()

# Google OAuth client used by both the SMS site and the cadet portal
GOOGLE_CLIENT_ID = "490734276503-9s44s89sdhgct8ismqnsm7s1d4v6e4uv.apps.googleusercontent.com"

# Google Workspace groups that decide roles
STAFF_GROUP = "staff@317atc.co.uk"
NCO_GROUP = "ncoteam@317atc.co.uk"

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
