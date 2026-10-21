import json
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.matching.scoring import CAPS, FACTORS, WEIGHTS, score_match

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


def prefs(**over):
    base = {
        "interests": ["computer science"], "skills": {"python", "git"}, "country": "NG",
        "education_level": "secondary", "needs_financial_aid": None,
        "remote_preference": None, "preferred_locations": [],
    }
    base.update(over)
    return base


def opp(category="admission", days=30, **over):
    base = {
        "category": category, "title": "BSc Computer Science", "country": "NG",
        "location": "Lagos", "work_mode": "onsite",
        "deadline_at": None if days is None else NOW + timedelta(days=days),
        "details": {"degree_type": "bachelor"},
    }
    base.update(over)
    return base


def elig(status="eligible", unknown=0):
    reqs = [{"outcome": "unknown", "label": f"req {i}", "reason": "not provided"} for i in range(unknown)]
    return {"status": status, "requirements": reqs}


def factor(result, name):
    return next(f for f in result["factors"] if f["name"] == name)


def test_weights_sum_to_100_and_every_factor_exists():
    for category, weights in WEIGHTS.items():
        assert sum(weights.values()) == 100, category
        assert set(weights) <= set(FACTORS)


def test_perfect_admission_scores_100():
    r = score_match(prefs(preferred_locations=["lagos"]), opp(), elig(), NOW)
    assert [f["points"] for f in r["factors"]] == [40, 30, 20, 10]
    assert r["score"] == r["raw_score"] == 100


@pytest.mark.parametrize("status", list(CAPS))
def test_eligibility_status_caps_the_score(status):
    r = score_match(prefs(preferred_locations=["lagos"]), opp(), elig(status), NOW)
    assert r["raw_score"] == 100
    assert r["score"] == CAPS[status]
    assert r["score_cap"] == CAPS[status]


def test_a_title_match_cannot_rescue_an_ineligible_opportunity():
    r = score_match(prefs(), opp(), elig("ineligible"), NOW)
    assert factor(r, "interest")["points"] == 40  # the title fits perfectly...
    assert r["score"] <= 15  # ...but it can never rank high


def test_unknown_inputs_earn_no_points_and_produce_hints():
    empty = prefs(interests=[], country=None, education_level=None)
    r = score_match(empty, opp(), elig("insufficient_information"), NOW)
    for name in ("interest", "level", "location"):
        assert factor(r, name)["points"] == 0 and factor(r, name)["known"] is False
    assert "Add your field of study or career interests" in r["gaps"]
    assert "Add your current education level" in r["gaps"]
    assert any("To improve this ranking" in s for s in r["next_steps"])


def test_partial_interest_overlap_is_discounted():
    r = score_match(prefs(interests=["data science"]), opp(title="Political Science Intro"), elig(), NOW)
    assert 0 < factor(r, "interest")["points"] < 40


def test_level_fit():
    assert factor(score_match(prefs(), opp(), elig(), NOW), "level")["points"] == 30
    master_target = opp(details={"degree_type": "master"})  # skipping a level is not a natural step
    assert factor(score_match(prefs(), master_target, elig(), NOW), "level")["points"] == 0
    from_bachelor = prefs(education_level="bachelor")
    assert factor(score_match(from_bachelor, master_target, elig(), NOW), "level")["points"] == 30


@pytest.mark.parametrize("days,points", [(30, 10), (10, 6), (3, 3), (None, 5)])
def test_deadline_timing(days, points):
    assert factor(score_match(prefs(), opp(days=days), elig(), NOW), "timing")["points"] == points


def test_location_does_not_match_inside_other_words():
    r = score_match(prefs(preferred_locations=["ng"]), opp(country="GB", location="Kingston"), elig(), NOW)
    assert factor(r, "location")["points"] == 0 and factor(r, "location")["known"] is True


def test_remote_opportunities_ignore_location():
    remote = opp("internship", work_mode="remote", details={"required_skills": []})
    assert factor(score_match(prefs(), remote, elig(), NOW), "location")["points"] == 15
    onsite_only = prefs(remote_preference="onsite")
    assert factor(score_match(onsite_only, remote, elig(), NOW), "location")["points"] == 0


def scholarship(need_based):
    return opp("scholarship", details={"funding_type": "full", "need_based": need_based})


def test_scholarship_financial_fit():
    def points(needs, need_based):
        r = score_match(prefs(needs_financial_aid=needs), scholarship(need_based), elig(), NOW)
        return factor(r, "financial")["points"], r

    assert points(True, True)[0] == 35
    assert points(False, False)[0] == 35
    assert points(False, True)[0] == 0
    unknown_points, result = points(None, True)
    assert unknown_points == 0
    assert any("financial aid" in g for g in result["gaps"])
    assert factor(result, "funding")["points"] == 20  # full funding


def test_internship_skills_ratio_and_gap():
    job = opp("internship", details={"required_skills": ["python", "sql"]})
    r = score_match(prefs(skills={"python"}), job, elig(), NOW)
    assert factor(r, "skills")["points"] == 18
    assert "Missing skills: sql" in r["gaps"]

    none_required = opp("internship", details={"required_skills": []})
    assert factor(score_match(prefs(), none_required, elig(), NOW), "skills")["points"] == 35


def test_work_mode_fit():
    def points(pref, mode):
        job = opp("internship", work_mode=mode, details={"required_skills": []})
        r = score_match(prefs(remote_preference=pref), job, elig(), NOW)
        return factor(r, "work_mode")["points"], r

    assert points("any", "onsite")[0] == 15
    assert points("remote", "remote")[0] == 15
    assert points("remote", "hybrid")[0] == 8  # half credit, rounded
    assert points("onsite", "remote")[0] == 0
    unset_points, result = points(None, "onsite")
    assert unset_points == 0 and "Set your remote-work preference" in result["gaps"]
    assert points("remote", "unknown")[0] == 0  # provider did not say: no gap blamed on the user


def test_requirement_outcomes_become_gaps_and_steps():
    e = {"status": "potentially_eligible", "requirements": [
        {"outcome": "pass", "label": "country is NG", "reason": None},
        {"outcome": "unknown", "label": "age at most 35", "reason": "date of birth not provided"},
    ]}
    r = score_match(prefs(), opp(days=5), e, NOW)
    assert any("age at most 35" in g for g in r["gaps"])
    assert any("1 requirement" in s for s in r["next_steps"])
    assert any("deadline is in 5 day" in s for s in r["next_steps"])


def test_result_is_deterministic_and_json_serializable():
    first = score_match(prefs(), opp(), elig(), NOW)
    assert first == score_match(prefs(), opp(), elig(), NOW)
    json.dumps(first)