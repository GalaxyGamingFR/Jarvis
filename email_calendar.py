"""Basic email (IMAP/SMTP) and calendar (subscribed ICS feed) awareness for Jarvis.

Deliberately avoids Google OAuth — this is a single-user local tool, so a Gmail "app password"
over IMAP/SMTP (both stdlib) is the pragmatic choice. Calendar reads a subscribed ICS feed URL
(the "secret address in iCal format" every major calendar provider exposes) rather than the
Google Calendar API, for the same reason.

Expected config.json fields (all optional — functions degrade to a clear spoken string, never
raise, if missing):
    email_address        - the Gmail (or other) address to log in as.
    email_app_password   - an app password for that account (Gmail: myaccount.google.com/apppasswords).
                            Falls back to the EMAIL_APP_PASSWORD env var.
    imap_host, imap_port - default "imap.gmail.com", 993. Override for non-Gmail providers.
    smtp_host, smtp_port - default "smtp.gmail.com", 587. Override for non-Gmail providers.
    calendar_ics_url      - the private ICS feed URL for the user's calendar (Google Calendar:
                            Settings > Settings for my calendars > Integrate calendar > "Secret
                            address in iCal format"). Falls back to the CALENDAR_ICS_URL env var.

Following the same secrets pattern as server.py's load_config(): secret/identity fields are read
with `config.get(key) or os.getenv(...)` rather than `setdefault`, so an explicit "" placeholder
copied from config.example.json doesn't permanently shadow a real value set via .env.
"""
import imaplib
import os
import smtplib
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header
from email.mime.text import MIMEText

import requests

try:
    import icalendar
    _ICALENDAR_AVAILABLE = True
except ImportError:  # pip package not installed yet — calendar functions degrade gracefully
    icalendar = None
    _ICALENDAR_AVAILABLE = False


LIST_UNREAD_EMAILS_SCHEMA = {
    "name": "list_unread_emails",
    "description": "List the user's most recent unread emails (sender, subject, date), most recent first. Use for 'read my unread emails' / 'do I have any new mail'.",
    "input_schema": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Max number of emails to return. Optional, defaults to 10."}
        },
    },
}

SEARCH_EMAILS_SCHEMA = {
    "name": "search_emails",
    "description": "Search recent email (subject, sender, and body) for a keyword, e.g. a person's name or company. Use for 'do I have anything from X' / 'find the email about Y'.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Keyword to search for — a name, company, or topic."},
            "limit": {"type": "integer", "description": "Max number of matching emails to return. Optional, defaults to 10."},
        },
        "required": ["query"],
    },
}

SEND_EMAIL_SCHEMA = {
    "name": "send_email",
    "description": (
        "Send an email on the user's behalf. This takes real, irreversible action, and voice input can be "
        "misheard — before calling this tool, always read back the recipient, subject, and body to the user "
        "in conversation and get explicit confirmation ('sending to jane@example.com, subject X, saying Y — "
        "sound good?'). Only call this after the user confirms."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address."},
            "subject": {"type": "string", "description": "Email subject line."},
            "body": {"type": "string", "description": "Email body text."},
        },
        "required": ["to", "subject", "body"],
    },
}

GET_TODAYS_EVENTS_SCHEMA = {
    "name": "get_todays_events",
    "description": "List everything on the user's calendar today. Use for 'what's on my calendar today' / 'what do I have going on today'.",
    "input_schema": {"type": "object", "properties": {}},
}

GET_UPCOMING_EVENTS_SCHEMA = {
    "name": "get_upcoming_events",
    "description": "List the user's upcoming calendar events over the next several days. Use for 'what's coming up this week' / 'what's on my calendar'.",
    "input_schema": {
        "type": "object",
        "properties": {
            "days": {"type": "integer", "description": "How many days ahead to look. Optional, defaults to 7."}
        },
    },
}

