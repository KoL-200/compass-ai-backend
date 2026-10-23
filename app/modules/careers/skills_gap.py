"""Skills-gap analysis. Pure Python."""


def _norm(skills) -> list[str]:
    return list(dict.fromkeys(s.strip().lower() for s in skills if s and s.strip()))


def analyze_gap(user_skills, required, preferred, required_months: int, user_months: int | None) -> dict:
    """Compare a person with one posting.

    If the person has recorded no skills at all, every skill is 'unknown' (we cannot say they
    lack it) and the coverage percentage is None.
    """
    have = {s.lower() for s in user_skills}
    recorded = bool(have)

    def group(skills) -> dict:
        skills = _norm(skills)
        if not recorded:
            return {"total": len(skills), "matched": [], "missing": [], "unknown": skills}
        return {
            "total": len(skills),
            "matched": [s for s in skills if s in have],
            "missing": [s for s in skills if s not in have],
            "unknown": [],
        }

    req, pref = group(required), group(preferred)
    coverage = None
    if recorded and req["total"]:
        coverage = round(100 * len(req["matched"]) / req["total"])

    if required_months <= 0:
        gap = 0
    elif user_months is None:
        gap = None
    else:
        gap = max(0, required_months - user_months)

    return {
        "skills_recorded": recorded,
        "required": req,
        "preferred": pref,
        "coverage_percent": coverage,
        "experience": {"required_months": required_months, "your_months": user_months, "gap_months": gap},
    }


def rank_skills_to_learn(user_skills, postings: list[dict], limit: int) -> list[dict]:
    """Which missing skills would help with the most postings?

    Ranked by: postings that require it, then postings where it is the ONLY missing required
    skill (learning it would complete the requirements), then postings that merely prefer it.
    """
    have = {s.lower() for s in user_skills}
    stats: dict[str, dict] = {}

    def entry(skill: str) -> dict:
        return stats.setdefault(
            skill, {"skill": skill, "required_in": 0, "preferred_in": 0, "would_complete": 0, "examples": []}
        )

    for posting in postings:
        required = _norm(posting.get("required_skills") or [])
        preferred = _norm(posting.get("preferred_skills") or [])
        missing = [s for s in required if s not in have]
        for skill in missing:
            e = entry(skill)
            e["required_in"] += 1
            if len(missing) == 1:
                e["would_complete"] += 1
            if len(e["examples"]) < 3:
                e["examples"].append({"id": posting["id"], "title": posting["title"]})
        for skill in preferred:
            if skill not in have and skill not in required:
                entry(skill)["preferred_in"] += 1

    ranked = sorted(
        stats.values(),
        key=lambda e: (-e["required_in"], -e["would_complete"], -e["preferred_in"], e["skill"]),
    )
    return ranked[:limit]