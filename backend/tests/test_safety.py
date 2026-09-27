"""Safety features: medication-aware advice, emergency contact, after-walk
check-in, and the heat check before routes."""
from __future__ import annotations

import httpx
import pytest
from sqlalchemy import text

from app.engines.activity_engine import after_walk_advice, recommend_activity
from app.models.enums import FeelingEnum
from app.schemas.activity import DailyCheckIn
from app.services import weather
from tests.conftest import TestingSessionLocal, auth_headers, register_and_login, valid_profile_payload
from tests.test_activity_engine import make_profile


# --- medication ---------------------------------------------------------------

def test_medication_users_are_told_to_carry_fast_sugar():
    assert "carry fast sugar" in recommend_activity(make_profile(takes_glucose_lowering_medication=True)).rationale
    assert "carry fast sugar" not in recommend_activity(make_profile()).rationale


def test_medication_raises_the_snack_threshold():
    reading = DailyCheckIn(feeling=FeelingEnum.good, glucose_value=105, glucose_unit="mg/dL")
    assert "snack" in recommend_activity(make_profile(takes_glucose_lowering_medication=True), checkin=reading).rationale
    assert "snack" not in recommend_activity(make_profile(), checkin=reading).rationale


def test_safety_details_round_trip_and_are_encrypted(client):
    headers = auth_headers(register_and_login(client)["access_token"])
    payload = valid_profile_payload(takes_glucose_lowering_medication=True,
                                    emergency_contact_name="Ama Mensah", emergency_contact_phone="+1 (205) 555-0142")
    assert client.put("/profile", json=payload, headers=headers).status_code == 200
    body = client.get("/profile", headers=headers).json()
    assert body["takes_glucose_lowering_medication"] is True
    assert body["emergency_contact_phone"] == "+1 (205) 555-0142"
    with TestingSessionLocal() as db:
        raw = db.execute(text("SELECT emergency_contact_name, emergency_contact_phone FROM health_profiles")).one()
    assert "Ama" not in raw[0] and "555" not in raw[1]


def test_old_clients_without_safety_fields_still_work(client):
    headers = auth_headers(register_and_login(client)["access_token"])
    assert client.put("/profile", json=valid_profile_payload(), headers=headers).status_code == 200
    assert client.get("/profile", headers=headers).json()["takes_glucose_lowering_medication"] is False


def test_bad_phone_numbers_are_rejected(client):
    headers = auth_headers(register_and_login(client)["access_token"])
    bad = valid_profile_payload(emergency_contact_phone="call me maybe")
    assert client.put("/profile", json=bad, headers=headers).status_code == 422


# --- after-walk check-in -----------------------------------------------------

@pytest.mark.parametrize("mmol, phrase", [(3.3, "fast sugar"), (5.0, "snack"), (7.0, "good range"), (18.0, "Drink water")])
def test_after_walk_advice_by_reading(mmol, phrase):
    assert phrase in after_walk_advice(mmol, None)


def test_after_walk_advice_never_repeats_the_reading():
    assert "3.3" not in after_walk_advice(3.3, "hard", True)


def _completed_walk(client, **checkin):
    headers = auth_headers(register_and_login(client)["access_token"])
    client.put("/profile", json=valid_profile_payload(takes_glucose_lowering_medication=True), headers=headers)
    rec = client.post("/activity/recommendation", headers=headers).json()
    start = {"activity_recommendation_id": rec["id"], "latitude": 33.5186, "longitude": -86.8104}
    option = client.post("/route/options", json=start, headers=headers).json()["options"][0]
    session = client.post("/route/select", json={**start, "candidate_label": option["label"],
                                                 "candidate_revision": option["candidate_revision"]}, headers=headers).json()
    return client.patch(f"/route/sessions/{session['id']}", json={"status": "completed", **checkin}, headers=headers)


def test_finishing_with_a_check_in_returns_advice_and_stores_it_encrypted(client):
    response = _completed_walk(client, effort="just_right", post_glucose_value=62, post_glucose_unit="mg/dL")
    assert response.status_code == 200
    body = response.json()
    assert body["effort"] == "just_right"
    assert "fast sugar" in body["after_walk_advice"]
    assert "check again before bed" in body["after_walk_advice"]
    with TestingSessionLocal() as db:
        stored = db.execute(text("SELECT post_glucose_mmol_l FROM activity_sessions")).scalar_one()
    assert stored and "3.4" not in stored


def test_finishing_without_a_check_in_is_unchanged(client):
    body = _completed_walk(client).json()
    assert body["status"] == "completed" and body["after_walk_advice"] is None


def test_a_check_in_can_be_added_once_after_the_walk_is_saved(client):
    saved = _completed_walk(client, measured_minutes=21.5)
    assert saved.status_code == 200 and saved.json()["measured_minutes"] == 21.5
    headers = saved.request.headers
    url = f"/route/sessions/{saved.json()['id']}/checkin"
    added = client.post(url, json={"effort": "easy", "post_glucose_value": 110, "post_glucose_unit": "mg/dL"},
                        headers={"Authorization": headers["Authorization"]})
    assert added.status_code == 200
    body = added.json()
    assert body["status"] == "completed" and body["effort"] == "easy" and body["after_walk_advice"]
    assert body["measured_minutes"] == 21.5
    again = client.post(url, json={"effort": "hard"}, headers={"Authorization": headers["Authorization"]})
    assert again.status_code == 409


