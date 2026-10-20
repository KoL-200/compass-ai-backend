from tests.helpers import auth_headers

P = "/api/v1/profiles/me"


def test_profile_requires_auth(client):
    assert client.get(P).status_code == 401


def test_new_profile_is_empty_and_lists_missing_fields(client):
    h = auth_headers(client)
    body = client.get(P, headers=h).json()
    assert body["qualifications"] == [] and body["skills"] == []
    assert set(body["missing_fields"]) == {
        "country", "nationality", "education_level", "field_of_study"
    }


def test_partial_update_normalizes_country_and_keeps_earlier_values(client):
    h = auth_headers(client)
    r = client.put(P, json={"country": "ng", "education_level": "secondary"}, headers=h)
    assert r.status_code == 200
    assert r.json()["country"] == "NG"
    assert "country" not in r.json()["missing_fields"]
    r2 = client.put(P, json={"nationality": "ng"}, headers=h)
    assert r2.json()["country"] == "NG" and r2.json()["nationality"] == "NG"


def test_profile_validation(client):
    h = auth_headers(client)
    assert client.put(P, json={"country": "NGA"}, headers=h).status_code == 422
    assert client.put(P, json={"education_level": "wizard"}, headers=h).status_code == 422
    assert client.put(P, json={"graduation_year": 1800}, headers=h).status_code == 422


def test_add_qualification_normalizes_subjects_and_ignores_verified(client):
    h = auth_headers(client)
    r = client.post(
        f"{P}/qualifications",
        json={
            "type": "WAEC",
            "subject_results": {" Mathematics ": "A1", "English": "B2"},
            "year": 2024,
            "verified": True,  # must be ignored
        },
        headers=h,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["subject_results"] == {"mathematics": "A1", "english": "B2"}
    assert body["verified"] is False


def test_qualification_score_cannot_exceed_scale(client):
    h = auth_headers(client)
    r = client.post(
        f"{P}/qualifications",
        json={"type": "JAMB", "overall_score": 450, "score_scale": 400},
        headers=h,
    )
    assert r.status_code == 422


def test_update_and_delete_own_qualification(client):
    h = auth_headers(client)
    q = client.post(f"{P}/qualifications", json={"type": "WAEC"}, headers=h).json()
    r = client.patch(f"{P}/qualifications/{q['id']}", json={"year": 2023}, headers=h)
    assert r.status_code == 200 and r.json()["year"] == 2023
    assert client.delete(f"{P}/qualifications/{q['id']}", headers=h).status_code == 204
    assert client.get(f"{P}/qualifications", headers=h).json() == []


def test_other_users_cannot_touch_my_qualification(client):
    a = auth_headers(client, "a@example.com")
    b = auth_headers(client, "b@example.com")
    qid = client.post(f"{P}/qualifications", json={"type": "WAEC"}, headers=a).json()["id"]

    assert client.patch(f"{P}/qualifications/{qid}", json={"year": 2020}, headers=b).status_code == 404
    assert client.delete(f"{P}/qualifications/{qid}", headers=b).status_code == 404
    assert client.get(f"{P}/qualifications", headers=b).json() == []
    assert len(client.get(f"{P}/qualifications", headers=a).json()) == 1


def test_skills_are_normalized_deduplicated_and_replaced(client):
    h = auth_headers(client)
    r = client.put(
        f"{P}/skills",
        json={"skills": [{"name": " Python ", "level": "advanced"}, {"name": "python"}, {"name": "SQL"}]},
        headers=h,
    )
    assert r.status_code == 200
    assert sorted(s["name"] for s in r.json()) == ["python", "sql"]

    r2 = client.put(f"{P}/skills", json={"skills": [{"name": "sql", "level": "beginner"}]}, headers=h)
    assert [s["name"] for s in r2.json()] == ["sql"]
    assert client.get(P, headers=h).json()["skills"] == [{"name": "sql", "level": "beginner"}]


def test_work_experience_add_list_delete_and_ownership(client):
    a = auth_headers(client, "a@example.com")
    b = auth_headers(client, "b@example.com")
    bad = client.post(
        f"{P}/experiences",
        json={"title": "Intern", "employer": "Acme", "start_date": "2024-06-01", "end_date": "2024-01-01"},
        headers=a,
    )
    assert bad.status_code == 422

    ok = client.post(
        f"{P}/experiences",
        json={"title": "Intern", "employer": "Acme", "start_date": "2024-01-01", "end_date": "2024-06-01"},
        headers=a,
    )
    assert ok.status_code == 201
    eid = ok.json()["id"]
    assert len(client.get(f"{P}/experiences", headers=a).json()) == 1
    assert client.delete(f"{P}/experiences/{eid}", headers=b).status_code == 404
    assert client.delete(f"{P}/experiences/{eid}", headers=a).status_code == 204