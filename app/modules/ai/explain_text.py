"""Builds an eligibility explanation from the engine's own data. No AI involved."""

SUMMARY = {
    "eligible": "You meet all the documented requirements for {title}.",
    "likely_eligible": "You meet the documented requirements for {title}, but confirm the details with the "
                       "provider because our data for it is not recently verified.",
    "potentially_eligible": "You do not fail any requirement for {title}, but some could not be checked yet.",
    "ineligible": "You do not currently meet at least one requirement for {title}.",
    "insufficient_information": "There is not enough information in your profile to assess {title}.",
    "needs_manual_verification": "Some requirements for {title} need manual verification before "
                                 "eligibility can be confirmed.",
}
NEXT_STEPS = {
    "eligible": ["Review the official application page and start tracking your application."],
    "likely_eligible": ["Confirm the details on the official source before applying."],
    "potentially_eligible": ["Complete your profile so the unchecked requirements can be verified."],
    "ineligible": ["Review the requirements you do not meet. As things stand this opportunity is "
                   "unlikely to be open to you."],
    "insufficient_information": ["Add the missing details to your profile, then check again."],
    "needs_manual_verification": ["Confirm the flagged or undocumented requirements with the provider "
                                  "before applying."],
}


def _clip(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _show(value) -> str:
    return ", ".join(str(v) for v in value) if isinstance(value, list) else str(value)


def build_explanation(payload: dict) -> dict:
    title, status = payload["opportunity"]["title"], payload["status"]
    requirements = payload["requirements"]

    strengths = [_clip(r["label"]) for r in requirements if r["outcome"] == "pass"]
    gaps, unknowns = [], []
    for r in requirements:
        if r["outcome"] == "fail":
            text = r["label"]
            if r.get("actual") is not None:
                text += f" (you have: {_show(r['actual'])})"
            gaps.append(_clip(text))
        elif r["outcome"] == "unknown":
            unknowns.append(_clip(f"{r['label']}: {r['reason']}" if r.get("reason") else r["label"]))
        elif r["outcome"] == "review":
            unknowns.append(_clip(f"{r['label']} (needs manual verification)"))

    return {
        "summary": _clip(SUMMARY[status].format(title=title), 600),
        "strengths": strengths[:10],
        "gaps": gaps[:10],
        "unknowns": unknowns[:10],
        "next_steps": list(NEXT_STEPS[status]),
    }