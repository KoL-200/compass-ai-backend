from tests.helpers import auth_headers
from tests.test_opportunites import FUTURE, admission, create, internship, make_org, manager

B = "/api/v1"


def job(org_id, **over):
    detail = {
        "job_level": "mid", "employment_type": "full_time", "role_family": " Software Engineering ",
        "salary_min": 400000, "salary_max": 600000, "salary_currency": "ngn", "salary_period": "month",
        "required_skills": ["Python", "SQL"], "preferred_skills": ["Docker"], "min_experience_months": 24,
    }
    detail.update(over.pop("job", {}))
    body = {"category": "job", "title": "Backend Developer", "organization_id": org_id, "status": "open",
            "deadline_at": FUTURE, "country": "NG", "work_mode": "hybrid", "job": detail}
    body.update(over)
    return body


def make_job(client, mgr, org, **over):
    r = create(client, mgr, job(org, **over))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def candidate(client, email="u@example.com", skills=("python", "git"), experience=None, field=None):
    h = auth_headers(client, email)
    body = {"country": "ng", "nationality": "ng", "education_level": "bachelor"}
    if field:
        body["field_of_study"] = field
    client.put(f"{B}/profiles/me", json=body, headers=h)
    if skills:
        client.put(f"{B}/profiles/me/skills", json={"skills": [{"name": s} for s in skills]}, headers=h)
    if experience:
        start, end = experience
        r = client.post(
            f"{B}/profiles/me/experiences",
            json={"title": "Developer", "employer": "Acme", "start_date": start, "end_date": end},
            headers=h,
        )
        assert r.status_code == 201, r.text
    return h


# 12, 36 and 17 whole months of experience, independent of today's date
ONE_YEAR = ("2020-01-01", "2021-01-01")
THREE_YEARS = ("2019-01-01", "2022-01-01")
SEVENTEEN_MONTHS = ("2020-01-01", "2021-07-01")


# ---------- the job category ----------

def test_create_a_job_and_normalise_it(client):
    mgr = manager(client)
    org = make_org(client, mgr, "Demo Tech", "NG", "employer")
    r = create(client, mgr, job(org, job={"required_skills": ["Python", "python", " SQL "]}))
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["category"] == "job"
    assert data["admission"] is None and data["scholarship"] is None and data["internship"] is None
    assert data["job"]["role_family"] == "software engineering"
    assert data["job"]["required_skills"] == ["python", "sql"]
    assert data["job"]["salary_currency"] == "NGN"


def test_job_validation(client):
    mgr = manager(client)
    org = make_org(client, mgr)
    invalid_details = [
        {"salary_currency": None},   # a salary needs a currency
        {"salary_period": None},     # ...and a period
        {"salary_min": 700000},      # minimum above the maximum
        {"job_level": "wizard"},
        {"employment_type": "forever"},
        {"min_experience_months": -1},
        {"surprise": 1},
    ]
    for override in invalid_details:
        assert create(client, mgr, job(org, job=override)).status_code == 422, override

    wrong_block = job(org)
    wrong_block["internship"] = {"duration_weeks": 4}
    assert create(client, mgr, wrong_block).status_code == 422
    missing_block = job(org)
    del missing_block["job"]
    assert create(client, mgr, missing_block).status_code == 422


def test_patch_merges_job_details_and_revalidates(client):
    mgr = manager(client)
    org = make_org(client, mgr)
    opp_id = make_job(client, mgr, org)
    url = f"{B}/admin/opportunities/{opp_id}"
    ok = client.patch(url, json={"details": {"job_level": "senior", "min_experience_months": 60}}, headers=mgr)
    assert ok.status_code == 200, ok.text
    assert ok.json()["job"]["job_level"] == "senior"
    assert ok.json()["job"]["required_skills"] == ["python", "sql"]  # untouched
    assert client.patch(url, json={"details": {"salary_max": 100}}, headers=mgr).status_code == 422


def test_jobs_appear_in_search_by_category(client):
    mgr = manager(client)
    user = auth_headers(client, "u@example.com")
    org = make_org(client, mgr)
    make_job(client, mgr, org)
    create(client, mgr, admission(org))
    jobs = client.get(f"{B}/opportunities", params={"category": "job"}, headers=user).json()
    assert [i["title"] for i in jobs["items"]] == ["Backend Developer"]
    assert client.get(f"{B}/opportunities", params={"q": "backend"}, headers=user).json()["total"] == 1


# ---------- skills gap for one posting ----------

