"""Blood sugar before and after walks, shown on Progress."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.models.activity import ActivityRecommendation
from tests.conftest import TestingSessionLocal, auth_headers, register_and_login, valid_profile_payload

START = {"latitude": 33.5186, "longitude": -86.8104}


def _walk(client, headers, before=None, after=None):
    checkin = {"feeling": "good"}
    if before is not None:
        checkin.update(glucose_value=before, glucose_unit="mg/dL")
    rec = client.post("/activity/recommendation", json={"checkin": checkin}, headers=headers).json()
    start = {"activity_recommendation_id": rec["id"], **START}
    option = client.post("/route/options", json=start, headers=headers).json()["options"][0]
    session = client.post("/route/select", json={**start, "candidate_label": option["label"],
                                                 "candidate_revision": option["candidate_revision"]},
                          headers=headers).json()
    body = {"status": "completed"}
    if after is not None:
        body.update(post_glucose_value=after, post_glucose_unit="mg/dL")
    assert client.patch(f"/route/sessions/{session['id']}", json=body, headers=headers).status_code == 200
    return rec["id"]


def _user(client):
    headers = auth_headers(register_and_login(client)["access_token"])
    client.put("/profile", json=valid_profile_payload(), headers=headers)
    return headers


def test_walks_with_readings_before_and_after_are_paired(client):
    headers = _user(client)
    _walk(client, headers, before=160, after=121)
    _walk(client, headers, before=140, after=119)
    _walk(client, headers, before=150)          # no reading after: left out
    progress = client.get("/progress", headers=headers).json()
    pairs = [(w["before_mg_dl"], w["after_mg_dl"]) for w in progress["glucose_walks"]]
    assert pairs == [(160, 121), (140, 119)]    # oldest first
    assert progress["average_glucose_change_mg_dl"] == -30


def test_the_morning_reading_is_stored_encrypted(client):
    headers = _user(client)
    _walk(client, headers, before=160, after=121)
    with TestingSessionLocal() as db:
        stored = db.execute(text("SELECT pre_glucose_mmol_l FROM activity_recommendations")).scalar_one()
    assert stored and "8.89" not in stored


def test_a_reading_from_hours_before_the_walk_is_not_called_before(client):
    headers = _user(client)
    rec_id = _walk(client, headers, before=160, after=121)
    with TestingSessionLocal() as db:
        rec = db.get(ActivityRecommendation, __import__("uuid").UUID(rec_id))
        rec.created_at = datetime.now(timezone.utc) - timedelta(hours=6)
        db.commit()
    progress = client.get("/progress", headers=headers).json()
    assert progress["glucose_walks"] == [] and progress["average_glucose_change_mg_dl"] is None


def test_the_care_team_spreadsheet_has_both_readings(client):
    headers = _user(client)
    _walk(client, headers, before=160, after=121)
    rows = list(csv.reader(io.StringIO(client.get("/progress/export.csv", headers=headers).text)))
    assert rows[0][-2:] == ["Blood sugar before (mg/dL)", "Blood sugar after (mg/dL)"]
    assert rows[1][-2:] == ["160", "121"]


def test_nobody_sees_another_persons_readings(client):
    _walk(client, _user(client), before=160, after=121)
    other = client.get("/progress", headers=_user(client)).json()
    assert other["glucose_walks"] == []