def test_a_check_in_needs_a_finished_walk_of_your_own(client):
    headers = auth_headers(register_and_login(client)["access_token"])
    client.put("/profile", json=valid_profile_payload(), headers=headers)
    rec = client.post("/activity/recommendation", headers=headers).json()
    start = {"activity_recommendation_id": rec["id"], "latitude": 33.5186, "longitude": -86.8104}
    option = client.post("/route/options", json=start, headers=headers).json()["options"][0]
    session = client.post("/route/select", json={**start, "candidate_label": option["label"],
                                                 "candidate_revision": option["candidate_revision"]}, headers=headers).json()
    url = f"/route/sessions/{session['id']}/checkin"
    assert client.post(url, json={"effort": "easy"}, headers=headers).status_code == 409
    other = auth_headers(register_and_login(client, email="other@example.com")["access_token"])
    assert client.post(url, json={"effort": "easy"}, headers=other).status_code == 404
    client.patch(f"/route/sessions/{session['id']}", json={"status": "completed"}, headers=headers)
    assert client.post(url, json={}, headers=headers).status_code == 422


def test_implausible_after_walk_reading_is_rejected(client):
    assert _completed_walk(client, post_glucose_value=900, post_glucose_unit="mmol/L").status_code == 422


# --- heat check -----------------------------------------------------------------

@pytest.mark.parametrize("temp, rh, level", [(72, 50, "ok"), (82, 50, "caution"), (90, 50, "extreme_caution"),
                                             (98, 70, "danger"), (35, 60, "cold")])
def test_heat_levels(temp, rh, level):
    assert weather.classify(temp, rh).level == level


def test_heat_index_matches_the_nws_table():
    # NWS heat index chart: 90 F at 70% humidity reads 106 F.
    assert weather.heat_index_f(90, 70) == pytest.approx(106, abs=1.5)


def _nws(temperature=96, humidity=65, fail=False):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if fail:
            return httpx.Response(503)
        if "/points/" in request.url.path:
            return httpx.Response(200, json={"properties": {"forecastHourly": "https://api.weather.gov/gridpoints/BMX/1,1/forecast/hourly"}})
        return httpx.Response(200, json={"properties": {"periods": [
            {"temperature": temperature, "temperatureUnit": "F", "relativeHumidity": {"value": humidity}}]}})
    return httpx.MockTransport(handler), calls


def test_heat_check_reads_the_nws_forecast(monkeypatch):
    monkeypatch.setattr(weather.get_settings(), "weather_enabled", True)
    weather.clear_weather_cache()
    transport, calls = _nws()
    check = weather.heat_check(33.5186, -86.8104, transport=transport)
    assert check.level == "danger" and "indoors" in check.advice
    assert all(request.headers["User-Agent"].startswith("FitWaze") for request in calls)
    assert "/points/33.52,-86.81" in str(calls[0].url)  # rounded: no exact home location is sent
    weather.heat_check(33.5186, -86.8104, transport=transport)
    assert len(calls) == 2, "cached for the next request"


def test_heat_check_failure_means_no_note(monkeypatch):
    monkeypatch.setattr(weather.get_settings(), "weather_enabled", True)
    weather.clear_weather_cache()
    transport, _ = _nws(fail=True)
    assert weather.heat_check(33.5, -86.8, transport=transport) is None


def test_route_options_carry_the_heat_note(client, monkeypatch):
    monkeypatch.setattr("app.routers.route.heat_check", lambda lat, lon: weather.classify(95, 70))
    headers = auth_headers(register_and_login(client)["access_token"])
    client.put("/profile", json=valid_profile_payload(), headers=headers)
    rec = client.post("/activity/recommendation", headers=headers).json()
    body = client.post("/route/options", json={"activity_recommendation_id": rec["id"], "latitude": 33.5,
                                               "longitude": -86.8}, headers=headers).json()
    assert body["weather"]["level"] == "danger"


def test_a_high_reading_after_a_walk_is_not_called_good():
    advice = after_walk_advice(15.0, None)  # 270 mg/dL
    assert "good range" not in advice and "higher than ideal" in advice


def test_an_after_walk_reading_must_state_its_unit(client):
    response = _completed_walk(client, post_glucose_value=30)
    assert response.status_code == 422


def test_rate_limit_key_ignores_caller_written_forwarding_entries(monkeypatch):
    from starlette.requests import Request
    from app.security import rate_limit

    def request(forwarded):
        return Request({"type": "http", "headers": [(b"x-forwarded-for", forwarded.encode())],
                        "client": ("10.0.0.1", 1234)})

    monkeypatch.setattr(rate_limit.get_settings(), "trusted_proxy_hops", 0)
    assert rate_limit.client_ip(request("1.2.3.4")) == "10.0.0.1"
    monkeypatch.setattr(rate_limit.get_settings(), "trusted_proxy_hops", 1)
    # The caller forged "6.6.6.6"; Render's proxy appended the real 203.0.113.9.
    assert rate_limit.client_ip(request("6.6.6.6, 203.0.113.9")) == "203.0.113.9"


def test_people_with_diabetes_get_the_after_meal_walk_tip():
    from app.models.enums import DiabetesStatusEnum
    assert "after a meal" in recommend_activity(make_profile(diabetes_status=DiabetesStatusEnum.type2)).rationale
    assert "after a meal" not in recommend_activity(make_profile()).rationale
