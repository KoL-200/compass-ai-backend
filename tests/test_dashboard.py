from datetime import datetime, timedelta, timezone

from tests.helpers import auth_headers
from tests.test_applications import move, track
from tests.test_matching_api import put_rules, student, world
from tests.test_opportunites import admission, create, make_org, manager

B = "/api/v1"


def add_course(client, mgr, title, source_url):
    org = make_org(client, mgr, "Uni Two", "NG")
    src = client.post(
        f"{B}/admin/sources",
        json={"name": "Uni Two", "url": source_url, "type": "official_website"},
        headers=mgr,
    ).json()["id"]
    opp_id = create(client, mgr, admission(org, title=title, source_id=src)).json()["id"]
    put_rules(client, mgr, opp_id, {"type": "LEAF", "field": "country", "op": "in", "value": ["NG"]})
    assert client.post(f"{B}/admin/opportunities/{opp_id}/verify", headers=mgr).status_code == 200
    return opp_id


def dashboard(client, h):
    r = client.get(f"{B}/dashboard", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_dashboard_requires_auth(client):
    assert client.get(f"{B}/dashboard").status_code == 401
    assert client.get(f"{B}/dashboard/next-actions").status_code == 401


def test_a_new_user_gets_an_honest_empty_dashboard(client):
    h = auth_headers(client)
    d = dashboard(client, h)

    assert d["profile"]["completeness_percent"] == 0 and len(d["profile"]["missing"]) == 9
    assert d["profile"]["email_verified"] is False
    assert d["goals"] == []
    assert d["applications"] == {"total": 0, "active": 0, "by_status": {}, "by_category": {}, "upcoming_deadlines": []}
    assert d["matches"]["last_computed_at"] is None and d["matches"]["visible_total"] == 0
    assert d["matches"]["top_by_category"] == {"admission": [], "scholarship": [], "internship": [], "job": []}
    assert (d["progress"]["matched"], d["progress"]["tracked"], d["progress"]["offers"]) == (0, 0, 0)
    assert "do not predict" in d["progress"]["note"]
    assert d["unread_notifications"] == 0
    assert [a["type"] for a in d["next_actions"]] == [
        "complete_profile", "refresh_matches", "verify_email", "set_goal",
    ]


def test_dashboard_ties_goals_matches_and_applications_together(client):
    mgr = manager(client)
    a, b, c = world(client, mgr)  # a: eligible course, b: ineligible course, c: internship
    d_id = add_course(client, mgr, "BSc Computer Science Evening", "https://uni.example.com/b")
    h = student(client)
    client.put(
        f"{B}/profiles/me",
        json={"nationality": "ng", "career_interests": ["software engineering"],
              "remote_preference": "any", "needs_financial_aid": False},
        headers=h,
    )
    client.post(f"{B}/goals", json={"title": "Get into a CS programme", "category": "admission"}, headers=h)
    client.post(f"{B}/matches/refresh", headers=h)

    app_id = track(client, h, a).json()["id"]
    move(client, h, app_id, "preparing")
    soon = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    assert client.patch(f"{B}/applications/{app_id}", json={"personal_deadline_at": soon}, headers=h).status_code == 200

    dash = dashboard(client, h)
    assert dash["profile"]["completeness_percent"] == 100 and dash["profile"]["missing"] == []
    assert [g["title"] for g in dash["goals"]] == ["Get into a CS programme"]

    apps = dash["applications"]
    assert (apps["total"], apps["active"]) == (1, 1)
    assert apps["by_status"] == {"preparing": 1} and apps["by_category"] == {"admission": 1}
    assert len(apps["upcoming_deadlines"]) == 1
    assert apps["upcoming_deadlines"][0]["application_id"] == app_id
    assert apps["upcoming_deadlines"][0]["days_left"] in (2, 3)

    tops = dash["matches"]["top_by_category"]
    admissions = {i["title"]: i for i in tops["admission"]}
    assert set(admissions) == {"BSc Computer Science", "BSc Computer Science Evening"}  # the ineligible one is hidden
    assert admissions["BSc Computer Science"]["tracked"] is True
    assert admissions["BSc Computer Science Evening"]["tracked"] is False
    assert all(i["goal_aligned"] for i in admissions.values())
    assert len(tops["internship"]) == 1 and tops["internship"][0]["goal_aligned"] is False
    assert tops["scholarship"] == [] and tops["job"] == []
    assert dash["matches"]["visible_total"] == 3 and dash["progress"]["matched"] == 3

    actions = dash["next_actions"]
    assert [x["type"] for x in actions] == ["deadline_approaching", "verify_email", "start_application"]
    assert actions[0]["priority"] == 1 and actions[0]["target"] == {"kind": "application", "id": app_id}
    assert actions[2]["target"] == {"kind": "opportunity", "id": d_id}
    assert client.get(f"{B}/dashboard/next-actions", headers=h).json() == actions

    # Submitting removes the deadline pressure and moves the funnel on.
    move(client, h, app_id, "submitted")
    for status in ("under_review", "interview_assessment", "offer_received"):
        assert move(client, h, app_id, status).status_code == 200
    after = dashboard(client, h)
    assert after["applications"]["upcoming_deadlines"] == []
    assert (after["progress"]["tracked"], after["progress"]["submitted"]) == (1, 1)
    assert (after["progress"]["interviews"], after["progress"]["offers"]) == (1, 1)
    assert [x["type"] for x in after["next_actions"]] == ["verify_email", "start_application"]


def test_one_users_dashboard_never_shows_anothers_data(client):
    mgr = manager(client)
    world(client, mgr)
    a = student(client, "a@example.com")
    b = auth_headers(client, "b@example.com")
    client.post(f"{B}/goals", json={"title": "Mine", "category": "job"}, headers=a)
    client.post(f"{B}/matches/refresh", headers=a)

    other = dashboard(client, b)
    assert other["goals"] == [] and other["matches"]["visible_total"] == 0
    assert other["applications"]["total"] == 0 and other["matches"]["last_computed_at"] is None