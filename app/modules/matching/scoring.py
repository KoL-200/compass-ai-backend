"""Pure match scoring. No database, network or LLM calls.

score = min(preference points, ceiling set by eligibility status).
"""
from __future__ import annotations

import re
from datetime import datetime

from app.core.levels import LEVEL_MONTHS

ALGORITHM_VERSION = "1.0.0"

# Eligibility is a constraint, not a preference: it caps what an opportunity can score.
CAPS = {
    "eligible": 100,
    "likely_eligible": 90,
    "potentially_eligible": 70,
    "needs_manual_verification": 60,
    "insufficient_information": 40,
    "ineligible": 15,
}

WEIGHTS: dict[str, dict[str, int]] = {  # each category sums to 100
    "admission": {"interest": 40, "level": 30, "location": 20, "timing": 10},
    "scholarship": {"financial": 35, "interest": 25, "funding": 20, "location": 10, "timing": 10},
    "internship": {"skills": 35, "interest": 25, "work_mode": 15, "location": 15, "timing": 10},
    "job": {"skills": 30, "interest": 20, "seniority": 15, "location": 15, "work_mode": 10, "timing": 10},
}

LABELS = {
    "interest": "Field and interest fit",
    "level": "Study level fit",
    "location": "Location fit",
    "timing": "Time until deadline",
    "financial": "Financial fit",
    "funding": "Funding offered",
    "skills": "Skills fit",
    "work_mode": "Work arrangement fit",
    "seniority": "Seniority fit",
}

# Natural next study steps from a profile's current education level.
NEXT_LEVELS = {
    "secondary": {"certificate", "diploma", "bachelor"},
    "diploma": {"certificate", "bachelor"},
    "bachelor": {"certificate", "master"},
    "master": {"certificate", "doctorate"},
    "doctorate": set(),
}
FUNDING_FRACTION = {"full": 1.0, "partial": 0.6, "stipend": 0.5, "other": 0.25}
STOPWORDS = {"and", "the", "for", "with", "bsc", "msc", "beng", "meng", "phd", "mba", "hnd", "ond"}

Outcome = tuple  # (fraction 0..1 or None if unknown, detail text, gap text or None)


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9+#]+", text.lower())
    return {w for w in words if len(w) >= 3 and w not in STOPWORDS}


def _opp_text(opp: dict) -> str:
    d = opp.get("details") or {}
    parts = [opp.get("title") or "", d.get("department") or "", d.get("role_family") or ""]
    parts += d.get("required_skills") or []
    return " ".join(parts)


# ---------- individual factors: each returns (fraction | None, detail, gap | None) ----------

def _interest(prefs: dict, opp: dict, now: datetime) -> Outcome:
    phrases = prefs.get("interests") or []
    if not phrases:
        return None, "No field of study or career interests recorded", \
            "Add your field of study or career interests"
    text = _opp_text(opp).lower()
    text_tokens = _tokens(text)
    best, best_phrase = 0.0, None
    for raw in phrases:
        phrase = raw.strip().lower()
        if not phrase:
            continue
        if phrase in text:
            fraction = 1.0
        else:
            tokens = _tokens(phrase)
            if not tokens:
                continue
            fraction = 0.6 * len(tokens & text_tokens) / len(tokens)  # partial overlap is discounted
        if fraction > best:
            best, best_phrase = fraction, phrase
    if best >= 1.0:
        return 1.0, f"'{best_phrase}' matches this opportunity", None
    if best > 0:
        return best, f"Partial overlap with '{best_phrase}'", None
    return 0.0, "No overlap with your field of study or interests", None


def _level(prefs: dict, opp: dict, now: datetime) -> Outcome:
    level = prefs.get("education_level")
    degree = (opp.get("details") or {}).get("degree_type")
    if level is None or level == "other":
        return None, "Your education level is not recorded", "Add your current education level"
    if degree in NEXT_LEVELS.get(level, set()):
        return 1.0, f"A {degree} is a natural next step after {level} education", None
    return 0.0, f"A {degree} is not the usual next step after {level} education", None


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def _location(prefs: dict, opp: dict, now: datetime) -> Outcome:
    if opp.get("work_mode") == "remote":
        if prefs.get("remote_preference") == "onsite":
            return 0.0, "This is remote and you prefer onsite", None
        return 1.0, "Remote, so location does not matter", None
    places = prefs.get("preferred_locations") or []
    country = (opp.get("country") or "").lower()
    location = (opp.get("location") or "").lower()
    if places:
        for place in places:
            in_text = len(place) > 3 and place in location
            if place == country or place in _words(location) or in_text:
                return 1.0, f"Matches your preferred location '{place}'", None
        return 0.0, "Not in your preferred locations", None
    home = prefs.get("country")
    if home and country:
        if home.lower() == country:
            return 0.7, "In your home country", None
        return 0.0, "Outside your home country", None
    return None, "Location fit cannot be assessed", "Add your country or preferred locations"


def _timing(prefs: dict, opp: dict, now: datetime) -> Outcome:
    deadline = opp.get("deadline_at")
    if deadline is None:
        return 0.5, "No deadline stated", None
    days = (deadline - now).days
    if deadline < now:
        return 0.0, "The deadline has passed", None
    if days >= 14:
        return 1.0, f"Deadline in {days} days, plenty of time", None
    if days >= 7:
        return 0.6, f"Deadline in {days} days", None
    if days >= 1:
        return 0.3, f"Deadline in {days} day(s), very soon", None
    return 0.3, "Deadline is within a day", None


