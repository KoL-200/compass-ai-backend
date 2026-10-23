from datetime import datetime, timedelta, timezone

import pytest

from app.modules.matching.scoring import WEIGHTS, score_match

NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


def prefs(**over):
    base = {"interests": [], "skills": {"python"}, "country": "NG", "education_level": "bachelor",
            "needs_financial_aid": None, "remote_preference": None, "preferred_locations": [],
            "experience_months": None}
    base.update(over)
    return base


def job(level="mid", **details):
    d = {"job_level": level, "role_family": "software engineering", "required_skills": ["python"],
         "preferred_skills": []}
    d.update(details)
    return {"category": "job", "title": "Backend Developer", "country": "NG", "location": "Lagos",
            "work_mode": "hybrid", "deadline_at": NOW + timedelta(days=30), "details": d}


def elig():
    return {"status": "eligible", "requirements": []}


def factor(result, name):
    return next(f for f in result["factors"] if f["name"] == name)


def test_job_weights():
    assert sum(WEIGHTS["job"].values()) == 100
    names = {f["name"] for f in score_match(prefs(), job(), elig(), NOW)["factors"]}
    assert names == set(WEIGHTS["job"])


@pytest.mark.parametrize(
    "months,level,points",
    [(None, "entry", 15), (0, "entry", 15), (3, "entry", 15), (None, "mid", 0), (12, "mid", 4),
     (30, "mid", 15), (100, "mid", 12), (6, "junior", 15), (200, "executive", 15)],
)
def test_seniority_fit(months, level, points):
    r = score_match(prefs(experience_months=months), job(level), elig(), NOW)
    assert factor(r, "seniority")["points"] == points


def test_unknown_experience_for_a_senior_role_produces_a_hint_not_a_penalty():
    r = score_match(prefs(experience_months=None), job("mid"), elig(), NOW)
    assert factor(r, "seniority")["known"] is False
    assert "Add your work experience" in r["gaps"]


def test_role_family_counts_towards_interest():
    r = score_match(prefs(interests=["data analysis"]), job(role_family="data analysis"), elig(), NOW)
    assert factor(r, "interest")["points"] == 20