def test_skills_gap_for_a_job(client):
    mgr = manager(client)
    org = make_org(client, mgr)
    job_id = make_job(client, mgr, org)  # python, sql | prefers docker | 24 months
    h = candidate(client, skills=("Python", "Git"), experience=ONE_YEAR)

    r = client.get(f"{B}/opportunities/{job_id}/skills-gap", headers=h)
    assert r.status_code == 200, r.text
    gap = r.json()
    assert gap["opportunity"]["title"] == "Backend Developer"
    assert gap["skills_recorded"] is True
    assert gap["required"] == {"total": 2, "matched": ["python"], "missing": ["sql"], "unknown": []}
    assert gap["preferred"] == {"total": 1, "matched": [], "missing": ["docker"], "unknown": []}
    assert gap["coverage_percent"] == 50
    assert gap["experience"] == {"required_months": 24, "your_months": 12, "gap_months": 12}


def test_skills_gap_for_a_person_with_nothing_recorded_is_unknown_not_missing(client):
    mgr = manager(client)
    org = make_org(client, mgr)
    job_id = make_job(client, mgr, org)
    blank = auth_headers(client, "blank@example.com")
    gap = client.get(f"{B}/opportunities/{job_id}/skills-gap", headers=blank).json()
    assert gap["skills_recorded"] is False
    assert gap["required"]["unknown"] == ["python", "sql"] and gap["required"]["missing"] == []
    assert gap["coverage_percent"] is None
    assert gap["experience"]["your_months"] is None and gap["experience"]["gap_months"] is None


def test_skills_gap_scope_and_visibility(client):
    mgr = manager(client)
    org = make_org(client, mgr)
    h = candidate(client)

    internship_id = create(client, mgr, internship(org)).json()["id"]
    r = client.get(f"{B}/opportunities/{internship_id}/skills-gap", headers=h)
    assert r.status_code == 200 and r.json()["preferred"]["total"] == 0

    admission_id = create(client, mgr, admission(org)).json()["id"]
    refused = client.get(f"{B}/opportunities/{admission_id}/skills-gap", headers=h)
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "not_applicable"

    draft_id = make_job(client, mgr, org, status="draft", title="Draft")
    assert client.get(f"{B}/opportunities/{draft_id}/skills-gap", headers=h).status_code == 404
    assert client.get(f"{B}/opportunities/{draft_id}/skills-gap").status_code == 401


# ---------- skills worth learning ----------

