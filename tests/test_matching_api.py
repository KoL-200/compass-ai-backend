from tests.helpers import auth_headers
from tests.test_eligibility_api import fill_profile
from tests.test_opportunites import admission, create, internship, make_org, manager

B = "/api/v1"


def put_rules(client, mgr, opp_id, tree):
    r = client.put(f"{B}/admin/opportunities/{opp_id}/requirements", json={"requirements": tree}, headers=mgr)
    assert r.status_code == 200, r.text


def world(client, mgr):
    """A: eligible and verified. B: ineligible (needs a master's). C: internship with no rules."""
    org = make_org(client, mgr)
    src = client.post(
        f"{B}/admin/sources",
        json={"name": "Uni", "url": "https://uni.example.com/a", "type": "official_website"},
        headers=mgr,
    ).json()["id"]

    a = create(client, mgr, admission(org, source_id=src)).json()["id"]  # "BSc Computer Science"
    put_rules(client, mgr, a, {"type": "LEAF", "field": "country", "op": "in", "value": ["NG"]})
    assert client.post(f"{B}/admin/opportunities/{a}/verify", headers=mgr).status_code == 200

    b = create(client, mgr, admission(org, title="BA Fine Art", source_id=src)).json()["id"]
    put_rules(client, mgr, b, {"type": "LEAF", "field": "education_level", "op": "gte", "value": "master"})

    c = create(client, mgr, internship(org)).json()["id"]  # skills python + sql, no rules
    return a, b, c


def student(client, email="u@example.com"):
    h = auth_headers(client, email)
    fill_profile(client, h)  # NG, secondary, JAMB 215/400
    client.put(f"{B}/profiles/me", json={"field_of_study": "Computer Science"}, headers=h)
    client.put(f"{B}/profiles/me/skills", json={"skills": [{"name": "Python"}, {"name": "Git"}]}, headers=h)
    return h


def ids(response):
    return [i["opportunity"]["id"] for i in response.json()["items"]]


def test_matching_requires_auth(client):
    assert client.post(f"{B}/matches/refresh").status_code == 401
    assert client.get(f"{B}/matches").status_code == 401


def test_ranking_uses_eligibility_as_a_gate(client):
    mgr = manager(client)
    a, b, c = world(client, mgr)
    h = student(client)

    refreshed = client.post(f"{B}/matches/refresh", headers=h).json()
    assert refreshed["computed"] == 3
    assert refreshed["truncated"] is False
    assert refreshed["by_status"] == {"eligible": 1, "ineligible": 1, "needs_manual_verification": 1}

    listing = client.get(f"{B}/matches", headers=h)
    assert ids(listing) == [a, c]  # the ineligible one is hidden by default
    items = {i["opportunity"]["id"]: i for i in listing.json()["items"]}
    assert items[a]["score"] == 94 and items[a]["eligibility_status"] == "eligible"
    assert items[c]["score"] == 28 and items[c]["score_cap"] == 60
    assert "not a probability" in listing.json()["note"]
    for item in items.values():
        assert sum(f["points"] for f in item["factors"]) == item["raw_score"]
        assert item["algorithm_version"] == "1.0.0"
        assert item["next_steps"]

    assert any("Missing skills: sql" in g for g in items[c]["gaps"])

    everything = client.get(f"{B}/matches", params={"include_ineligible": True}, headers=h)
    assert ids(everything) == [a, c, b]
    ineligible = {i["opportunity"]["id"]: i for i in everything.json()["items"]}[b]
    assert ineligible["raw_score"] == 54  # it fits the user's likes...
    assert ineligible["score"] == 15      # ...but eligibility caps it
    assert any("Requirement not met" in g for g in ineligible["gaps"])

    only_ineligible = client.get(f"{B}/matches", params={"status": "ineligible"}, headers=h)
    assert ids(only_ineligible) == [b]


def test_refresh_is_idempotent_and_follows_profile_changes(client):
    mgr = manager(client)
    a, b, c = world(client, mgr)
    h = student(client)

    client.post(f"{B}/matches/refresh", headers=h)
    client.post(f"{B}/matches/refresh", headers=h)
    assert client.get(f"{B}/matches", headers=h).json()["total"] == 2

    client.put(f"{B}/profiles/me", json={"country": "gh"}, headers=h)  # rule says country must be NG
    client.post(f"{B}/matches/refresh", headers=h)
    assert ids(client.get(f"{B}/matches", headers=h)) == [c]


def test_missing_data_ranks_low_but_is_never_called_ineligible(client):
    mgr = manager(client)
    a, b, c = world(client, mgr)
    blank = auth_headers(client, "blank@example.com")  # empty profile

    client.post(f"{B}/matches/refresh", headers=blank)
    listing = client.get(f"{B}/matches", headers=blank).json()
    by_id = {i["opportunity"]["id"]: i for i in listing["items"]}
    assert set(by_id) == {a, b, c}  # nothing was hidden as ineligible
    assert by_id[a]["eligibility_status"] == "insufficient_information"
    assert by_id[a]["score"] <= 40
    assert any("Cannot check yet" in g for g in by_id[a]["gaps"])


def test_matches_are_private(client):
    mgr = manager(client)
    world(client, mgr)
    a_user = student(client, "a@example.com")
    b_user = auth_headers(client, "b@example.com")

    client.post(f"{B}/matches/refresh", headers=a_user)
    assert client.get(f"{B}/matches", headers=a_user).json()["total"] == 2
    assert client.get(f"{B}/matches", headers=b_user).json()["total"] == 0  # b never refreshed


def test_archived_opportunity_disappears_without_a_refresh(client):
    mgr = manager(client)
    a, b, c = world(client, mgr)
    h = student(client)
    client.post(f"{B}/matches/refresh", headers=h)

    client.patch(f"{B}/admin/opportunities/{a}", json={"status": "archived"}, headers=mgr)
    assert ids(client.get(f"{B}/matches", headers=h)) == [c]


def test_filters_and_pagination_limits(client):
    mgr = manager(client)
    a, b, c = world(client, mgr)
    h = student(client)
    client.post(f"{B}/matches/refresh", headers=h)

    assert ids(client.get(f"{B}/matches", params={"category": "internship"}, headers=h)) == [c]
    assert client.get(f"{B}/matches", params={"category": "job"}, headers=h).status_code == 422
    assert client.get(f"{B}/matches", params={"limit": 500}, headers=h).status_code == 422
    page = client.get(f"{B}/matches", params={"limit": 1}, headers=h).json()
    assert len(page["items"]) == 1 and page["total"] == 2