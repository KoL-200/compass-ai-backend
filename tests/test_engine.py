import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app.modules.eligibility.engine import evaluate
from app.modules.eligibility.rules import validate_tree

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)
FRESH = NOW - timedelta(days=5)


def L(field, op, value, **extra):
    return {"type": "LEAF", "field": field, "op": op, "value": value, **extra}


def AND(*children):
    return {"type": "AND", "children": list(children)}


def OR(*children):
    return {"type": "OR", "children": list(children)}


def run(snapshot, tree, quality="verified", verified_at=FRESH):
    if tree is not None:
        tree = validate_tree(tree)  # same normalization as production
    return evaluate(snapshot, tree, data_quality=quality, last_verified_at=verified_at, now=NOW)


def qual(type_="JAMB", score=215, scale=400, subjects=None):
    return {
        "type": type_,
        "subject_results": subjects if subjects is not None else {"mathematics": "A1", "english": "B2"},
        "overall_score": score,
        "score_scale": scale,
        "year": 2024,
    }


def student(**over):
    snap = {
        "country": "NG", "nationality": "NG", "education_level": "secondary",
        "field_of_study": None, "graduation_year": 2024, "needs_financial_aid": None,
        "date_of_birth": date(2005, 3, 1), "skills": [], "experiences": [],
        "qualifications": [qual()],
    }
    snap.update(over)
    return snap


SPEC_TREE = AND(
    L("education_level", "gte", "secondary"),
    L("qualification_score", "gte", 200, qualification_type="jamb", scale=400),
    L("subjects", "contains_all", ["mathematics", "english"]),
    L("country", "in", ["NG"]),
)


# ----- the example from the specification -----

def test_spec_example_is_eligible():
    r = run(student(), SPEC_TREE)
    assert r["status"] == "eligible"
    assert r["counts"] == {"satisfied": 4, "unsatisfied": 0, "unknown": 0, "review": 0}
    assert r["data_freshness"]["is_fresh"] is True


def test_unverified_or_stale_data_is_only_likely_eligible():
    unverified = run(student(), SPEC_TREE, quality="unverified", verified_at=None)
    assert unverified["status"] == "likely_eligible"
    stale = run(student(), SPEC_TREE, verified_at=NOW - timedelta(days=200))
    assert stale["status"] == "likely_eligible"
    assert stale["data_freshness"]["is_fresh"] is False


def test_score_below_threshold_is_ineligible():
    r = run(student(qualifications=[qual(score=150)]), SPEC_TREE)
    assert r["status"] == "ineligible"
    assert r["counts"]["unsatisfied"] == 1
    assert "qualification score" in r["explanation"]


def test_missing_grade_is_not_ineligible():
    r = run(student(qualifications=[qual(score=None, scale=None)]), SPEC_TREE)
    assert r["status"] == "potentially_eligible"
    assert r["counts"]["unknown"] == 1 and r["counts"]["unsatisfied"] == 0


def test_empty_profile_is_insufficient_information():
    empty = student(country=None, nationality=None, education_level=None,
                    date_of_birth=None, qualifications=[])
    assert run(empty, SPEC_TREE)["status"] == "insufficient_information"


# ----- AND / OR logic -----

def test_fail_beats_unknown_in_and():
    r = run(student(country="GH", qualifications=[]), SPEC_TREE)
    assert r["status"] == "ineligible"


def test_or_group_passes_and_hides_absorbed_failures():
    tree = OR(L("country", "in", ["GH"]), L("country", "in", ["NG"]))
    r = run(student(), tree)
    assert r["status"] == "eligible"
    assert len(r["requirements"]) == 1 and r["requirements"][0]["outcome"] == "pass"


def test_or_with_fail_and_unknown_is_not_a_failure():
    tree = OR(L("country", "eq", "GH"), L("subjects", "contains", "physics"))
    r = run(student(), tree)
    assert r["status"] == "potentially_eligible"


def test_set_requirements_never_fail():
    r = run(student(skills=["java"]), L("skills", "contains", "python"))
    assert r["status"] != "ineligible"
    assert "python" in r["requirements"][0]["reason"]


# ----- grades, scales, levels -----

@pytest.mark.parametrize(
    "grade,expected",
    [("c4", "eligible"), ("c6", "eligible"), ("a1", "eligible"),
     ("d7", "ineligible"), ("pass", "insufficient_information")],
)
def test_subject_grade_comparison(grade, expected):
    tree = L("subject_grade", "gte", "c6", subject="english", grade_scale="waec")
    snap = student(qualifications=[qual("WAEC", None, None, {"english": grade})])
    assert run(snap, tree)["status"] == expected


def test_subject_grade_missing_subject_is_unknown():
    tree = L("subject_grade", "gte", "c6", subject="english", grade_scale="waec")
    snap = student(qualifications=[qual("WAEC", None, None, {"physics": "A1"})])
    assert run(snap, tree)["status"] == "insufficient_information"


def test_score_scale_conversion_and_boundary():
    tree = L("qualification_score", "gte", 200, qualification_type="jamb", scale=400)
    assert run(student(qualifications=[qual(score=300, scale=600)]), tree)["status"] == "eligible"
    assert run(student(qualifications=[qual(score=299, scale=600)]), tree)["status"] == "ineligible"
    no_scale = run(student(qualifications=[qual(score=300, scale=None)]), tree)
    assert no_scale["status"] == "insufficient_information"


@pytest.mark.parametrize(
    "level,expected",
    [("secondary", "ineligible"), ("bachelor", "eligible"),
     ("master", "eligible"), ("other", "insufficient_information")],
)
def test_education_level_is_ordinal(level, expected):
    assert run(student(education_level=level), L("education_level", "gte", "bachelor"))["status"] == expected


# ----- numbers and dates -----

def test_age_boundary_on_birthday():
    tree = L("age", "gte", 18)
    assert run(student(date_of_birth=date(2008, 10, 20)), tree)["status"] == "eligible"
    assert run(student(date_of_birth=date(2008, 10, 21)), tree)["status"] == "ineligible"


def test_experience_months_merges_overlapping_jobs():
    jobs = [
        {"start_date": date(2025, 1, 1), "end_date": date(2025, 12, 31)},
        {"start_date": date(2025, 6, 1), "end_date": date(2026, 6, 30)},
    ]  # one continuous span of about 17 months, not 28
    assert run(student(experiences=jobs), L("experience_months", "gte", 12))["status"] == "eligible"
    assert run(student(experiences=jobs), L("experience_months", "gte", 18))["status"] == "ineligible"
    assert run(student(), L("experience_months", "gte", 6))["status"] == "insufficient_information"


# ----- review flags and edge cases -----

def test_needs_review_flag_and_fail_precedence():
    tree = AND(L("country", "in", ["NG"]), L("field_of_study", "eq", "engineering", needs_review=True))
    assert run(student(), tree)["status"] == "needs_manual_verification"
    assert run(student(country="GH"), tree)["status"] == "ineligible"


def test_no_requirements_needs_manual_verification():
    r = run(student(), None)
    assert r["status"] == "needs_manual_verification" and r["requirements"] == []


def test_result_is_deterministic_and_json_serializable():
    tree = AND(L("date_of_birth", "before", "2010-01-01"), SPEC_TREE)
    first, second = run(student(), tree), run(student(), tree)
    assert first == second
    json.dumps(first)  # dates and sets must already be converted