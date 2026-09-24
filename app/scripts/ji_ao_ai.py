"""AI authoring of the free-text description sections in the JI/AO documents.

Takes the same Event317 data the deterministic generator uses and asks an LLM
to turn it into a properly written paragraph. Goes through core.llm's chain like
every other AI feature — GLM on NVIDIA's per-minute free tier leads, so an
on-demand button click no longer has to be kept off Gemini's tiny daily quota.
"""

import re

from core.llm import generate

SYSTEM_PROMPT = "You write formal joining instructions and admin orders for an Air Cadets squadron."

JI_PROMPT_TEMPLATE = """
Write the activity description paragraph for a Joining Instruction (JI) document.

Event details:
- Title: {title}
- Dates: {date_from_to}
- Location: {location}
- Dress: {dress}
- Raw notes from the event scraper (may be messy or incomplete): {raw_description}

STYLE:
- Formal but clear, written for cadets and parents.
- One to three short paragraphs, no headings, no bullet points.
- Only use facts present above — never invent activities, times or requirements that aren't given.
- If the raw notes are empty, write a brief generic paragraph describing the event from the title, dates and location alone.
- Do not repeat the dress code, cost or arrival/departure times — those are covered elsewhere in the document.

Return ONLY the description paragraph(s), no preamble.
"""

AO_PROMPT_TEMPLATE = """
Write a short "Activity Description" paragraph for an Admin Order (AO) document.

Event details:
- Title: {title}
- Dates: {date_from} to {date_to}
- Location: {location}
- Raw notes from the event scraper (may be messy or incomplete): {raw_description}

STYLE:
- Formal, concise, one paragraph.
- Briefly state what the activity is and what cadets will be doing, for staff reading the admin order.
- Only use facts present above — never invent activities, staff or requirements that aren't given.
- If the raw notes are empty, write a brief generic paragraph describing the event from the title, dates and location alone.

Return ONLY the paragraph, no preamble.
"""


def _write(prompt: str) -> str:
    output, _model = generate(prompt, SYSTEM_PROMPT, temperature=0.5,
                              max_tokens=4000, groq_max_tokens=1000)
    # Scraped notes arrive with stray non-breaking spaces and runs of whitespace;
    # the models copy them through.
    return re.sub(r"[ \t\u00a0]+", " ", output).strip()


def _clean_notes(raw: str | None) -> str:
    return re.sub(r"[\s\u00a0]+", " ", raw or "").strip() or "(none provided)"


def _location_text(event) -> str:
    if not event.location:
        return "N/A"
    first_line = getattr(event.location, "first_line", "").strip()
    postcode = getattr(event.location, "postcode", "").strip()
    return f"{first_line}, {postcode}" if first_line and postcode else first_line or postcode or "N/A"


def generate_ji_description_ai(event) -> str:
    """AI-authored replacement for the JI's {{ description }} placeholder."""
    if event.date_from and event.date_to:
        if event.date_from.date() == event.date_to.date():
            date_from_to = event.date_from.strftime("%d/%m/%Y")
        else:
            date_from_to = f"{event.date_from.strftime('%d/%m/%Y')} - {event.date_to.strftime('%d/%m/%Y')}"
    else:
        date_from_to = "N/A"

    prompt = JI_PROMPT_TEMPLATE.format(
        title=event.title,
        date_from_to=date_from_to,
        location=_location_text(event),
        dress=event.dress or "N/A",
        raw_description=_clean_notes(event.description),
    )
    return _write(prompt)


def generate_ao_description_ai(event) -> str:
    """AI-authored "Activity Description" paragraph, inserted into the AO since
    the deterministic template has no free-text section of its own."""
    prompt = AO_PROMPT_TEMPLATE.format(
        title=event.title,
        date_from=event.date_from.strftime("%d/%m/%Y") if event.date_from else "N/A",
        date_to=event.date_to.strftime("%d/%m/%Y") if event.date_to else "N/A",
        location=_location_text(event),
        raw_description=_clean_notes(event.description),
    )
    return _write(prompt)
