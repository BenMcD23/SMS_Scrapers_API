"""One-time helper to mint a Gmail refresh token for the noreply mailbox.

Run this once, locally, signed in as noreply@317atc.co.uk. It performs the
installed-app OAuth flow for the gmail.send scope and prints a refresh token that
lets the API send mail *as that mailbox only* — no domain-wide delegation involved.

Usage:
    GMAIL_OAUTH_CLIENT_ID=... GMAIL_OAUTH_CLIENT_SECRET=... \
        python app/google_admin_api/mint_noreply_token.py

The client id/secret come from an OAuth 2.0 "Desktop app" client in the Google
Cloud console. A browser window opens for consent; sign in as the noreply account.
Copy the printed token into GMAIL_NOREPLY_REFRESH_TOKEN in the API's .env.
"""

import os
from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def main() -> None:
    client_id = os.getenv("GMAIL_OAUTH_CLIENT_ID")
    client_secret = os.getenv("GMAIL_OAUTH_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SystemExit(
            "Set GMAIL_OAUTH_CLIENT_ID and GMAIL_OAUTH_CLIENT_SECRET first "
            "(from an OAuth 2.0 'Desktop app' client)."
        )

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        scopes=SCOPES,
    )
    # access_type=offline + prompt=consent forces Google to return a refresh token.
    creds = flow.run_local_server(
        port=0, access_type="offline", prompt="consent",
        authorization_prompt_message="Sign in as noreply@317atc.co.uk: {url}",
    )

    if not creds.refresh_token:
        raise SystemExit("No refresh token returned — revoke prior access and retry.")

    print("\nAdd this to the API .env:\n")
    print(f"GMAIL_NOREPLY_REFRESH_TOKEN={creds.refresh_token}")


if __name__ == "__main__":
    main()
