import uuid

import pytest

from app.modules.ai.agents.application import (
    CHECKLIST_AGENT, REVIEW_AGENT, build_rule_checklist, build_rule_review,
)
from app.modules.ai.agents.compare import COMPARE_AGENT, build_rule_comparison, choose_best
from app.modules.ai.agents.discover import DISCOVER_AGENT, build_rule_picks
from app.modules.ai.agents.tracker import TRACKER_AGENT, build_rule_summary
from app.modules.ai.mock_provider import MockProvider
from app.modules.ai.orchestrator import run_agent
from app.modules.dashboard.actions import next_actions
from tests.ai_helpers import ScriptedProvider
from tests.test_dashboard_logic import NOW, ctx as dashboard_ctx


def uid(n):
    return str(uuid.UUID(int=n))


def run(agent, facts, *answers):
    return run_agent(agent, facts, provider=ScriptedProvider(*answers) if answers else MockProvider())


# ======================= Opportunity Hunter =======================

def cand(n, score, goal=False, status="eligible"):
    return {"opportunity_id": uid(n), "title": f"Opp {n}", "category": "admission", "organization": "Uni",
            "score": score, "eligibility_status": status, "deadline": None, "goal_aligned": goal,
            "data_flags": [], "top_factors": [f"Field and interest fit: match {n}"]}


def discover_facts(candidates, limit=3):
    return {"limit": limit, "category": None, "query": None, "already_tracking": 0, "candidates": candidates}


def test_hunter_rules_put_goals_first_then_score():
    facts = discover_facts([cand(1, 50), cand(2, 90), cand(3, 70, goal=True), cand(4, 95)])
    picks = build_rule_picks(facts)["picks"]
    assert [p["opportunity_id"] for p in picks] == [uid(3), uid(4), uid(2)]
    assert "matches one of your goals" in picks[0]["reason"]
    assert "You appear to meet the documented requirements" in picks[1]["reason"]
    assert "Field and interest fit: match 4" in picks[1]["reason"]


def test_the_mock_provider_passes_the_hunter_checks():
    facts = discover_facts([cand(1, 50), cand(2, 90)])
    result = run(DISCOVER_AGENT, facts)
    assert result.source == "ai" and result.attempts == 1 and result.output == build_rule_picks(facts)
    empty = run(DISCOVER_AGENT, discover_facts([]))
    assert empty.source == "ai" and empty.output["picks"] == []


def pick(n, reason="A good fit."):
    return {"opportunity_id": uid(n), "reason": reason}


@pytest.mark.parametrize("answer", [
    {"overview": "x", "picks": [pick(99)]},                         # an opportunity we never retrieved
    {"overview": "x", "picks": [pick(1), pick(1)]},                 # the same one twice
    {"overview": "x", "picks": []},                                 # nothing, though candidates exist
    {"overview": "x", "picks": [pick(1), pick(2)]},                 # more than the limit
    {"overview": "x", "picks": [pick(1, "You have a 90% chance.")]},
    {"overview": "x", "picks": [pick(1, "A guaranteed place.")]},
])
def test_the_hunter_cannot_invent_or_overpromise(answer):
    facts = discover_facts([cand(1, 90), cand(2, 80)], limit=1)
    result = run(DISCOVER_AGENT, facts, answer, answer)
    assert result.source == "fallback" and result.attempts == 2
    assert result.output == build_rule_picks(facts)


def test_a_valid_hunter_answer_is_accepted_and_ids_are_normalised():
    facts = discover_facts([cand(1, 90), cand(2, 80)])
    answer = {"overview": "Two good options.", "picks": [{"opportunity_id": uid(2).upper(), "reason": "Fits."}]}
    result = run(DISCOVER_AGENT, facts, answer)
    assert result.source == "ai" and result.output["picks"][0]["opportunity_id"] == uid(2)


# ======================= Matchmaker =======================

def item(n, score, status="eligible", deadline=None, title=None):
    return {
        "opportunity_id": uid(n), "title": title or f"Opp {n}", "category": "job", "organization": "Acme",
        "score": score, "eligibility_status": status, "deadline": deadline, "location": "Lagos",
        "work_mode": "hybrid", "data_flags": [],
        "factors": [
            {"label": "Skills fit", "points": 30, "max_points": 30, "detail": "You have 3 of 3", "known": True},
            {"label": "Location fit", "points": 0, "max_points": 15, "detail": "Not in your preferred locations", "known": True},
            {"label": "Seniority fit", "points": 0, "max_points": 15, "detail": "No work experience recorded", "known": False},
        ],
        "gaps": ["Add your work experience", "Set your remote-work preference", "Third gap"],
    }


