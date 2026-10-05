"""LLM calls that turn raw programme text into the formatted SMS messages.

The model chain and its fallbacks live in core.llm — this module is only the
prompt and the parsing of what comes back.

The examples in the prompt are real texts staff sent (after editing the AI
draft), picked from the ones whose wording follows from the programme alone —
so the model copies what staff actually want, not what an earlier model wrote.
"""

import re

from core.llm import PRIMARY_MODEL, generate, model_label  # noqa: F401  (re-exported for the texts router)

SYSTEM_PROMPT = (
    "You write the weekly parade-night text message that 317 (Failsworth) Squadron, "
    "Air Cadets, sends to its cadets and their parents. You only ever state what the "
    "programme says."
)

UNIFORM_EXPANSIONS = {
    "no.3 sd": "No.3 SD (MTP/DPM)",
    "no.2a sd": "No.2a SD (Wedgewood and tie)",
    "no.2c sd": "No.2c SD (Working Blues)",
}

CASUAL_UNIFORMS = ("civvies", "sports kit")

# Probationary cadets have no uniform of their own yet. Staff change this by hand
# once an intake has theirs — it's a line in code, not the model, so it's the same
# every week and never gets "creatively" reworded.
C_FLIGHT_UNIFORM = "Uniform - Civvies"

# What the model writes for C Flight when there is nothing C-Flight-specific to say.
NO_C_FLIGHT = "NONE"


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
Write this week's parade-night text for 317 Failsworth Air Cadets from the programme below.
It goes out as an SMS under a "Uniform:" line and above a "DNCO:" line, so never mention
the uniform or the duty NCO yourself.

READING THE PROGRAMME
- "1st Period" is the first half of the night, "2nd Period" the second half, after the break.
- In each block the activity comes first, then the staff running it.
- "A Flight:" / "B Flight:" is that flight's own activity. "Both Flights:" and "Whole Squadron:"
  mean everyone does it together.
- A "/" between ACTIVITIES means the cadets are split across them at the same time, staff paired
  up in order. A "/" (or "&") between staff on ONE activity just means several staff run it.

HOW STAFF WANT IT WRITTEN
- Short. It's a text message: usually one to three short lines. Say what the night IS, not a
  timetable of who is where.
