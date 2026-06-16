from docx import Document
from docx.shared import Inches
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
import os
import io
from datetime import datetime, timedelta


from database.database import SessionLocal
from database.models import Event317

from scripts.ji_ao_ai import generate_ji_description_ai, generate_ao_description_ai


contacts = {
    "McDonald": {
        "email": "ben.mcdonald100@rafac.mod.gov.uk",
        "phone": "07743443608"
    },
    "Stone": {
        "email": "sophie.stone101@rafac.mod.gov.uk",
        "phone": "07735218557"
    },
    "Morris": {
        "email": "gareth.lloyd-morris100@rafac.mod.gov.uk",
        "phone": "07940258406"
    },
    "Doherty": {
        "email": "oc.317@rafac.mod.gov.uk",
        "phone": "07807809776"
    },
    "Gill": {
        "email": "joseph.gill100@rafac.mod.gov.uk",
        "phone": "07543659277"
    },
    "MacGregor": {
        "email": "calum.macgregor100@rafac.mod.gov.uk",
        "phone": "07944026545"
    },
    "Barker": {
        "email": "jonathon.barker100@rafac.mod.gov.uk",
        "phone": "07955063409"
    },
    "Tyrell": {
        "email": "llerytvanessa@gmail.com",
        "phone": "07514586684"
    },
    "N/A": {
        "email": "",
        "phone": ""
    }
}

# Path helpers
def get_template_path(filename):
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "word_templates", filename)
    )

def get_signature_path(last_name):
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "signatures", f"{last_name}.png")
    )


def replace_text_preserve_format(paragraph, replacements):
    """
    Replace placeholders in a paragraph, preserving formatting and images.
    Handles placeholders split across multiple runs or containing hidden characters.
    """
    for key, value in replacements.items():
        full_text = ''.join(run.text for run in paragraph.runs)
        if key in full_text:
            new_text = full_text.replace(key, str(value))
            for run in paragraph.runs:
                run.text = ''
            if paragraph.runs:
                paragraph.runs[0].text = new_text
            else:
                paragraph.add_run(new_text)

def replace_placeholder_with_signature(paragraph, placeholder, name, email, signature_path, width=Inches(2)):
    """
    Replace a placeholder in a paragraph with a signature image followed by text.
    """
    if placeholder not in paragraph.text:
        return

    # Clear existing text in the paragraph
    paragraph.text = ""

    run = paragraph.add_run()
    try:
        # Add the signature image
        run.add_picture(signature_path, width=width)
    except Exception as e:
        print(f"Could not add signature image for {name}: {e}")
        return
    
    # Add a line break before the text
    paragraph.add_run().add_break()

    # Add text immediately after the image
    paragraph.add_run(f"{name} RAFAC")