def test_skills_to_learn_ignores_postings_you_are_ruled_out_of(client):
    mgr = manager(client)
    org = make_org(client, mgr)
    make_job(client, mgr, org, title="A", job={"required_skills": ["python", "sql"], "preferred_skills": []})
    make_job(client, mgr, org, title="B", job={"required_skills": ["python", "docker"], "preferred_skills": ["terraform"]})
    c = make_job(client, mgr, org, title="C", job={"required_skills": ["sql", "kubernetes"], "preferred_skills": []})
    client.put(
        f"{B}/admin/opportunities/{c}/requirements",
        json={"requirements": {"type": "LEAF", "field": "country", "op": "eq", "value": "GH"}},
        headers=mgr,
    )
    h = candidate(client, skills=("python",))  # lives in NG, so C is a real failure

    r = client.get(f"{B}/careers/skills-to-learn", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["postings_considered"] == 2 and data["excluded_ineligible"] == 1
    assert data["skills_recorded"] is True
    assert [(s["skill"], s["required_in"], s["would_complete"], s["preferred_in"]) for s in data["skills"]] == [
        ("docker", 1, 1, 0), ("sql", 1, 1, 0), ("terraform", 0, 0, 1),
    ]
    assert [e["title"] for e in data["skills"][1]["examples"]] == ["A"]
    assert "not a promise" in data["note"]

    assert len(client.get(f"{B}/careers/skills-to-learn", params={"limit": 1}, headers=h).json()["skills"]) == 1
    assert client.get(f"{B}/careers/skills-to-learn", params={"category": "admission"}, headers=h).status_code == 422
    assert client.get(f"{B}/careers/skills-to-learn", params={"limit": 0}, headers=h).status_code == 422
    assert client.get(f"{B}/careers/skills-to-learn").status_code == 401


# ---------- career paths ----------

def test_career_path_from_open_postings(client):
    mgr = manager(client)
    org = make_org(client, mgr)

    def post(title, level, months, skills):
        make_job(client, mgr, org, title=title, job={"job_level": level, "min_experience_months": months,
                                                     "required_skills": skills, "preferred_skills": []})

    post("Junior", "junior", 6, ["python", "sql", "git"])
    post("Mid", "mid", 24, ["python", "sql", "docker"])
    post("Senior", "senior", 60, ["python", "system design", "docker"])
    make_job(client, mgr, org, title="Designer",
             job={"role_family": "design", "job_level": "mid", "required_skills": ["figma"]})
    h = candidate(client, skills=("python",), experience=THREE_YEARS)

    r = client.get(f"{B}/careers/paths", params={"role_family": " Software Engineering "}, headers=h)
    assert r.status_code == 200, r.text
    path = r.json()
    assert path["role_family"] == "software engineering" and path["postings_analyzed"] == 3
    assert path["estimated_current_level"] == "mid"  # 36 months
    assert [lv["level"] for lv in path["levels"]] == ["junior", "mid", "senior"]
    senior = path["levels"][2]
    assert senior["typical_min_experience_months"] == 60
    assert [(c["skill"], c["required_by_percent"]) for c in senior["common_skills"]] == [
        ("docker", 100), ("python", 100), ("system design", 100),
    ]
    assert senior["salary"] == [
        {"currency": "NGN", "period": "month", "lowest": 400000.0, "highest": 600000.0, "postings": 1}
    ]
    assert path["next_step"] == {
        "level": "senior", "skills_recorded": True,
        "already_have": ["python"], "to_learn": ["docker", "system design"], "unknown": [],
    }
    assert "not a guarantee" in path["note"]


def test_career_path_for_someone_with_nothing_recorded(client):
    mgr = manager(client)
    org = make_org(client, mgr)
    make_job(client, mgr, org, title="Junior", job={"job_level": "junior", "required_skills": ["python", "sql"]})
    blank = auth_headers(client, "blank@example.com")
    path = client.get(f"{B}/careers/paths", params={"role_family": "software engineering"}, headers=blank).json()
    assert path["estimated_current_level"] is None
    step = path["next_step"]
    assert step["level"] == "junior" and step["skills_recorded"] is False
    assert step["to_learn"] == [] and set(step["unknown"]) == {"python", "sql"}


def test_unknown_role_family_and_listing(client):
    mgr = manager(client)
    org = make_org(client, mgr)
    make_job(client, mgr, org, title="A")
    make_job(client, mgr, org, title="B")
    make_job(client, mgr, org, title="D", job={"role_family": "design"})
    h = candidate(client)

    assert client.get(f"{B}/careers/role-families", headers=h).json() == [
        {"role_family": "design", "postings": 1},
        {"role_family": "software engineering", "postings": 2},
    ]
    missing = client.get(f"{B}/careers/paths", params={"role_family": "astronaut"}, headers=h)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "role_family_not_found"
    assert missing.json()["error"]["details"] == ["design", "software engineering"]
    assert client.get(f"{B}/careers/paths", headers=h).status_code == 422
    assert client.get(f"{B}/careers/paths", params={"role_family": "design"}).status_code == 401


# ---------- matching ----------

def test_jobs_are_ranked_with_seniority_fit(client):
    mgr = manager(client)
    org = make_org(client, mgr)
    skills = ["python", "sql", "git"]
    make_job(client, mgr, org, title="Junior Backend Developer",
             job={"job_level": "junior", "min_experience_months": 6, "required_skills": skills, "preferred_skills": []})
    make_job(client, mgr, org, title="Senior Backend Engineer",
             job={"job_level": "senior", "min_experience_months": 60, "required_skills": skills, "preferred_skills": []})
    h = candidate(client, skills=tuple(skills), experience=SEVENTEEN_MONTHS, field="software engineering")

    assert client.post(f"{B}/matches/refresh", headers=h).status_code == 200
    items = client.get(f"{B}/matches", params={"category": "job"}, headers=h).json()["items"]
    by_title = {i["opportunity"]["title"]: i for i in items}
    junior, senior = by_title["Junior Backend Developer"], by_title["Senior Backend Engineer"]

    def points(item, name):
        return next(f for f in item["factors"] if f["name"] == name)["points"]

    assert {f["name"] for f in junior["factors"]} == {"skills", "interest", "seniority", "location", "work_mode", "timing"}
    assert points(junior, "seniority") == 15 and points(senior, "seniority") == 2
    assert points(junior, "skills") == 30 and points(junior, "interest") == 20  # role family matches the field
    assert junior["raw_score"] > senior["raw_score"]  # eligibility caps can tie final scores; raw scores differ


# ---------- professional profile ----------

def test_professional_summary_and_links(client):
    h = auth_headers(client, "u@example.com")
    url = f"{B}/profiles/me"
    ok = client.put(
        url,
        json={"headline": " Backend developer ", "summary": "Builds APIs.", "links": ["https://github.com/example"]},
        headers=h,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["headline"] == "Backend developer" and ok.json()["links"] == ["https://github.com/example"]
    assert client.put(url, json={"links": ["ftp://bad.example"]}, headers=h).status_code == 422
    assert client.put(url, json={"links": ["https://a.example"] * 11}, headers=h).status_code == 422
    assert client.put(url, json={"links": None}, headers=h).json()["links"] == []