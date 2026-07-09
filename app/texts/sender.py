"""GOV.UK Notify sending plus the Tue/Thu scheduled send job."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from notifications_python_client.notifications import NotificationsAPIClient
from sqlalchemy import func
from sqlalchemy.orm import Session

from database.database import SessionLocal
from database.models import ParadeNightMessage, SmsRecipient

from core.config import NOTIFY_API_KEY, NOTIFY_SMS_TEMPLATE_ID, ALERT_EMAIL
from core.emailer import send_email, FOOTER

LONDON = ZoneInfo("Europe/London")


def _notify_client() -> NotificationsAPIClient:
    if not NOTIFY_API_KEY or not NOTIFY_SMS_TEMPLATE_ID:
        raise RuntimeError("NOTIFY_API_KEY / NOTIFY_SMS_TEMPLATE_ID not configured")
    return NotificationsAPIClient(NOTIFY_API_KEY)


def build_message_body(message: ParadeNightMessage) -> str:
    """Assemble the full SMS body — the Notify template is just the greeting
    plus ((body)), so this is exactly what lands on phones (and what the UI
    preview shows). Sections are skipped entirely when empty."""
    sections = [
        f"Uniform: {message.uniform}" if message.uniform else "",
        message.main_message,
        f"C Flight\n{message.c_flight_message}" if message.c_flight_message else "",
        f"DNCO: {message.dnco}" if message.dnco else "",
    ]
    return "\n\n".join(s for s in sections if s)


def build_personalisation(message: ParadeNightMessage, rank: str, surname: str) -> dict:
    return {
        "rank": rank,
        "surname": surname,
        "body": build_message_body(message),
    }


def send_test_sms(message: ParadeNightMessage, phone_number: str) -> None:
    _notify_client().send_sms_notification(
        phone_number=phone_number,
        template_id=NOTIFY_SMS_TEMPLATE_ID,
        personalisation=build_personalisation(message, rank="Test", surname="Recipient"),
    )


def send_parade_message(db: Session, message: ParadeNightMessage) -> list[dict]:
    """Send `message` to every recipient, record results, and mark it sent."""
    client = _notify_client()
    recipients = db.query(SmsRecipient).all()
    if not recipients:
        raise RuntimeError("No SMS recipients configured")

    results = []
    for r in recipients:
        try:
            client.send_sms_notification(
                phone_number=r.phone_number,
                template_id=NOTIFY_SMS_TEMPLATE_ID,
                personalisation=build_personalisation(message, rank=r.rank, surname=r.surname),
            )
            results.append({"phone": r.phone_number, "status": "sent"})
        except Exception as e:
            results.append({"phone": r.phone_number, "status": "failed", "error": str(e)})

    message.status = "sent"
    message.sent_at = datetime.now()
    message.send_results = results
    db.commit()
    return results


def _alert_staff(parade_date, detail: str) -> None:
    date_str = parade_date.strftime("%A %d %B %Y")
    send_email(
        ALERT_EMAIL,
        f"Parade night text problem — {date_str}",
        f"""
        <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px">
          <h2 style="margin:0 0 4px">Parade Night Text Problem</h2>
          <hr style="border:none;border-top:2px solid #c62828;margin:0 0 20px">
          <p>Scheduled text for <strong>{date_str}</strong>: {detail}</p>
          <p>Check the Texts section in the SMS site — a text can be sent manually with "Send now".</p>
          {FOOTER}
        </div>
        """,
    )


def scheduled_send_job() -> None:
    """Runs Tue/Thu 16:00 London — sends the ready message for tomorrow's parade night."""
    target = (datetime.now(LONDON) + timedelta(days=1)).date()

    db = SessionLocal()
    try:
        message = (
            db.query(ParadeNightMessage)
            .filter(func.date(ParadeNightMessage.parade_date) == target)
            .first()
        )
        if message is None:
            _alert_staff(datetime(target.year, target.month, target.day),
                         "not sent — no message has been generated for this date")
        elif message.status == "ready":
            results = send_parade_message(db, message)
            failed = [r for r in results if r["status"] == "failed"]
            if failed:
                _alert_staff(message.parade_date,
                             f"sent, but {len(failed)} of {len(results)} messages failed")
        elif message.status == "draft":
            _alert_staff(message.parade_date,
                         "not sent — the message exists but was not marked as ready")
        # already "sent" — nothing to do
    except Exception as e:
        print(f"[scheduled_send_job] error: {e}")
    finally:
        db.close()
