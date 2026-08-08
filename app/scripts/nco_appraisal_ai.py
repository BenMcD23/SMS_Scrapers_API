"""AI drafting of the NCO appraisal's five free-text sections.

Staff jot down the overall points they want to make ("high attender, great on
drill, won't tell cadets off, needs to lead a session on his own") and this turns
them into the written appraisal. It is a *drafting* aid — everything comes back
into the editable form for staff to correct before saving.

The prompt carries two pieces of squadron context so the wording matches what
317 actually writes: the appraisal word bank (the agreed vocabulary for
strengths, weaknesses and targets) and one real worked example. Both are
reference material only — the model must never award a strength or weakness the
staff notes don't support, which is the one failure mode that would matter here.
"""

from core.llm import generate

SYSTEM_PROMPT = (
    "You write formal NCO appraisals for a Royal Air Force Air Cadets squadron. "
    "You write only what the assessing staff member's notes support."
)

# The squadron's agreed appraisal vocabulary, grouped as staff use it. Given to
# the model as phrasing to draw on — not a menu to pick from at random.
WORD_BANK = """
STRENGTHS

Commitment, Reliability & Professionalism
- Demonstrates exceptional dedication and reliability, with consistently high attendance and follow-through on assigned tasks.
- Dependable presence within the NCO team; trusted to deliver quality outcomes even at short notice.
- Maintains strong personal standards of dress, punctuality, and deportment, setting a positive example for cadets.

Leadership & Command
- Displays confidence and competence rooted in technical skill, enabling effective command and control.
- Calm, methodical, and level-headed under pressure, particularly during periods of increased responsibility or squadron transition.
- Shows strong potential as a stabilising figure within the NCO team and as a representative at Sector/Wing level.
- Demonstrates a clear transition from tactical task execution to higher-level strategic awareness and planning.

Instruction, Knowledge & Skill Transfer
- Academically strong, leveraging qualifications (e.g. MOI, QAIC, Synthetic Training) to deliver complex instruction effectively.
- Recognised as a subject matter expert in specific disciplines (e.g. drill, aviation, space).
- Actively transfers skills and qualifications to develop junior cadets and NCOs.

Interpersonal Skills & Welfare
- Personable, approachable, and considerate; builds strong rapport across cadet and NCO groups.
- Effective in welfare and advisory roles, demonstrating empathy and emotional intelligence.
- Improved emotional regulation and mood consistency, contributing to predictable and stable leadership.

Adaptability, Initiative & Growth Mindset
- Highly adaptable; able to step into a wide range of roles at short notice.
- Proactive and forthcoming, regularly volunteering for activities and representation.
- Demonstrates resilience through adversity and a quiet, organic approach to personal development.
- Integrates well into teams, even following periods of absence.

WEAKNESSES

Authority, Assertiveness & Presence
- Ongoing difficulty balancing "friend vs NCO" boundaries, sometimes leading to reluctance in enforcing discipline.
- Hesitant to tackle low-level poor behaviour or conflict head-on, often to avoid confrontation or unpopularity.
- Can struggle to project a strong command voice or assert presence, particularly around larger personalities or large groups.

Leadership Consistency & Professional Boundaries
- Occasional lapses into cadet-like behaviours when not directly tasked or supervised.
- Influence from dominant peers can overshadow personal leadership voice.
- Tendency toward people-pleasing, prioritising harmony over necessary firmness.

Management, Initiative & Strategic Thinking
- Reliance on senior NCO direction for relatively simple tasks; needs greater self-direction.
- Delegation weaknesses, including taking on too much personally and failing to follow up on assigned tasks.
- Risk of overextension or burnout due to poor workload sharing.
- Strategic understanding can remain theoretical rather than applied in practice.

Confidence, Resilience & Decision-Making
- Can dwell excessively on mistakes, slowing recovery and forward momentum.
- Tentative or overly cautious decision-making when empowered to act independently.
- Underestimates own ability, leading to reduced conviction and authority.
- Internal pressure and nerves can result in flustered delivery in high-stakes situations.

Development Focus & Knowledge Gaps
- Tendency to prioritise breadth of qualifications over mastery of core disciplines.
- Occasional gaps in drill or uniform knowledge following rapid progression.
- Hesitation to lead outside familiar squadron environments.

TARGETS (development objectives)

Leadership Presence & Authority
- Assert a visible and confident presence so that standards improve immediately upon arrival.
- Enforce dress, behaviour, and discipline issues consistently with polite but firm challenges.
- Maintain clear professional boundaries between rank and friendship at all times.

Independent Leadership & Initiative
- Step forward to lead sessions independently, building confidence without senior NCO support.
- Transition from being a "doer" to an "initiator" by identifying squadron-wide issues and proposing solutions proactively.
- Volunteer for leadership roles in unfamiliar or off-squadron environments.

Skill Mastery & Professional Growth
- Move from broad competence to subject matter expertise in one or two core disciplines.
- Attend Wing-level leadership and MOI courses to benchmark performance beyond the squadron.
- Apply qualifications directly to squadron improvement rather than personal achievement alone.

Mentorship & Team Development
- Identify and mentor specific at-risk or struggling cadets within the flight.
- Use personal accolades and experience to develop others, prioritising team growth over self-promotion.
- Actively develop junior NCOs through trust, delegation, and accountability.

Communication & Management
- Improve concise information flow across the NCO chain, ensuring shared understanding of objectives.
- Use correct reporting pathways (e.g. Sgt/FS link) to maintain effective vertical communication.
- Follow up consistently on delegated tasks to ensure completion to the required standard.
"""

