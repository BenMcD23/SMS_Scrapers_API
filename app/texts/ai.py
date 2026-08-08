"""LLM calls that turn raw programme text into the formatted SMS messages.

The model chain itself (Gemini first, Groq as the quota fallback) lives in
core.llm — this module is only the prompt and the parsing of what comes back.
"""

import re

from core.llm import PRIMARY_MODEL, model_label, generate  # noqa: F401  (re-exported for the texts router)

SYSTEM_PROMPT = "You generate structured squadron SMS messages."

UNIFORM_EXPANSIONS = {
    "no.3 sd": "No.3 SD (MTP/DPM)",
    "no.2a sd": "No.2a SD (Wedgewood and tie)",
}

CASUAL_UNIFORMS = ("civvies", "sports kit")


def format_uniform(raw: str) -> str:
    """Deterministic uniform formatting — expansion glossary plus the
    "come in civvies/sports kit and change into the rest later" rule."""
    items = [i.strip() for i in re.split(r"[,\n]", raw) if i.strip()]
    casual = [i for i in items if i.lower() in CASUAL_UNIFORMS]
    formal = [i for i in items if i.lower() not in CASUAL_UNIFORMS]

    if casual and formal:
        return f"{casual[0]} (bring {' and '.join(formal)} to change into)"
    return ", ".join(UNIFORM_EXPANSIONS.get(i.lower(), i) for i in items)

PROMPT_TEMPLATE = """
You write the weekly parade night SMS for 317 Failsworth Air Cadets.

INPUT FORMAT:
- The programme data is split into "1st Period" and "2nd Period" — what the cadets do first, then after the break.
- Within each activity block, the activity name comes first and the staff running it follow on the next line(s).
- "A Flight:" / "B Flight:" label each flight's own activity.
- "Both Flights:" means A and B Flight do that activity together.
- "Whole Squadron:" means everyone does it together.
- A "/" between several ACTIVITIES means the cadets are split between them, with staff paired up respectively (first activity with first staff member, and so on).
- A "/" between staff names for a single activity just means it has multiple staff — write "with CWO Tyrell and CI Boxall", never "split between" staff.

STYLE — write like a person, not a timetable:
- Friendly and enthusiastic; the occasional exclamation mark or playful line is welcome.
- NEVER invent activities, staff or details that are not in the programme data.
- When A and B Flight do the same activities in opposite halves of the night, do NOT use flight labels — describe the night once, e.g. "Classifications running alongside Flight Time".
- Only use flight labels when the flights genuinely do different things. The format is then strict: "A Flight:" on its own line, that flight's full night on the next line(s), ONE blank line, then "B Flight:" and theirs — no intro line before the labels. A "Both Flights" period then appears in BOTH flights' lines.
- Keep activities in chronological order: 1st Period first, then 2nd Period after a connector. Never swap the order, never drop a period. Vary connectors: "followed by", "and then...", "Then".
- If the same activity runs in both periods, mention it once instead of repeating it.
- For split ("/") activities, list them naturally, e.g. "Archery, Exams & Resits & Ceremonial Drill".
- Staff names are optional — include them where they read well ("with Sgt Davies"); drop them when there are many or the sentence gets cluttered.
- Expand abbreviations: "Trg" becomes "Training". The activity "Uniform" means uniform maintenance — call it "Uniform maintenance".
- Do NOT include the words "1st Period", "2nd Period", "Main Flight:" or "C Flight:" in the output.

C FLIGHT RULES:
- C Flight are the probationary cadets. Their message must ALWAYS start with exactly "Uniform - Civvies" followed by a blank line, then their activities.
- Keep it to one short sentence, combining their periods naturally with "and", e.g. "Map Reading Pt1 and Drill" — not "followed by" every time.

EXAMPLES of the style wanted:

Input main body (the flights swap the same two activities, so no labels):
1st Period
A Flight:
Flight Time
FS Wimbury

B Flight:
Classifications
CWO Tyrell / CI Boxall

2nd Period
A Flight:
Classifications
CWO Tyrell / CI Boxall

B Flight:
Flight Time
FS Beverley

Good MAIN output:
Classifications running alongside Flight Time

Input main body:
1st Period
Both Flights:
Archery Practice / Exams & Resits / Ceremonial Drill
CI Stone / Fg Off Barker / FS Gill

2nd Period
Both Flights:
Task Master
CWO Tyrell

Good MAIN output:
Archery, Exams & Resits & Ceremonial Drill
and then...
CWO Tyrell will become the Task Master!

Input main body (flights genuinely differ, so labels are needed; the 1st Period "Both Flights" activity appears in both lines):
1st Period
Both Flights:
Cook Off / Night Ex Prep
Sgt Smith / CI Jones

2nd Period
A Flight:
Drill, FS Hall / Chess, CWO Lee

B Flight:
Banner, Fg Off Cole

Good MAIN output:
A Flight:
Cook Off and Night Ex Prep, then a split between Drill with FS Hall and Chess with CWO Lee.

B Flight:
Cook Off and Night Ex Prep, followed by Banner with Fg Off Cole.

Input C Flight:
1st Period:
Drill
Sgt Lloyd Morris

2nd Period:
Drill
Sgt Lloyd Morris

Good C output:
Uniform - Civvies

Drill with Sgt Lloyd Morris

Programme Data:

Main Body:
{main_body}

C Flight:
{c_flight}

Return EXACTLY in this format:

===MAIN===
<main message>

===C===
<C Flight message starting with "Uniform - Civvies">
"""


def generate_message(main_body: str, c_flight: str) -> tuple[str, str, str]:
    """Return (main_message, c_flight_message, model_id) — model_id is whichever
    model actually answered, so callers can report fallbacks."""
    prompt = PROMPT_TEMPLATE.format(main_body=main_body, c_flight=c_flight)
    # Groq is capped lower than Gemini — it needs no thinking headroom here,
    # and its free-tier tokens-per-minute budget is much tighter.
    output, model_id = generate(prompt, SYSTEM_PROMPT, groq_max_tokens=3000)

    main_match = re.search(r"===MAIN===\s*([\s\S]*?)===C===", output)
    c_match = re.search(r"===C===\s*([\s\S]*)", output)

    main_message = main_match.group(1) if main_match else ""
    c_message = c_match.group(1).strip() if c_match else ""

    main_message = re.sub(r"\n\s*\n\s*\n", "\n\n", main_message)
    main_message = re.sub(r"[ \t]+\n", "\n", main_message).strip()

    # The model sometimes keeps A/B labels even when both flights have identical
    # text — collapse that to a single unlabelled description
    both = re.match(r"^A Flight:\n([\s\S]*?)\n\nB Flight:\n([\s\S]*)$", main_message)
    if both and both.group(1).strip() == both.group(2).strip():
        main_message = both.group(1).strip()

    return main_message, c_message, model_id
