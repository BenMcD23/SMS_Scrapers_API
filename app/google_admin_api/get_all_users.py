import os
from dotenv import load_dotenv
from googleapiclient.discovery import build
from google.oauth2 import service_account

load_dotenv()

_SA_EMAIL      = os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL")
_SA_PRIVATE_KEY = os.getenv("GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY", "").replace("\\n", "\n").strip('"')
ADMIN_EMAIL    = os.getenv("GOOGLE_IMPERSONATE_EMAIL", os.getenv("GOOGLE_ADMIN_EMAIL"))
WORKSPACE_DOMAIN = os.getenv("GOOGLE_DOMAIN")

SCOPES = ["https://www.googleapis.com/auth/admin.directory.user.readonly"]


def get_workspace_users() -> list[dict]:
    """Fetch all users from Google Workspace, returning name + email dicts."""
    creds = service_account.Credentials.from_service_account_info(
        {
            "type": "service_account",
            "client_email": _SA_EMAIL,
            "private_key": _SA_PRIVATE_KEY,
            "token_uri": "https://oauth2.googleapis.com/token",
            "private_key_id": "",
            "client_id": "",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        },
        scopes=SCOPES,
    ).with_subject(ADMIN_EMAIL)
    service = build("admin", "directory_v1", credentials=creds)

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
                "first_name":     first,
                "last_name":      last,
                "first_name_key": first.upper(),
                "last_name_key":  last.upper(),
            })

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return users
