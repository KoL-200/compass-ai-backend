import uuid
from typing import get_args

from app.core.db import SessionLocal
from app.modules.applications.models import Application
from app.modules.applications.schemas import ApplicationStatus
from app.modules.applications.transitions import STATUSES, TRANSITIONS, USER_CANNOT_SET
from tests.helpers import auth_headers
from tests.test_opportunites import FUTURE, PAST, admission, create, make_org, manager

B = "/api/v1"


def open_opp(client, mgr, title="BSc Computer Science", org=None, **over):
    org = org or make_org(client, mgr)
    r = create(client, mgr, admission(org, title=title, **over))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def track(client, h, opp_id):
    return client.post(f"{B}/applications", json={"opportunity_id": opp_id}, headers=h)


def move(client, h, app_id, status, **extra):
    return client.patch(f"{B}/applications/{app_id}", json={"status": status, **extra}, headers=h)


def setup(client):
    mgr = manager(client)
    user = auth_headers(client, "u@example.com")
    app_id = track(client, user, open_opp(client, mgr)).json()["id"]
    return mgr, user, app_id


def force_status(app_id, status):
    """Simulate what the Part J expiry sweep will do."""
    with SessionLocal() as db:
        db.get(Application, uuid.UUID(app_id)).status = status
        db.commit()


# ---------- the transition map (pure) ----------

def test_transition_map_is_consistent():
    assert set(TRANSITIONS) == set(STATUSES)
    assert get_args(ApplicationStatus) == STATUSES
    for targets in TRANSITIONS.values():
        assert targets <= set(STATUSES)
    for final in ("completed", "rejected", "withdrawn"):
        assert TRANSITIONS[final] == set()
    assert USER_CANNOT_SET == {"expired"}
    assert all("expired" not in targets for targets in TRANSITIONS.values())


# ---------- creating ----------

def test_requires_auth(client):
    assert client.get(f"{B}/applications").status_code == 401
    assert client.post(f"{B}/applications", json={"opportunity_id": str(uuid.uuid4())}).status_code == 401


def test_create_application_and_first_timeline_event(client):
    mgr = manager(client)
    h = auth_headers(client, "u@example.com")
    r = track(client, h, open_opp(client, mgr))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "interested"
    assert body["opportunity"]["title"] == "BSc Computer Science"
    assert body["opportunity"]["organization"]["name"] == "Uni One"
    assert body["submitted_at"] is None
    assert body["reminders_enabled"] is True and body["reminder_days"] == [7, 3, 1]
    assert body["effective_deadline_at"].startswith("2030-01-01")
    assert body["checklist"] == []

    events = client.get(f"{B}/applications/{body['id']}/timeline", headers=h).json()
    assert len(events) == 1
    assert events[0]["from_status"] is None and events[0]["to_status"] == "interested"
    assert events[0]["actor"] == "user"

    again = track(client, h, body["opportunity"]["id"])
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_tracking"


def test_cannot_track_hidden_or_closed_opportunities(client):
    mgr = manager(client)
    h = auth_headers(client, "u@example.com")
    org = make_org(client, mgr)
    draft = open_opp(client, mgr, "Draft", org, status="draft")
    closed = open_opp(client, mgr, "Closed", org, status="closed")
    past = open_opp(client, mgr, "Past", org, deadline_at=PAST)

    assert track(client, h, draft).status_code == 404
    assert track(client, h, str(uuid.uuid4())).status_code == 404
    for opp_id in (closed, past):
        r = track(client, h, opp_id)
        assert r.status_code == 422 and r.json()["error"]["code"] == "opportunity_closed"


# ---------- status changes ----------

def test_full_journey_is_recorded_in_order(client):
    mgr, h, app_id = setup(client)
    path = ["preparing", "ready_to_apply", "submitted", "under_review",
            "interview_assessment", "offer_received", "completed"]
    for status in path:
        r = move(client, h, app_id, status)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == status
        if status == "ready_to_apply":
            assert r.json()["submitted_at"] is None  # nothing is submitted until the user says so
    assert r.json()["submitted_at"] is not None

    events = client.get(f"{B}/applications/{app_id}/timeline", headers=h).json()
    assert [e["to_status"] for e in events] == ["interested"] + path
    assert events[1]["from_status"] == "interested"
    assert all(e["actor"] == "user" for e in events)

    assert move(client, h, app_id, "preparing").status_code == 422  # completed is final


