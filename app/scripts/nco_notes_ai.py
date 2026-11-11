"""Split one end-of-night debrief into a note per NCO.

Staff can talk through several NCOs in one go ("Smith was great on drill, Jones
late again and quiet with his section"). This finds who each part is about,
matched against the NCO roster, and returns one note per NCO for staff to check
before anything is saved.

The model only sorts and lightly tidies what was said. It must not add praise
or criticism, and anything it can't pin on one NCO comes back unassigned rather
than guessed — a note filed against the wrong cadet is worse than one staff
have to place by hand.
"""

import json
import re

from core.llm import generate

SYSTEM_PROMPT = (
    "You sort a squadron staff member's spoken notes about their cadet NCOs into "
    "one note per NCO. You never add anything that wasn't said."
)

MAX_NOTE_CHARS = 2000


def _prompt(text: str, roster: list[dict]) -> str:
    lines = "\n".join(f"- {r['cin']}: {r['name']}" for r in roster)
    return f"""The NCOs on the roster (id: rank and name):
{lines}

The staff member's notes, transcribed from speech (names may be misheard,
first names or surnames alone, or nicknames):
\"\"\"
{text}
\"\"\"

Split the notes into one note per NCO they talk about.
- Use the roster id of the NCO each part is about. Match misheard or partial
  names only when exactly one roster NCO fits; otherwise leave it unassigned.
- Write each note in the third person or as the speaker said it, keeping their
  meaning and wording. Fix obvious transcription slips and filler words
  ("um", "and then yeah"). Do not add, soften or strengthen anything.
- If one remark is about several NCOs, give each of them their own copy.
- Anything not about a specific roster NCO goes in "unassigned", verbatim.

Reply with JSON only, in this shape:
{{"notes": [{{"id": 123, "note": "..."}}], "unassigned": ["..."]}}"""


def _parse(raw: str) -> dict:
    """The JSON object in the reply, tolerating a ```json fence or a line of
    preamble around it."""
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        raise ValueError("no JSON object in the reply")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("reply wasn't a JSON object")
    return data


def split_notes(text: str, roster: list[dict]) -> tuple[list[dict], list[str], str]:
    """(notes, unassigned, model id). `roster` is [{"cin", "name"}].

    Each note is {"cin", "body"}, at most one per NCO (two parts about the same
    NCO are joined). An id the roster doesn't have, which a model can invent,
    sends that note to unassigned instead of trusting it.
    """
    raw, model = generate(_prompt(text, roster), SYSTEM_PROMPT, temperature=0.1, max_tokens=8000)
    data = _parse(raw)

    known = {r["cin"] for r in roster}
    by_cin: dict[int, list[str]] = {}
    unassigned = [str(u).strip() for u in data.get("unassigned") or [] if str(u).strip()]
    for item in data.get("notes") or []:
        if not isinstance(item, dict):
            continue
        body = str(item.get("note") or "").strip()
        if not body:
            continue
        try:
            cin = int(item.get("id"))
        except (TypeError, ValueError):
            cin = None
        if cin in known:
            by_cin.setdefault(cin, []).append(body)
        else:
            unassigned.append(body)

    # Roster order, so the review list reads the same way every time.
    notes = [
        {"cin": r["cin"], "body": " ".join(by_cin[r["cin"]])[:MAX_NOTE_CHARS]}
        for r in roster if r["cin"] in by_cin
    ]
    return notes, unassigned, model
