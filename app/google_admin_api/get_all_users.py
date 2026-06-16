from google_admin_api._auth import get_directory_service, WORKSPACE_DOMAIN

SCOPES = ["https://www.googleapis.com/auth/admin.directory.user.readonly"]


def get_workspace_users() -> list[dict]:
    """Fetch all users from Google Workspace, returning name + email dicts."""
    service = get_directory_service(SCOPES)

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
