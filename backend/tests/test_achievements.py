"""Achievements on Progress, from the person's own finished walks."""
from __future__ import annotations

from datetime import date

from app.routers.progress import _best_week_minutes, _longest_streak_days
from tests.test_glucose_trend import _user, _walk


def test_longest_streak_finds_the_longest_run():
    days = {date(2026, 9, d) for d in (1, 2, 3, 7, 8, 9, 10, 20)}
    assert _longest_streak_days(days) == 4
    assert _longest_streak_days(set()) == 0


def test_best_week_uses_calendar_weeks():
    # Sunday 20 Sept and Monday 21 Sept fall in different weeks.
    assert _best_week_minutes({date(2026, 9, 20): 100, date(2026, 9, 21): 100, date(2026, 9, 22): 30}) == 130


def test_new_person_has_everything_still_to_earn(client):
    progress = client.get("/progress", headers=_user(client)).json()
    assert progress["achievements"] and not any(a["earned"] for a in progress["achievements"])


def test_first_walk_and_blood_sugar_badges_are_earned(client):
    headers = _user(client)
    _walk(client, headers, before=150, after=120)
    by_key = {a["key"]: a for a in client.get("/progress", headers=headers).json()["achievements"]}
    assert by_key["first_walk"]["earned"] and by_key["sugar_check"]["earned"]
    assert not by_key["walks_10"]["earned"] and by_key["walks_10"]["current"] == 1 and by_key["walks_10"]["goal"] == 10
