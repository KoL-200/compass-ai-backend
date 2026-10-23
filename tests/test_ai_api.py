import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import app.modules.ai.jobs  # noqa: F401  (registers the ai-cleanup job)
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.jobs import run_job
from app.modules.ai import orchestrator, pipelines
from app.modules.ai import service as ai_service
from app.modules.ai.agents.explain import EXPLAIN_AGENT
from app.modules.ai.llm import LLMResponse, ProviderError
from app.modules.ai.models import AIRun
from app.modules.ai.orchestrator import Pipeline, Step
from app.modules.auth.models import User
from tests.ai_helpers import SAMPLE_TEXT, ScriptedProvider, explain_payload, explanation
from tests.helpers import auth_headers
from tests.test_eligibility_api import fill_profile, open_opportunity
from tests.test_opportunites import manager

B = "/api/v1"


@pytest.fixture(autouse=True)
def fast_settings(monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_retry_backoff_seconds", 0)


def extract(client, h, text=SAMPLE_TEXT):
    r = client.post(f"{B}/ai/profile/extract", json={"text": text}, headers=h)
    assert r.status_code == 202, r.text
    return r.json()["run_id"]


def get_run(client, h, run_id):
    r = client.get(f"{B}/ai/runs/{run_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def new_run(email, task="profile_extract"):
    """A queued run that has not been executed."""
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        return str(ai_service.create_run(db, user, task, {"chars": 50}).id)


def status_of(run_id):
    with SessionLocal() as db:
        return db.get(AIRun, uuid.UUID(run_id)).status


# ---------- profile extraction and approval ----------

def test_ai_endpoints_require_auth(client):
    assert client.post(f"{B}/ai/profile/extract", json={"text": SAMPLE_TEXT}).status_code == 401
    assert client.get(f"{B}/ai/runs").status_code == 401
    assert client.get(f"{B}/ai/usage").status_code == 401


def test_extraction_is_a_proposal_until_the_user_applies_it(client):
    h = auth_headers(client)
    client.put(f"{B}/profiles/me/skills", json={"skills": [{"name": "python", "level": "advanced"}]}, headers=h)
    run_id = extract(client, h)
    run = get_run(client, h, run_id)

    assert run["status"] == "succeeded" and run["task_type"] == "profile_extract"
    assert run["requires_approval"] is True and run["applied_at"] is None
    assert run["provider"] == "mock" and run["output"]["source"] == "ai"
    assert run["output"]["proposal"]["country"] == "NG" and run["tokens_in"] > 0
    assert [(s["name"], s["agent_name"], s["status"]) for s in run["steps"]] == [
        ("extract", "profile_intelligence", "succeeded")]
    assert run["steps"][0]["meta"]["prompt_version"] == "profile_extract_v1"
    assert client.get(f"{B}/profiles/me", headers=h).json()["country"] is None  # nothing saved yet

    # the text the user typed is not stored
    assert SAMPLE_TEXT[:40] not in json.dumps(run)
    with SessionLocal() as db:
        assert db.get(AIRun, uuid.UUID(run_id)).input_meta == {"chars": len(SAMPLE_TEXT)}

    applied = client.post(f"{B}/ai/runs/{run_id}/apply", json={"fields": ["country", "skills"]}, headers=h)
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] == ["country", "skills"]
    assert {c["field"] for c in applied.json()["changes"]} == {"country", "skills"}

    profile = client.get(f"{B}/profiles/me", headers=h).json()
    assert profile["country"] == "NG" and profile["field_of_study"] is None  # only what was chosen
    levels = {s["name"]: s["level"] for s in profile["skills"]}
    assert levels["python"] == "advanced" and "sql" in levels  # existing levels are preserved

    again = client.post(f"{B}/ai/runs/{run_id}/apply", json={}, headers=h)
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_applied"


def test_apply_everything_and_the_validation_around_it(client):
    h = auth_headers(client)
    run_id = extract(client, h)
    bogus = client.post(f"{B}/ai/runs/{run_id}/apply", json={"fields": ["full_name"]}, headers=h)
    assert bogus.status_code == 422 and bogus.json()["error"]["code"] == "invalid_fields"

    assert client.post(f"{B}/ai/runs/{run_id}/apply", json={}, headers=h).status_code == 200
    profile = client.get(f"{B}/profiles/me", headers=h).json()
    assert (profile["country"], profile["graduation_year"], profile["remote_preference"]) == ("NG", 2027, "remote")
    assert profile["needs_financial_aid"] is True and profile["career_interests"] == ["data science", "machine learning"]
    assert profile["qualifications"] == []  # grades are never taken from AI extraction

    empty_id = extract(client, h, "Hello there, this message contains nothing useful at all.")
    empty = client.post(f"{B}/ai/runs/{empty_id}/apply", json={}, headers=h)
    assert empty.status_code == 422 and empty.json()["error"]["code"] == "nothing_to_apply"


def test_extraction_input_validation(client):
    h = auth_headers(client)
    for text in ("too short", "x" * 5001):
        assert client.post(f"{B}/ai/profile/extract", json={"text": text}, headers=h).status_code == 422
    assert client.post(f"{B}/ai/profile/extract", json={"text": SAMPLE_TEXT, "extra": 1}, headers=h).status_code == 422


def test_a_provider_outage_fails_the_run_cleanly(client, monkeypatch):
    h = auth_headers(client)
    down = ProviderError("down", retryable=True)
    monkeypatch.setattr(orchestrator, "get_provider", lambda: ScriptedProvider(down, down, down))
    run = get_run(client, h, extract(client, h))
    assert run["status"] == "failed" and run["error_code"] == "ai_unavailable" and run["output"] is None
    assert run["steps"][0]["status"] == "failed" and run["steps"][0]["attempts"] == 3
    assert "unavailable" in run["error"]
    nothing = client.post(f"{B}/ai/runs/{run['id']}/apply", json={}, headers=h)
    assert nothing.status_code == 422 and nothing.json()["error"]["code"] == "not_applicable"


def test_unknown_provider_is_a_clear_error(client, monkeypatch):
    h = auth_headers(client)
    monkeypatch.setattr(get_settings(), "ai_provider", "nonexistent")
    r = client.post(f"{B}/ai/profile/extract", json={"text": SAMPLE_TEXT}, headers=h)
    assert r.status_code == 503 and r.json()["error"]["code"] == "ai_unavailable"


# ---------- eligibility explanation ----------

def test_explaining_an_eligibility_result(client):
    mgr, user = manager(client), auth_headers(client, "u@example.com")
    opp_id = open_opportunity(client, mgr)
    fill_profile(client, user)

    r = client.post(f"{B}/ai/eligibility/explain", json={"opportunity_id": opp_id}, headers=user)
    assert r.status_code == 202, r.text
    run = get_run(client, user, r.json()["run_id"])
    assert run["status"] == "succeeded" and run["requires_approval"] is False
    assert [(s["name"], s["kind"]) for s in run["steps"]] == [("evaluate", "service"), ("explain", "agent")]
    out = run["output"]
    assert out["eligibility_status"] == "eligible" and out["source"] == "ai"
    assert "BSc Computer Science" in out["explanation"]["summary"]
    assert out["opportunity"]["title"] == "BSc Computer Science"
    assert client.get(f"{B}/eligibility", headers=user).json()["total"] == 1


def test_the_model_cannot_change_an_eligibility_result(client, monkeypatch):
    mgr, user = manager(client), auth_headers(client, "u@example.com")
    opp_id = open_opportunity(client, mgr)
    fill_profile(client, user, score=150)  # below the minimum of 200: genuinely ineligible
    lying = ScriptedProvider(explanation("eligible"), explanation("eligible"))
    monkeypatch.setattr(orchestrator, "get_provider", lambda: lying)

    r = client.post(f"{B}/ai/eligibility/explain", json={"opportunity_id": opp_id}, headers=user)
    out = get_run(client, user, r.json()["run_id"])["output"]
    assert out["eligibility_status"] == "ineligible" and out["source"] == "fallback"
    assert out["explanation"]["gaps"]  # the plain, engine-built explanation
    stored = client.get(f"{B}/eligibility", headers=user).json()["items"][0]
    assert stored["status"] == "ineligible"


def test_explain_rejects_hidden_and_invalid_opportunities(client):
    mgr, user = manager(client), auth_headers(client, "u@example.com")
    draft_id = open_opportunity(client, mgr, verify=False, status="draft")
    for opp_id in (draft_id, str(uuid.uuid4())):
        assert client.post(f"{B}/ai/eligibility/explain", json={"opportunity_id": opp_id}, headers=user).status_code == 404
    assert client.post(f"{B}/ai/eligibility/explain", json={"opportunity_id": "nope"}, headers=user).status_code == 422


# ---------- privacy, limits, cancellation, cleanup ----------

def test_runs_are_private(client):
    a, b = auth_headers(client, "a@example.com"), auth_headers(client, "b@example.com")
    run_id = extract(client, a)
    assert client.get(f"{B}/ai/runs/{run_id}", headers=b).status_code == 404
    assert client.post(f"{B}/ai/runs/{run_id}/cancel", headers=b).status_code == 404
    assert client.post(f"{B}/ai/runs/{run_id}/apply", json={}, headers=b).status_code == 404
    assert client.get(f"{B}/ai/runs", headers=b).json()["total"] == 0
    assert client.get(f"{B}/ai/runs", headers=a).json()["total"] == 1
    assert client.get(f"{B}/ai/runs", params={"limit": 500}, headers=a).status_code == 422


def test_daily_quota_and_usage(client, monkeypatch):
    h = auth_headers(client)
    monkeypatch.setattr(get_settings(), "ai_daily_run_limit", 2)
    extract(client, h)
    extract(client, h)
    blocked = client.post(f"{B}/ai/profile/extract", json={"text": SAMPLE_TEXT}, headers=h)
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "ai_quota_exceeded"
    assert int(blocked.headers["retry-after"]) > 0

    usage = client.get(f"{B}/ai/usage", headers=h).json()
    assert (usage["runs_today"], usage["daily_limit"], usage["provider"]) == (2, 2, "mock")
    assert usage["tokens_in_today"] > 0 and usage["cost_usd_today"] == 0


def test_cancelling_queued_and_flagged_runs(client, monkeypatch):
    h = auth_headers(client, "u@example.com")
    provider = ScriptedProvider(explanation())
    monkeypatch.setattr(orchestrator, "get_provider", lambda: provider)

    queued = new_run("u@example.com")
    r = client.post(f"{B}/ai/runs/{queued}/cancel", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    orchestrator.execute_run(uuid.UUID(queued), {"text": SAMPLE_TEXT})  # a late background task does nothing
    assert status_of(queued) == "cancelled"

    flagged = new_run("u@example.com")  # the cancel request arrives after the run has started
    with SessionLocal() as db:
        db.get(AIRun, uuid.UUID(flagged)).cancel_requested = True
        db.commit()
    orchestrator.execute_run(uuid.UUID(flagged), {"text": SAMPLE_TEXT})
    assert status_of(flagged) == "cancelled" and provider.requests == []
    assert get_run(client, h, flagged)["steps"] == []

    with SessionLocal() as db:
        db.get(AIRun, uuid.UUID(queued)).status = "succeeded"
        db.commit()
    finished = client.post(f"{B}/ai/runs/{queued}/cancel", headers=h)
    assert finished.status_code == 409 and finished.json()["error"]["code"] == "run_finished"


def test_stale_runs_are_closed_by_the_cleanup_job(client):
    auth_headers(client, "u@example.com")
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        user_id = db.scalar(select(User.id))

        def add(status, created, started=None):
            row = AIRun(user_id=user_id, task_type="profile_extract", status=status, input_meta={},
                        provider="mock", model="mock-1", created_at=created, started_at=started)
            db.add(row)
            return row

        old_queued = add("queued", now - timedelta(minutes=30))
        stuck = add("running", now - timedelta(minutes=30), now - timedelta(minutes=30))
        fresh = add("queued", now - timedelta(minutes=1))
        working = add("running", now - timedelta(minutes=2), now - timedelta(minutes=2))
        db.commit()
        ids = [r.id for r in (old_queued, stuck, fresh, working)]

    assert run_job("ai-cleanup") == {"queued_failed": 1, "running_failed": 1}
    with SessionLocal() as db:
        rows = {r.id: r for r in db.scalars(select(AIRun)).all()}
    assert (rows[ids[0]].status, rows[ids[0]].error_code) == ("failed", "never_started")
    assert (rows[ids[1]].status, rows[ids[1]].error_code) == ("failed", "interrupted")
    assert rows[ids[2]].status == "queued" and rows[ids[3]].status == "running"


# ---------- the orchestrator itself ----------

class ConcurrentProvider:
    name = "concurrent"

    def __init__(self):
        self.lock, self.active, self.max_active = threading.Lock(), 0, 0

    def complete(self, request):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.2)
        with self.lock:
            self.active -= 1
        return LLMResponse(json.dumps(explanation("eligible")), 10, 10, "mock-1")


def test_independent_agent_steps_run_in_parallel_and_dependants_wait(client, monkeypatch):
    h = auth_headers(client, "u@example.com")
    provider = ConcurrentProvider()
    monkeypatch.setattr(orchestrator, "get_provider", lambda: provider)

    def agent_step(name):
        return Step(name=name, kind="agent", agent=EXPLAIN_AGENT, payload=lambda ctx: ctx["input"])

    pipeline = Pipeline(
        task="parallel_test",
        steps=[agent_step("a"), agent_step("b"),
               Step(name="combine", kind="service", depends_on=("a", "b"),
                    run=lambda ctx, db: {"same": ctx["a"]["summary"] == ctx["b"]["summary"]})],
        result=lambda ctx: ctx["combine"],
    )
    monkeypatch.setitem(pipelines.PIPELINES, "parallel_test", pipeline)

    run_id = new_run("u@example.com", task="parallel_test")
    orchestrator.execute_run(uuid.UUID(run_id), explain_payload("eligible"))
    run = get_run(client, h, run_id)
    assert run["status"] == "succeeded" and run["output"] == {"same": True}
    assert provider.max_active == 2  # both agent calls were in flight at once
    assert [s["name"] for s in run["steps"]] == ["a", "b", "combine"]
    assert run["tokens_in"] == 20 and run["tokens_out"] == 20


def test_a_broken_pipeline_fails_safely(client):
    h = auth_headers(client, "u@example.com")
    broken = Pipeline(task="broken", steps=[Step(name="x", kind="service", depends_on=("ghost",),
                                                 run=lambda ctx, db: {})], result=lambda ctx: {})
    pipelines.PIPELINES["broken"] = broken
    try:
        run_id = new_run("u@example.com", task="broken")
        orchestrator.execute_run(uuid.UUID(run_id), {})
    finally:
        del pipelines.PIPELINES["broken"]
    run = get_run(client, h, run_id)
    assert run["status"] == "failed" and run["error_code"] == "internal_error"

    unknown = new_run("u@example.com", task="no_such_task")
    orchestrator.execute_run(uuid.UUID(unknown), {})
    assert get_run(client, h, unknown)["error_code"] == "unknown_task"