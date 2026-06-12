"""Groq call that turns raw programme text into the formatted SMS messages."""

import re

import httpx

from core.config import GROQ_API_KEY

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

PROMPT_TEMPLATE = """
You write professional SMS messages for 317 Failsworth Air Cadets.

CRITICAL FORMAT RULES:
- A Flight and B Flight must each be on their own line.
- There must be exactly ONE blank line between A Flight and B Flight.
- Do NOT add extra blank lines.
- Do NOT include the words "Main Flight:" or "C Flight:" in the output.
- C Flight output must contain ONLY the activity text.

FORMATTING RULES:
- Combine activities and staff properly.
- If an activity and a staff member are on separate lines, combine them as:
  "Activity with Staff Name"
- If multiple activities share the same staff, combine naturally.
- Always structure as:
  Activity with Staff, followed by Activity with Staff.

Example:

A Flight:
Interflight Archery Competition with CI Stone, followed by Cook Off with Gold DofE Team.

B Flight:
Cook Off with Gold DofE Team, followed by Interflight Archery Competition with CI Stone.

Programme Data:

Main Body:
{main_body}

C Flight:
{c_flight}

Return EXACTLY in this format:

===MAIN===
<Formatted A and B Flight text>

===C===
<C flight text only>
"""


def generate_message(main_body: str, c_flight: str) -> tuple[str, str]:
    """Return (main_message, c_flight_message) formatted by the AI."""
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not configured")

    resp = httpx.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": "You generate structured squadron SMS messages."},
                {"role": "user", "content": PROMPT_TEMPLATE.format(main_body=main_body, c_flight=c_flight)},
            ],
            "temperature": 0.2,
            "max_tokens": 400,
        },
        timeout=60,
    )
    data = resp.json()
    if "choices" not in data:
        raise RuntimeError(f"Groq API error: {resp.text}")

    output = data["choices"][0]["message"]["content"].strip()

    main_match = re.search(r"===MAIN===\s*([\s\S]*?)===C===", output)
    c_match = re.search(r"===C===\s*([\s\S]*)", output)

    main_message = main_match.group(1) if main_match else ""
    c_message = c_match.group(1).strip() if c_match else ""

    main_message = re.sub(r"\n\s*\n\s*\n", "\n\n", main_message)
    main_message = re.sub(r"[ \t]+\n", "\n", main_message).strip()

    return main_message, c_message
