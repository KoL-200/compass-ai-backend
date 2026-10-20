from sqlalchemy import select

from app.core.db import SessionLocal
from app.modules.auth.models import Role, User


def grant_role(email: str, role_name: str) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        role = db.scalar(select(Role).where(Role.name == role_name))
        user.roles.append(role)
        db.commit()

BASE = "/api/v1"
PASSWORD = "correct-horse-1"


def auth_headers(client, email="a@example.com", password=PASSWORD):
    client.post(f"{BASE}/auth/register", json={"email": email, "password": password})
    tokens = client.post(
        f"{BASE}/auth/login", json={"email": email, "password": password}
    ).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}