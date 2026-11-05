"""Who did it, for history lines: the signed-in person's name, else their email.

Shared so every stores history line names people the same way.
"""


def actor_name(idinfo: dict) -> str | None:
    name = f"{idinfo.get('given_name') or ''} {idinfo.get('family_name') or ''}".strip()
    return name or idinfo.get("name") or idinfo.get("email")