def generate_ji(event, use_ai=False):
    """Generate a JI for the selected event"""
    template_path = get_template_path("ji_template.docx")
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Template file not found: {template_path}")
    
    doc = Document(template_path)
    
    if event.date_from and event.date_to:
        if event.date_from.date() == event.date_to.date():
            date_from_to = event.date_from.strftime("%d/%m/%Y")
        else:
            date_from_to = f"{event.date_from.strftime('%d/%m/%Y')} - {event.date_to.strftime('%d/%m/%Y')}"
    else:
        date_from_to = "N/A"

    if event.location:
        first_line = getattr(event.location, 'first_line', '').strip()
        postcode = getattr(event.location, 'postcode', '').strip()
        location_text = f"{first_line}, {postcode}" if first_line and postcode else first_line or postcode or "N/A"
    else:
        location_text = "N/A"

    description = generate_ji_description_ai(event) if use_ai else (event.description or "")

    replacements = {
        "{{ title }}": event.title,
        "{{ date_from_to }}": date_from_to,
        "{{ arrival_time }}": event.date_from.strftime("%H:%M"),
        "{{ arrival_date }}": event.date_from.strftime("%d/%m/%Y"),
        "{{ departure_time }}": event.date_to.strftime("%H:%M"),
        "{{ departure_date }}": event.date_to.strftime("%d/%m/%Y"),
        "{{ description }}": description,
        "{{ location }}": location_text,
        "{{ cost }}": f"Cadets are required to pay £{event.cost:.2f} to attend this event. This can be paid via cash/card at squadron or through BACS." if event.cost and event.cost > 0 else "There is no cost for cadets to attend this event.",
        "{{ dress }}": event.dress or "",
        "{{ adult_ic }}": event.adult_ic,
        "{{ adult_ic_email }}": contacts.get(event.adult_ic.strip().split()[-1], {}).get("email", ""),
        "{{ tg_form_req }}": "TG 21/23 Forms are not required" if event.location.first_line == "317 Squadron HQ" else "TG 21/23 Forms are required"
    }

    # Replace in paragraphs
    for paragraph in doc.paragraphs:
        replace_text_preserve_format(paragraph, replacements)

    # Handle signature
    for paragraph in doc.paragraphs:
        if "{{ adult_ic_signature }}" in paragraph.text:
            last_name = event.adult_ic.strip().split()[-1]
            signature_path = get_signature_path(last_name)
            replace_placeholder_with_signature(
                paragraph,
                "{{ adult_ic_signature }}",
                name=event.adult_ic,
                email=contacts.get(last_name, {}).get("email", ""),
                signature_path=signature_path,
                width=Inches(2)
            )

    # Save to memory
    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer

def generate_ao(event, use_ai=False):
    """Generate a AO for the selected event"""
    template_path = get_template_path("ao_template.docx")
    
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Template file not found: {template_path}")

    doc = Document(template_path)

    if event.location:
        first_line = getattr(event.location, 'first_line', '').strip()
        postcode = getattr(event.location, 'postcode', '').strip()
        location_text = f"{first_line}, {postcode}" if first_line and postcode else first_line or postcode or "N/A"
    else:
        location_text = "N/A"

    replacements = {
        "{{ todays_date }}": datetime.today().strftime("%d %B %Y"),
        "{{ event_ref }}": event.reference,
        "{{ event_title }}": event.title,
        "{{ event_location }}": location_text,
        "{{ date_from }}": event.date_from.strftime("%d/%m/%Y") if event.date_from else "0",
        "{{ date_to }}": event.date_to.strftime("%d/%m/%Y") if event.date_to else "0",
        "{{ course_ic }}": f"{event.adult_ic} - {contacts.get(event.adult_ic.strip().split()[-1], {}).get('email', '')}",
        "{{ instructor_start_time }}": (event.date_from - timedelta(minutes=30)).strftime("%H:%M"),
        "{{ cadet_start_time }}": event.date_from.strftime("%H:%M"),
        "{{ departure_time }}": event.date_to.strftime("%H:%M"),
    }

    # Replace in paragraphs
    for paragraph in doc.paragraphs:
        replace_text_preserve_format(paragraph, replacements)

    # The AO template has no free-text section of its own, so the AI
    # description gets inserted as a new paragraph rather than replacing a
    # placeholder.
    if use_ai:
        description = generate_ao_description_ai(event)
        for paragraph in doc.paragraphs:
            if "{{ adult_ic_signature }}" in paragraph.text:
                paragraph.insert_paragraph_before("Activity Description:").runs[0].bold = True
                paragraph.insert_paragraph_before(description)
                paragraph.insert_paragraph_before("")
                break

    # Handle signature
    for paragraph in doc.paragraphs:
        if "{{ adult_ic_signature }}" in paragraph.text:
            last_name = event.adult_ic.strip().split()[-1]
            signature_path = get_signature_path(last_name)
            replace_placeholder_with_signature(
                paragraph,
                "{{ adult_ic_signature }}",
                name=event.adult_ic,
                email=contacts.get(last_name, {}).get("email", ""),
                signature_path=signature_path,
                width=Inches(2)
            )

    # Save to memory
    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer