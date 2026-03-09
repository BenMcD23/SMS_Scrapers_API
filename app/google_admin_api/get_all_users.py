import os
from dotenv import load_dotenv
from googleapiclient.discovery import build
from google.oauth2 import service_account

load_dotenv()

SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "service_account.json")
ADMIN_EMAIL          = os.getenv("GOOGLE_ADMIN_EMAIL")
WORKSPACE_DOMAIN     = os.getenv("GOOGLE_DOMAIN")

SCOPES = ["https://www.googleapis.com/auth/admin.directory.user.readonly"]


def get_workspace_users() -> list[dict]:
    """Fetch all users from Google Workspace, returning name + email dicts."""
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES
    )
    delegated = creds.with_subject(ADMIN_EMAIL)
    service = build("admin", "directory_v1", credentials=delegated)

    users = []
    page_token = None

    while True:
        result = service.users().list(
            domain=WORKSPACE_DOMAIN,
            maxResults=500,
            orderBy="email",
            pageToken=page_token,
            fields="nextPageToken,users(primaryEmail,name/givenName,name/familyName)"
        ).execute()

        for u in result.get("users", []):
            first = u["name"].get("givenName", "").strip()
            last  = u["name"].get("familyName", "").strip()
            users.append({
                "email":          u["primaryEmail"],
                "first_name":     first,          # original casing for DB
                "last_name":      last,           # original casing for DB
                "first_name_key": first.upper(),  # uppercase for comparison only
                "last_name_key":  last.upper(),   # uppercase for comparison only
            })

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return users
