"""Outbound email via the Gmail API, plus the HTML templates we send."""

import base64
import html
import os
import email.mime.multipart
import email.mime.text
import email.mime.base
import email.encoders

from googleapiclient.discovery import build as google_build

from core.config import SA_EMAIL, SA_PRIVATE_KEY, NOREPLY_EMAIL, SITE_BASE_URL
from core.security import _service_account_creds

FOOTER = (
    '<p style="margin:20px 0 0;font-size:12px;color:#999">'
    "This is an automated notification from 317 SMS, do not reply, this mailbox isn't monitored</p>"
)


def send_email(to: str, subject: str, html_body: str, attachment: bytes | None = None,
               attachment_filename: str = "attachment.pdf",
               attachments: list[tuple[str, bytes, str]] | None = None) -> None:
    """Send an HTML email, optionally with attachments.

    ``attachment``/``attachment_filename`` are the legacy single-PDF params (kept
    for existing callers). ``attachments`` is a list of ``(filename, data,
    mime_type)`` tuples for multiple/mixed files; both may be combined.
    """
    if os.getenv("EMAIL_DISABLED", "").lower() in ("1", "true", "yes"):
        print(f"[send_email] skipped (EMAIL_DISABLED): would send to {to}: {subject}")
        return
    if not SA_EMAIL or not SA_PRIVATE_KEY or not NOREPLY_EMAIL:
        print("[send_email] skipped: service account or noreply email not configured")
        return

    # Normalise the legacy single attachment into the list form.
    all_attachments: list[tuple[str, bytes, str]] = list(attachments or [])
    if attachment:
        all_attachments.insert(0, (attachment_filename, attachment, "application/pdf"))

    try:
        creds = _service_account_creds(
            ["https://www.googleapis.com/auth/gmail.send"]
        ).with_subject(NOREPLY_EMAIL)
        gmail = google_build("gmail", "v1", credentials=creds, cache_discovery=False)

        msg = email.mime.multipart.MIMEMultipart("mixed")
        msg["From"] = f"317 ATC <{NOREPLY_EMAIL}>"
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(email.mime.text.MIMEText(html_body, "html"))
        for filename, data, mime_type in all_attachments:
            maintype, _, subtype = (mime_type or "application/octet-stream").partition("/")
            att = email.mime.base.MIMEBase(maintype, subtype or "octet-stream")
            att.set_payload(data)
            email.encoders.encode_base64(att)
            att.add_header("Content-Disposition", "attachment", filename=filename)
            msg.attach(att)

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        gmail.users().messages().send(userId="me", body={"raw": raw}).execute()
        print(f"[send_email] sent to {to}: {subject}")
    except Exception as e:
        print(f"[send_email] error sending to {to}: {e}")


