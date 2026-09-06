"""Squadron-wide settings for the parade-night texts.

Just the WhatsApp community invite link so far. It lives in the database rather
than the environment because WhatsApp invite links get reset from time to time,
and staff fixing one shouldn't need a redeploy.
"""

from urllib.parse import urlparse

from sqlalchemy.orm import Session

from database.models import TextSettings


def get_text_settings(db: Session) -> TextSettings:
    """The one settings row, created empty on first use."""
    settings = db.query(TextSettings).order_by(TextSettings.id).first()
    if settings is None:
        settings = TextSettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def community_invite_url(db: Session) -> str:
    """The invite link, or "" when staff haven't set one — callers show the
    join prompt only when there's somewhere to send people."""
    return get_text_settings(db).whatsapp_invite_url or ""


def clean_invite_url(value: str) -> str:
    """A pasted invite link, or a ValueError saying what's wrong with it.

    WhatsApp hands out community and group invites as https://chat.whatsapp.com/
    links. The check stops at the scheme and the domain: anything stricter would
    reject a link format WhatsApp changes under us, and the failure mode there —
    staff unable to save a link that works — is worse than accepting one that
    doesn't. An empty value clears it.
    """
    url = (value or "").strip()
    if not url:
        return ""

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("The invite link must start with https://")
    host = (parsed.hostname or "").lower()
    if host != "whatsapp.com" and not host.endswith(".whatsapp.com"):
        raise ValueError(
            "That isn't a WhatsApp invite link — it should look like "
            "https://chat.whatsapp.com/..."
        )
    return url
