from datetime import date, timedelta
from typing import get_args

import pytest

from app.modules.ai.agents.strategy import STRATEGY_AGENT, Kind, PlanProposal
from app.modules.ai.mock_provider import MockProvider
from app.modules.ai.orchestrator import run_agent
from app.modules.strategy.planning import KINDS, MAX_TASKS, build_rule_plan, schedule
from app.modules.strategy.schemas import TaskStatus
from tests.ai_helpers import ScriptedProvider

TODAY = date(2026, 10, 20)


def task(effort, kind="prepare_materials"):
    return {"title": "t", "detail": "", "kind": kind, "effort_days": effort, "requirement": None, "skill": None}


def facts(**over):
    base = {
        "today": "2026-10-20", "status": "potentially_eligible",
        "opportunity": {"title": "Backend Developer", "category": "job", "organization": "Acme"},
        "requirements": [], "counts": {}, "data_freshness": {}, "engine_explanation": "",
        "deadline_date": "2027-01-31", "days_left": 100, "missing_skills": [], "skills_known": True,
        "experience_gap_months": None, "profile_missing": [],
    }
    base.update(over)
    return base


def kinds(plan):
    return [t["kind"] for t in plan["tasks"]]


# ---------- scheduling ----------

def due(tasks, deadline):
    scheduled, warnings = schedule(tasks, TODAY, deadline)
    return [t["due_date"] for t in scheduled], warnings


def test_schedule_without_a_deadline():
    dates, warnings = due([task(1), task(3), task(2)], None)
    assert dates == [TODAY + timedelta(days=n) for n in (1, 4, 6)] and warnings == []


def test_schedule_with_plenty_of_time_is_not_compressed():
    dates, warnings = due([task(1), task(3), task(2)], TODAY + timedelta(days=30))
    assert dates[-1] == TODAY + timedelta(days=6) and warnings == []


def test_schedule_compresses_when_time_is_short_and_never_passes_the_deadline():
    deadline = date(2026, 10, 25)
    dates, warnings = due([task(2), task(3), task(5)], deadline)
    assert dates == [date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 24)]
    assert len(warnings) == 1 and "compressed" in warnings[0]
    assert max(dates) <= deadline - timedelta(days=1)


@pytest.mark.parametrize("days_to_deadline", [0, 1])
def test_a_deadline_within_a_day_makes_everything_due_now(days_to_deadline):
    dates, warnings = due([task(2), task(3)], TODAY + timedelta(days=days_to_deadline))
    assert dates == [TODAY, TODAY] and "very close" in warnings[0]


@pytest.mark.parametrize("horizon_days,efforts", [(2, [1, 1, 1, 1, 1]), (5, [3, 4, 5]), (10, [1, 2, 3, 4, 5, 6])])
def test_the_last_task_is_always_due_before_the_deadline(horizon_days, efforts):
    deadline = TODAY + timedelta(days=horizon_days)
    dates, _ = due([task(e) for e in efforts], deadline)
    assert max(dates) <= deadline - timedelta(days=1)
    assert dates == sorted(dates)


# ---------- the rules-based plan ----------

def test_minimal_plan():
    plan = build_rule_plan(facts())
    assert kinds(plan) == ["gather_documents", "prepare_materials", "submit", "follow_up"]
    assert plan["summary"] == "A step-by-step plan for Backend Developer. The deadline is 2027-01-31."


def test_plan_reflects_gaps_unknowns_and_failures():
    plan = build_rule_plan(facts(
        profile_missing=["Skills", "Career interests"],
        requirements=[
            {"label": "age at most 35", "outcome": "unknown", "reason": "date of birth not provided"},
            {"label": "country is NG", "outcome": "pass", "reason": None},
            {"label": "graduation year at least 2015", "outcome": "fail", "reason": None},
        ],
        missing_skills=["sql", "docker"],
    ))
    assert kinds(plan) == ["complete_profile", "confirm_requirement", "confirm_requirement", "learn_skill",
                           "learn_skill", "gather_documents", "prepare_materials", "submit", "follow_up"]
    titles = [t["title"] for t in plan["tasks"]]
    assert titles[1] == "Confirm: age at most 35" and titles[2] == "Look into: graduation year at least 2015"
    assert plan["tasks"][1]["requirement"] == "age at most 35"
    assert [t["skill"] for t in plan["tasks"] if t["kind"] == "learn_skill"] == ["sql", "docker"]
    assert "Skills, Career interests" in plan["tasks"][0]["detail"]


