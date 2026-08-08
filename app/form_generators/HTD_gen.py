"""ACCTS 7101 Home-to-Duty travel claim money logic.

Unlike F1771e (dynamic journey rows), the 7101 is a fixed form — just
placeholder substitution, which lives in form_generators.docx_fill along with
the template's mixed brace styles: double `{{ rank }}`, single `{ sn_1 }`, and
the malformed `{{ num_j_2 }` (one closing brace).
"""

from form_generators.docx_fill import fill_template

RATE_PER_MILE = 0.25
UPLIFT = 1.07  # 25p/mile, then +7% on top


def compute_htd(distance: float, journeys: list[int]) -> dict:
    """Pure money logic. `journeys` = nights attended per month (≤ 6 entries).

    car_cost = miles × 25p + 7%; Total A carries that same figure down;
    each month's amount = journeys × Total A; total claimed = sum of amounts.
    """
    car_cost = round(distance * RATE_PER_MILE * UPLIFT, 2)
    total_a = car_cost  # 7% already applied at car_cost, carried down
    amounts = [round(j * total_a, 2) for j in journeys]
    return {
        "car_cost": car_cost,
        "total_a": total_a,
        "amounts": amounts,
        "totals": list(amounts),  # claim-for-self only; passenger column blank
        "total_claimed": round(sum(amounts), 2),
    }


def fill_form(template_path: str, output, context: dict):
    """Fill the 7101 template and write to `output` (path or file-like)."""
    fill_template(template_path, output, context)
