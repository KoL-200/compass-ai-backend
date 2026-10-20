BASE = "/api/v1"
PASSWORD = "correct-horse-1"


def auth_headers(client, email="a@example.com", password=PASSWORD):
    client.post(f"{BASE}/auth/register", json={"email": email, "password": password})
    tokens = client.post(
        f"{BASE}/auth/login", json={"email": email, "password": password}
    ).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}