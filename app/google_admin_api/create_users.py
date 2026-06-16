"""Creates Google Workspace accounts for new cadets.

Username scheme: surname alone if it's free, otherwise surname + a growing
prefix of the first name (initial, then initial + 1 letter, and so on) until
the local part is free. Every account gets the same starting password.
"""

from google_admin_api._auth import get_directory_service, WORKSPACE_DOMAIN
from google_admin_api.get_all_users import get_workspace_users

SCOPES = ["https://www.googleapis.com/auth/admin.directory.user"]

DEFAULT_PASSWORD = "Failsworth1"


def generate_username(first_name: str, last_name: str, existing_locals: set[str]) -> str:
    """Pick a free local part (the bit before @) for a cadet's email."""
    last = last_name.strip().lower().replace(" ", "")
    first = first_name.strip().lower().replace(" ", "")

    if last not in existing_locals:
        return last

    for n in range(1, len(first) + 1):
        candidate = f"{last}.{first[:n]}"
        if candidate not in existing_locals:
            return candidate

    # Same first + last name as an existing account — fall back to a numeric suffix
    suffix = 2
    while f"{last}.{first}{suffix}" in existing_locals:
        suffix += 1
    return f"{last}.{first}{suffix}"


def create_cadet_google_account(
    first_name: str,
    last_name: str,
    secondary_email: str | None = None,
    phone_number: str | None = None,
    existing_locals: set[str] | None = None,
) -> str:
    """Create the Workspace account and return the email address created."""
    if existing_locals is None:
        existing_locals = {u["email"].split("@")[0].lower() for u in get_workspace_users()}

    username = generate_username(first_name, last_name, existing_locals)
    email = f"{username}@{WORKSPACE_DOMAIN}"

    body = {
        "primaryEmail": email,
        "name": {"givenName": first_name, "familyName": last_name},
        "password": DEFAULT_PASSWORD,
    }
    if secondary_email:
        body["recoveryEmail"] = secondary_email
    if phone_number:
        body["recoveryPhone"] = phone_number

    service = get_directory_service(SCOPES)
    service.users().insert(body=body).execute()

    existing_locals.add(username)
    return email


def create_accounts_for_new_cadets(new_cadets: list[dict]) -> list[str]:
    """new_cadets: [{"first_name", "last_name", "secondary_email", "phone_number"}, ...]

    Returns the email addresses created, so the caller can report them."""
    existing_locals = {u["email"].split("@")[0].lower() for u in get_workspace_users()}

    created_emails = []
    for cadet in new_cadets:
        email = create_cadet_google_account(
            cadet["first_name"],
            cadet["last_name"],
            secondary_email=cadet.get("secondary_email"),
            phone_number=cadet.get("phone_number"),
            existing_locals=existing_locals,
        )
        created_emails.append(email)

    return created_emails