- Friendly and upbeat. A short lead-in or sign-off line adds warmth ("Get ready for...", "Let's
  see which flight comes out on top...") — one at most, and only about things the programme
  actually says. Vary the wording week to week; don't lean on the same phrase every time.
- First half, then second half, in that order, joined by "followed by", "then", "and then...",
  or on a new line. If it's the same all night, say so once ("running all night").
- Split activities are one list: "Archery, Exams & Resits & Ceremonial Drill".
- Staff names: leave most out. Name at most one or two people, and only where one person is
  clearly running a headline activity ("with CI Stone", "brought to you by Sgt Lloyd Morris").
  Never list every instructor. Never name groups like "Flight NCOs", "SNCOs", "All I/Cs",
  "The Staff" or "Staff".
- Flights: when both flights swap the same two activities between halves, don't mention flights
  at all ("Classifications running alongside Flight Time"). When they genuinely differ, say so
  inline ("Flight Time for A Flight and Slacks Repair for B Flight"). Only use separate
  "A Flight:" / "B Flight:" blocks when the flights differ in BOTH halves and one line would be
  a mess — then "A Flight:" on its own line, their night on the next, a blank line, then
  "B Flight:" and theirs, with no intro line.
- If the programme says the squadron is stood down / not parading, say plainly that there is no
  parade night and cadets should not attend.
- Expand shorthand: "Trg" → "Training", "Adv" → "Advanced", "Exped" → "Expedition". Leave
  "WTD" (Wing Training Day) as it is — everyone knows it.
- NEVER invent an activity, a person, a time, a place or a detail. Never write "1st Period",
  "2nd Period", "Main Flight" or "C Flight".

C FLIGHT (the probationary cadets, listed separately)
- One short line of their activities, combined with "and": "Map Reading 1 and Drill".
  Mention the instructor only if one person runs the whole night.
- If C Flight has no real activity of its own — empty, "Awaiting Intake", stood down, or just
  the same as the whole squadron — write exactly {no_c_flight}.
- Don't write their uniform; that's added for you.

EXAMPLES — real texts staff sent

Programme:
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
Text:
Classifications running alongside Flight Time!

Programme:
1st Period
Both Flights:
Archery Practice / Exams & Resits / Ceremonial Drill
CI Stone / Fg Off Barker / FS Gill

2nd Period
Both Flights:
Task Master
CWO Tyrell
Text:
Archery, Exams & Resits & Ceremonial Drill
and then...
CWO Tyrell will become the Task Master!

Programme:
1st Period
A Flight:
Flight Time
Flight NCO

B Flight:
Slacks Repair
Cpl Tyrell

2nd Period
Both Flights:
Rounders Training
CI Stone
Text:
Flight Time for A Flight and Slacks Repair for B Flight first half, followed by Rounders Training with CI Stone!

Programme:
1st Period
Both Flights:
The Hunger Games
Sgt Lloyd Morris

2nd Period
Both Flights:
The Hunger Games
Sgt Lloyd Morris
Text:
The Hunger Games! Sgt Lloyd Morris is running it all night — may the odds be ever in your favour...

Programme:
1st Period
Both Flights:
Classification Training / Exams / DofE Sign Off
ASgt Tyrell / Sgt Lloyd Morris / CI Boxall

2nd Period
Both Flights:
Classification Training / Exams / DofE Sign Off
ASgt Tyrell / Sgt Lloyd Morris / CI Boxall
Text:
Classification Training, Exams & DofE Sign Off running all night!

Programme:
1st Period
A Flight:
Inter Flight GAS EX
FS Wimbury

B Flight:
Inter Flight Quiz
Sgt Mack

2nd Period
A Flight:
Inter Flight Quiz
Sgt Mack

B Flight:
Inter Flight GAS EX
FS Wimbury
Text:
Get ready for a night of friendly competition! The Inter Flight GAS EX and the Inter Flight Quiz are running this evening. Let's see which flight comes out on top...

C Flight programme:
1st Period:
Map Reading 1
FS Beverley

2nd Period:
Drill
FS Gill
C Flight text:
Map Reading 1 and Drill

C Flight programme:
1st Period:
Awaiting Intake
C Flight text:
{no_c_flight}

NOW WRITE THIS WEEK'S

Programme:
{main_body}

C Flight programme:
{c_flight}

Return EXACTLY this, nothing before or after:

===MAIN===
<text>

===C===
<C Flight text, or {no_c_flight}>
"""


def _expand_uniform(raw: str) -> str:
    """A programme activity of just "Uniform" is uniform maintenance. Done here
    rather than asked of the model, which kept writing "Uniform!" — and a looser
    rule turned "Uniform Prep" into "Uniform maintenance preparation"."""
    return re.sub(r"(?m)(^|/\s*)Uniform(?=\s*(?:/|\(|$))", r"\1Uniform Maintenance", raw)


def generate_message(main_body: str, c_flight: str) -> tuple[str, str, str]:
    """Return (main_message, c_flight_message, model_id) — model_id is whichever
    model actually answered, so callers can report fallbacks."""
    prompt = PROMPT_TEMPLATE.format(
        main_body=_expand_uniform(main_body.strip()) or "(nothing programmed)",
        c_flight=_expand_uniform(c_flight.strip()) or "(empty)",
        no_c_flight=NO_C_FLIGHT,
    )
    # Groq is capped lower — it needs no thinking headroom here, and its
    # free-tier tokens-per-minute budget is much tighter.
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

    # No C Flight programme means no C Flight section — don't trust the model to
    # notice, an empty input used to come back as a lone "Uniform - Civvies".
    if not c_flight.strip() or c_message.strip(" .").upper() == NO_C_FLIGHT or not c_message:
        c_message = ""
    else:
        c_message = f"{C_FLIGHT_UNIFORM}\n\n{c_message}"

    return main_message, c_message, model_id
