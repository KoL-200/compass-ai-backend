import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.jobs import enqueue, run_job
from app.modules.notifications.jobs import expire_applications, scan_deadlines
from app.modules.notifications.reminders import build_reminder, due_reminder
from tests.helpers import auth_headers
from tests.test_applications import move, open_opp, setup, track
from tests.test_opportunites import make_org, manager

B = "/api/v1"
UTC = timezone.utc
DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)
SECRET = "test-job-secret-not-for-production"


def at(y, m, d, h=10):
    return datetime(y, m, d, h, tzinfo=UTC)


def scan(now):
    with SessionLocal() as db:
        return scan_deadlines(db, now)


def expire(now):
    with SessionLocal() as db:
        return expire_applications(db, now)


def notifications(client, headers, **params):
    r = client.get(f"{B}/notifications", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- pure reminder logic ----------

@pytest.mark.parametrize(
    "now,days,expected",
    [
        (at(2029, 12, 20), [7, 3, 1], None),      # 12 days out: too early
        (at(2029, 12, 25), [7, 3, 1], (7, 7)),
        (at(2029, 12, 26), [7, 3, 1], (7, 6)),
        (at(2029, 12, 28), [7, 3, 1], (7, 4)),    # between thresholds: still the 7-day one
        (at(2029, 12, 29), [7, 3, 1], (3, 3)),
        (at(2029, 12, 31), [7, 3, 1], (1, 1)),
        (at(2029, 12, 25), [3], None),
        (at(2029, 12, 25), [], None),
        (at(2030, 1, 1, 0), [7, 3, 1], None),     # exactly at the deadline: it has passed
        (at(2030, 1, 2), [7, 3, 1], None),
    ],
)
def test_due_reminder(now, days, expected):
    assert due_reminder(DEADLINE, days, now) == expected


def test_due_reminder_same_day_and_timezones():
    evening = datetime(2030, 1, 1, 18, tzinfo=UTC)
    assert due_reminder(evening, [7, 3, 1], datetime(2030, 1, 1, 8, tzinfo=UTC)) == (1, 0)
    lagos_deadline = datetime(2030, 1, 1, 0, 30, tzinfo=timezone(timedelta(hours=1)))  # 23:30 UTC the day before
    assert due_reminder(lagos_deadline, [7, 3, 1], at(2029, 12, 30)) == (1, 1)


def test_build_reminder_wording():
    kwargs = dict(opportunity_title="BSc CS", organization="Uni One", deadline=DEADLINE,
                  personal=False, status="preparing", next_action=None)
    assert build_reminder(days_left=0, **kwargs)[0] == "Deadline is today: BSc CS"
    assert build_reminder(days_left=1, **kwargs)[0] == "Deadline is tomorrow: BSc CS"
    title, body = build_reminder(days_left=5, **{**kwargs, "personal": True, "next_action": "Get transcript"})
    assert title == "Your target date is in 5 days: BSc CS"
    assert "preparing" in body and "Get transcript" in body and "01 Jan 2030" in body
    long_title, _ = build_reminder(days_left=5, **{**kwargs, "opportunity_title": "X" * 300})
    assert len(long_title) <= 200


# ---------- the deadline scan ----------

def test_scan_sends_each_reminder_once(client):
    mgr, h, app_id = setup(client)
    assert scan(at(2029, 12, 27))["notifications"] == 1         # 5 days left: the 7-day reminder
    assert scan(at(2029, 12, 27, 18))["notifications"] == 0     # same day again: deduplicated
    assert scan(at(2029, 12, 28))["notifications"] == 0         # still the same 7-day reminder
    assert scan(at(2029, 12, 29))["notifications"] == 1         # the 3-day reminder

    page = notifications(client, h)
    assert page["total"] == 2 and page["unread_count"] == 2
    assert [i["title"] for i in page["items"]] == [
        "Deadline is in 3 days: BSc Computer Science",
        "Deadline is in 5 days: BSc Computer Science",
    ]
    assert page["items"][0]["type"] == "deadline_reminder"
    assert page["items"][0]["data"]["application_id"] == app_id
    assert page["items"][0]["data"]["days_left"] == 3


def test_a_moved_deadline_creates_fresh_reminders(client):
    mgr, h, app_id = setup(client)
    opp_id = client.get(f"{B}/applications/{app_id}", headers=h).json()["opportunity"]["id"]
    assert scan(at(2029, 12, 31))["notifications"] == 1  # 1 day left
    client.patch(f"{B}/admin/opportunities/{opp_id}", json={"deadline_at": "2030-02-01T00:00:00Z"}, headers=mgr)
    assert scan(at(2030, 1, 30))["notifications"] == 1   # the new deadline gets its own reminders
    assert notifications(client, h)["total"] == 2


def test_scan_skips_what_should_not_be_reminded(client):
    mgr = manager(client)
    h = auth_headers(client, "u@example.com")
    org = make_org(client, mgr)
    apps = {name: track(client, h, open_opp(client, mgr, name, org)).json()["id"]
            for name in ("Muted", "Sent", "Archived", "Included")}

    client.patch(f"{B}/applications/{apps['Muted']}", json={"reminders_enabled": False}, headers=h)
    move(client, h, apps["Sent"], "submitted")
    archived_opp = client.get(f"{B}/applications/{apps['Archived']}", headers=h).json()["opportunity"]["id"]
    client.patch(f"{B}/admin/opportunities/{archived_opp}", json={"status": "archived"}, headers=mgr)

    assert scan(at(2029, 12, 27))["notifications"] == 1
    items = notifications(client, h)["items"]
    assert len(items) == 1 and "Included" in items[0]["title"]


def test_personal_target_date_drives_reminders(client):
    mgr, h, app_id = setup(client)
    client.patch(
        f"{B}/applications/{app_id}",
        json={"personal_deadline_at": "2029-12-28T00:00:00Z", "reminder_days": [3]},
        headers=h,
    )
    assert scan(at(2029, 12, 24))["notifications"] == 0  # 4 days to the target, threshold is 3
    assert scan(at(2029, 12, 26))["notifications"] == 1
    item = notifications(client, h)["items"][0]
    assert item["title"].startswith("Your target date is in 2 days")
    assert item["data"]["days_left"] == 2
    assert scan(at(2029, 12, 29))["notifications"] == 0  # target date passed: reminders stop


# ---------- the expiry sweep ----------

def test_expiry_sweep(client):
    mgr = manager(client)
    h = auth_headers(client, "u@example.com")
    org = make_org(client, mgr)
    sent = track(client, h, open_opp(client, mgr, "Sent", org)).json()["id"]
    editing = track(client, h, open_opp(client, mgr, "Editing", org)).json()["id"]
    personal = track(client, h, open_opp(client, mgr, "Personal", org)).json()["id"]
    move(client, h, sent, "submitted")
    client.patch(f"{B}/applications/{personal}", json={"personal_deadline_at": "2028-06-01T00:00:00Z"}, headers=h)

    # A personal target date passing is NOT the real deadline: nothing expires.
    assert expire(at(2029, 6, 1))["expired"] == 0

    report = expire(at(2030, 1, 2))
    assert report["expired"] == 2 and report["notifications"] == 2
    status = lambda app_id: client.get(f"{B}/applications/{app_id}", headers=h).json()["status"]
    assert status(editing) == "expired" and status(personal) == "expired"
    assert status(sent) == "submitted"  # submitted applications are never touched

    last = client.get(f"{B}/applications/{editing}/timeline", headers=h).json()[-1]
    assert last["actor"] == "system" and last["from_status"] == "interested" and last["to_status"] == "expired"
    expired_notes = [i for i in notifications(client, h)["items"] if i["type"] == "application_expired"]
    assert len(expired_notes) == 2 and expired_notes[0]["title"].startswith("Deadline passed:")

    again = expire(at(2030, 1, 3))
    assert again["expired"] == 0 and again["notifications"] == 0
    assert scan(at(2030, 1, 2))["notifications"] == 0  # expired applications get no reminders

    # The user did submit before the deadline but never recorded it: they can fix the record.
    fixed = move(client, h, editing, "submitted", submitted_at="2026-01-05T10:00:00Z")
    assert fixed.status_code == 200
    assert fixed.json()["status"] == "submitted" and fixed.json()["submitted_at"].startswith("2026-01-05")


# ---------- notifications API ----------

def test_notification_api(client):
    mgr, a, app_id = setup(client)
    b = auth_headers(client, "b@example.com")
    assert client.get(f"{B}/notifications").status_code == 401

    scan(at(2029, 12, 27))
    scan(at(2029, 12, 29))
    assert client.post(f"{B}/notifications/read-all", headers=b).status_code == 204  # b cannot touch a's
    page = notifications(client, a)
    assert page["total"] == 2 and page["unread_count"] == 2
    first = page["items"][0]["id"]

    assert client.patch(f"{B}/notifications/{first}/read", headers=b).status_code == 404
    assert client.patch(f"{B}/notifications/{uuid.uuid4()}/read", headers=a).status_code == 404
    assert client.patch(f"{B}/notifications/{first}/read", headers=a).status_code == 204
    assert client.patch(f"{B}/notifications/{first}/read", headers=a).status_code == 204  # idempotent

    assert notifications(client, a)["unread_count"] == 1
    assert notifications(client, a, unread=True)["total"] == 1
    assert notifications(client, b)["total"] == 0

    assert client.post(f"{B}/notifications/read-all", headers=a).status_code == 204
    after = notifications(client, a, unread=True)
    assert after["total"] == 0 and after["unread_count"] == 0
    assert notifications(client, a)["total"] == 2  # read ones are kept
    assert client.get(f"{B}/notifications", params={"limit": 500}, headers=a).status_code == 422


# ---------- the cron endpoint and the job registry ----------

def test_internal_job_endpoint(client, monkeypatch):
    url = "/internal/jobs/daily"
    good = {"X-Job-Secret": SECRET}
    assert client.post(url).status_code == 401
    assert client.post(url, headers={"X-Job-Secret": "wrong"}).status_code == 401
    assert client.get(url, headers=good).status_code == 405

    ok = client.post(url, headers=good)
    assert ok.status_code == 200, ok.text
    assert set(ok.json()) == {"expire-applications", "scan-deadlines"}
    assert ok.json()["expire-applications"]["expired"] == 0

    single = client.post("/internal/jobs/scan-deadlines", headers=good).json()
    assert set(single) == {"checked", "notifications", "truncated"}
    unknown = client.post("/internal/jobs/nope", headers=good)
    assert unknown.status_code == 404 and "daily" in unknown.json()["error"]["details"]

    monkeypatch.setattr(get_settings(), "job_secret", "short")  # too short counts as not configured
    assert client.post(url, headers={"X-Job-Secret": "short"}).status_code == 503


def test_enqueue_runs_inline_and_rejects_unknown_backends(monkeypatch):
    assert set(enqueue("daily")) == {"expire-applications", "scan-deadlines"}
    assert run_job("expire-applications")["expired"] == 0
    monkeypatch.setattr(get_settings(), "job_backend", "celery")
    with pytest.raises(RuntimeError):
        enqueue("daily")