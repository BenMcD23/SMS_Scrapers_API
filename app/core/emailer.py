"""Outbound email via the Gmail API, plus the HTML templates we send."""

import base64
import os
import email.mime.multipart
import email.mime.text
import email.mime.base
import email.encoders

from googleapiclient.discovery import build as google_build

from core.config import SA_EMAIL, SA_PRIVATE_KEY, NOREPLY_EMAIL
from core.security import _service_account_creds

FOOTER = (
    '<p style="margin:20px 0 0;font-size:12px;color:#999">'
    "This is an automated notification from 317 SMS, do not reply, this mailbox isn't monitored</p>"
)


def send_email(to: str, subject: str, html_body: str, attachment: bytes | None = None,
               attachment_filename: str = "attachment.pdf") -> None:
    if os.getenv("EMAIL_DISABLED", "").lower() in ("1", "true", "yes"):
        print(f"[send_email] skipped (EMAIL_DISABLED): would send to {to}: {subject}")
        return
    if not SA_EMAIL or not SA_PRIVATE_KEY or not NOREPLY_EMAIL:
        print("[send_email] skipped: service account or noreply email not configured")
        return
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
        if attachment:
            att = email.mime.base.MIMEBase("application", "pdf")
            att.set_payload(attachment)
            email.encoders.encode_base64(att)
            att.add_header("Content-Disposition", "attachment", filename=attachment_filename)
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
