import pytest

from app.core.levels import level_for_months
from app.modules.careers.paths import build_path
from app.modules.careers.skills_gap import analyze_gap, rank_skills_to_learn


@pytest.mark.parametrize(
    "months,level",
    [(None, None), (0, "entry"), (5, "entry"), (6, "junior"), (23, "junior"), (24, "mid"),
     (59, "mid"), (60, "senior"), (84, "lead"), (120, "executive"), (500, "executive")],
)
def test_level_for_months(months, level):
    assert level_for_months(months) == level


def test_gap_for_a_person_with_skills():
    g = analyze_gap({"python"}, ["Python", "SQL"], ["docker"], 24, 12)
    assert g["required"] == {"total": 2, "matched": ["python"], "missing": ["sql"], "unknown": []}
    assert g["preferred"]["missing"] == ["docker"]
    assert g["coverage_percent"] == 50
    assert g["experience"] == {"required_months": 24, "your_months": 12, "gap_months": 12}


def test_no_recorded_skills_means_unknown_not_missing():
    g = analyze_gap(set(), ["python", "sql"], [], 24, None)
    assert g["skills_recorded"] is False
    assert g["required"]["unknown"] == ["python", "sql"] and g["required"]["missing"] == []
    assert g["coverage_percent"] is None
    assert g["experience"]["gap_months"] is None


def test_gap_edge_cases():
    assert analyze_gap({"python"}, [], [], 0, None)["coverage_percent"] is None  # nothing required
    assert analyze_gap({"python"}, ["python"], [], 0, None)["experience"]["gap_months"] == 0
    assert analyze_gap({"python"}, ["python"], [], 12, 30)["experience"]["gap_months"] == 0


def test_rank_skills_to_learn_ordering_and_examples():
    postings = [
        {"id": "1", "title": "A", "required_skills": ["python", "sql"], "preferred_skills": []},
        {"id": "2", "title": "B", "required_skills": ["python", "docker"], "preferred_skills": ["terraform"]},
        {"id": "3", "title": "C", "required_skills": ["sql", "docker", "aws"], "preferred_skills": []},
    ]
    ranked = rank_skills_to_learn({"python"}, postings, 10)
    assert [(r["skill"], r["required_in"], r["would_complete"], r["preferred_in"]) for r in ranked] == [
        ("docker", 2, 1, 0),
        ("sql", 2, 1, 0),
        ("aws", 1, 0, 0),
        ("terraform", 0, 0, 1),
    ]
    assert [e["title"] for e in ranked[0]["examples"]] == ["B", "C"]
    assert len(rank_skills_to_learn({"python"}, postings, 2)) == 2


def test_examples_are_capped_at_three():
    postings = [{"id": str(n), "title": f"P{n}", "required_skills": ["sql"], "preferred_skills": []} for n in range(5)]
    top = rank_skills_to_learn(set(), postings, 5)[0]
    assert top["required_in"] == 5 and len(top["examples"]) == 3


def posting(level, months, skills, low=None, high=None, currency="NGN", period="month"):
    return {"job_level": level, "min_experience_months": months, "required_skills": skills,
            "salary_min": low, "salary_max": high, "salary_currency": currency, "salary_period": period}


def test_path_levels_are_ordered_and_summarised():
    rows = [
        posting("senior", 60, ["python", "docker"], 900, 1400),
        posting("junior", 6, ["python", "sql"], 300, 450),
        posting("senior", 72, ["python", "aws"], 1000, 1500),
        posting("senior", 66, ["python"], 50, 60, currency="USD", period="year"),
    ]
    path = build_path("software engineering", rows, {"python"}, 30)
    assert [lv["level"] for lv in path["levels"]] == ["junior", "senior"]  # only levels that have postings
    senior = path["levels"][1]
    assert senior["postings"] == 3 and senior["typical_min_experience_months"] == 66
    assert senior["common_skills"][0] == {"skill": "python", "postings": 3, "required_by_percent": 100}
    assert senior["salary"] == [
        {"currency": "NGN", "period": "month", "lowest": 900.0, "highest": 1500.0, "postings": 2},
        {"currency": "USD", "period": "year", "lowest": 50.0, "highest": 60.0, "postings": 1},
    ]


def test_path_next_step():
    rows = [posting("junior", 6, ["python", "sql"]), posting("mid", 24, ["python", "docker"]),
            posting("senior", 60, ["python", "docker", "aws"])]
    path = build_path("x", rows, {"python"}, 30)  # 30 months: estimated mid
    assert path["estimated_current_level"] == "mid"
    assert path["next_step"] == {
        "level": "senior", "skills_recorded": True,
        "already_have": ["python"], "to_learn": ["aws", "docker"], "unknown": [],
    }

    nothing = build_path("x", rows, set(), None)  # no experience, no skills recorded
    assert nothing["estimated_current_level"] is None
    assert nothing["next_step"]["level"] == "junior"
    assert nothing["next_step"]["to_learn"] == [] and nothing["next_step"]["unknown"] == ["python", "sql"]

    top = build_path("x", rows, {"python"}, 200)  # beyond every level in the data
    assert top["estimated_current_level"] == "executive" and top["next_step"] is None