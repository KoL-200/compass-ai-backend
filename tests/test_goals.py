from tests.helpers import auth_headers

B = "/api/v1"


def goal(client, h, **over):
    body = {"title": "Land a backend job", "category": "job"}
    body.update(over)
    return client.post(f"{B}/goals", json=body, headers=h)


def test_goals_require_auth(client):
    assert client.get(f"{B}/goals").status_code == 401
    assert client.post(f"{B}/goals", json={"title": "x"}).status_code == 401


def test_goal_lifecycle_and_validation(client):
    h = auth_headers(client)
    r = goal(client, h, title="  Land a backend job  ", target_date="2030-01-01", notes="By graduation")
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["title"] == "Land a backend job" and created["status"] == "active"
    assert created["category"] == "job" and created["target_date"] == "2030-01-01"

    assert goal(client, h, category="event").status_code == 422
    assert goal(client, h, target_date="2020-01-01").status_code == 422
    assert goal(client, h, title="   ").status_code == 422
    assert goal(client, h, surprise=1).status_code == 422

    assert len(client.get(f"{B}/goals", headers=h).json()) == 1
    assert client.get(f"{B}/goals", params={"status": "achieved"}, headers=h).json() == []
    assert client.get(f"{B}/goals", params={"status": "bogus"}, headers=h).status_code == 422

    url = f"{B}/goals/{created['id']}"
    done = client.patch(url, json={"status": "achieved"}, headers=h)
    assert done.status_code == 200 and done.json()["status"] == "achieved"
    assert client.patch(url, json={"title": None}, headers=h).status_code == 422
    assert client.patch(url, json={"status": None}, headers=h).status_code == 422
    assert client.patch(url, json={"target_date": None}, headers=h).json()["target_date"] is None

    assert client.delete(url, headers=h).status_code == 204
    assert client.delete(url, headers=h).status_code == 404


def test_active_goal_limit(client):
    h = auth_headers(client)
    ids = [goal(client, h, title=f"Goal {n}").json()["id"] for n in range(10)]
    full = goal(client, h, title="One too many")
    assert full.status_code == 422 and full.json()["error"]["code"] == "goal_limit"

    client.patch(f"{B}/goals/{ids[0]}", json={"status": "abandoned"}, headers=h)
    assert goal(client, h, title="Fits now").status_code == 201
    again = client.patch(f"{B}/goals/{ids[0]}", json={"status": "active"}, headers=h)
    assert again.status_code == 422 and again.json()["error"]["code"] == "goal_limit"


def test_goals_are_private(client):
    a = auth_headers(client, "a@example.com")
    b = auth_headers(client, "b@example.com")
    gid = goal(client, a).json()["id"]
    assert client.get(f"{B}/goals", headers=b).json() == []
    assert client.patch(f"{B}/goals/{gid}", json={"status": "achieved"}, headers=b).status_code == 404
    assert client.delete(f"{B}/goals/{gid}", headers=b).status_code == 404
    assert len(client.get(f"{B}/goals", headers=a).json()) == 1