# A real 317 appraisal, to fix the register and the shape of each section:
# flowing prose for the first two, labelled points for strengths/weaknesses,
# short imperatives for targets.
EXAMPLE_APPRAISAL = """
===GENERAL_OBSERVATIONS===
Cpl Sawczuk has always been a valued member at 317. His determination and commitment to the organisation has allowed him to rightfully earn the rank of a Cpl. It is clear that Cpl Sawczuk faces challenges with effectively communicating how he feels to others which recently has caused him to shut down. It is still very important for him to continue to remain resilient in challenging times. Cpl Sawczuk always looks for ways to help make the squadron parade nights run more smoothly which has made him a reliable member of the NCO team.

===EFFECTIVENESS_IN_ROLE===
Cpl Sawczuk is a high attender at squadron, always ensuring that the parade night is ready and working with his fellow JNCOs to help when required. It is clear that Cpl Sawczuk is always actively seeking ways to develop himself further at squadron by taking on responsibilities that can help further his leadership and confidence. However, Cpl Sawczuk can become very emotional and will show clear visible signs of stress when something does not work out. It is worth acknowledging that every NCO has made mistakes whether on a smaller or larger scale but it is just a part of the learning journey. Cpl Sawczuk must learn how to not show these signs in front of the cadets as he is who they look up to and he must show that he is someone they can put full confidence in.

===STRENGTHS===
Determination & Passion - Cpl Sawczuk embodies the Squadron First Approach to a T, he has poured countless hours of work into himself and the squadron in order to develop. This does not go unnoticed.
Reliable - Due to his commitment, Cpl Sawczuk is a constant presence on squadron activities, this means he is in the loop with developments on squadron and is always there to complete tasks or fulfil roles and responsibilities.
Directly Task Oriented - When given a single task, Cpl Sawczuk ensures it is completed, whether of his own volition or following up on delegation. This is a vital part of being an effective JNCO.

===WEAKNESSES===
Easily Overwhelmed - Whilst good with simple or singular tasks, we have seen Cpl Sawczuk struggle when presented with multiple situations to sort out. NCOs need to be adaptable and quick-thinking to enable the smooth flow of activities or parade nights. Internal pressure and nerves have resulted in flustered delivery in pressure situations.
Non-Approachable - Cadets do not approach Cpl Sawczuk for guidance or advice, it is difficult to see a connection between the two parties. As an NCO, Cpl Sawczuk should invest energy into creating bonds with the cadets in his flight and becoming a point of information for them.
Actioning Thoughts and Processes - When watching Cpl Sawczuk on a parade night, it is sometimes difficult to differentiate between a lack of initiative and an inability to act. JNCOs are vital in the control and direction of the cadets, so they need to recognise that when situations are unfolding in front of them, more than likely, there are actions that should be taken by them.

===TARGETS===
Become an active part of the training programme by running sessions or specialising in a subject to teach cadets.
Explore wider cadet experiences E.g. Attend camps to see how other cadets in the corps operate.
Work on building a stronger rapport with cadets on squadron as you are there to bridge the gap between SNCOs and Cadets.
Begin finding step by step solutions to problems and reflect afterwards to see what could be done better next time.
"""