_NOT_CONFIGURED_EMAIL = "Email isn't configured — add email_address and email_app_password to config.json."
_NOT_CONFIGURED_CALENDAR = "Calendar isn't configured — add calendar_ics_url to config.json."


def _email_credentials(config: dict) -> tuple[str, str]:
    address = config.get("email_address") or os.getenv("EMAIL_ADDRESS", "")
    password = config.get("email_app_password") or os.getenv("EMAIL_APP_PASSWORD", "")
    return address, password


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = ""
    for text, encoding in parts:
        if isinstance(text, bytes):
            out += text.decode(encoding or "utf-8", errors="replace")
        else:
            out += text
    return out


def _imap_connect(config: dict):
    """Returns (connection, error_string). Exactly one is non-None."""
    address, password = _email_credentials(config)
    if not address or not password:
        return None, _NOT_CONFIGURED_EMAIL

    host = config.get("imap_host") or "imap.gmail.com"
    port = config.get("imap_port") or 993
    try:
        conn = imaplib.IMAP4_SSL(host, port, timeout=15)
        conn.login(address, password)
        return conn, None
    except imaplib.IMAP4.error:
        return None, "Couldn't log into email — check email_address and email_app_password (Gmail needs an app password, not your normal password)."
    except (OSError, TimeoutError) as e:
        return None, f"Couldn't connect to the email server: {e}"


def _fetch_message_summary(conn, msg_id: bytes) -> str | None:
    status, msg_data = conn.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
    if status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
        return None
    msg = message_from_bytes(msg_data[0][1])
    sender = _decode_header_value(msg.get("From")) or "Unknown sender"
    subject = _decode_header_value(msg.get("Subject")) or "(no subject)"
    date_str = msg.get("Date", "")
    return f"- From {sender}: \"{subject}\" ({date_str})"


def list_unread_emails(config: dict, limit: int = 10) -> str:
    conn, err = _imap_connect(config)
    if err:
        return err
    try:
        status, _ = conn.select("INBOX")
        if status != "OK":
            return "Couldn't open the inbox."
        status, data = conn.search(None, "UNSEEN")
        if status != "OK" or not data or not data[0]:
            return "No unread emails."
        ids = data[0].split()
        if not ids:
            return "No unread emails."
        ids = list(reversed(ids))[:limit]  # IMAP IDs ascend by arrival — reverse for most-recent-first
        lines = [s for s in (_fetch_message_summary(conn, i) for i in ids) if s]
        return "\n".join(lines) if lines else "No unread emails."
    except (imaplib.IMAP4.error, OSError, TimeoutError) as e:
        return f"Error reading unread email: {e}"
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def search_emails(config: dict, query: str, limit: int = 10) -> str:
    conn, err = _imap_connect(config)
    if err:
        return err
    try:
        status, _ = conn.select("INBOX")
        if status != "OK":
            return "Couldn't open the inbox."

        safe_query = query.replace('"', "").replace("\\", "")
        since_date = (datetime.now() - timedelta(days=180)).strftime("%d-%b-%Y")
        criteria = f'(SINCE {since_date} (OR (OR (SUBJECT "{safe_query}") (FROM "{safe_query}")) (BODY "{safe_query}")))'
        status, data = conn.search(None, criteria)
        if status != "OK" or not data or not data[0]:
            return f"No emails found matching '{query}'."
        ids = data[0].split()
        if not ids:
            return f"No emails found matching '{query}'."
        ids = list(reversed(ids))[:limit]
        lines = [s for s in (_fetch_message_summary(conn, i) for i in ids) if s]
        return "\n".join(lines) if lines else f"No emails found matching '{query}'."
    except (imaplib.IMAP4.error, OSError, TimeoutError) as e:
        return f"Error searching email: {e}"
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def send_email(config: dict, to: str, subject: str, body: str) -> str:
    address, password = _email_credentials(config)
    if not address or not password:
        return _NOT_CONFIGURED_EMAIL

    host = config.get("smtp_host") or "smtp.gmail.com"
    port = config.get("smtp_port") or 587

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = to

    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            server.login(address, password)
            server.sendmail(address, [to], msg.as_string())
        return f"Email sent to {to}."
    except smtplib.SMTPAuthenticationError:
        return "Couldn't send the email — email login failed (Gmail needs an app password, not your normal password)."
    except smtplib.SMTPException as e:
        return f"Couldn't send the email: {e}"
    except (OSError, TimeoutError) as e:
        return f"Couldn't connect to the email server to send: {e}"


