"""LLM calls that turn raw programme text into the formatted SMS messages.

Models are tried in order until one answers. Gemini 3.5 Flash writes the best
messages but its free tier only allows 20 requests/day (per model), so 2.5
Flash (250/day) catches the overflow and Groq's gpt-oss-120b is the final
fallback. All are thinking models — keep token budgets high enough that
reasoning doesn't starve the actual answer.
"""

import re
import time

import httpx

from core.config import GEMINI_API_KEY, GROQ_API_KEY

GEMINI_MODELS = ["gemini-3.5-flash", "gemini-2.5-flash"]
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"

# Preference order, best first. Anything other than the first entry means we
# fell back — usually because the best model's daily free-tier quota ran out.
MODEL_PREFERENCE = GEMINI_MODELS + [GROQ_MODEL]
PRIMARY_MODEL = MODEL_PREFERENCE[0]

MODEL_LABELS = {
    "gemini-3.5-flash": "Gemini 3.5 Flash",
    "gemini-2.5-flash": "Gemini 2.5 Flash",
    "openai/gpt-oss-120b": "Groq gpt-oss-120b",
}


def model_label(model_id: str | None) -> str:
    if not model_id:
        return "Unknown"
    return MODEL_LABELS.get(model_id, model_id)


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


def _call_gemini(model: str, prompt: str) -> str:
    resp = httpx.post(
        GEMINI_URL.format(model=model) + f"?key={GEMINI_API_KEY}",
        json={
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.6, "maxOutputTokens": 8000},
        },
        timeout=120,
    )
    data = resp.json()
    if "candidates" not in data:
        if resp.status_code == 429:
            raise RuntimeError("rate limited (free tier quota)")
        raise RuntimeError(f"Gemini API error: {resp.text[:200]}")

    parts = data["candidates"][0]["content"].get("parts", [])
    return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()


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
        "max_tokens": 3000,
        "reasoning_effort": "low",
    }

    # Generating a whole month trips the free tier's tokens-per-minute limit,
    # so wait out 429s instead of failing the batch
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


def _generate_raw(prompt: str) -> tuple[str, str]:
    """Try each model in preference order; return (output_text, model_id used)."""
    if GEMINI_API_KEY:
        for model in GEMINI_MODELS:
            try:
                return _call_gemini(model, prompt), model
            except Exception as e:
                print(f"[generate_message] {model} failed, trying next: {e}")
    return _call_groq(prompt), GROQ_MODEL


def generate_message(main_body: str, c_flight: str) -> tuple[str, str, str]:
    """Return (main_message, c_flight_message, model_id) — model_id is whichever
    model actually answered, so callers can report fallbacks."""
    prompt = PROMPT_TEMPLATE.format(main_body=main_body, c_flight=c_flight)
    output, model_id = _generate_raw(prompt)

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
