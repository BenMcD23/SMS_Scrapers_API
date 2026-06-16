import os
from dotenv import load_dotenv
from googleapiclient.discovery import build
from google.oauth2 import service_account

load_dotenv()

_SA_EMAIL       = os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL")
_SA_PRIVATE_KEY = os.getenv("GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY", "").replace("\\n", "\n").strip('"')
ADMIN_EMAIL      = os.getenv("GOOGLE_IMPERSONATE_EMAIL", os.getenv("GOOGLE_ADMIN_EMAIL"))
WORKSPACE_DOMAIN = os.getenv("GOOGLE_DOMAIN")


def get_directory_service(scopes: list[str]):
    """Build an Admin SDK Directory service, impersonating the squadron admin."""
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
        scopes=scopes,
    ).with_subject(ADMIN_EMAIL)
    return build("admin", "directory_v1", credentials=creds)
