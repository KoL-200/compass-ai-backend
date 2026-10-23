import uuid

from tests.helpers import auth_headers, grant_role

B = "/api/v1"
FUTURE = "2030-01-01T00:00:00Z"
PAST = "2020-01-01T00:00:00Z"


def manager(client, email="m@example.com"):
    headers = auth_headers(client, email)
    grant_role(email, "data_manager")  # roles are read from the DB, no re-login needed
    return headers


def make_org(client, h, name="Uni One", country="NG", org_type="university"):
    r = client.post(
        f"{B}/admin/organizations",
        json={"name": name, "type": org_type, "country": country},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def admission(org_id, **over):
    body = {
        "category": "admission",
        "title": "BSc Computer Science",
        "organization_id": org_id,
        "country": "NG",
        "status": "open",
        "deadline_at": FUTURE,
        "admission": {"degree_type": "bachelor"},
    }
    body.update(over)
    return body


def scholarship(org_id, **over):
    body = {
        "category": "scholarship",
        "title": "Global Award",
        "organization_id": org_id,
        "status": "open",
        "deadline_at": FUTURE,
        "scholarship": {"funding_type": "full"},
    }
    body.update(over)
    return body


def internship(org_id, **over):
    body = {
        "category": "internship",
        "title": "Data Intern",
        "organization_id": org_id,
        "status": "open",
        "deadline_at": FUTURE,
        "internship": {"duration_weeks": 12, "required_skills": ["Python ", " SQL"]},
    }
    body.update(over)
    return body


def create(client, h, body):
    return client.post(f"{B}/admin/opportunities", json=body, headers=h)


def test_admin_endpoints_require_manager_role(client):
    user = auth_headers(client, "u@example.com")
    org = {"name": "X", "type": "university", "country": "NG"}
    assert client.post(f"{B}/admin/organizations", json=org).status_code == 401
    assert client.post(f"{B}/admin/organizations", json=org, headers=user).status_code == 403
    assert client.get(f"{B}/admin/opportunities", headers=user).status_code == 403
    assert client.get(f"{B}/opportunities").status_code == 401


def test_organization_create_and_duplicate(client):
    h = manager(client)
    make_org(client, h, "Uni One", "NG")
    dup = client.post(
        f"{B}/admin/organizations",
        json={"name": "Uni One", "type": "university", "country": "ng"},
        headers=h,
    )
    assert dup.status_code == 409
    # same name in another country is fine
    make_org(client, h, "Uni One", "GB")


def test_create_admission_opportunity(client):
    h = manager(client)
    org = make_org(client, h)
    r = create(
        client,
        h,
        admission(org, admission={"degree_type": "bachelor", "tuition_amount": 1500, "tuition_currency": "usd"}),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["category"] == "admission"
    assert body["admission"]["degree_type"] == "bachelor"
    assert body["admission"]["tuition_currency"] == "USD"
    assert body["scholarship"] is None and body["internship"] is None
    assert body["organization"]["name"] == "Uni One"
    assert body["data_quality"] == "unverified"
    assert body["last_verified_at"] is None
    assert body["requirements"] is None


def test_category_must_match_details(client):
    h = manager(client)
    org = make_org(client, h)
    wrong_block = admission(org)
    wrong_block["scholarship"] = {"funding_type": "full"}
    assert create(client, h, wrong_block).status_code == 422
    missing = admission(org)
    del missing["admission"]
    assert create(client, h, missing).status_code == 422
    assert create(client, h, admission(org, category="job")).status_code == 422


def test_unknown_organization_rejected(client):
    h = manager(client)
    r = create(client, h, admission(str(uuid.uuid4())))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_reference"


def test_invalid_dates_rejected(client):
    h = manager(client)
    org = make_org(client, h)
    backwards = admission(org, opens_at="2030-06-01T00:00:00Z", deadline_at="2030-01-01T00:00:00Z")
    assert create(client, h, backwards).status_code == 422
    naive = admission(org, deadline_at="2030-01-01T00:00:00")
    assert create(client, h, naive).status_code == 422


def test_draft_and_archived_hidden_from_public(client):
    h = manager(client)
    u = auth_headers(client, "u@example.com")
    org = make_org(client, h)

    draft_body = admission(org, title="Draft Course")
    del draft_body["status"]
    draft = create(client, h, draft_body).json()
    assert draft["status"] == "draft"
    open_opp = create(client, h, admission(org, title="Open Course")).json()

    titles = [i["title"] for i in client.get(f"{B}/opportunities", headers=u).json()["items"]]
    assert titles == ["Open Course"]
    assert client.get(f"{B}/opportunities/{draft['id']}", headers=u).status_code == 404
    assert client.get(f"{B}/opportunities/{open_opp['id']}", headers=u).status_code == 200
    assert len(client.get(f"{B}/admin/opportunities", headers=h).json()["items"]) == 2

    client.patch(f"{B}/admin/opportunities/{open_opp['id']}", json={"status": "archived"}, headers=h)
    assert client.get(f"{B}/opportunities", headers=u).json()["total"] == 0
    assert client.get(f"{B}/opportunities/{open_opp['id']}", headers=u).status_code == 404


def test_expired_not_listed_but_detail_still_readable(client):
    h = manager(client)
    u = auth_headers(client, "u@example.com")
    org = make_org(client, h)
    opp = create(client, h, admission(org, deadline_at=PAST)).json()
    assert client.get(f"{B}/opportunities", headers=u).json()["total"] == 0
    assert client.get(f"{B}/opportunities/{opp['id']}", headers=u).status_code == 200


def test_search_and_filters(client):
    h = manager(client)
    u = auth_headers(client, "u@example.com")
    ng = make_org(client, h, "Uni One", "NG")
    gb = make_org(client, h, "British Council", "GB", "funder")
    create(client, h, admission(ng))
    create(client, h, scholarship(gb, country="GB"))

    def titles(**params):
        r = client.get(f"{B}/opportunities", params=params, headers=u)
        assert r.status_code == 200, r.text
        return sorted(i["title"] for i in r.json()["items"])

    assert titles(category="scholarship") == ["Global Award"]
    assert titles(country="NG") == ["BSc Computer Science"]
    assert titles(q="computer") == ["BSc Computer Science"]
    assert titles(q="british") == ["Global Award"]  # matches organization name
    assert titles(q="nothing-like-this") == []
    assert client.get(f"{B}/opportunities", params={"category": "event"}, headers=u).status_code == 422


def test_search_treats_percent_literally(client):
    h = manager(client)
    u = auth_headers(client, "u@example.com")
    org = make_org(client, h)
    create(client, h, scholarship(org, title="100% Funded Award"))
    create(client, h, scholarship(org, title="1000 Funded Award"))
    r = client.get(f"{B}/opportunities", params={"q": "100%"}, headers=u).json()
    assert [i["title"] for i in r["items"]] == ["100% Funded Award"]


def test_pagination(client):
    h = manager(client)
    u = auth_headers(client, "u@example.com")
    org = make_org(client, h)
    for n in range(3):
        create(client, h, admission(org, title=f"Course {n}"))
    page1 = client.get(f"{B}/opportunities", params={"limit": 2}, headers=u).json()
    assert len(page1["items"]) == 2 and page1["total"] == 3
    page2 = client.get(f"{B}/opportunities", params={"limit": 2, "offset": 2}, headers=u).json()
    assert len(page2["items"]) == 1
    assert client.get(f"{B}/opportunities", params={"limit": 500}, headers=u).status_code == 422


def test_patch_merges_details_and_protects_fields(client):
    h = manager(client)
    org = make_org(client, h)
    opp = create(client, h, admission(org)).json()
    url = f"{B}/admin/opportunities/{opp['id']}"

    r = client.patch(url, json={"details": {"duration_months": 48}}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["admission"]["duration_months"] == 48
    assert r.json()["admission"]["degree_type"] == "bachelor"  # untouched

    assert client.patch(url, json={"details": {"bogus": 1}}, headers=h).status_code == 422
    assert client.patch(url, json={"category": "job"}, headers=h).status_code == 422
    assert client.patch(url, json={"title": None}, headers=h).status_code == 422

    cleared = client.patch(url, json={"deadline_at": None}, headers=h)
    assert cleared.status_code == 200 and cleared.json()["deadline_at"] is None


def test_verification_rules(client):
    h = manager(client)
    org = make_org(client, h)
    src = client.post(
        f"{B}/admin/sources",
        json={"name": "Uni One site", "url": "https://uni.example.com/admissions", "type": "official_website"},
        headers=h,
    ).json()
    assert src["is_official"] is True

    no_source = create(client, h, admission(org)).json()
    r = client.post(f"{B}/admin/opportunities/{no_source['id']}/verify", headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "source_required"

    with_source = create(client, h, admission(org, title="Sourced", source_id=src["id"])).json()
    url = f"{B}/admin/opportunities/{with_source['id']}"
    verified = client.post(f"{url}/verify", headers=h).json()
    assert verified["data_quality"] == "verified"
    assert verified["last_verified_at"] is not None

    # publishing state changes keep verification...
    assert client.patch(url, json={"status": "closed"}, headers=h).json()["data_quality"] == "verified"
    # ...but changing content means it needs re-checking.
    edited = client.patch(url, json={"title": "Sourced v2"}, headers=h).json()
    assert edited["data_quality"] == "needs_review"


def test_detail_validation_per_category(client):
    h = manager(client)
    org = make_org(client, h)

    no_currency = admission(org, admission={"degree_type": "bachelor", "tuition_amount": 100})
    assert create(client, h, no_currency).status_code == 422
    bad_funding = scholarship(org, scholarship={"funding_type": "magic"})
    assert create(client, h, bad_funding).status_code == 422
    money_no_currency = scholarship(org, scholarship={"funding_type": "full", "funding_amount": 5000})
    assert create(client, h, money_no_currency).status_code == 422

    r = create(client, h, internship(org))
    assert r.status_code == 201, r.text
    assert r.json()["internship"]["required_skills"] == ["python", "sql"]  # normalized