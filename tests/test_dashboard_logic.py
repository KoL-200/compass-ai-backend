from datetime import datetime, timedelta, timezone

from app.modules.dashboard.actions import next_actions
from app.modules.dashboard.completeness import assess

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


# ---------- profile completeness ----------

def profile(**over):
    base = {"country": "NG", "nationality": "NG", "education_level": "bachelor", "field_of_study": "cs",
            "qualification_count": 1, "skill_count": 2, "interest_count": 1,
            "has_location_preference": True, "needs_financial_aid": False}
    base.update(over)
    return base


def test_full_profile_is_complete():
    assert assess(profile()) == {"percent": 100, "missing": []}


def test_empty_profile_is_zero_and_lists_everything():
    empty = {"country": None, "nationality": None, "education_level": None, "field_of_study": None,
             "qualification_count": 0, "skill_count": 0, "interest_count": 0,
             "has_location_preference": False, "needs_financial_aid": None}
    result = assess(empty)
    assert result["percent"] == 0 and len(result["missing"]) == 9


def test_partial_profile_and_false_counts_as_answered():
    result = assess(profile(needs_financial_aid=None))
    assert result["percent"] == 89  # 8 of 9
    assert result["missing"] == [
        {"field": "financial_aid", "label": "Whether you need financial aid", "helps": ["scholarship"]}
    ]
    three = assess(profile(nationality=None, field_of_study=None, skill_count=0,
                           interest_count=0, has_location_preference=False, needs_financial_aid=None))
    assert three["percent"] == 33


# ---------- next actions ----------

def ctx(**over):
    base = {"email_verified": True, "completeness": {"percent": 100, "missing": []}, "active_goals": 1,
            "matches_last_computed_at": NOW - timedelta(days=1), "matches": [], "applications": []}
    base.update(over)
    return base


def app(status="preparing", days=None, updated_days_ago=1, title="BSc Computer Science", app_id="app-1"):
    return {"id": app_id, "title": title, "category": "admission", "status": status,
            "deadline_at": None if days is None else NOW + timedelta(days=days),
            "updated_at": NOW - timedelta(days=updated_days_ago)}


def match(title="Course", score=80, status="eligible", tracked=False, days=60, opp_id="opp-1"):
    return {"opportunity_id": opp_id, "title": title, "category": "admission", "score": score,
            "eligibility_status": status, "deadline_at": None if days is None else NOW + timedelta(days=days),
            "tracked": tracked}


def types(actions):
    return [a["type"] for a in actions]


def test_nothing_to_do_gives_no_actions():
    assert next_actions(ctx(), NOW) == []


def test_account_and_profile_actions():
    assert types(next_actions(ctx(email_verified=False), NOW)) == ["verify_email"]
    assert next_actions(ctx(active_goals=0), NOW)[0]["priority"] == 5

    missing = [{"label": f"Item {n}"} for n in range(1, 5)]
    low = next_actions(ctx(completeness={"percent": 44, "missing": missing}), NOW)[0]
    assert low["type"] == "complete_profile" and low["priority"] == 2
    assert "Item 1, Item 2, Item 3" in low["detail"] and "Item 4" not in low["detail"]
    assert next_actions(ctx(completeness={"percent": 70, "missing": missing}), NOW)[0]["priority"] == 4


def test_match_refresh_prompts():
    never = next_actions(ctx(matches_last_computed_at=None), NOW)
    assert (never[0]["type"], never[0]["priority"]) == ("refresh_matches", 2)
    old = next_actions(ctx(matches_last_computed_at=NOW - timedelta(days=10)), NOW)
    assert old[0]["priority"] == 4 and "10 days ago" in old[0]["detail"]
    assert next_actions(ctx(matches_last_computed_at=NOW - timedelta(days=3)), NOW) == []


def test_deadline_actions_depend_on_status_and_distance():
    soon = next_actions(ctx(applications=[app(days=3)]), NOW)
    assert (soon[0]["type"], soon[0]["priority"]) == ("deadline_approaching", 1)
    assert soon[0]["title"] == "Deadline is in 3 days: BSc Computer Science"
    assert soon[0]["target"] == {"kind": "application", "id": "app-1"}

    assert next_actions(ctx(applications=[app(days=10)]), NOW)[0]["priority"] == 2
    assert next_actions(ctx(applications=[app(days=20)]), NOW) == []
    assert next_actions(ctx(applications=[app(status="submitted", days=3)]), NOW) == []
    assert next_actions(ctx(applications=[app(days=-1)]), NOW) == []  # the deadline has passed
    assert next_actions(ctx(applications=[app(days=None)]), NOW) == []

    tomorrow = ctx(applications=[app(days=None)])
    tomorrow["applications"][0]["deadline_at"] = NOW + timedelta(hours=30)
    assert next_actions(tomorrow, NOW)[0]["title"].startswith("Deadline is tomorrow")


def test_stale_application_status():
    stale = next_actions(ctx(applications=[app(status="submitted", updated_days_ago=30)]), NOW)
    assert stale[0]["type"] == "update_application_status" and "30 days" in stale[0]["detail"]
    assert next_actions(ctx(applications=[app(status="submitted", updated_days_ago=5)]), NOW) == []
    assert next_actions(ctx(applications=[app(status="rejected", updated_days_ago=90)]), NOW) == []


def test_start_application_suggestions_follow_the_rules():
    matches = [
        match("Best", 95, opp_id="1", days=10),
        match("Tracked already", 99, tracked=True, opp_id="2"),
        match("Not sure yet", 90, status="potentially_eligible", opp_id="3"),
        match("Low score", 40, opp_id="4"),
        match("Later", 70, status="likely_eligible", opp_id="5", days=90),
        match("Second", 85, opp_id="6", days=None),
        match("Fourth place", 61, opp_id="7"),
    ]
    actions = next_actions(ctx(matches=matches), NOW)
    assert [a["title"] for a in actions] == [
        "Consider applying: Best",      # priority 3: deadline within 30 days
        "Consider applying: Second",    # priority 4, no deadline
        "Consider applying: Later",     # priority 4, deadline in 90 days
    ]
    assert [a["priority"] for a in actions] == [3, 4, 4]
    assert actions[0]["target"] == {"kind": "opportunity", "id": "1"}
    assert "confirm the details" in actions[2]["detail"]


def test_actions_are_ordered_by_priority_then_deadline_and_can_be_limited():
    c = ctx(email_verified=False, active_goals=0,
            applications=[app(days=12, title="Later", app_id="a"), app(days=2, title="Urgent", app_id="b")])
    actions = next_actions(c, NOW)
    assert types(actions) == ["deadline_approaching", "deadline_approaching", "verify_email", "set_goal"]
    assert [a["title"] for a in actions[:2]] == [
        "Deadline is in 2 days: Urgent", "Deadline is in 12 days: Later"
    ]
    assert len(next_actions(c, NOW, limit=2)) == 2