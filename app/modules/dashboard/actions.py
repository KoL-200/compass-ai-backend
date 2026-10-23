"""Decides what a user should do next. Pure Python: every action says why it exists."""
from datetime import datetime, timezone

from app.modules.applications.transitions import PRE_SUBMISSION

FAR = datetime.max.replace(tzinfo=timezone.utc)
AWAITING_REPLY = {"submitted", "under_review", "interview_assessment"}
DEADLINE_WINDOW_DAYS = 14
URGENT_DAYS = 7
STALE_STATUS_DAYS = 21
STALE_MATCH_DAYS = 7
APPLY_MIN_SCORE = 60
SOON_DAYS = 30
MAX_START_SUGGESTIONS = 3

CONFIDENCE = {
    "eligible": "You appear to meet the documented requirements",
    "likely_eligible": "You appear to meet the documented requirements (confirm the details with the provider)",
}


def _when(days: int) -> str:
    return "is today" if days == 0 else "is tomorrow" if days == 1 else f"is in {days} days"


def next_actions(ctx: dict, now: datetime, limit: int = 10) -> list[dict]:
    actions: list[dict] = []

    def add(kind, priority, title, detail, target=None, due_at=None):
        actions.append(
            {"type": kind, "priority": priority, "title": title, "detail": detail,
             "target": target, "due_at": due_at}
        )

    # --- applications ---
    for app in ctx["applications"]:
        target = {"kind": "application", "id": str(app["id"])}
        deadline = app["deadline_at"]
        if app["status"] in PRE_SUBMISSION and deadline is not None and deadline > now:
            days = (deadline - now).days
            if days <= DEADLINE_WINDOW_DAYS:
                add(
                    "deadline_approaching",
                    1 if days <= URGENT_DAYS else 2,
                    f"Deadline {_when(days)}: {app['title']}",
                    f"This application is still '{app['status'].replace('_', ' ')}'. "
                    f"Finish and submit before {deadline:%d %b %Y}.",
                    target,
                    deadline,
                )
        elif app["status"] in AWAITING_REPLY:
            quiet = (now - app["updated_at"]).days
            if quiet >= STALE_STATUS_DAYS:
                add(
                    "update_application_status", 4,
                    f"Update the status of: {app['title']}",
                    f"Nothing has been recorded for {quiet} days. If you have heard back, record it.",
                    target,
                )

    # --- profile and account ---
    if not ctx["email_verified"]:
        add("verify_email", 3, "Confirm your email address",
            "Confirming your address lets you recover your account if you lose access.")
    percent = ctx["completeness"]["percent"]
    if percent < 100:
        labels = ", ".join(m["label"] for m in ctx["completeness"]["missing"][:3])
        add("complete_profile", 2 if percent < 60 else 4, f"Complete your profile ({percent}% done)",
            f"Still missing: {labels}. A fuller profile gives more accurate eligibility results and matches.")
    if ctx["active_goals"] == 0:
        add("set_goal", 5, "Set a goal",
            "Tell us what you are aiming for (an admission, scholarship, internship or job) "
            "so your dashboard can focus on it.")

    # --- matches ---
    last = ctx["matches_last_computed_at"]
    if last is None:
        add("refresh_matches", 2, "Find your matches",
            "Run a match refresh to see which opportunities fit you.")
    elif (now - last).days >= STALE_MATCH_DAYS:
        add("refresh_matches", 4, "Refresh your matches",
            f"Your matches were last updated {(now - last).days} days ago.")

    candidates = [
        m for m in ctx["matches"]
        if not m["tracked"] and m["eligibility_status"] in CONFIDENCE and m["score"] >= APPLY_MIN_SCORE
    ]
    candidates.sort(key=lambda m: (-m["score"], m["title"]))
    for m in candidates[:MAX_START_SUGGESTIONS]:
        deadline = m["deadline_at"]
        soon = deadline is not None and 0 <= (deadline - now).days <= SOON_DAYS
        detail = f"{CONFIDENCE[m['eligibility_status']]}. Match score {m['score']}."
        if deadline is not None:
            detail += f" Deadline {deadline:%d %b %Y}."
        add("start_application", 3 if soon else 4, f"Consider applying: {m['title']}", detail,
            {"kind": "opportunity", "id": str(m["opportunity_id"])}, deadline)

    actions.sort(
        key=lambda a: (
            a["priority"],
            a["due_at"] if a["type"] == "deadline_approaching" else FAR,
        )
    )
    return actions[:limit]