def test_plan_size_is_capped_and_always_ends_correctly():
    many = [{"label": f"requirement {n}", "outcome": "unknown", "reason": None} for n in range(10)]
    plan = build_rule_plan(facts(requirements=many, missing_skills=["a", "b", "c", "d", "e"],
                                 profile_missing=["Skills"]))
    assert len(plan["tasks"]) <= MAX_TASKS
    assert sum(k == "confirm_requirement" for k in kinds(plan)) == 4
    assert sum(k == "learn_skill" for k in kinds(plan)) == 3
    assert kinds(plan).count("submit") == 1 and kinds(plan)[-2:] == ["submit", "follow_up"]
    assert all(k in KINDS for k in kinds(plan))
    assert all(1 <= t["effort_days"] <= 14 for t in plan["tasks"])


def test_literals_match_the_planning_module():
    assert get_args(Kind) == KINDS
    assert get_args(TaskStatus) == ("todo", "done", "skipped")


# ---------- the Strategy agent and its checks ----------

def proposal(tasks=None, summary="A clear plan."):
    return {"summary": summary, "tasks": tasks or [
        {"title": "Gather your documents", "kind": "gather_documents", "effort_days": 3},
        {"title": "Prepare your materials", "kind": "prepare_materials", "effort_days": 3},
        {"title": "Submit on the official page", "kind": "submit", "effort_days": 1},
    ]}


def run(provider_answers, data=None):
    return run_agent(STRATEGY_AGENT, data or facts(), provider=ScriptedProvider(*provider_answers))


def test_a_valid_plan_is_accepted():
    result = run([proposal()])
    assert result.source == "ai" and result.attempts == 1
    assert [t["kind"] for t in result.output["tasks"]] == ["gather_documents", "prepare_materials", "submit"]


def test_the_mock_provider_passes_every_check():
    for data in (facts(), facts(profile_missing=["Skills"], missing_skills=["sql"],
                                requirements=[{"label": "age at most 35", "outcome": "unknown", "reason": "x"}])):
        result = run_agent(STRATEGY_AGENT, data, provider=MockProvider())
        assert result.source == "ai" and result.attempts == 1
        assert result.output == build_rule_plan(data)


def bad_plans():
    gather = {"title": "Gather", "kind": "gather_documents", "effort_days": 2}
    prepare = {"title": "Prepare", "kind": "prepare_materials", "effort_days": 2}
    submit = {"title": "Submit", "kind": "submit", "effort_days": 1}
    return {
        "unknown skill": [gather, {"title": "Learn", "kind": "learn_skill", "effort_days": 3, "skill": "cobol"}, submit],
        "made-up requirement": [gather, {"title": "Check", "kind": "confirm_requirement", "effort_days": 1,
                                          "requirement": "invented rule"}, submit],
        "two submits": [gather, submit, submit],
        "no submit": [gather, prepare, {"title": "Wait", "kind": "follow_up", "effort_days": 1}],
        "work after submit": [gather, submit, prepare],
        "too few tasks": [gather, submit],
    }


@pytest.mark.parametrize("name", list(bad_plans()))
def test_ungrounded_plans_are_repaired_once_then_replaced_by_the_rules_plan(name):
    answer = proposal(bad_plans()[name])
    result = run([answer, answer])
    assert result.source == "fallback" and result.attempts == 2
    assert result.output == build_rule_plan(facts())


def test_probability_language_is_rejected():
    tasks = proposal()["tasks"]
    answer = {"summary": "You have a 90% chance if you follow this.", "tasks": tasks}
    assert run([answer, answer]).source == "fallback"
    padded = [{**tasks[0], "title": "Gather documents to guarantee a place"}, *tasks[1:]]
    answer = proposal(padded)
    assert run([answer, answer]).source == "fallback"


def test_a_repaired_answer_can_still_succeed():
    result = run([proposal(bad_plans()["two submits"]), proposal()])
    assert result.source == "ai" and result.attempts == 2


def test_the_agent_may_use_facts_it_was_given():
    data = facts(missing_skills=["sql"], requirements=[{"label": "age at most 35", "outcome": "unknown", "reason": "x"}])
    tasks = proposal()["tasks"]
    good = [
        {"title": "Confirm age", "kind": "confirm_requirement", "effort_days": 1, "requirement": "age at most 35"},
        {"title": "Learn SQL", "kind": "learn_skill", "effort_days": 5, "skill": " SQL "},
        *tasks,
    ]
    result = run([proposal(good)], data)
    assert result.source == "ai"
    assert [t["skill"] for t in result.output["tasks"] if t["kind"] == "learn_skill"] == ["sql"]


def test_extra_keys_in_the_answer_are_rejected():
    schema = PlanProposal.model_json_schema()
    assert "tasks" in schema["properties"] and schema["properties"]["tasks"]["maxItems"] == 20