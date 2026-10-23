"""Career paths built from real postings. Pure Python."""
from collections import Counter, defaultdict
from statistics import median

from app.core.levels import LADDER, level_for_months

TOP_SKILLS = 8
NEXT_LEVEL_MIN_SHARE = 50  # a skill counts as "expected" if at least this % of postings require it


def _salary_summary(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, dict] = defaultdict(lambda: {"lows": [], "highs": [], "n": 0})
    for p in rows:
        low, high = p.get("salary_min"), p.get("salary_max")
        if low is None and high is None:
            continue
        g = groups[(p["salary_currency"], p["salary_period"])]
        g["lows"].append(low if low is not None else high)
        g["highs"].append(high if high is not None else low)
        g["n"] += 1
    return [
        {
            "currency": currency,
            "period": period,
            "lowest": round(min(g["lows"]), 2),
            "highest": round(max(g["highs"]), 2),
            "postings": g["n"],
        }
        for (currency, period), g in sorted(groups.items())
    ]


def build_path(role_family: str, postings: list[dict], user_skills, user_months: int | None) -> dict:
    have = {s.lower() for s in user_skills}
    by_level: dict[str, list[dict]] = defaultdict(list)
    for p in postings:
        by_level[p["job_level"]].append(p)

    levels = []
    for level in LADDER:  # always in career order, whatever order the postings arrived in
        rows = by_level.get(level)
        if not rows:
            continue
        counts: Counter = Counter()
        for p in rows:
            counts.update({s.strip().lower() for s in p["required_skills"] if s.strip()})
        common = [
            {"skill": skill, "postings": n, "required_by_percent": round(100 * n / len(rows))}
            for skill, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_SKILLS]
        ]
        levels.append(
            {
                "level": level,
                "postings": len(rows),
                "typical_min_experience_months": int(median(p["min_experience_months"] for p in rows)),
                "common_skills": common,
                "salary": _salary_summary(rows),
            }
        )

    available = [entry["level"] for entry in levels]
    current = level_for_months(user_months)
    if current is None:
        next_level = available[0] if available else None
    else:
        next_level = next((lv for lv in available if LADDER.index(lv) > LADDER.index(current)), None)

    next_step = None
    if next_level is not None:
        target = next(entry for entry in levels if entry["level"] == next_level)
        expected = [c["skill"] for c in target["common_skills"] if c["required_by_percent"] >= NEXT_LEVEL_MIN_SHARE]
        recorded = bool(have)
        next_step = {
            "level": next_level,
            "skills_recorded": recorded,
            "already_have": [s for s in expected if s in have],
            "to_learn": [s for s in expected if recorded and s not in have],
            "unknown": [] if recorded else expected,
        }

    return {
        "role_family": role_family,
        "postings_analyzed": len(postings),
        "estimated_current_level": current,
        "levels": levels,
        "next_step": next_step,
    }