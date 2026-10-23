import uuid
from datetime import date, datetime, timedelta, timezone

from app.modules.ai import orchestrator
from tests.ai_helpers import ScriptedProvider
from tests.helpers import auth_headers
from tests.test_eligibility_api import fill_profile, open_opportunity
from tests.test_jobs import candidate, make_job
from tests.test_opportunites import make_org, manager

B = "/api/v1"
ADMISSION_KINDS = ["complete_profile", "gather_documents", "prepare_materials", "submit", "follow_up"]


def today():
    return datetime.now(timezone.utc).date()


def run_of(client, h, response):
    assert response.status_code == 202, response.text
    r = client.get(f"{B}/ai/runs/{response.json()['run_id']}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def generate(client, h, opp_id, **extra):
    return run_of(client, h, client.post(f"{B}/strategies/generate", json={"opportunity_id": opp_id, **extra}, headers=h))


def plan_id(client, h, opp_id, **extra):
    run = generate(client, h, opp_id, **extra)
    assert run["status"] == "succeeded", run
    return run["output"]["strategy_id"]


def fetch(client, h, sid):
    r = client.get(f"{B}/strategies/{sid}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def setup(client, score=215):
    mgr = manager(client)
    user = auth_headers(client, "u@example.com")
    opp = open_opportunity(client, mgr)
    fill_profile(client, user, score=score)
    return mgr, user, opp


# ---------- generating a plan ----------

def test_strategy_endpoints_require_auth(client):
    assert client.post(f"{B}/strategies/generate", json={"opportunity_id": str(uuid.uuid4())}).status_code == 401
    assert client.get(f"{B}/strategies").status_code == 401
    assert client.get(f"{B}/strategies/{uuid.uuid4()}").status_code == 401


def test_a_plan_is_a_draft_with_dates_scheduled_by_code(client):
    mgr, user, opp = setup(client)
    run = generate(client, user, opp)

    assert run["status"] == "succeeded" and run["requires_approval"] is True
    assert [(s["name"], s["kind"]) for s in run["steps"]] == [("facts", "service"), ("plan", "agent"), ("save", "service")]
    assert run["output"]["source"] == "ai" and run["output"]["eligibility_status"] == "eligible"

    strategy = fetch(client, user, run["output"]["strategy_id"])
    assert strategy["status"] == "draft" and strategy["source"] == "ai" and strategy["run_id"] == run["id"]
    assert strategy["opportunity"]["title"] == "BSc Computer Science" and strategy["warnings"] == []
    tasks = strategy["tasks"]
    assert [t["kind"] for t in tasks] == ADMISSION_KINDS
    assert [t["position"] for t in tasks] == [1, 2, 3, 4, 5]
    assert [t["due_date"] for t in tasks] == [(today() + timedelta(days=n)).isoformat() for n in (1, 4, 7, 8, 9)]
    assert {t["origin"] for t in tasks} == {"ai"} and {t["status"] for t in tasks} == {"todo"}
    assert "Nationality" in tasks[0]["detail"]
    # generating a plan commits the user to nothing
    assert client.get(f"{B}/applications", headers=user).json()["total"] == 0


def test_a_model_that_keeps_failing_gives_a_rules_based_plan(client, monkeypatch):
    mgr, user, opp = setup(client)
    monkeypatch.setattr(orchestrator, "get_provider", lambda: ScriptedProvider("not json", "still not json"))
    run = generate(client, user, opp)
    assert run["status"] == "succeeded" and run["output"]["source"] == "fallback"
    assert run["steps"][1]["meta"]["fallback_reason"] == "invalid_output"
    strategy = fetch(client, user, run["output"]["strategy_id"])
    assert strategy["source"] == "rules" and {t["origin"] for t in strategy["tasks"]} == {"rules"}
    assert [t["kind"] for t in strategy["tasks"]] == ADMISSION_KINDS


def test_skills_the_user_lacks_become_learning_tasks(client):
    mgr = manager(client)
    org = make_org(client, mgr)
    job_id = make_job(client, mgr, org)  # needs python and sql; no eligibility rules
    candidate(client, skills=("python", "git"))
    user = auth_headers(client, "u@example.com")
    strategy = fetch(client, user, plan_id(client, user, job_id))
    learning = [t for t in strategy["tasks"] if t["kind"] == "learn_skill"]
    assert [t["title"] for t in learning] == ["Build or evidence the skill: sql"]
    assert learning[0]["effort_days"] == 5


def test_ineligible_opportunities_need_force(client):
    mgr, user, opp = setup(client, score=150)  # below the minimum of 200
    refused = generate(client, user, opp)
    assert refused["status"] == "failed" and refused["error_code"] == "not_recommended"
    assert client.get(f"{B}/strategies", headers=user).json()["total"] == 0

    forced = generate(client, user, opp, force=True)
    assert forced["status"] == "succeeded" and forced["output"]["eligibility_status"] == "ineligible"
    titles = [t["title"] for t in fetch(client, user, forced["output"]["strategy_id"])["tasks"]]
    assert any(t.startswith("Look into: qualification score") for t in titles)


def test_closed_and_hidden_opportunities(client):
    mgr = manager(client)
    user = auth_headers(client, "u@example.com")
    closed = open_opportunity(client, mgr, status="closed")
    run = generate(client, user, closed)
    assert run["status"] == "failed" and run["error_code"] == "opportunity_closed"
    missing = client.post(f"{B}/strategies/generate", json={"opportunity_id": str(uuid.uuid4())}, headers=user)
    assert missing.status_code == 404


# ---------- decisions: accept, reject, replace ----------

def test_drafts_replace_each_other_and_accepting_supersedes(client):
    mgr, user, opp = setup(client)
    status = lambda sid: fetch(client, user, sid)["status"]
    first, second = plan_id(client, user, opp), plan_id(client, user, opp)
    assert status(first) == "rejected" and status(second) == "draft"

    accepted = client.patch(f"{B}/strategies/{second}", json={"status": "accepted"}, headers=user)
    assert accepted.status_code == 200 and accepted.json()["status"] == "accepted"
    assert accepted.json()["accepted_at"] is not None

    third = plan_id(client, user, opp)  # a new draft does not disturb the accepted plan
    assert status(second) == "accepted" and status(third) == "draft"
    assert client.patch(f"{B}/strategies/{third}", json={"status": "accepted"}, headers=user).status_code == 200
    assert status(second) == "superseded" and status(third) == "accepted"

    again = client.patch(f"{B}/strategies/{first}", json={"status": "accepted"}, headers=user)
    assert again.status_code == 409 and again.json()["error"]["code"] == "not_a_draft"
    assert client.patch(f"{B}/strategies/{second}", json={"status": "rejected"}, headers=user).status_code == 409
    assert client.patch(f"{B}/strategies/{third}", json={"status": "rejected"}, headers=user).status_code == 200
    assert client.patch(f"{B}/strategies/{third}", json={"status": "draft"}, headers=user).status_code == 422


def test_listing_and_counts(client):
    mgr, user, opp = setup(client)
    first, second = plan_id(client, user, opp), plan_id(client, user, opp)
    task_id = fetch(client, user, second)["tasks"][0]["id"]
    client.patch(f"{B}/strategies/{second}/tasks/{task_id}", json={"status": "done"}, headers=user)

    everything = client.get(f"{B}/strategies", headers=user).json()
    assert everything["total"] == 2
    drafts = client.get(f"{B}/strategies", params={"status": "draft"}, headers=user).json()
    assert [i["id"] for i in drafts["items"]] == [second]
    assert (drafts["items"][0]["tasks_total"], drafts["items"][0]["tasks_done"]) == (5, 1)
    assert client.get(f"{B}/strategies", params={"opportunity_id": opp}, headers=user).json()["total"] == 2
    assert client.get(f"{B}/strategies", params={"opportunity_id": str(uuid.uuid4())}, headers=user).json()["total"] == 0
    assert client.get(f"{B}/strategies", params={"status": "bogus"}, headers=user).status_code == 422
    assert client.get(f"{B}/strategies", params={"limit": 500}, headers=user).status_code == 422


def test_regenerating_creates_a_fresh_draft(client):
    mgr, user, opp = setup(client)
    first = plan_id(client, user, opp)
    run = run_of(client, user, client.post(f"{B}/strategies/{first}/regenerate", headers=user))
    assert run["status"] == "succeeded"
    second = run["output"]["strategy_id"]
    assert second != first
    assert fetch(client, user, first)["status"] == "rejected" and fetch(client, user, second)["status"] == "draft"
    other = auth_headers(client, "other@example.com")
    assert client.post(f"{B}/strategies/{first}/regenerate", headers=other).status_code == 404


# ---------- editing ----------

def test_tasks_are_editable_and_deadlines_stay_the_users_choice(client):
    mgr, user, opp = setup(client)
    sid = plan_id(client, user, opp)
    url = f"{B}/strategies/{sid}"
    tasks = fetch(client, user, sid)["tasks"]
    first = tasks[0]["id"]

    new_date = (today() + timedelta(days=20)).isoformat()
    edited = client.patch(f"{url}/tasks/{first}", json={"title": "  Finish my profile  ", "due_date": new_date,
                                                         "detail": None, "effort_days": 2}, headers=user)
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert (body["title"], body["due_date"], body["detail"], body["effort_days"]) == ("Finish my profile", new_date, "", 2)

    done = client.patch(f"{url}/tasks/{first}", json={"status": "done"}, headers=user).json()
    assert done["status"] == "done" and done["done_at"] is not None
    undone = client.patch(f"{url}/tasks/{first}", json={"status": "todo"}, headers=user).json()
    assert undone["done_at"] is None
    assert client.patch(f"{url}/tasks/{first}", json={"due_date": None}, headers=user).json()["due_date"] is None

    yesterday = (today() - timedelta(days=1)).isoformat()
    for bad in ({"due_date": yesterday}, {"title": None}, {"status": None}, {"status": "weird"}, {"kind": "submit"}):
        assert client.patch(f"{url}/tasks/{first}", json=bad, headers=user).status_code == 422, bad

    added = client.post(f"{url}/tasks", json={"title": "Call the admissions office"}, headers=user)
    assert added.status_code == 201
    assert (added.json()["kind"], added.json()["origin"], added.json()["position"]) == ("custom", "user", 6)
    assert client.delete(f"{url}/tasks/{added.json()['id']}", headers=user).status_code == 204
    assert client.delete(f"{url}/tasks/{added.json()['id']}", headers=user).status_code == 404
    assert len(fetch(client, user, sid)["tasks"]) == 5


def test_plans_have_a_task_limit_and_closed_plans_are_read_only(client):
    mgr, user, opp = setup(client)
    sid = plan_id(client, user, opp)
    url = f"{B}/strategies/{sid}/tasks"
    for n in range(15):
        assert client.post(url, json={"title": f"Extra {n}"}, headers=user).status_code == 201
    full = client.post(url, json={"title": "One too many"}, headers=user)
    assert full.status_code == 422 and full.json()["error"]["code"] == "task_limit"

    client.patch(f"{B}/strategies/{sid}", json={"status": "rejected"}, headers=user)
    task_id = fetch(client, user, sid)["tasks"][0]["id"]
    for response in (
        client.post(url, json={"title": "Late"}, headers=user),
        client.patch(f"{url}/{task_id}", json={"status": "done"}, headers=user),
        client.delete(f"{url}/{task_id}", headers=user),
    ):
        assert response.status_code == 409 and response.json()["error"]["code"] == "read_only"


def test_plans_are_private(client):
    mgr, user, opp = setup(client)
    sid = plan_id(client, user, opp)
    task_id = fetch(client, user, sid)["tasks"][0]["id"]
    other = auth_headers(client, "other@example.com")
    url = f"{B}/strategies/{sid}"
    assert client.get(url, headers=other).status_code == 404
    assert client.patch(url, json={"status": "accepted"}, headers=other).status_code == 404
    assert client.post(f"{url}/tasks", json={"title": "x"}, headers=other).status_code == 404
    assert client.patch(f"{url}/tasks/{task_id}", json={"status": "done"}, headers=other).status_code == 404
    assert client.delete(f"{url}/tasks/{task_id}", headers=other).status_code == 404
    assert client.get(f"{B}/strategies", headers=other).json()["total"] == 0


# ---------- the combined guidance pipeline ----------

def test_guidance_explains_and_plans_in_one_run(client):
    mgr, user, opp = setup(client)
    response = client.post(f"{B}/ai/guidance", json={"opportunity_id": opp}, headers=user)
    run = run_of(client, user, response)

    assert run["status"] == "succeeded" and run["requires_approval"] is True
    assert [(s["name"], s["kind"]) for s in run["steps"]] == [
        ("facts", "service"), ("explain", "agent"), ("plan", "agent"), ("save", "service")]
    out = run["output"]
    assert out["eligibility_status"] == "eligible" and out["explanation_source"] == "ai" and out["plan_source"] == "ai"
    assert "BSc Computer Science" in out["explanation"]["summary"]
    assert fetch(client, user, out["strategy_id"])["status"] == "draft"
    assert run["tokens_in"] == sum(s["tokens_in"] for s in run["steps"])

    hidden = client.post(f"{B}/ai/guidance", json={"opportunity_id": str(uuid.uuid4())}, headers=user)
    assert hidden.status_code == 404
    assert client.post(f"{B}/ai/guidance", json={"opportunity_id": opp}).status_code == 401