def compare_facts(items):
    best = choose_best(items)
    return {"items": items, "best_fit_id": best,
            "best_fit_title": next((i["title"] for i in items if i["opportunity_id"] == best), None),
            "alternatives": []}


def test_best_fit_is_decided_by_code():
    assert choose_best([item(1, 80), item(2, 90)]) == uid(2)
    assert choose_best([item(1, 99, "ineligible"), item(2, 40, "potentially_eligible")]) == uid(2)
    assert choose_best([item(1, 99, "insufficient_information"), item(2, 99, "needs_manual_verification")]) is None
    assert choose_best([item(1, 99, "ineligible")]) is None
    assert choose_best([item(1, 80, deadline="2027-03-01"), item(2, 80, deadline="2027-01-01")]) == uid(2)
    assert choose_best([item(1, 80, deadline=None), item(2, 80, deadline="2027-01-01")]) == uid(2)
    assert choose_best([item(2, 80, title="B"), item(1, 80, title="A")]) == uid(1)  # title breaks the last tie


def test_comparison_rules_split_strengths_and_trade_offs():
    out = build_rule_comparison(compare_facts([item(1, 70)]))
    comparison = out["comparisons"][0]
    assert comparison["strengths"] == ["Skills fit: You have 3 of 3"]
    assert comparison["trade_offs"] == [
        "Location fit: Not in your preferred locations",
        "Seniority fit: not enough information yet",
        "Add your work experience", "Set your remote-work preference",
    ]
    assert "The highest-scoring one you can still pursue is Opp 1" in out["summary"]
    none = build_rule_comparison(compare_facts([item(1, 70, "ineligible")]))
    assert "None of them clearly fits" in none["summary"]


def test_the_mock_provider_passes_the_matchmaker_checks():
    facts = compare_facts([item(1, 70), item(2, 60, "potentially_eligible")])
    result = run(COMPARE_AGENT, facts)
    assert result.source == "ai" and result.output == build_rule_comparison(facts)


def comparison(n, **over):
    return {"opportunity_id": uid(n), "strengths": ["Good skills fit."], "trade_offs": [], **over}


@pytest.mark.parametrize("answer", [
    {"summary": "x", "comparisons": [comparison(1)]},                       # one is missing
    {"summary": "x", "comparisons": [comparison(1), comparison(1)]},       # one is repeated
    {"summary": "x", "comparisons": [comparison(1), comparison(3)]},       # one was never asked about
    {"summary": "x", "comparisons": [comparison(1), comparison(2, strengths=["A 95% chance of success."])]},
])
def test_matchmaker_answers_must_cover_exactly_the_requested_opportunities(answer):
    facts = compare_facts([item(1, 70), item(2, 60)])
    result = run(COMPARE_AGENT, facts, answer, answer)
    assert result.source == "fallback"


def test_a_valid_matchmaker_answer_is_accepted():
    facts = compare_facts([item(1, 70), item(2, 60)])
    answer = {"summary": "Both are reasonable.", "comparisons": [comparison(2), comparison(1)]}
    result = run(COMPARE_AGENT, facts, answer)
    assert result.source == "ai" and result.output["comparisons"][0]["opportunity_id"] == uid(2)


# ======================= Application: checklist =======================

REQS = [
    {"label": "qualification score (jamb) at least 200", "field": "qualification_score",
     "qualification_type": "jamb", "subject": None},
    {"label": "country is one of NG", "field": "country", "qualification_type": None, "subject": None},
    {"label": "something unusual", "field": "unmapped_field", "qualification_type": None, "subject": None},
]


def checklist_facts(existing=(), limit=15, requirements=REQS, category="admission"):
    return {"application_id": uid(1), "opportunity": {"title": "BSc", "category": category, "organization": "Uni"},
            "requirements": list(requirements), "existing_items": list(existing), "limit": limit}


def test_checklist_rules():
    out = build_rule_checklist(checklist_facts())["items"]
    assert [(i["label"], i["source"]) for i in out] == [
        ("Official result for your JAMB score", "requirement"),
        ("Proof of residence", "requirement"),
        ("Application form completed on the official portal", "standard"),
        ("Official transcripts or result slips", "standard"),
        ("Valid ID or passport", "standard"),
    ]
    assert out[0]["requirement"] == REQS[0]["label"] and out[2]["requirement"] is None
    assert "Supports the requirement" in out[0]["reason"] and "Confirm on the official page" in out[2]["reason"]


def test_checklist_rules_skip_existing_items_and_respect_the_limit():
    out = build_rule_checklist(checklist_facts(existing=["valid id or passport", "Proof of residence"], limit=3))
    assert [i["label"] for i in out["items"]] == [
        "Official result for your JAMB score",
        "Application form completed on the official portal",
        "Official transcripts or result slips",
    ]
    jobs = build_rule_checklist(checklist_facts(requirements=[], category="job"))["items"]
    assert [i["label"] for i in jobs][0] == "Up-to-date CV" and len(jobs) == 3


