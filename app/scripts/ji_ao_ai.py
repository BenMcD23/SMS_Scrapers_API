"""AI authoring of the free-text description sections in the JI/AO documents.

Takes the same Event317 data the deterministic generator uses and asks an LLM
to turn it into a properly written paragraph. Only Groq's gpt-oss-120b is used
here (no Gemini fallback) — this is a one-off, on-demand generation triggered
by a button click, so the lower latency matters more than Gemini's better
prose, and Gemini's tiny free-tier daily quota is better saved for the SMS
generator that runs every week.
"""

import time

import httpx

from core.config import GROQ_API_KEY

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"

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


def _call_groq(prompt: str) -> str:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not configured")

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.6,
        "max_tokens": 1000,
        "reasoning_effort": "low",
    }

    for _ in range(5):
        resp = httpx.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json=payload,
            timeout=60,
        )
        if resp.status_code != 429:
            break
        time.sleep(min(float(resp.headers.get("retry-after", 10)) + 1, 60))

    data = resp.json()
    if "choices" not in data:
        raise RuntimeError(f"Groq API error: {resp.text}")
    return data["choices"][0]["message"]["content"].strip()


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
        raw_description=event.description or "(none provided)",
    )
    return _call_groq(prompt)


def generate_ao_description_ai(event) -> str:
    """AI-authored "Activity Description" paragraph, inserted into the AO since
    the deterministic template has no free-text section of its own."""
    prompt = AO_PROMPT_TEMPLATE.format(
        title=event.title,
        date_from=event.date_from.strftime("%d/%m/%Y") if event.date_from else "N/A",
        date_to=event.date_to.strftime("%d/%m/%Y") if event.date_to else "N/A",
        location=_location_text(event),
        raw_description=event.description or "(none provided)",
    )
    return _call_groq(prompt)
