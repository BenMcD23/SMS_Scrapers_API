"""Site suggestions / bug reports submitted from the SMS site.

Each submission is emailed to the owner and a copy is sent back to the
submitter so they have a record of what they raised.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.config import OWNER_EMAIL
from core.emailer import send_email, suggestion_email_html
from core.security import require_staff_or_nco

router = APIRouter()


class TaggedElement(BaseModel):
    label: str
    selector: str | None = None


class SuggestionIn(BaseModel):
    message: str
    page_url: str | None = None
    page_title: str | None = None
    about_current_page: bool = True
    tagged_elements: list[TaggedElement] = []


@router.post("/suggestions")
def submit_suggestion(
    data: SuggestionIn,
    idinfo: dict = Depends(require_staff_or_nco),
):
    message = data.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="A description is required")

    submitter_email = idinfo["email"]
    submitter_name = idinfo.get("name") or submitter_email

    # Cap the tag list so a runaway client can't send an unbounded email.
    tagged = [(t.label, t.selector) for t in data.tagged_elements[:25]]

    html_body = suggestion_email_html(
        submitter_name=submitter_name,
        submitter_email=submitter_email,
        message=message,
        page_title=data.page_title,
        page_url=data.page_url,
        about_current_page=data.about_current_page,
        tagged=tagged,
        submitted_at=datetime.now().strftime("%d %b %Y, %H:%M"),
    )

    # One send to both addresses — the owner gets the report and the submitter
    # gets an identical copy as confirmation.
    send_email(
        to=f"{OWNER_EMAIL}, {submitter_email}",
        subject=f"Site suggestion from {submitter_name}",
        html_body=html_body,
    )
    return {"status": "success"}