def test_invalid_transitions(client):
    mgr, h, app_id = setup(client)

    skip = move(client, h, app_id, "under_review")
    assert skip.status_code == 422
    error = skip.json()["error"]
    assert error["code"] == "invalid_transition"
    assert error["details"] == ["preparing", "ready_to_apply", "submitted", "withdrawn"]

    expired = move(client, h, app_id, "expired")
    assert expired.status_code == 422 and expired.json()["error"]["code"] == "status_not_allowed"
    assert move(client, h, app_id, "flying").status_code == 422

    same = move(client, h, app_id, "interested")  # no change, no new event
    assert same.status_code == 200
    assert len(client.get(f"{B}/applications/{app_id}/timeline", headers=h).json()) == 1

    move(client, h, app_id, "withdrawn")
    assert move(client, h, app_id, "preparing").status_code == 422  # withdrawn is final


def test_submitted_date_rules(client):
    mgr, h, app_id = setup(client)
    url = f"{B}/applications/{app_id}"

    assert client.patch(url, json={"note": "no status"}, headers=h).status_code == 422
    wrong = move(client, h, app_id, "preparing", submitted_at="2026-01-01T10:00:00Z")
    assert wrong.status_code == 422
    future = move(client, h, app_id, "submitted", submitted_at="2030-01-01T00:00:00Z")
    assert future.status_code == 422
    assert move(client, h, app_id, "submitted", submitted_at="2026-01-01T10:00:00").status_code == 422

    ok = move(client, h, app_id, "submitted", submitted_at="2026-01-01T10:00:00Z", note="Sent by email")
    assert ok.status_code == 200
    assert ok.json()["submitted_at"].startswith("2026-01-01T10:00:00")

    fixed = move(client, h, app_id, "submitted", submitted_at="2026-01-02T09:00:00Z")
    assert fixed.json()["submitted_at"].startswith("2026-01-02T09:00:00")
    events = client.get(f"{url}/timeline", headers=h).json()
    assert len(events) == 2  # correcting the date is not a new status change
    assert events[1]["note"] == "Sent by email"


def test_expired_can_only_reopen_while_the_deadline_is_open(client):
    mgr, h, app_id = setup(client)
    opp_id = client.get(f"{B}/applications/{app_id}", headers=h).json()["opportunity"]["id"]

    force_status(app_id, "expired")
    assert move(client, h, app_id, "preparing").status_code == 200  # deadline is still in the future

    force_status(app_id, "expired")
    client.patch(f"{B}/admin/opportunities/{opp_id}", json={"deadline_at": PAST}, headers=mgr)
    r = move(client, h, app_id, "preparing")
    assert r.status_code == 422 and r.json()["error"]["code"] == "deadline_passed"


# ---------- editing fields ----------

