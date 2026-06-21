"""Outbound email via the Gmail API, plus the HTML templates we send."""

import base64
import email.mime.multipart
import email.mime.text
import email.mime.base
import email.encoders

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build as google_build

from core.config import (
    NOREPLY_EMAIL,
    GMAIL_OAUTH_CLIENT_ID,
    GMAIL_OAUTH_CLIENT_SECRET,
    GMAIL_NOREPLY_REFRESH_TOKEN,
)

FOOTER = (
    '<p style="margin:20px 0 0;font-size:12px;color:#999">'
    "This is an automated notification from 317 SMS, do not reply, this mailbox isn't monitored</p>"
)


def send_email(to: str, subject: str, html_body: str, attachment: bytes | None = None,
               attachment_filename: str = "attachment.pdf") -> None:
    if not GMAIL_OAUTH_CLIENT_ID or not GMAIL_OAUTH_CLIENT_SECRET or not GMAIL_NOREPLY_REFRESH_TOKEN or not NOREPLY_EMAIL:
        print("[send_email] skipped: noreply Gmail credentials not configured")
        return
    try:
        # A user credential for the noreply mailbox only — it can send *as noreply*
        # and nothing else, so the sender identity is enforced by the credential
        # rather than by domain-wide delegation.
        creds = Credentials(
            None,
            refresh_token=GMAIL_NOREPLY_REFRESH_TOKEN,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GMAIL_OAUTH_CLIENT_ID,
            client_secret=GMAIL_OAUTH_CLIENT_SECRET,
            scopes=["https://www.googleapis.com/auth/gmail.send"],
        )
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