def assessment_email_html(cadet_name: str, assessment_type: str, passed: bool, date: str, assessor_name: str) -> str:
    result_colour = "#2e7d32" if passed else "#c62828"
    result_text = "PASSED" if passed else "NOT PASSED"
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px">
      <h2 style="margin:0 0 4px">Assessment Completed</h2>
      <hr style="border:none;border-top:2px solid #1565c0;margin:0 0 20px">
      <table style="width:100%;border-collapse:collapse">
        <tr><td style="padding:6px 0;color:#555;width:140px">Cadet</td><td style="padding:6px 0;font-weight:bold">{cadet_name}</td></tr>
        <tr><td style="padding:6px 0;color:#555">Assessment</td><td style="padding:6px 0">{assessment_type}</td></tr>
        <tr><td style="padding:6px 0;color:#555">Date</td><td style="padding:6px 0">{date}</td></tr>
        <tr><td style="padding:6px 0;color:#555">Assessor</td><td style="padding:6px 0">{assessor_name}</td></tr>
        <tr><td style="padding:6px 0;color:#555">Result</td><td style="padding:6px 0;font-weight:bold;color:{result_colour}">{result_text}</td></tr>
      </table>
      {FOOTER}
    </div>
    """


def quali_expiry_email_html(rows: list[tuple[str, str, str, int]]) -> str:
    """rows: (cadet_name, qualification, expires_str, days_left), soonest first."""
    trs = "".join(
        f'<tr>'
        f'<td style="padding:6px 8px;border-bottom:1px solid #eee">{name}</td>'
        f'<td style="padding:6px 8px;border-bottom:1px solid #eee">{qual}</td>'
        f'<td style="padding:6px 8px;border-bottom:1px solid #eee">{expires}</td>'
        f'<td style="padding:6px 8px;border-bottom:1px solid #eee;font-weight:bold;'
        f'color:{"#c62828" if days <= 30 else "#e65100"}">{days} days</td>'
        f'</tr>'
        for name, qual, expires, days in rows
    )
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;padding:24px">
      <h2 style="margin:0 0 4px">Qualifications Expiring Soon</h2>
      <hr style="border:none;border-top:2px solid #1565c0;margin:0 0 20px">
      <p>The following cadet qualifications expire within the next 3 months:</p>
      <table style="width:100%;border-collapse:collapse;font-size:14px">
        <tr>
          <th style="text-align:left;padding:6px 8px;border-bottom:2px solid #ccc">Cadet</th>
          <th style="text-align:left;padding:6px 8px;border-bottom:2px solid #ccc">Qualification</th>
          <th style="text-align:left;padding:6px 8px;border-bottom:2px solid #ccc">Expires</th>
          <th style="text-align:left;padding:6px 8px;border-bottom:2px solid #ccc">Time Left</th>
        </tr>
        {trs}
      </table>
      {FOOTER}
    </div>
    """


def ban_alert_email_html(rows: list[tuple[str, str]]) -> str:
    """rows: (cadet_name, event_title) — a banned cadet found signed up to an event."""
    trs = "".join(
        f'<tr>'
        f'<td style="padding:6px 8px;border-bottom:1px solid #eee;font-weight:bold">{name}</td>'
        f'<td style="padding:6px 8px;border-bottom:1px solid #eee">{event}</td>'
        f'</tr>'
        for name, event in rows
    )
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;padding:24px">
      <h2 style="margin:0 0 4px">Banned Cadet(s) On Events</h2>
      <hr style="border:none;border-top:2px solid #c62828;margin:0 0 20px">
      <p>The following banned cadet(s) are signed up to events on Bader:</p>
      <table style="width:100%;border-collapse:collapse;font-size:14px">
        <tr>
          <th style="text-align:left;padding:6px 8px;border-bottom:2px solid #ccc">Cadet</th>
          <th style="text-align:left;padding:6px 8px;border-bottom:2px solid #ccc">Event</th>
        </tr>
        {trs}
      </table>
      {FOOTER}
    </div>
    """


def ready_to_collect_email_html(cadet_name: str, item_name: str, item_kind: str, size: str = "") -> str:
    size_line = f'<p style="font-size:14px;color:#555;margin:0 0 16px">Size: {size}</p>' if size else ""
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px">
      <h2 style="margin:0 0 4px">Item Ready to Collect</h2>
      <hr style="border:none;border-top:2px solid #1565c0;margin:0 0 20px">
      <p>Hi {cadet_name},</p>
      <p>Your {item_kind} item is now ready to collect from the stores:</p>
      <p style="font-size:18px;font-weight:bold;margin:16px 0 4px">{item_name}</p>
      {size_line}
      <p>If you have any questions, speak to a member of staff.</p>
      {FOOTER}
    </div>
    """


def _gbp(amount: float) -> str:
    return f"£{amount:,.2f}"


