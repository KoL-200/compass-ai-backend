import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.modules.ai import orchestrator
from app.modules.ai.models import AIRun
from app.modules.strategy.models import StrategyTask
from tests.ai_helpers import ScriptedProvider
from tests.helpers import auth_headers
from tests.test_applications import move, track
from tests.test_jobs import candidate, make_job
from tests.test_matching_api import student, world
from tests.test_opportunites import admission, create, make_org, manager
from tests.test_strategy_api import plan_id, setup as strategy_setup

B = "/api/v1"
TEXT = ("I built several Python services and wrote automated tests for them.\n\n"
        "I enjoy mentoring junior developers and sharing what I learn with the team.")


def run_of(client, h, response):
    assert response.status_code == 202, response.text
    r = client.get(f"{B}/ai/runs/{response.json()['run_id']}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def post_run(client, h, path, body=None):
    return run_of(client, h, client.post(f"{B}{path}", json=body, headers=h) if body is not None
                  else client.post(f"{B}{path}", headers=h))


def titles(run):
    return [i["title"] for i in run["output"]["items"]]


def today():
    return datetime.now(timezone.utc).date()


def test_the_new_endpoints_require_auth(client):
    some = str(uuid.uuid4())
    for path, body in [("/ai/discover", {}), ("/ai/compare", {"opportunity_ids": [some, str(uuid.uuid4())]}),
                       (f"/ai/applications/{some}/checklist", None),
                       (f"/ai/applications/{some}/review", {"text": "x" * 60}),
                       ("/ai/progress-summary", {}), (f"/ai/runs/{some}/apply-checklist", {})]:
        assert client.post(f"{B}{path}", json=body).status_code == 401, path


# ======================= Opportunity Hunter =======================

def test_discover_ranks_open_opportunities_and_flags_data_quality(client):
    mgr = manager(client)
    a, b, c = world(client, mgr)  # a: eligible and verified; b: ineligible; c: internship, no rules, no source
    h = student(client)
    run = post_run(client, h, "/ai/discover", {})

    assert run["status"] == "succeeded" and run["requires_approval"] is False
    assert [(s["name"], s["kind"]) for s in run["steps"]] == [("candidates", "service"), ("select", "agent")]
    out = run["output"]
    assert [i["opportunity_id"] for i in out["items"]] == [a, c]  # the ineligible one is never offered
    assert out["candidates_considered"] == 2 and out["already_tracking"] == 0 and out["source"] == "ai"
    assert "not a probability" in out["note"]

    first, second = out["items"]
    assert first["title"] == "BSc Computer Science" and first["eligibility_status"] == "eligible"
    assert first["caveats"] == [] and first["score"] > second["score"]
    assert second["eligibility_status"] == "needs_manual_verification"
    assert second["caveats"] == [
        "No source is recorded for this opportunity",
        "Our information has not been verified",
        "No requirements are documented, so eligibility cannot be assessed",
    ]
    assert "You appear to meet the documented requirements" in first["reason"]
    assert client.get(f"{B}/matches", headers=h).json()["total"] == 2  # the run refreshed the stored matches


def test_discover_respects_goals_filters_and_what_you_already_track(client):
    mgr = manager(client)
    a, b, c = world(client, mgr)
    h = student(client)
    ids = lambda **body: [i["opportunity_id"] for i in post_run(client, h, "/ai/discover", body)["output"]["items"]]

    assert ids(category="internship") == [c] and ids(q="intern") == [c] and ids(q="computer") == [a]
    assert ids(limit=1) == [a]

    client.post(f"{B}/goals", json={"title": "Get an internship", "category": "internship"}, headers=h)
    run = post_run(client, h, "/ai/discover", {})
    assert [i["opportunity_id"] for i in run["output"]["items"]] == [c, a]  # goal-aligned first
    assert [i["goal_aligned"] for i in run["output"]["items"]] == [True, False]

    track(client, h, a)
    run = post_run(client, h, "/ai/discover", {})
    assert [i["opportunity_id"] for i in run["output"]["items"]] == [c]
    assert run["output"]["already_tracking"] == 1


def test_discover_with_nothing_to_offer(client):
    auth_headers(client, "blank@example.com")
    run = post_run(client, auth_headers(client, "blank@example.com"), "/ai/discover", {})
    assert run["status"] == "succeeded" and run["output"]["items"] == []
    assert "No open opportunities matched" in run["output"]["overview"]


def test_the_hunter_cannot_invent_an_opportunity(client, monkeypatch):
    mgr = manager(client)
    a, b, c = world(client, mgr)
    h = student(client)
    invented = {"overview": "Here you go.", "picks": [{"opportunity_id": str(uuid.uuid4()), "reason": "Great."}]}
    monkeypatch.setattr(orchestrator, "get_provider", lambda: ScriptedProvider(invented, invented))
    run = post_run(client, h, "/ai/discover", {})
    assert run["status"] == "succeeded" and run["output"]["source"] == "fallback"
    assert [i["opportunity_id"] for i in run["output"]["items"]] == [a, c]
    assert run["steps"][-1]["meta"]["fallback_reason"] == "invalid_output"


def test_discover_validation(client):
    h = auth_headers(client)
    for body in ({"limit": 0}, {"limit": 11}, {"category": "event"}, {"q": "x" * 101}, {"surprise": 1}):
        assert client.post(f"{B}/ai/discover", json=body, headers=h).status_code == 422, body


# ======================= Matchmaker =======================

def test_the_best_fit_is_decided_by_code_not_by_the_model(client, monkeypatch):
    mgr = manager(client)
    a, b, c = world(client, mgr)
    h = student(client)

    run = post_run(client, h, "/ai/compare", {"opportunity_ids": [c, a]})
    out = run["output"]
    assert run["status"] == "succeeded" and out["source"] == "ai"
    assert out["best_fit_id"] == a  # c needs manual verification, so it cannot be "the best fit"
    assert [i["opportunity_id"] for i in out["items"]] == [c, a]  # in the order requested
    assert "BSc Computer Science" in out["summary"] and out["alternatives"] == []
    assert all(isinstance(i["strengths"], list) and isinstance(i["trade_offs"], list) for i in out["items"])
    assert "not a probability" in out["note"]

    ineligible = post_run(client, h, "/ai/compare", {"opportunity_ids": [a, b]})["output"]
    by_id = {i["opportunity_id"]: i for i in ineligible["items"]}
    assert ineligible["best_fit_id"] == a and by_id[b]["eligibility_status"] == "ineligible"
    assert any("Requirement not met" in t for t in by_id[b]["trade_offs"])

    answer = {"summary": "Pick the second one!", "comparisons": [
        {"opportunity_id": c, "strengths": ["Looks great."], "trade_offs": []},
        {"opportunity_id": a, "strengths": [], "trade_offs": ["Looks poor."]}]}
    monkeypatch.setattr(orchestrator, "get_provider", lambda: ScriptedProvider(answer))
    lured = post_run(client, h, "/ai/compare", {"opportunity_ids": [c, a]})["output"]
    assert lured["source"] == "ai" and lured["best_fit_id"] == a  # the model's wording cannot change it


def test_compare_validation(client):
    mgr = manager(client)
    a, b, c = world(client, mgr)
    h = student(client)
    uuids = [str(uuid.uuid4()) for _ in range(5)]
    assert client.post(f"{B}/ai/compare", json={"opportunity_ids": [a]}, headers=h).status_code == 422
    assert client.post(f"{B}/ai/compare", json={"opportunity_ids": [a, a]}, headers=h).status_code == 422
    assert client.post(f"{B}/ai/compare", json={"opportunity_ids": uuids}, headers=h).status_code == 422
    assert client.post(f"{B}/ai/compare", json={"opportunity_ids": [a, uuids[0]]}, headers=h).status_code == 404

    org = make_org(client, mgr, "Uni Two", "NG")
    closed = create(client, mgr, admission(org, title="Closed Course", status="closed")).json()["id"]
    run = post_run(client, h, "/ai/compare", {"opportunity_ids": [a, closed]})
    assert run["status"] == "failed" and run["error_code"] == "not_comparable"


# ======================= Application agent: checklist =======================

def tracked(client):
    mgr, user, opp = strategy_setup(client)
    app_id = track(client, user, opp).json()["id"]
    return mgr, user, opp, app_id


def test_checklist_proposal_and_approval(client):
    mgr, user, opp, app_id = tracked(client)
    run = post_run(client, user, f"/ai/applications/{app_id}/checklist")
    assert run["status"] == "succeeded" and run["requires_approval"] is True
    assert [(s["name"], s["kind"]) for s in run["steps"]] == [("facts", "service"), ("propose", "agent")]
    items = run["output"]["items"]
    assert [i["label"] for i in items][:4] == [
        "Proof of residence", "Certificate for your highest completed education",
        "Official result for your JAMB score", "Result slips showing your subjects"]
    assert [i["source"] for i in items] == ["requirement"] * 4 + ["standard"] * 3
    assert items[0]["requirement"] == "country is one of NG" and items[4]["requirement"] is None
    assert "not confirmed requirements" in run["output"]["note"]
    assert client.get(f"{B}/applications/{app_id}", headers=user).json()["checklist"] == []  # nothing added yet

    applied = client.post(f"{B}/ai/runs/{run['id']}/apply-checklist", json={"items": [0, 5, 0]}, headers=user)
    assert applied.status_code == 200, applied.text
    assert applied.json() == {"added": ["Proof of residence", "Official transcripts or result slips"], "skipped": []}
    saved = client.get(f"{B}/applications/{app_id}", headers=user).json()["checklist"]
    assert [c["label"] for c in saved] == ["Proof of residence", "Official transcripts or result slips"]

    again = client.post(f"{B}/ai/runs/{run['id']}/apply-checklist", json={}, headers=user)
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_applied"

    second = post_run(client, user, f"/ai/applications/{app_id}/checklist")  # what you have is left out
    assert len(second["output"]["items"]) == 5
    assert client.post(f"{B}/ai/runs/{second['id']}/apply-checklist", json={"items": [99]}, headers=user).status_code == 422
    everything = client.post(f"{B}/ai/runs/{second['id']}/apply-checklist", json={}, headers=user)
    assert len(everything.json()["added"]) == 5
    assert len(client.get(f"{B}/applications/{app_id}", headers=user).json()["checklist"]) == 7


def test_checklist_edge_cases(client, monkeypatch):
    mgr, user, opp, app_id = tracked(client)
    other = auth_headers(client, "other@example.com")
    assert client.post(f"{B}/ai/applications/{app_id}/checklist", headers=other).status_code == 404

    bad = {"items": [{"label": "Valid ID or passport", "source": "standard", "requirement": None, "reason": ""}]}
    client.post(f"{B}/applications/{app_id}/checklist", json={"label": "valid id or passport"}, headers=user)
    monkeypatch.setattr(orchestrator, "get_provider", lambda: ScriptedProvider(bad, bad))
    run = post_run(client, user, f"/ai/applications/{app_id}/checklist")  # the model repeated an item you have
    assert run["output"]["source"] == "fallback"
    assert "Valid ID or passport" not in [i["label"] for i in run["output"]["items"]]
    monkeypatch.undo()

    profile = client.post(f"{B}/ai/profile/extract", json={"text": "I am a Nigerian student of maths."}, headers=user)
    wrong_kind = client.post(f"{B}/ai/runs/{profile.json()['run_id']}/apply-checklist", json={}, headers=user)
    assert wrong_kind.status_code == 422 and wrong_kind.json()["error"]["code"] == "not_applicable"
    assert client.post(f"{B}/ai/runs/{run['id']}/apply-checklist", json={}, headers=other).status_code == 404

    for n in range(48):  # 1 item exists already; fill the checklist to 49, then to 50
        client.post(f"{B}/applications/{app_id}/checklist", json={"label": f"Item {n}"}, headers=user)
    client.post(f"{B}/applications/{app_id}/checklist", json={"label": "Last one"}, headers=user)
    full = post_run(client, user, f"/ai/applications/{app_id}/checklist")
    assert full["status"] == "failed" and full["error_code"] == "checklist_full"

    move(client, user, app_id, "withdrawn")
    closed = post_run(client, user, f"/ai/applications/{app_id}/checklist")
    assert closed["status"] == "failed" and closed["error_code"] in ("application_closed", "checklist_full")


# ======================= Application agent: draft review =======================

def job_application(client):
    mgr = manager(client)
    org = make_org(client, mgr)
    job_id = make_job(client, mgr, org)  # python, sql | prefers docker
    h = candidate(client, skills=("python",))
    return h, track(client, h, job_id).json()["id"]


def test_draft_review_is_feedback_only_and_the_draft_is_not_stored(client):
    h, app_id = job_application(client)
    run = post_run(client, h, f"/ai/applications/{app_id}/review", {"text": TEXT, "kind": "cv"})
    assert run["status"] == "succeeded" and run["requires_approval"] is False
    out = run["output"]
    assert out["kind"] == "cv" and out["source"] == "ai"
    assert out["stats"] == {"words": len(TEXT.split()), "characters": len(TEXT), "paragraphs": 2}
    assert out["skill_coverage"] == {"mentioned": ["python"], "not_mentioned": ["sql", "docker"]}
    assert out["missing_topics"] == ["sql", "docker"]
    assert len(out["observations"]) == 3 and out["observations"][0]["point"].startswith("The posting asks for sql")
    assert "feedback only" in out["note"] and "responsible for everything you submit" in out["note"]

    assert "mentoring junior developers" not in json.dumps(run)  # the draft is not in the response...
    with SessionLocal() as db:
        meta = db.get(AIRun, uuid.UUID(run["id"])).input_meta
    assert meta == {"application_id": app_id, "kind": "cv", "chars": len(TEXT)}  # ...nor in the database


def test_review_quotes_must_come_from_the_draft(client, monkeypatch):
    h, app_id = job_application(client)
    good = {"summary": "A clear start.", "missing_topics": ["sql"],
            "observations": [{"point": "Your opening is direct.", "quote": "I built several Python services"}]}
    monkeypatch.setattr(orchestrator, "get_provider", lambda: ScriptedProvider(good))
    run = post_run(client, h, f"/ai/applications/{app_id}/review", {"text": TEXT})
    assert run["output"]["source"] == "ai"
    assert run["output"]["observations"][0]["quote"] == "I built several Python services"

    forged = {**good, "observations": [{"point": "Nice.", "quote": "I led a team of 40 engineers"}]}
    monkeypatch.setattr(orchestrator, "get_provider", lambda: ScriptedProvider(forged, forged))
    assert post_run(client, h, f"/ai/applications/{app_id}/review", {"text": TEXT})["output"]["source"] == "fallback"


def test_review_for_an_admission_application_and_validation(client):
    mgr, user, opp, app_id = tracked(client)
    run = post_run(client, user, f"/ai/applications/{app_id}/review", {"text": TEXT})
    assert run["output"]["skill_coverage"] == {"mentioned": [], "not_mentioned": []}
    assert run["output"]["summary"] == "Check your draft against the requirements on the official page."

    url = f"{B}/ai/applications/{app_id}/review"
    other = auth_headers(client, "other@example.com")
    assert client.post(url, json={"text": TEXT}, headers=other).status_code == 404
    assert client.post(url, json={"text": "too short"}, headers=user).status_code == 422
    assert client.post(url, json={"text": TEXT, "kind": "poem"}, headers=user).status_code == 422
    assert client.post(url, json={"text": "x" * 8001}, headers=user).status_code == 422


# ======================= Tracker =======================

def overdue_plan(client):
    """An accepted plan whose first step was due five days ago."""
    mgr, user, opp = strategy_setup(client)
    sid = plan_id(client, user, opp)
    assert client.patch(f"{B}/strategies/{sid}", json={"status": "accepted"}, headers=user).status_code == 200
    with SessionLocal() as db:
        db.execute(update(StrategyTask).where(StrategyTask.strategy_id == uuid.UUID(sid), StrategyTask.position == 1)
                   .values(due_date=today() - timedelta(days=5)))
        db.commit()
    return user, sid


def test_progress_summary_for_a_new_user(client):
    h = auth_headers(client)
    run = post_run(client, h, "/ai/progress-summary", {})
    out = run["output"]
    assert run["status"] == "succeeded" and out["source"] == "ai" and out["notification_created"] is False
    assert out["summary"] == {"headline": "You are not tracking any applications yet.", "highlights": [],
                              "attention": ["Next: Complete your profile (0 percent done)"]}
    assert "does not predict outcomes" in out["note"]
    assert [(s["name"], s["kind"]) for s in run["steps"]] == [
        ("facts", "service"), ("summary", "agent"), ("notify", "service")]


def test_progress_summary_reports_overdue_plan_steps_and_can_notify_once_a_day(client):
    user, _ = overdue_plan(client)
    run = post_run(client, user, "/ai/progress-summary", {"notify": True})
    summary = run["output"]["summary"]
    assert summary["highlights"] == ["0 of 5 step(s) in your accepted plans are done."]
    assert summary["attention"] == ["Overdue by 5 day(s): Complete your profile"]
    assert run["output"]["notification_created"] is True

    notes = client.get(f"{B}/notifications", headers=user).json()
    assert notes["total"] == 1 and notes["items"][0]["type"] == "progress_summary"
    assert notes["items"][0]["title"] == summary["headline"]
    assert "Overdue by 5 day(s)" in notes["items"][0]["body"]

    again = post_run(client, user, "/ai/progress-summary", {"notify": True})
    assert again["output"]["notification_created"] is False  # one per day
    assert client.get(f"{B}/notifications", headers=user).json()["total"] == 1


def test_the_tracker_cannot_claim_progress_that_is_not_recorded(client, monkeypatch):
    h = auth_headers(client)
    claims = {"headline": "You submitted 12345 applications.", "highlights": [], "attention": []}
    monkeypatch.setattr(orchestrator, "get_provider", lambda: ScriptedProvider(claims, claims))
    run = post_run(client, h, "/ai/progress-summary", {})
    assert run["output"]["source"] == "fallback"
    assert run["output"]["summary"]["headline"] == "You are not tracking any applications yet."

    honest = {"headline": "You are not tracking any applications yet.", "highlights": [], "attention": []}
    monkeypatch.setattr(orchestrator, "get_provider", lambda: ScriptedProvider(honest))
    assert post_run(client, h, "/ai/progress-summary", {})["output"]["source"] == "ai"


def test_dashboard_shows_steps_from_accepted_plans(client):
    user, sid = overdue_plan(client)
    dash = client.get(f"{B}/dashboard", headers=user).json()
    assert dash["plan_tasks"] == {"open": 5, "done": 0, "overdue": 1, "due_soon": 0}
    first = dash["next_actions"][0]
    assert (first["type"], first["priority"]) == ("overdue_task", 1)
    assert first["title"] == "Overdue: Complete your profile" and first["target"]["kind"] == "strategy_task"

    task_id = first["target"]["id"]
    done = client.patch(f"{B}/strategies/{sid}/tasks/{task_id}", json={"status": "done"}, headers=user)
    assert done.status_code == 200
    after = client.get(f"{B}/dashboard", headers=user).json()
    assert after["plan_tasks"] == {"open": 4, "done": 1, "overdue": 0, "due_soon": 0}
    assert all(a["type"] != "overdue_task" for a in after["next_actions"])


def test_drafts_do_not_count_as_plans(client):
    mgr, user, opp = strategy_setup(client)
    plan_id(client, user, opp)  # left as a draft
    dash = client.get(f"{B}/dashboard", headers=user).json()
    assert dash["plan_tasks"] == {"open": 0, "done": 0, "overdue": 0, "due_soon": 0}