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
            users.append({
                "email":      u["primaryEmail"],
                "first_name": u["name"].get("givenName", "").strip(),
                "last_name":  u["name"].get("familyName", "").strip(),
            })

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    print(f"[Google] Fetched {len(users)} workspace users")
    return users

