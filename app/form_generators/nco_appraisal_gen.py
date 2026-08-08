"""NCO appraisal → the squadron's Word template.

The template is the paper form with `{{ }}` placeholders in its table cells, so
filling it is pure substitution (form_generators.docx_fill). What lives here is
the mapping from a stored appraisal to what the form prints: the yes/no boxes,
the "12 months (5 Jan 2027)" review line, and the numbered targets list.

`appraisal_context` is shared with the PDF builder so the two exports can never
word the same field differently.
"""

from form_generators.docx_fill import fill_template

# Section keys the appraisal's free text is stored under, paired with the
# template placeholder they fill. Targets are the odd one out — they go in as a
# numbered list, so the template calls the field `targets_numbered`.
SECTION_PLACEHOLDERS = {
    "general_observations": "general_observations",
    "effectiveness_in_role": "effectiveness_in_role",
    "strengths": "strengths",
    "weaknesses": "weaknesses",
}


def yes_no(value: bool) -> str:
    return "Yes" if value else "No"


def number_targets(targets: str) -> str:
    """One target per line, numbered. Lines the writer (or the AI) already
    numbered or bulleted are renumbered rather than double-marked."""
    lines = []
    for raw in (targets or "").split("\n"):
        line = raw.strip().lstrip("-•*").strip()
        # Drop a leading "1." / "2)" so re-saving an appraisal doesn't produce
        # "1. 1. Assert a visible presence".
        while line[:1].isdigit():
            stripped = line.lstrip("0123456789").lstrip()
            if stripped[:1] in (".", ")"):
                line = stripped[1:].strip()
            else:
                break
        if line:
            lines.append(line)
    return "\n".join(f"{i}. {line}" for i, line in enumerate(lines, 1))


def next_review_label(months: int, next_review_date) -> str:
    """"12 months (5 Jan 2027)" — the interval staff chose plus the date it
    lands on, so the form is readable without doing the arithmetic."""
    label = f"{months} months"
    if next_review_date:
        label += f" ({next_review_date.strftime('%d/%m/%Y')})"
    return label


def appraisal_context(appraisal) -> dict:
    """Everything the template (and the PDF) prints, from an NcoAppraisal row."""
    return {
        "name": appraisal.nco_name or "",
        "age": appraisal.age or "",
        "attendance": appraisal.attendance or "",
        **{
            placeholder: getattr(appraisal, field) or ""
            for field, placeholder in SECTION_PLACEHOLDERS.items()
        },
        "targets_numbered": number_targets(appraisal.targets),
        "next_review": next_review_label(
            appraisal.next_review_months, appraisal.next_review_date
        ),
        "cause_for_concern": yes_no(appraisal.cause_for_concern),
        "extend_probation": yes_no(appraisal.extend_probation),
    }


def build_appraisal_docx(template_path: str, output, appraisal) -> None:
    """Fill the appraisal template, writing to `output` (path or file-like)."""
    fill_template(template_path, output, appraisal_context(appraisal))