def test_the_mock_provider_passes_the_checklist_checks():
    facts = checklist_facts(existing=["Valid ID or passport"])
    result = run(CHECKLIST_AGENT, facts)
    assert result.source == "ai" and result.output == build_rule_checklist(facts)


def item_answer(label, source="standard", requirement=None, reason="Common."):
    return {"label": label, "source": source, "requirement": requirement, "reason": reason}


@pytest.mark.parametrize("answer", [
    {"items": []},
    {"items": [item_answer("Valid ID or passport")]},                                   # already on the list
    {"items": [item_answer("Passport photo"), item_answer("passport PHOTO")]},          # repeated
    {"items": [item_answer("Proof", "requirement", "an invented requirement")]},        # not a real requirement
    {"items": [item_answer("Passport photo", "standard", REQS[1]["label"])]},           # standard with a requirement
    {"items": [item_answer("Result slip", reason="Guarantees a 100% chance.")]},
    {"items": [item_answer("A"), item_answer("B"), item_answer("C")]},                  # more than the limit
])
def test_checklist_answers_are_checked(answer):
    facts = checklist_facts(existing=["Valid ID or passport"], limit=2)
    result = run(CHECKLIST_AGENT, facts, answer, answer)
    assert result.source == "fallback"


def test_a_valid_checklist_answer_is_accepted():
    answer = {"items": [item_answer("Passport photo"), item_answer("Proof", "requirement", REQS[1]["label"])]}
    result = run(CHECKLIST_AGENT, checklist_facts(limit=2), answer)
    assert result.source == "ai" and [i["label"] for i in result.output["items"]] == ["Passport photo", "Proof"]


# ======================= Application: draft review =======================

TEXT = "I built several Python services and wrote tests.\n\nI enjoy mentoring junior developers."


def review_facts(text=TEXT, not_mentioned=("sql", "docker")):
    return {"kind": "cv", "opportunity": {"title": "Backend Developer", "category": "job", "organization": "Acme"},
            "requirements": [],
            "skills": {"required": ["python", "sql"], "preferred": ["docker"], "mentioned": ["python"],
                       "not_mentioned": list(not_mentioned), "not_mentioned_count": len(not_mentioned)},
            "stats": {"words": len(text.split()), "characters": len(text), "paragraphs": 2}, "text": text}


def test_review_rules():
    out = build_rule_review(review_facts())
    assert out["missing_topics"] == ["sql", "docker"]
    assert out["observations"][0]["point"].startswith("The posting asks for sql")
    assert out["observations"][-1]["point"] == f"Your draft is {len(TEXT.split())} words in 2 paragraph(s)."
    assert out["summary"] == "Your draft does not mention 2 skill(s) the posting asks for."
    assert build_rule_review(review_facts(not_mentioned=()))["summary"] == "Your draft mentions every skill the posting asks for."


def test_the_mock_provider_passes_the_review_checks():
    facts = review_facts()
    result = run(REVIEW_AGENT, facts)
    assert result.source == "ai" and result.output == build_rule_review(facts)


def review_answer(**over):
    base = {"summary": "A clear start.", "observations": [{"point": "Your opening is direct.", "quote": None}],
            "missing_topics": []}
    base.update(over)
    return base


def test_quotes_must_be_exact_but_formatting_is_forgiven():
    ok = review_answer(observations=[{"point": "Strong opening.", "quote": "I built   several PYTHON services"}])
    assert run(REVIEW_AGENT, review_facts(), ok).source == "ai"
    wrong = review_answer(observations=[{"point": "Strong opening.", "quote": "I built many services"}])
    assert run(REVIEW_AGENT, review_facts(), wrong, wrong).source == "fallback"


@pytest.mark.parametrize("answer", [
    review_answer(missing_topics=["kubernetes"]),                                   # not a skill code found missing
    review_answer(summary="You wrote 99 tests."),                                   # a number nobody gave us
    review_answer(observations=[{"point": "That is a 90% match.", "quote": None}]),
    review_answer(observations=[{"point": "A guaranteed interview.", "quote": None}]),
])
def test_review_answers_that_invent_things_are_rejected(answer):
    assert run(REVIEW_AGENT, review_facts(), answer, answer).source == "fallback"


def test_numbers_from_the_draft_or_the_data_are_allowed():
    words = len(TEXT.split())
    answer = review_answer(summary=f"At {words} words your draft is short.", missing_topics=["sql"])
    assert run(REVIEW_AGENT, review_facts(), answer).source == "ai"
    with_number = "In 2019 I built several Python services for a team of 12 people. " + TEXT
    answer = review_answer(summary="You mention 2019 and 12 people.")
    assert run(REVIEW_AGENT, review_facts(with_number), answer).source == "ai"