def committee_request_submitted_html(reference: str, title: str, total: float,
                                     requester_name: str) -> str:
    """To the OC when a staff member submits a new committee request."""
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px">
      <h2 style="margin:0 0 4px">New Committee Request</h2>
      <hr style="border:none;border-top:2px solid #1565c0;margin:0 0 20px">
      <p>A new committee purchase request has been submitted for your review.</p>
      <table style="width:100%;border-collapse:collapse">
        <tr><td style="padding:6px 0;color:#555;width:140px">Reference</td><td style="padding:6px 0;font-weight:bold">{reference}</td></tr>
        <tr><td style="padding:6px 0;color:#555">Title</td><td style="padding:6px 0">{title}</td></tr>
        <tr><td style="padding:6px 0;color:#555">Requested by</td><td style="padding:6px 0">{requester_name}</td></tr>
        <tr><td style="padding:6px 0;color:#555">Total</td><td style="padding:6px 0;font-weight:bold">{_gbp(total)}</td></tr>
      </table>
      <p style="font-size:14px;color:#555">The full request is attached as a PDF. Log in to 317 SMS to send it to the committee.</p>
      {FOOTER}
    </div>
    """


def committee_request_to_committee_html(reference: str, title: str, total: float,
                                        requester_name: str) -> str:
    """To the committee when the OC forwards a request for approval."""
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px">
      <h2 style="margin:0 0 4px">Committee Purchase Request</h2>
      <hr style="border:none;border-top:2px solid #1565c0;margin:0 0 20px">
      <p>The OC has forwarded the following purchase request for the committee's approval.</p>
      <table style="width:100%;border-collapse:collapse">
        <tr><td style="padding:6px 0;color:#555;width:140px">Reference</td><td style="padding:6px 0;font-weight:bold">{reference}</td></tr>
        <tr><td style="padding:6px 0;color:#555">Title</td><td style="padding:6px 0">{title}</td></tr>
        <tr><td style="padding:6px 0;color:#555">Requested by</td><td style="padding:6px 0">{requester_name}</td></tr>
        <tr><td style="padding:6px 0;color:#555">Total</td><td style="padding:6px 0;font-weight:bold">{_gbp(total)}</td></tr>
      </table>
      <p style="font-size:14px;color:#555">Full details are attached as a PDF.</p>
      {FOOTER}
    </div>
    """


def committee_request_decision_html(reference: str, title: str, approved: bool,
                                    reason: str | None = None) -> str:
    """To the requester when the OC approves or rejects a request."""
    colour = "#2e7d32" if approved else "#c62828"
    heading = "Committee Request Approved" if approved else "Committee Request Rejected"
    if approved:
        body = ("<p>Your committee request has been approved. You can go ahead and "
                "purchase the goods, then upload your receipts in 317 SMS and send "
                "them off for payment.</p>")
    else:
        reason_html = (f'<p style="margin:12px 0 0"><strong>Reason:</strong> {reason}</p>'
                       if reason else "")
        body = ("<p>Your committee request has been rejected. Speak to the OC if you "
                f"have any questions.</p>{reason_html}")
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px">
      <h2 style="margin:0 0 4px;color:{colour}">{heading}</h2>
      <hr style="border:none;border-top:2px solid {colour};margin:0 0 20px">
      <table style="width:100%;border-collapse:collapse">
        <tr><td style="padding:6px 0;color:#555;width:140px">Reference</td><td style="padding:6px 0;font-weight:bold">{reference}</td></tr>
        <tr><td style="padding:6px 0;color:#555">Title</td><td style="padding:6px 0">{title}</td></tr>
      </table>
      {body}
      {FOOTER}
    </div>
    """


def committee_request_withdrawn_html(reference: str, title: str,
                                     requester_name: str) -> str:
    """To the OC when the requester withdraws a request."""
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px">
      <h2 style="margin:0 0 4px">Committee Request Withdrawn</h2>
      <hr style="border:none;border-top:2px solid #6b7280;margin:0 0 20px">
      <p>{requester_name} has withdrawn a committee request.</p>
      <table style="width:100%;border-collapse:collapse">
        <tr><td style="padding:6px 0;color:#555;width:140px">Reference</td><td style="padding:6px 0;font-weight:bold">{reference}</td></tr>
        <tr><td style="padding:6px 0;color:#555">Title</td><td style="padding:6px 0">{title}</td></tr>
      </table>
      <p style="font-size:14px;color:#555">No further action is needed.</p>
      {FOOTER}
    </div>
    """