def test_update_fields_and_deadlines(client):
    mgr, h, app_id = setup(client)
    url = f"{B}/applications/{app_id}"

    r = client.patch(
        url,
        json={"notes": "Call admissions", "next_action": "Request transcript",
              "personal_deadline_at": "2028-06-01T00:00:00Z",
              "reminder_days": [1, 7, 3, 3], "reminders_enabled": False},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["notes"] == "Call admissions" and body["next_action"] == "Request transcript"
    assert body["reminder_days"] == [7, 3, 1] and body["reminders_enabled"] is False
    assert body["effective_deadline_at"].startswith("2028-06-01")

    late = client.patch(url, json={"personal_deadline_at": "2031-01-01T00:00:00Z"}, headers=h)
    assert late.status_code == 422  # cannot be after the official deadline

    cleared = client.patch(url, json={"personal_deadline_at": None}, headers=h).json()
    assert cleared["personal_deadline_at"] is None
    assert cleared["effective_deadline_at"].startswith("2030-01-01")

    for bad in ({"reminder_days": [-1]}, {"reminder_days": [100]}, {"reminder_days": [1, 2, 3, 4, 5, 6]},
                {"reminders_enabled": None}, {"opportunity_id": str(uuid.uuid4())},
                {"personal_deadline_at": "2028-06-01T00:00:00"}):
        assert client.patch(url, json=bad, headers=h).status_code == 422, bad
    assert client.patch(url, json={}, headers=h).status_code == 200  # empty patch is harmless


# ---------- ownership ----------

def test_other_users_cannot_see_or_change_my_application(client):
    mgr, a, app_id = setup(client)
    b = auth_headers(client, "b@example.com")
    url = f"{B}/applications/{app_id}"

    assert client.get(url, headers=b).status_code == 404
    assert client.patch(url, json={"notes": "hacked"}, headers=b).status_code == 404
    assert move(client, b, app_id, "withdrawn").status_code == 404
    assert client.delete(url, headers=b).status_code == 404
    assert client.get(f"{url}/timeline", headers=b).status_code == 404
    assert client.post(f"{url}/checklist", json={"label": "x"}, headers=b).status_code == 404
    assert client.get(f"{B}/applications", headers=b).json()["total"] == 0
    assert client.get(url, headers=a).status_code == 200


def test_application_survives_archiving_of_its_opportunity(client):
    mgr, h, app_id = setup(client)
    opp_id = client.get(f"{B}/applications/{app_id}", headers=h).json()["opportunity"]["id"]
    client.patch(f"{B}/admin/opportunities/{opp_id}", json={"status": "archived"}, headers=mgr)
    r = client.get(f"{B}/applications/{app_id}", headers=h)
    assert r.status_code == 200 and r.json()["opportunity"]["status"] == "archived"


# ---------- checklist ----------

def test_checklist_lifecycle_and_limit(client):
    mgr, h, app_id = setup(client)
    url = f"{B}/applications/{app_id}"

    created = client.post(f"{url}/checklist", json={"label": " Transcript "}, headers=h)
    assert created.status_code == 201
    item = created.json()
    assert item["label"] == "Transcript" and item["is_done"] is False and item["done_at"] is None

    done = client.patch(f"{url}/checklist/{item['id']}", json={"is_done": True}, headers=h).json()
    assert done["is_done"] is True and done["done_at"] is not None
    undone = client.patch(
        f"{url}/checklist/{item['id']}", json={"label": "Official transcript", "is_done": False}, headers=h
    ).json()
    assert undone["label"] == "Official transcript" and undone["done_at"] is None

    assert len(client.get(url, headers=h).json()["checklist"]) == 1
    assert client.post(f"{url}/checklist", json={"label": "   "}, headers=h).status_code == 422
    assert client.delete(f"{url}/checklist/{item['id']}", headers=h).status_code == 204
    assert client.delete(f"{url}/checklist/{item['id']}", headers=h).status_code == 404

    for n in range(50):
        assert client.post(f"{url}/checklist", json={"label": f"Item {n}"}, headers=h).status_code == 201
    full = client.post(f"{url}/checklist", json={"label": "One too many"}, headers=h)
    assert full.status_code == 422 and full.json()["error"]["code"] == "checklist_full"


# ---------- delete ----------

def test_delete_only_before_submission(client):
    mgr, h, app_id = setup(client)
    opp_id = client.get(f"{B}/applications/{app_id}", headers=h).json()["opportunity"]["id"]

    assert client.delete(f"{B}/applications/{app_id}", headers=h).status_code == 204
    assert client.get(f"{B}/applications/{app_id}", headers=h).status_code == 404

    again = track(client, h, opp_id).json()["id"]  # tracking it afresh is allowed
    move(client, h, again, "submitted")
    r = client.delete(f"{B}/applications/{again}", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "cannot_delete"


# ---------- listing ----------

def test_list_sorting_filters_and_pagination(client):
    mgr = manager(client)
    h = auth_headers(client, "u@example.com")
    org = make_org(client, mgr)
    a = track(client, h, open_opp(client, mgr, "Course A", org)).json()["id"]  # official deadline 2030
    b = track(client, h, open_opp(client, mgr, "Course B", org, deadline_at="2029-01-01T00:00:00Z")).json()["id"]
    c = track(client, h, open_opp(client, mgr, "Course C", org, deadline_at=None)).json()["id"]
    client.patch(f"{B}/applications/{a}", json={"personal_deadline_at": "2028-06-01T00:00:00Z"}, headers=h)

    def titles(**params):
        r = client.get(f"{B}/applications", params=params, headers=h)
        assert r.status_code == 200, r.text
        return [i["opportunity"]["title"] for i in r.json()["items"]]

    assert titles() == ["Course A", "Course B", "Course C"]  # soonest effective deadline first, none last

    move(client, h, b, "preparing")
    move(client, h, c, "withdrawn")
    assert titles(status="preparing") == ["Course B"]
    assert titles(active="true") == ["Course A", "Course B"]
    assert titles(active="false") == ["Course C"]

    assert client.get(f"{B}/applications", params={"status": "bogus"}, headers=h).status_code == 422
    assert client.get(f"{B}/applications", params={"limit": 500}, headers=h).status_code == 422
    page = client.get(f"{B}/applications", params={"limit": 1}, headers=h).json()
    assert len(page["items"]) == 1 and page["total"] == 3