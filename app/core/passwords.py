from app.core.errors import AppError

COMMON_BASES = {
    "password", "passw0rd", "qwerty", "qwertyuiop", "qwertyuiopasdf", "letmein", "welcome",
    "iloveyou", "admin", "administrator", "football", "baseball", "monkey", "dragon", "master",
    "sunshine", "princess", "abc", "abcdef", "abcdefgh", "abcdefghij", "login", "changeme",
    "trustno1", "superman", "batman", "whatever", "freedom", "computer", "internet", "michael",
    "jennifer", "starwars", "hello", "secret", "asdfghjkl", "zxcvbnm", "pass", "default",
}
_TRAILING = "0123456789!@#$%^&*._-"


def check_password_policy(password: str, email: str | None = None) -> None:
    """Lightweight checks. Length is enforced by the request schema.

    A lookup against breached-password lists would be stronger and is a planned upgrade.
    """
    problems: list[str] = []
    lowered = password.lower()
    base = lowered.rstrip(_TRAILING)  # "Password123!" -> "password"

    if base == "" or base in COMMON_BASES:
        problems.append("This password is too common or too predictable.")
    if len(set(password)) < 4:
        problems.append("Use more than a few repeated characters.")
    if email:
        name = email.split("@")[0].lower()
        if len(name) >= 4 and name in lowered:
            problems.append("The password must not contain your email name.")

    if problems:
        raise AppError(422, "weak_password", "Choose a stronger password.", problems)