def committee_request_payment_html(reference: str, title: str, total: float,
                                   requester_name: str, account_name: str,
                                   sort_code: str, account_number: str,
                                   receipt_count: int) -> str:
    """To the committee when the requester sends receipts off for payment."""
    receipts_line = f"{receipt_count} receipt{'s' if receipt_count != 1 else ''}"
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px">
      <h2 style="margin:0 0 4px">Payment Request</h2>
      <hr style="border:none;border-top:2px solid #1565c0;margin:0 0 20px">
      <p>{requester_name} has uploaded receipts for an approved committee request
         and is requesting reimbursement.</p>
      <table style="width:100%;border-collapse:collapse">
        <tr><td style="padding:6px 0;color:#555;width:150px">Reference</td><td style="padding:6px 0;font-weight:bold">{reference}</td></tr>
        <tr><td style="padding:6px 0;color:#555">Title</td><td style="padding:6px 0">{title}</td></tr>
        <tr><td style="padding:6px 0;color:#555">Amount</td><td style="padding:6px 0;font-weight:bold">{_gbp(total)}</td></tr>
        <tr><td style="padding:6px 0;color:#555">Receipts attached</td><td style="padding:6px 0">{receipts_line}</td></tr>
      </table>
      <h3 style="margin:20px 0 4px;font-size:15px">Payment Details</h3>
      <table style="width:100%;border-collapse:collapse">
        <tr><td style="padding:6px 0;color:#555;width:150px">Account name</td><td style="padding:6px 0;font-weight:bold">{account_name}</td></tr>
        <tr><td style="padding:6px 0;color:#555">Sort code</td><td style="padding:6px 0;font-weight:bold">{sort_code}</td></tr>
        <tr><td style="padding:6px 0;color:#555">Account number</td><td style="padding:6px 0;font-weight:bold">{account_number}</td></tr>
      </table>
      <p style="font-size:14px;color:#555">The receipts and the original request are attached.</p>
      {FOOTER}
    </div>
    """


# ── NCO session plans ────────────────────────────────────────────────────────
# Unlike the committee templates above, these interpolate free-text a cadet NCO
# typed into a form, so every value goes through _esc before reaching the HTML.

def _esc(text: str | None) -> str:
    return html.escape(text or "")


def _plan_button(plan_id: int, label: str, colour: str = "#1565c0") -> str:
    return (
        f'<p style="margin:20px 0 0">'
        f'<a href="{SITE_BASE_URL}/session-plans/{plan_id}" '
        f'style="display:inline-block;background:{colour};color:#fff;text-decoration:none;'
        f'padding:10px 18px;border-radius:6px;font-size:14px">{label}</a></p>'
    )


def _plan_details_table(session_name: str, session_ic: str, session_date: str) -> str:
    return f"""
      <table style="width:100%;border-collapse:collapse">
        <tr><td style="padding:6px 0;color:#555;width:140px">Session</td><td style="padding:6px 0;font-weight:bold">{_esc(session_name)}</td></tr>
        <tr><td style="padding:6px 0;color:#555">Session I/C</td><td style="padding:6px 0">{_esc(session_ic) or "—"}</td></tr>
        <tr><td style="padding:6px 0;color:#555">Date</td><td style="padding:6px 0">{_esc(session_date) or "—"}</td></tr>
      </table>
    """


def session_plan_submitted_html(plan_id: int, session_name: str, session_ic: str,
                                session_date: str, author_name: str,
                                resubmission: bool) -> str:
    """To staff when an NCO submits (or resubmits) a session plan for approval."""
    heading = "Session Plan Resubmitted" if resubmission else "Session Plan Submitted"
    lead = (
        f"{_esc(author_name)} has updated their session plan after the requested "
        "amendments and sent it back for approval."
        if resubmission else
        f"{_esc(author_name)} has submitted a session plan for staff approval."
    )
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px">
      <h2 style="margin:0 0 4px">{heading}</h2>
      <hr style="border:none;border-top:2px solid #1565c0;margin:0 0 20px">
      <p>{lead}</p>
      {_plan_details_table(session_name, session_ic, session_date)}
      <p style="font-size:14px;color:#555">Review it in 317 SMS to approve it or send it back with
         the changes you want.</p>
      {_plan_button(plan_id, "Review session plan")}
      {FOOTER}
    </div>
    """


