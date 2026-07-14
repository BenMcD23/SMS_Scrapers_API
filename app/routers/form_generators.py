"""Form generators — mileage lookup and the F1771e travel claim."""

import io
import os
import tempfile
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.db import get_db, get_or_create_user
from core.security import require_staff

router = APIRouter()

TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "word_templates", "F1771e_template.docx"
)
HTD_TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "word_templates", "7101_HTD.docx"
)


class MileageRequest(BaseModel):
    from_address: str
    to_address: str


class F1771eJourney(BaseModel):
    model_config = {"populate_by_name": True}
    dateOfJourney:      str
    timeOfDeparture:    str
    timeOfArrival:      str
    from_:              str = Field(alias="from")
    to:                 str
    natureOfActivity:   str
    nameRankNo:         str
    gbtHotelRef:        str
    miscExpenses:       str
    numberOfPassengers: str
    method:             str
    mileageClaimed:     str


class F1771eRequest(BaseModel):
    journeys: list[F1771eJourney]


class HTDMonth(BaseModel):
    label: str          # "MM/YY"
    journeys: int


class HTDRequest(BaseModel):
    rank: str = ""
    initials: str = ""
    surname: str = ""
    service_number: str = ""
    street_house_num: str = ""
    town: str = ""
    city: str = ""
    postcode: str = ""
    distance: float = 0.0
    bank_last3: str = ""
    date: str = ""        # dd/mm/yyyy
    months: list[HTDMonth] = []


async def geocode(client: httpx.AsyncClient, address: str) -> tuple[float, float]:
    resp = await client.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": address, "format": "json", "limit": 1},
        headers={"User-Agent": "317-SMS-Site/1.0"},
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise HTTPException(status_code=422, detail=f"Could not geocode address: {address}")
    return float(results[0]["lon"]), float(results[0]["lat"])


@router.post("/form-generators/calculate-mileage")
async def calculate_mileage(
    data: MileageRequest,
    idinfo: dict = Depends(require_staff),
):
    async with httpx.AsyncClient() as client:
        from_lon, from_lat = await geocode(client, data.from_address)
        to_lon, to_lat = await geocode(client, data.to_address)
        resp = await client.get(
            f"https://router.project-osrm.org/route/v1/driving/{from_lon},{from_lat};{to_lon},{to_lat}",
            params={"overview": "false"},
            timeout=10,
        )
        resp.raise_for_status()
        route = resp.json()
    if route.get("code") != "Ok" or not route.get("routes"):
        raise HTTPException(status_code=422, detail="Could not calculate route between addresses.")
    distance_metres = route["routes"][0]["distance"]
    miles = round(distance_metres / 1609.344, 1)
    return {"miles": miles}


@router.post("/form-generators/f1771e")
def generate_f1771e(
    data: F1771eRequest,
    db: Session = Depends(get_db),
    idinfo: dict = Depends(require_staff),
):
    from form_generators.F1771e_gen import fill_form

    user = get_or_create_user(db, idinfo)
    p = user.profile

    personal = {
        "rank":        p.rank        if p else "",
        "initials":    p.initials    if p else "",
        "surname":     p.surname     if p else "",
        "jpa_number":  p.jpa_number  if p else "",
        "appointment": p.appointment if p else "",
        "car_reg":     p.car_reg     if p else "",
    }

    journeys = []
    for j in data.journeys:
        try:
            date_str = datetime.strptime(j.dateOfJourney, "%Y-%m-%d").strftime("%d/%m/%y")
        except ValueError:
            date_str = j.dateOfJourney

        journeys.append({
            "date":          date_str,
            "time_depart":   j.timeOfDeparture,
            "time_arrive":   j.timeOfArrival,
            "from":          j.from_.replace("\n", ", "),
            "to":            j.to.replace("\n", ", "),
            "activity":      j.natureOfActivity,
            "name_rank_pax": j.nameRankNo,
            "hotel_ref":     j.gbtHotelRef,
            "misc_expenses": j.miscExpenses,
            "passengers":    j.numberOfPassengers,
            "method":        j.method,
            "miles":         j.mileageClaimed,
        })

    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        fill_form(
            template_path=TEMPLATE_PATH,
            output_path=tmp_path,
            personal=personal,
            journeys=journeys,
        )
        with open(tmp_path, "rb") as f:
            doc_bytes = f.read()
    finally:
        os.unlink(tmp_path)

    today = datetime.now().strftime("%Y%m%d")
    surname = (personal["surname"] or "UNKNOWN").upper().replace(" ", "-")
    jpa = (personal["jpa_number"] or "UNKNOWN").replace(" ", "")
    filename = f"{today}-F1771-{surname}-{jpa}-OSP.docx"

    return StreamingResponse(
        io.BytesIO(doc_bytes),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/form-generators/htd")
def generate_htd(
    data: HTDRequest,
    idinfo: dict = Depends(require_staff),
):
    from form_generators.HTD_gen import fill_form, compute_htd

    calc = compute_htd(data.distance, [m.journeys for m in data.months])

    sn = "".join(c for c in data.service_number if c.isdigit())[:8]
    bank = "".join(c for c in data.bank_last3 if c.isdigit())[:3]

    context = {
        "rank":             data.rank,
        "initials":         data.initials,
        "surname":          data.surname.upper(),
        "street_house_num": data.street_house_num,
        "town":             data.town,
        "city":             data.city,
        "postcode":         data.postcode,
        "distance":         f"{data.distance:g}",
        "date":             data.date,
        "car_cost":         f"{calc['car_cost']:.2f}",
        "tota_jl_cost":     f"{calc['total_a']:.2f}",
        "total_claimed":    f"{calc['total_claimed']:.2f}",
    }
    for i in range(8):
        context[f"sn_{i + 1}"] = sn[i] if i < len(sn) else ""
    for i in range(3):
        context[f"b_{i + 1}"] = bank[i] if i < len(bank) else ""
    for i in range(6):
        m = data.months[i] if i < len(data.months) else None
        context[f"my_{i + 1}"]     = m.label if m else ""
        context[f"num_j_{i + 1}"]  = str(m.journeys) if m else ""
        context[f"amount_{i + 1}"] = f"{calc['amounts'][i]:.2f}" if i < len(calc["amounts"]) else ""
        context[f"total_{i + 1}"]  = f"{calc['totals'][i]:.2f}" if i < len(calc["totals"]) else ""

    buffer = io.BytesIO()
    fill_form(HTD_TEMPLATE_PATH, buffer, context)
    buffer.seek(0)

    surname = (data.surname or "UNKNOWN").upper().replace(" ", "_")
    filename = f"HTD_{surname}.docx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