# ======================= Tracker =======================

def tfacts(**over):
    base = {
        "today": "2026-10-20",
        "applications": {"total": 0, "active": 0, "by_status": {}, "submitted_ever": 0,
                         "interviews_ever": 0, "offers_ever": 0},
        "upcoming_deadlines": [],
        "plan_tasks": {"done": 0, "open": 0, "total": 0, "overdue": [], "due_soon": []},
        "status_changes_last_14_days": 0, "next_actions": [], "active_goals": 0,
    }
    base.update(over)
    return base


BUSY = tfacts(
    applications={"total": 3, "active": 2, "by_status": {"preparing": 2, "offer_received": 1},
                  "submitted_ever": 1, "interviews_ever": 1, "offers_ever": 1},
    upcoming_deadlines=[{"title": "BSc Computer Science", "status": "preparing", "days_left": 4}],
    plan_tasks={"done": 2, "open": 3, "total": 5,
                "overdue": [{"title": "Request transcript", "days_overdue": 2}], "due_soon": []},
    status_changes_last_14_days=5,
    next_actions=[{"title": "Update the status of: Course", "priority": 4}],
)


def test_tracker_rules():
    empty = build_rule_summary(tfacts())
    assert empty == {"headline": "You are not tracking any applications yet.", "highlights": [], "attention": []}
    out = build_rule_summary(BUSY)
    assert out["headline"] == "You are tracking 3 application(s), 2 still active."
    assert out["highlights"] == [
        "You have recorded 1 submission(s).", "You have reached the interview stage on 1 application(s).",
        "You have received 1 offer(s).", "2 of 5 step(s) in your accepted plans are done.",
        "5 status change(s) recorded in the last 14 days.",
    ]
    assert out["attention"] == ["Overdue by 2 day(s): Request transcript",
                                "BSc Computer Science: deadline in 4 day(s)."]
    nothing_urgent = build_rule_summary(tfacts(next_actions=[{"title": "Find your matches", "priority": 2}]))
    assert nothing_urgent["attention"] == ["Next: Find your matches"]


@pytest.mark.parametrize("facts", [tfacts(), BUSY])
def test_the_mock_provider_passes_the_tracker_checks(facts):
    result = run(TRACKER_AGENT, facts)
    assert result.source == "ai" and result.output == build_rule_summary(facts)


def summary(headline="You are tracking 3 application(s), 2 still active.", highlights=(), attention=()):
    return {"headline": headline, "highlights": list(highlights), "attention": list(attention)}


@pytest.mark.parametrize("facts,answer", [
    (tfacts(), summary("You submitted 3 applications.")),                    # nothing was submitted
    (tfacts(), summary("You have been interviewed twice.")),
    (tfacts(), summary("You received an offer.")),
    (tfacts(), summary("You completed your plan.")),
    (tfacts(), summary("You are tracking 12345 applications.")),             # a number nobody gave us
    (tfacts(), summary("A great start, with a 90% chance of success.")),
])
def test_the_tracker_cannot_claim_what_is_not_recorded(facts, answer):
    result = run(TRACKER_AGENT, facts, answer, answer)
    assert result.source == "fallback" and result.output == build_rule_summary(facts)


def test_the_tracker_may_state_what_is_recorded():
    answer = summary("You submitted 1 application and finished 2 steps.", ["An interview stage was reached."])
    assert run(TRACKER_AGENT, BUSY, answer).source == "ai"


# ======================= Dashboard: steps from accepted plans =======================

def task(title="Request transcript", days=-3, task_id="t-1"):
    from datetime import timedelta
    return {"id": task_id, "title": title, "due_date": (NOW + timedelta(days=days)).date(),
            "opportunity": "BSc Computer Science"}


def test_plan_steps_become_actions():
    actions = next_actions(dashboard_ctx(tasks=[task(days=-3), task("Soon", days=1, task_id="t-2"),
                                                task("Later", days=3, task_id="t-3"),
                                                task("Today", days=0, task_id="t-4")]), NOW)
    assert [(a["type"], a["priority"]) for a in actions] == [
        ("overdue_task", 1), ("task_due_soon", 3), ("task_due_soon", 3), ("task_due_soon", 4)]
    assert actions[0]["title"] == "Overdue: Request transcript"
    assert "was due 3 day(s) ago" in actions[0]["detail"]
    assert {a["title"] for a in actions[1:]} == {"Due today: Today", "Due tomorrow: Soon", "Due in 3 days: Later"}
    assert actions[0]["target"] == {"kind": "strategy_task", "id": "t-1"}
    assert next_actions(dashboard_ctx(tasks=[]), NOW) == []