def session_plan_amendments_html(plan_id: int, session_name: str, session_ic: str,
                                 session_date: str, reviewer_name: str,
                                 amendment_note: str | None,
                                 section_feedback: list[tuple[str, str]]) -> str:
    """To the NCO when staff send a plan back for amendment.

    ``section_feedback`` is ``(section label, comment)`` for the sections the
    reviewer actually commented on — the paper sheet's "Staff Check & Feedback"
    column, so the NCO sees exactly which part needs work.
    """
    note_html = (
        f'<div style="margin:16px 0 0;padding:12px 14px;background:#fff4e5;'
        f'border-left:4px solid #e65100;border-radius:4px">'
        f'<p style="margin:0;white-space:pre-wrap">{_esc(amendment_note)}</p></div>'
        if amendment_note else ""
    )
    feedback_rows = "".join(
        f'<tr>'
        f'<td style="padding:8px;border-bottom:1px solid #eee;font-weight:bold;'
        f'width:170px;vertical-align:top">{_esc(label)}</td>'
        f'<td style="padding:8px;border-bottom:1px solid #eee;white-space:pre-wrap">{_esc(comment)}</td>'
        f'</tr>'
        for label, comment in section_feedback
    )
    feedback_html = (
        f'<h3 style="margin:24px 0 6px;font-size:15px">Staff Feedback</h3>'
        f'<table style="width:100%;border-collapse:collapse;font-size:14px">{feedback_rows}</table>'
        if feedback_rows else ""
    )
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px">
      <h2 style="margin:0 0 4px;color:#e65100">Session Plan Needs Amending</h2>
      <hr style="border:none;border-top:2px solid #e65100;margin:0 0 20px">
      <p>{_esc(reviewer_name)} has reviewed your session plan and would like some changes
         before it can be approved.</p>
      {_plan_details_table(session_name, session_ic, session_date)}
      {note_html}
      {feedback_html}
      <p style="font-size:14px;color:#555">Make the changes in 317 SMS and submit it again —
         staff will be told automatically.</p>
      {_plan_button(plan_id, "Open session plan", "#e65100")}
      {FOOTER}
    </div>
    """


def session_plan_approved_html(plan_id: int, session_name: str, session_ic: str,
                               session_date: str, reviewer_name: str,
                               amendment_note: str | None) -> str:
    """To the NCO when staff approve their session plan."""
    note_html = (
        f'<p style="margin:16px 0 0"><strong>Note from staff:</strong><br>'
        f'<span style="white-space:pre-wrap">{_esc(amendment_note)}</span></p>'
        if amendment_note else ""
    )
    return f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px">
      <h2 style="margin:0 0 4px;color:#2e7d32">Session Plan Approved</h2>
      <hr style="border:none;border-top:2px solid #2e7d32;margin:0 0 20px">
      <p>{_esc(reviewer_name)} has approved your session plan — you're good to run it.</p>
      {_plan_details_table(session_name, session_ic, session_date)}
      {note_html}
      {_plan_button(plan_id, "View session plan", "#2e7d32")}
      {FOOTER}
    </div>
    """
