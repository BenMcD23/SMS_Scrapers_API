"""Groq call that turns raw programme text into the formatted SMS messages."""

import re

import httpx

from core.config import GROQ_API_KEY

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

PROMPT_TEMPLATE = """
You write professional SMS messages for 317 Failsworth Air Cadets.

INPUT FORMAT:
- The programme data is split into "1st Period" and "2nd Period" — what the cadets do first, then after the break.
- Within each activity block, the activity name comes first and the staff running it follow on the next line(s).
- "A Flight:" / "B Flight:" label each flight's own activity.
- "Both Flights:" means A and B Flight do that activity together.
- "Whole Squadron:" means everyone does it together.
- A "/" means the cadets are SPLIT between those parallel activities, with staff paired up respectively (first activity with first staff member, second with second, and so on).

CRITICAL FORMAT RULES:
- Combine the two periods into one flowing description: "<1st Period activity>, followed by <2nd Period activity>". The 1st Period activity MUST come first — never swap the order, and never drop a period.
- A "Both Flights" period applies to BOTH A and B Flight — if the output uses flight labels, that activity must appear in each flight's line.
- If the exact same activity runs in both periods, describe it ONCE (e.g. "Drill with Sgt Lloyd Morris") instead of repeating it with "followed by".
- Do NOT include the words "1st Period", "2nd Period", "Main Flight:" or "C Flight:" in the output.
- If A Flight and B Flight do different things, they must each be on their own line, with exactly ONE blank line between them and no extra blank lines.
- If the Main Body only contains "Both Flights" or "Whole Squadron" blocks, or A and B Flight would end up with identical text, output a single description with NO "A Flight:"/"B Flight:" labels.
- C Flight output must contain ONLY the activity text.

FORMATTING RULES:
- Combine activities and staff naturally as "Activity with Staff Name".
- If multiple activities share the same staff, combine naturally.
- Describe split ("/") activities like: "split between Archery Practice with CI Stone, Exams & Resits with Fg Off Barker, and Ceremonial Drill with FS Gill".
- A "/" only means a split when there are several ACTIVITIES. A "/" between staff names for a single activity just means that activity has multiple staff — write "Classifications with CWO Tyrell and CI Boxall", never "split between" staff.

UNIFORM RULES:
- If the uniform lists Civvies or Sports Kit alongside another uniform, cadets come down in the Civvies/Sports Kit and get changed into the other uniform during the night. Write it like: "Civvies (bring No.2a SD to change into)".
- Otherwise return the uniform exactly as given.

WORKED EXAMPLE — this input:

Main Body:
1st Period
Both Flights:
Cook Off / Night Ex Prep
Sgt Smith / CI Jones

2nd Period
A Flight:
Drill, FS Hall / Chess, CWO Lee

B Flight:
Banner, Fg Off Cole

must produce this MAIN output (note the 1st Period "Both Flights" activity appears in both lines, before "followed by"):

A Flight:
Split between Cook Off with Sgt Smith and Night Ex Prep with CI Jones, followed by split between Drill with FS Hall and Chess with CWO Lee.

B Flight:
Split between Cook Off with Sgt Smith and Night Ex Prep with CI Jones, followed by Banner with Fg Off Cole.

Programme Data:

Main Body:
{main_body}

C Flight:
{c_flight}

Uniform:
{uniform}

Return EXACTLY in this format:

===MAIN===
<Formatted A and B Flight text>

===C===
<C flight text only>

===UNIFORM===
<Uniform text only>
"""


def generate_message(main_body: str, c_flight: str, uniform: str) -> tuple[str, str, str]:
    """Return (main_message, c_flight_message, uniform) formatted by the AI."""
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not configured")

    resp = httpx.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": "You generate structured squadron SMS messages."},
                {"role": "user", "content": PROMPT_TEMPLATE.format(
                    main_body=main_body, c_flight=c_flight, uniform=uniform)},
            ],
            "temperature": 0.2,
            "max_tokens": 500,
        },
        timeout=60,
    )
    data = resp.json()
    if "choices" not in data:
        raise RuntimeError(f"Groq API error: {resp.text}")

    output = data["choices"][0]["message"]["content"].strip()

    main_match = re.search(r"===MAIN===\s*([\s\S]*?)===C===", output)
    c_match = re.search(r"===C===\s*([\s\S]*?)(?:===UNIFORM===|$)", output)
    uniform_match = re.search(r"===UNIFORM===\s*([\s\S]*)", output)

    main_message = main_match.group(1) if main_match else ""
    c_message = c_match.group(1).strip() if c_match else ""
    uniform_message = uniform_match.group(1).strip() if uniform_match else uniform

    main_message = re.sub(r"\n\s*\n\s*\n", "\n\n", main_message)
    main_message = re.sub(r"[ \t]+\n", "\n", main_message).strip()

    # The model sometimes keeps A/B labels even when both flights have identical
    # text — collapse that to a single unlabelled description
    both = re.match(r"^A Flight:\n([\s\S]*?)\n\nB Flight:\n([\s\S]*)$", main_message)
    if both and both.group(1).strip() == both.group(2).strip():
        main_message = both.group(1).strip()

    return main_message, c_message, uniform_message
