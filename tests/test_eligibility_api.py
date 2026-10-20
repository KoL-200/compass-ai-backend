from tests.helpers import auth_headers
from tests.test_opportunites import admission, create, make_org, manager

B = "/api/v1"

TREE = {
    "type": "AND",
    "children": [
        {"type": "LEAF", "field": "country", "op": "in", "value": ["ng"]},
        {"type": "LEAF", "field": "education_level", "op": "gte", "value": "secondary"},
        {"type": "LEAF", "field": "qualification_score", "qualification_type": "jamb",
         "scale": 400, "op": "gte", "value": 200},
        {"type": "LEAF", "field": "subjects", "op": "contains_all",
         "value": ["mathematics", "english"]},
    ],
}


def open_opportunity(client, mgr, *, tree=TREE, verify=True, status="open"):
    org = make_org(client, mgr)
    src = client.post(
        f"{B}/admin/sources",
        json={"name": "Uni", "url": "https://uni.example.com/a", "type": "official_website"},
        headers=mgr,
    ).json()
    opp = create(client, mgr, admission(org, source_id=src["id"], status=status)).json()
    if tree is not None:
        r = client.put(
            f"{B}/admin/opportunities/{opp['id']}/requirements",
            json={"requirements": tree}, headers=mgr,
        )
        assert r.status_code == 200, r.text
    if verify:  # verify last: editing rules drops a verified opportunity back to needs_review
        assert client.post(f"{B}/admin/opportunities/{opp['id']}/verify", headers=mgr).status_code == 200
    return opp["id"]


def fill_profile(client, h, score=215):
    client.put(f"{B}/profiles/me", json={"country": "ng", "education_level": "secondary"}, headers=h)
    q = client.post(
        f"{B}/profiles/me/qualifications",
        json={"type": "JAMB", "overall_score": score, "score_scale": 400,
              "subject_results": {"mathematics": "A1", "english": "B2"}},
        headers=h,
    ).json()
    return q["id"]


def test_requirements_endpoint_validates_versions_and_clears(client):
    mgr = manager(client)
    org = make_org(client, mgr)
    opp = create(client, mgr, admission(org)).json()
    url = f"{B}/admin/opportunities/{opp['id']}/requirements"

    bad = client.put(url, json={"requirements": {"type": "LEAF", "field": "nope", "op": "eq", "value": 1}}, headers=mgr)
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_requirements"
    assert bad.json()["error"]["details"][0]["path"] == "$.field"

    ok = client.put(url, json={"requirements": TREE}, headers=mgr).json()
    assert ok["requirements_version"] == 1
    assert ok["requirements"]["children"][0]["value"] == ["NG"]  # normalized
    assert client.put(url, json={"requirements": TREE}, headers=mgr).json()["requirements_version"] == 2

    cleared = client.put(url, json={"requirements": None}, headers=mgr).json()
    assert cleared["requirements"] is None and cleared["requirements_version"] == 3

    user = auth_headers(client, "u@example.com")
    assert client.put(url, json={"requirements": TREE}, headers=user).status_code == 403


def test_changing_rules_of_verified_opportunity_needs_review(client):
    mgr = manager(client)
    opp_id = open_opportunity(client, mgr)
    before = client.get(f"{B}/admin/opportunities/{opp_id}", headers=mgr).json()
    assert before["data_quality"] == "verified"
    client.put(f"{B}/admin/opportunities/{opp_id}/requirements", json={"requirements": TREE}, headers=mgr)
    after = client.get(f"{B}/admin/opportunities/{opp_id}", headers=mgr).json()
    assert after["data_quality"] == "needs_review"


def test_full_evaluation_flow_updates_one_stored_result(client):
    mgr = manager(client)
    user = auth_headers(client, "u@example.com")
    opp_id = open_opportunity(client, mgr)
    qid = fill_profile(client, user)

    r = client.post(f"{B}/opportunities/{opp_id}/eligibility", headers=user)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "eligible"
    assert body["counts"]["satisfied"] == 4
    assert body["ruleset_version"] == 1
    assert body["data_freshness"]["is_fresh"] is True

    # remove the score: must become "potentially eligible", never "ineligible"
    client.patch(f"{B}/profiles/me/qualifications/{qid}", json={"overall_score": None}, headers=user)
    r2 = client.post(f"{B}/opportunities/{opp_id}/eligibility", headers=user).json()
    assert r2["status"] == "potentially_eligible"

    # a score below the minimum is a real failure
    client.patch(f"{B}/profiles/me/qualifications/{qid}", json={"overall_score": 150}, headers=user)
    r3 = client.post(f"{B}/opportunities/{opp_id}/eligibility", headers=user).json()
    assert r3["status"] == "ineligible"

    listing = client.get(f"{B}/eligibility", headers=user).json()
    assert listing["total"] == 1  # updated in place, not duplicated
    assert client.get(f"{B}/eligibility", params={"status": "ineligible"}, headers=user).json()["total"] == 1
    assert client.get(f"{B}/eligibility", params={"status": "eligible"}, headers=user).json()["total"] == 0
    assert client.get(f"{B}/eligibility", params={"status": "bogus"}, headers=user).status_code == 422


def test_unverified_opportunity_gives_likely_eligible(client):
    mgr = manager(client)
    user = auth_headers(client, "u@example.com")
    opp_id = open_opportunity(client, mgr, verify=False)
    fill_profile(client, user)
    r = client.post(f"{B}/opportunities/{opp_id}/eligibility", headers=user).json()
    assert r["status"] == "likely_eligible"


def test_opportunity_without_requirements(client):
    mgr = manager(client)
    user = auth_headers(client, "u@example.com")
    opp_id = open_opportunity(client, mgr, tree=None, verify=False)
    r = client.post(f"{B}/opportunities/{opp_id}/eligibility", headers=user).json()
    assert r["status"] == "needs_manual_verification"


def test_hidden_or_unknown_opportunities_are_404(client):
    mgr = manager(client)
    user = auth_headers(client, "u@example.com")
    draft_id = open_opportunity(client, mgr, verify=False, status="draft")
    assert client.post(f"{B}/opportunities/{draft_id}/eligibility", headers=user).status_code == 404
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.post(f"{B}/opportunities/{missing}/eligibility", headers=user).status_code == 404
    assert client.post(f"{B}/opportunities/{draft_id}/eligibility").status_code == 401


def test_results_are_private_per_user(client):
    mgr = manager(client)
    a = auth_headers(client, "a@example.com")
    b = auth_headers(client, "b@example.com")
    opp_id = open_opportunity(client, mgr)
    fill_profile(client, a)

    assert client.post(f"{B}/opportunities/{opp_id}/eligibility", headers=a).json()["status"] == "eligible"
    assert client.get(f"{B}/eligibility", headers=b).json()["total"] == 0

    empty = client.post(f"{B}/opportunities/{opp_id}/eligibility", headers=b).json()
    assert empty["status"] == "insufficient_information"
    assert client.get(f"{B}/eligibility", params={"status": "eligible"}, headers=a).json()["total"] == 1