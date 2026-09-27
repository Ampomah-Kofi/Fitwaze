"""A daily reminder the person adds to their own phone calendar.

Web push would need a background worker and does not work on an iPhone
unless the app is installed, so the reminder lives in the calendar instead:
it works on every phone, offline, and costs nothing to run. The file holds
no personal data, so it needs no sign-in.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Query
from fastapi.responses import Response

router = APIRouter(tags=["reminder"])


def reminder_calendar(hour: int, minute: int, today: date | None = None) -> str:
    today = today or date.today()
    start = datetime(today.year, today.month, today.day, hour, minute) + timedelta(days=1)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//FitWaze//Daily reminder//EN",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:fitwaze-daily-{hour:02d}{minute:02d}@fitwaze",
        f"DTSTAMP:{stamp}",
        # No time zone: a "floating" time, so 7:30 stays 7:30 wherever the phone is.
        f"DTSTART:{start:%Y%m%dT%H%M%S}",
        "DURATION:PT20M",
        "RRULE:FREQ=DAILY",
        "SUMMARY:FitWaze: check in and take a walk",
        "DESCRIPTION:Open FitWaze\\, tell it how you feel and your blood sugar\\, and get today's walk.",
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        "DESCRIPTION:Time for your FitWaze check-in and walk",
        "TRIGGER:PT0M",
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"


@router.get("/reminder.ics", include_in_schema=False)
def daily_reminder(time: str = Query(default="07:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")) -> Response:
    hour, minute = (int(part) for part in time.split(":"))
    return Response(
        content=reminder_calendar(hour, minute),
        media_type="text/calendar",
        headers={"Content-Disposition": 'inline; filename="fitwaze-reminder.ics"'},
    )
