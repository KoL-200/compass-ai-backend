import sys

from sqlalchemy import select

from app.core.db import SessionLocal
from app.modules.auth.models import Role, User


def main(email: str, role_name: str) -> int:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email.strip().lower()))
        role = db.scalar(select(Role).where(Role.name == role_name))
        if user is None:
            print(f"No user with email {email}")
            return 1
        if role is None:
            print(f"Unknown role {role_name}. Valid: user, admin, data_manager, reviewer")
            return 1
        if role not in user.roles:
            user.roles.append(role)
            db.commit()
        print(f"{user.email} now has roles: {sorted(r.name for r in user.roles)}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python -m app.scripts.grant_role <email> <role>")
        sys.exit(1)
    sys.exit(main(sys.argv[1], sys.argv[2]))