PROMPT_TEMPLATE = """
Write the five sections of an NCO appraisal for {name}, a cadet NCO at 317 (Failsworth) Squadron.

WHO THIS IS ABOUT
- Name and rank (use exactly this form when referring to them): {name}
- Age: {age}
- Squadron attendance: {attendance}

THE ASSESSING STAFF MEMBER'S NOTES — the only facts you may use:
{points}

HARD RULES
- Every point you make must trace back to the notes above. Never invent an
  incident, qualification, course, camp, role or behaviour that is not in them.
- If the notes are thin, write less. Short and true beats padded.
- Do not contradict the notes to be kinder, and do not soften a stated concern
  out of existence — this is a formal record staff and the NCO both read.
- Refer to them as "{name}" (or "they") throughout. Never guess a gender: if the
  notes don't make it explicit, use they/them.
- Address the appraisal about them in the third person for the first four
  sections; the Targets are written to them ("Step forward to lead...").
- British English. No headings, no markdown, no bullet characters.

SECTIONS AND THEIR SHAPE
1. General Observations — 3 to 5 sentences of flowing prose. Who they are on
   squadron overall, what they contribute, and the honest headline.
2. Effectiveness in Role — 4 to 6 sentences of flowing prose. How they actually
   perform the NCO job: attendance, reliability, running things, and the "however"
   where performance falls short.
3. Strengths — 2 to 4 points. Each point is ONE line, formatted exactly as
   "Label - explanation.", where the label is a two-or-three-word name for the
   strength and the explanation is one or two sentences of evidence.
4. Weaknesses — 2 to 4 points, same "Label - explanation." format. Say what the
   gap is, why it matters to the role, and what good would look like. Be direct
   but never personal or unkind.
5. Targets — 3 to 5 lines, one objective per line, written as an instruction to
   them. No numbering (it is added afterwards). Each target must be concrete
   enough to judge as done or not done by the next review.

VOCABULARY
The squadron's appraisal word bank is below. Draw on its phrasing where it fits
the notes; adapt it to this NCO rather than pasting it, and never use a line from
it that the notes don't support.
{word_bank}

WORKED EXAMPLE
A real appraisal from this squadron, to show the register and shape. Do not reuse
its content — only its style:
{example}

Return EXACTLY this format, with nothing before or after it:

===GENERAL_OBSERVATIONS===
<text>

===EFFECTIVENESS_IN_ROLE===
<text>

===STRENGTHS===
<one point per line>

===WEAKNESSES===
<one point per line>

===TARGETS===
<one target per line>
"""

# Marker → the appraisal field it fills, in the order the model returns them.
SECTION_MARKERS = [
    ("GENERAL_OBSERVATIONS", "general_observations"),
    ("EFFECTIVENESS_IN_ROLE", "effectiveness_in_role"),
    ("STRENGTHS", "strengths"),
    ("WEAKNESSES", "weaknesses"),
    ("TARGETS", "targets"),
]


def _split_sections(output: str) -> dict:
    """Pull each ===MARKER=== block out of the model's answer.

    Parsed by slicing between markers rather than one regex per section, so a
    section that happens to contain "===" inside it can't swallow the next one.
    A marker the model dropped comes back as "" — the caller shows the draft in
    an editable form, so a missing section is a gap to fill in, not an error.
    """
    positions = []
    for marker, field in SECTION_MARKERS:
        token = f"==={marker}==="
        index = output.find(token)
        if index != -1:
            positions.append((index + len(token), field))
    positions.sort()

    sections = {field: "" for _, field in SECTION_MARKERS}
    for i, (start, field) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(output)
        text = output[start:end]
        # Trim the next marker's own token off the tail of this slice.
        for marker, _ in SECTION_MARKERS:
            text = text.split(f"==={marker}===")[0]
        sections[field] = _clean(text)
    return sections


def _clean(text: str) -> str:
    """Strip the markdown and bullet characters the models sprinkle in despite
    being told not to, and collapse runs of blank lines."""
    lines = []
    for raw in text.strip().split("\n"):
        line = raw.strip().lstrip("-•*").strip()
        line = line.replace("**", "").replace("__", "")
        if line or (lines and lines[-1]):
            lines.append(line)
    return "\n".join(lines).strip()


def generate_appraisal(name: str, age: str, attendance: str, points: str) -> tuple[dict, str]:
    """Draft the five sections from staff notes.

    Returns ``({field: text}, model_id)`` — the model id is whichever model in
    core.llm's chain answered, so the UI can flag a quota fallback.
    """
    prompt = PROMPT_TEMPLATE.format(
        name=name or "the NCO",
        age=age or "not recorded",
        attendance=attendance or "not recorded",
        points=points.strip(),
        word_bank=WORD_BANK,
        example=EXAMPLE_APPRAISAL,
    )
    output, model_id = generate(prompt, SYSTEM_PROMPT, temperature=0.7, groq_max_tokens=4000)
    return _split_sections(output), model_id
