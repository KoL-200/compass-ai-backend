"""Building and scheduling plans. Pure Python: dates are decided here, never by a model."""
import math
from datetime import date, timedelta

KINDS = (
    "complete_profile", "confirm_requirement", "learn_skill", "gather_documents",
    "prepare_materials", "submit", "follow_up",
)
STORED_KINDS = KINDS + ("custom",)  # "custom" is for tasks the user adds themselves
MAX_TASKS = 12
BUFFER_DAYS = 1  # the last step is due at least a day before the deadline


def _task(title, detail, kind, effort, requirement=None, skill=None) -> dict:
    return {"title": title[:120], "detail": detail[:300], "kind": kind, "effort_days": effort,
            "requirement": requirement, "skill": skill}


def build_rule_plan(facts: dict) -> dict:
    """A plan built only from the facts, with no AI. Also the safety net when the model fails."""
    tasks: list[dict] = []
    if facts["profile_missing"]:
        missing = ", ".join(facts["profile_missing"][:4])
        tasks.append(_task("Complete your profile", f"Still missing: {missing}. This lets us check every requirement.",
                           "complete_profile", 1))

    flagged = [r for r in facts["requirements"] if r["outcome"] in ("fail", "unknown", "review")]
    for r in flagged[:4]:
        if r["outcome"] == "fail":
            tasks.append(_task(f"Look into: {r['label']}",
                               "This requirement is currently not met. Check whether it can be met or waived.",
                               "confirm_requirement", 2, requirement=r["label"]))
        else:
            tasks.append(_task(f"Confirm: {r['label']}",
                               r.get("reason") or "This could not be checked automatically.",
                               "confirm_requirement", 1, requirement=r["label"]))

    for skill in facts["missing_skills"][:3]:
        tasks.append(_task(f"Build or evidence the skill: {skill}",
                           f"The posting asks for {skill}, which is not in your profile yet.",
                           "learn_skill", 5, skill=skill))

    tasks += [
        _task("Check the official page and gather your documents",
              "Open the official application page and collect everything it asks for.", "gather_documents", 3),
        _task("Prepare your application materials",
              "Prepare what the provider asks for, such as a CV or personal statement.", "prepare_materials", 3),
        _task("Review everything and submit on the official page",
              "Submit through the provider's official channel, not through Compass AI.", "submit", 1),
        _task("Record your submission",
              "Update the application status in Compass AI once you have submitted.", "follow_up", 1),
    ]
    title = facts["opportunity"]["title"]
    deadline = f" The deadline is {facts['deadline_date']}." if facts.get("deadline_date") else ""
    return {"summary": f"A step-by-step plan for {title}.{deadline}", "tasks": tasks}


def schedule(tasks: list[dict], today: date, deadline: date | None) -> tuple[list[dict], list[str]]:
    """Give each task a due date. Returns (tasks with due_date, warnings)."""
    total = sum(t["effort_days"] for t in tasks)
    warnings: list[str] = []
    factor = 1.0
    if deadline is not None:
        horizon = (deadline - today).days - BUFFER_DAYS
        if horizon <= 0:
            factor = 0.0
            warnings.append("The deadline is very close, so every step is due now. Start straight away.")
        elif total > horizon:
            factor = horizon / total
            warnings.append(
                f"The plan needs about {total} days but only {horizon} are available before the deadline, "
                "so the dates are compressed. Consider skipping steps you do not need."
            )
    scheduled, cumulative = [], 0
    for t in tasks:
        cumulative += t["effort_days"]
        offset = math.floor(cumulative * factor + 0.5)
        scheduled.append({**t, "due_date": today + timedelta(days=offset)})
    return scheduled, warnings