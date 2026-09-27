"""The daily calendar reminder."""
from __future__ import annotations

from datetime import date

from app.routers.reminder import reminder_calendar


def test_the_reminder_is_a_daily_calendar_event_with_an_alert(client):
    response = client.get("/reminder.ics?time=07:45")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    body = response.text
    assert "RRULE:FREQ=DAILY" in body and "BEGIN:VALARM" in body and "T074500" in body
    assert "\r\n" in body


def test_it_starts_tomorrow_at_the_chosen_local_time():
    assert "DTSTART:20260929T063000\r\n" in reminder_calendar(6, 30, today=date(2026, 9, 28))


def test_bad_times_are_refused(client):
    for bad in ("25:00", "7:30", "07:30;X", "abc"):
        assert client.get(f"/reminder.ics?time={bad}").status_code == 422