def _financial(prefs: dict, opp: dict, now: datetime) -> Outcome:
    needs = prefs.get("needs_financial_aid")
    need_based = (opp.get("details") or {}).get("need_based")
    if needs is None:
        return None, "You have not said whether you need financial aid", \
            "State whether you need financial aid"
    if needs:
        if need_based:
            return 1.0, "Need-based, and you need financial aid", None
        return 20 / 35, "Not need-based, but funding still helps", None
    if need_based is False:
        return 1.0, "Merit-based, and you do not need aid", None
    if need_based:
        return 0.0, "Need-based, and you said you do not need aid", None
    return 20 / 35, "Basis of award not stated", None


def _funding(prefs: dict, opp: dict, now: datetime) -> Outcome:
    funding = (opp.get("details") or {}).get("funding_type")
    fraction = FUNDING_FRACTION.get(funding, 0.0)
    return fraction, f"{funding or 'Unknown'} funding", None


def _skills(prefs: dict, opp: dict, now: datetime) -> Outcome:
    required = [s.lower() for s in (opp.get("details") or {}).get("required_skills") or []]
    if not required:
        return 1.0, "No specific skills required", None
    have = {s.lower() for s in prefs.get("skills") or []}
    if not have:
        return None, "No skills recorded in your profile", "Add your skills to your profile"
    missing = [s for s in required if s not in have]
    matched = len(required) - len(missing)
    gap = f"Missing skills: {', '.join(missing)}" if missing else None
    return matched / len(required), f"You have {matched} of {len(required)} required skills", gap


def _work_mode(prefs: dict, opp: dict, now: datetime) -> Outcome:
    mode, pref = opp.get("work_mode"), prefs.get("remote_preference")
    if mode in (None, "unknown"):
        return None, "Work arrangement not stated by the provider", None
    if pref is None:
        return None, "Your work arrangement preference is not set", "Set your remote-work preference"
    if pref == "any" or pref == mode:
        return 1.0, f"{mode} work suits your preference", None
    if "hybrid" in (pref, mode):
        return 0.5, f"{mode} work partly fits your preference for {pref}", None
    return 0.0, f"{mode} work conflicts with your preference for {pref}", None

def _seniority(prefs: dict, opp: dict, now: datetime) -> Outcome:
    level = (opp.get("details") or {}).get("job_level")
    window = LEVEL_MONTHS.get(level)
    if window is None:
        return None, "Job level not stated", None
    months = prefs.get("experience_months")
    low, high = window
    if months is None:
        if low == 0:
            return 1.0, "Entry-level roles suit candidates with no recorded experience", None
        return None, "No work experience recorded", "Add your work experience"
    if months < low:
        return 0.5 * months / low, f"About {months} months of experience; a {level} role usually needs {low}+", None
    if months > high:
        return 0.8, f"More experience ({months} months) than is typical for a {level} role", None
    return 1.0, f"Your {months} months of experience fits a {level} role", None

FACTORS = {
    "interest": _interest, "level": _level, "location": _location, "timing": _timing,
    "financial": _financial, "funding": _funding, "skills": _skills, "work_mode": _work_mode,
    "seniority": _seniority,
}


# ---------- explanations ----------

def _requirement_gaps(eligibility: dict) -> list[str]:
    gaps = []
    for r in eligibility.get("requirements") or []:
        if r["outcome"] == "fail":
            gaps.append(f"Requirement not met: {r['label']}")
        elif r["outcome"] == "unknown":
            gaps.append(f"Cannot check yet: {r['label']} ({r.get('reason') or 'no data'})")
        elif r["outcome"] == "review":
            gaps.append(f"Needs manual verification: {r['label']}")
    return gaps


def _next_steps(status: str, eligibility: dict, pref_gaps: list[str], opp: dict, now: datetime):
    steps: list[str] = []
    unknown = sum(r["outcome"] == "unknown" for r in eligibility.get("requirements") or [])
    if status == "ineligible":
        steps.append("Your profile does not meet a documented requirement, so this is unlikely "
                     "to be open to you. See the requirements that are not met.")
    elif status in ("potentially_eligible", "insufficient_information"):
        steps.append(f"Complete your profile so we can check {unknown} requirement(s) "
                     f"that could not be verified yet.")
    elif status == "needs_manual_verification":
        steps.append("Confirm the flagged or undocumented requirements with the provider "
                     "before applying.")
    elif status == "likely_eligible":
        steps.append("Confirm the details on the official source before applying: our data "
                     "for this opportunity is not recently verified.")
    else:
        steps.append("Review the official application page and start tracking your application.")

    deadline = opp.get("deadline_at")
    if deadline is not None and status != "ineligible" and now <= deadline:
        days = (deadline - now).days
        if days < 14:
            steps.append(f"The deadline is in {days} day(s). Plan your preparation now.")
    if pref_gaps:
        steps.append("To improve this ranking: " + "; ".join(pref_gaps) + ".")
    return steps


# ---------- the public function ----------

def score_match(prefs: dict, opp: dict, eligibility: dict, now: datetime) -> dict:
    factors, pref_gaps = [], []
    for name, weight in WEIGHTS[opp["category"]].items():
        fraction, detail, gap = FACTORS[name](prefs, opp, now)
        points = 0 if fraction is None else min(weight, int(weight * fraction + 0.5))
        factors.append({
            "name": name, "label": LABELS[name], "points": points,
            "max_points": weight, "detail": detail, "known": fraction is not None,
        })
        if gap and gap not in pref_gaps:
            pref_gaps.append(gap)

    raw = sum(f["points"] for f in factors)
    status = eligibility["status"]
    cap = CAPS[status]
    return {
        "score": min(raw, cap),
        "raw_score": raw,
        "score_cap": cap,
        "eligibility_status": status,
        "factors": factors,
        "gaps": _requirement_gaps(eligibility) + pref_gaps,
        "next_steps": _next_steps(status, eligibility, pref_gaps, opp, now),
        "algorithm_version": ALGORITHM_VERSION,
    }