def _fetch_calendar(config: dict):
    """Returns (icalendar.Calendar, error_string). Exactly one is non-None."""
    if not _ICALENDAR_AVAILABLE:
        return None, "Calendar support needs the 'icalendar' package installed (pip install icalendar)."

    url = config.get("calendar_ics_url") or os.getenv("CALENDAR_ICS_URL", "")
    if not url:
        return None, _NOT_CONFIGURED_CALENDAR

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        return None, f"Couldn't fetch the calendar feed: {e}"

    try:
        cal = icalendar.Calendar.from_ical(resp.content)
    except Exception as e:  # icalendar doesn't guarantee a narrow exception type on malformed feeds
        return None, f"Couldn't parse the calendar feed — is calendar_ics_url a valid ICS URL? ({e})"

    return cal, None


def _format_time(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def _collect_events(cal, start_local: datetime, end_local: datetime) -> list[tuple[datetime, str, object]]:
    """Returns (start, summary, raw_dtstart) tuples for VEVENTs starting in [start_local, end_local).

    start_local/end_local must be timezone-aware local datetimes. Timed events are compared as UTC
    instants; all-day events (a plain date, with no time/timezone of their own) are compared against
    the local wall-clock calendar date directly — converting them through UTC first would risk
    shifting them a day in either direction depending on the local UTC offset.

    Note: this does not expand RRULE-recurring events into their individual occurrences — a
    recurring event only shows up on the date of its original/first instance. Fine for a v1;
    proper recurrence expansion would need a heavier library.
    """
    start_utc, end_utc = start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)
    start_date, end_date = start_local.date(), end_local.date()

    events = []
    for component in cal.walk("VEVENT"):
        dtstart_prop = component.get("dtstart")
        if dtstart_prop is None:
            continue
        raw = dtstart_prop.dt
        summary = str(component.get("summary", "(untitled event)"))

        if isinstance(raw, datetime):
            dt = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
            if start_utc <= dt < end_utc:
                events.append((dt, summary, raw))
        else:  # all-day event: a plain, timezone-less date
            if start_date <= raw < end_date:
                dt = datetime.combine(raw, datetime.min.time(), tzinfo=start_local.tzinfo)
                events.append((dt, summary, raw))

    events.sort(key=lambda e: e[0])
    return events


def _describe_event(dt_utc: datetime, summary: str, raw) -> str:
    local = dt_utc.astimezone()
    if isinstance(raw, datetime):
        return f"- {summary} — {local.strftime('%a %b %d')} at {_format_time(local)}"
    return f"- {summary} — {local.strftime('%a %b %d')} (all day)"


def get_todays_events(config: dict) -> str:
    cal, err = _fetch_calendar(config)
    if err:
        return err

    now_local = datetime.now().astimezone()
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)

    events = _collect_events(cal, start_local, end_local)
    if not events:
        return "Nothing on your calendar today."
    return "\n".join(_describe_event(dt, summary, raw) for dt, summary, raw in events)


def get_upcoming_events(config: dict, days: int = 7) -> str:
    cal, err = _fetch_calendar(config)
    if err:
        return err

    now_local = datetime.now().astimezone()
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=days)

    events = _collect_events(cal, start_local, end_local)
    if not events:
        return f"Nothing on your calendar in the next {days} days."
    return "\n".join(_describe_event(dt, summary, raw) for dt, summary, raw in events)
