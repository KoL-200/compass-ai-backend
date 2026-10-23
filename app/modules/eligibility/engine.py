"""Pure eligibility engine. No database, network or LLM calls.

Same input always gives the same output. Time only enters through `now`.
"""
from __future__ import annotations

from datetime import date, datetime

from app.modules.eligibility.rules import FIELDS, GRADE_SCALES, LEVELS, grade_rank

ENGINE_VERSION = "1.0.0"
STALE_AFTER_DAYS = 90

PASS, FAIL, UNKNOWN, REVIEW = "pass", "fail", "unknown", "review"

OP_WORDS = {
    "eq": "is", "ne": "is not", "in": "is one of", "not_in": "is not one of",
    "gte": "at least", "lte": "at most", "between": "between",
    "contains": "includes", "contains_all": "includes all of", "contains_any": "includes any of",
    "before": "before", "after": "after",
}


# ---------- combining outcomes ----------

def _combine_and(outcomes: list[str]) -> str:
    for o in (FAIL, REVIEW, UNKNOWN):
        if o in outcomes:
            return o
    return PASS


def _combine_or(outcomes: list[str]) -> str:
    for o in (PASS, REVIEW, UNKNOWN):
        if o in outcomes:
            return o
    return FAIL


# ---------- resolving profile values ----------

def _age(dob: date, today: date) -> int:
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _experience_months(experiences: list[dict], today: date) -> int | None:
    spans = []
    for e in experiences:
        start = e.get("start_date")
        if start is None or start > today:
            continue
        end = min(e.get("end_date") or today, today)
        if end >= start:
            spans.append((start, end))
    if not spans:
        return None
    spans.sort()
    merged = [spans[0]]
    for s, e in spans[1:]:
        if s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    days = sum((e - s).days for s, e in merged)
    return days * 12 // 365  # approximate whole months; overlapping jobs counted once

experience_months = _experience_months

def _resolve(leaf: dict, snap: dict, today: date):
    """Return (actual_value, reason). actual_value None means 'unknown'."""
    f = leaf["field"]
    quals = snap.get("qualifications") or []

    def missing(what: str):
        return None, f"{what} not provided"

    if f in ("country", "nationality", "field_of_study"):
        v = snap.get(f)
        if not v or not str(v).strip():
            return missing(f.replace("_", " "))
        v = str(v).strip()
        return (v.lower() if f == "field_of_study" else v.upper()), None
    if f in ("education_level", "graduation_year", "needs_financial_aid"):
        v = snap.get(f)
        if v is None or v == "":
            return missing(f.replace("_", " "))
        return (v.lower() if isinstance(v, str) else v), None
    if f in ("age", "date_of_birth"):
        dob = snap.get("date_of_birth")
        if dob is None:
            return missing("date of birth")
        return (_age(dob, today) if f == "age" else dob), None
    if f == "experience_months":
        months = _experience_months(snap.get("experiences") or [], today)
        return (months, None) if months is not None else (None, "no work experience recorded")
    if f == "skills":
        s = {str(x).strip().lower() for x in snap.get("skills") or [] if str(x).strip()}
        return (s, None) if s else (None, "no skills recorded")
    if f == "qualification_types":
        s = {str(q["type"]).strip().lower() for q in quals if q.get("type")}
        return (s, None) if s else (None, "no qualifications recorded")
    if f == "subjects":
        s = {k.strip().lower() for q in quals for k in (q.get("subject_results") or {})}
        return (s, None) if s else (None, "no subject results recorded")

    if f == "qualification_score":
        qtype = leaf["qualification_type"]
        matching = [q for q in quals if str(q.get("type", "")).strip().lower() == qtype]
        if not matching:
            return None, f"no {qtype} qualification recorded"
        rule_scale = leaf.get("scale")
        best = None
        for q in matching:
            score = q.get("overall_score")
            if score is None:
                continue
            if rule_scale is not None:
                q_scale = q.get("score_scale")
                if not q_scale:
                    continue
                score = score * rule_scale / q_scale  # multiply first: keeps boundaries exact
            best = score if best is None else max(best, score)
        if best is None:
            return None, f"{qtype} score (or its scale) not recorded"
        return best, None

    if f == "subject_grade":
        subject, scale = leaf["subject"], leaf["grade_scale"]
        best = None
        for q in quals:
            rank = grade_rank(scale, (q.get("subject_results") or {}).get(subject))
            if rank is not None:
                best = rank if best is None else max(best, rank)
        if best is None:
            return None, f"no usable {subject} grade recorded on the {scale} scale"
        return best, None

    raise ValueError(f"unsupported field {f}")


# ---------- checking one leaf ----------

def _cmp(op: str, a, e) -> bool:
    if op == "eq":
        return a == e
    if op == "gte":
        return a >= e
    if op == "lte":
        return a <= e
    if op == "between":
        return e[0] <= a <= e[1]
    raise ValueError(op)


def _check(leaf: dict, actual):
    kind = FIELDS[leaf["field"]].kind
    op, exp = leaf["op"], leaf["value"]

    if kind == "set":
        needed = set(exp if isinstance(exp, list) else [exp])
        ok = bool(actual & needed) if op == "contains_any" else needed <= actual
        if ok:
            return PASS, None
        absent = needed if op == "contains_any" else needed - actual
        return UNKNOWN, f"not found in what you have recorded: {', '.join(sorted(absent))}"

    if kind in ("text", "level"):
        if kind == "level" and op in ("gte", "lte"):
            rank = LEVELS.get(actual)
            if rank is None:
                return UNKNOWN, "an education level of 'other' cannot be ranked"
            return (PASS if _cmp(op, rank, LEVELS[exp]) else FAIL), None
        if op == "eq":
            ok = actual == exp
        elif op == "ne":
            ok = actual != exp
        elif op == "in":
            ok = actual in exp
        else:
            ok = actual not in exp
        return (PASS if ok else FAIL), None

    if kind in ("number", "bool"):
        return (PASS if _cmp(op, actual, exp) else FAIL), None

    if kind == "date":
        limit = date.fromisoformat(exp)
        ok = actual < limit if op == "before" else actual > limit
        return (PASS if ok else FAIL), None

    if kind == "grade":
        return (PASS if _cmp(op, actual, grade_rank(leaf["grade_scale"], exp)) else FAIL), None

    raise ValueError(kind)


def _value_text(op: str, value) -> str:
    if op == "between":
        return f"{value[0]} and {value[1]}"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def auto_label(leaf: dict) -> str:
    f, extra = leaf["field"], ""
    if f == "qualification_score":
        extra = f" ({leaf['qualification_type']})"
    elif f == "subject_grade":
        extra = f" ({leaf['subject']}, {leaf['grade_scale']} scale)"
    return f"{f.replace('_', ' ')}{extra} {OP_WORDS[leaf['op']]} {_value_text(leaf['op'], leaf['value'])}"


def _display(leaf: dict, actual):
    if actual is None:
        return None
    if isinstance(actual, set):
        return sorted(actual)
    if isinstance(actual, date):
        return actual.isoformat()
    if isinstance(actual, float):
        return round(actual, 2)
    if FIELDS[leaf["field"]].kind == "grade":
        order = GRADE_SCALES[leaf["grade_scale"]]
        return order[len(order) - actual]
    return actual


def _eval_leaf(leaf: dict, snap: dict, today: date, path: str) -> dict:
    base = {
        "path": path,
        "label": leaf.get("label") or auto_label(leaf),
        "field": leaf["field"],
        "op": leaf["op"],
        "expected": leaf["value"],
    }
    actual, reason = _resolve(leaf, snap, today)
    if leaf.get("needs_review"):
        return {**base, "actual": _display(leaf, actual), "outcome": REVIEW,
                "reason": "Flagged by the data team: this requirement needs manual verification"}
    if actual is None:
        return {**base, "actual": None, "outcome": UNKNOWN, "reason": reason}
    outcome, why = _check(leaf, actual)
    return {**base, "actual": _display(leaf, actual), "outcome": outcome, "reason": why}


def _eval(node: dict, snap: dict, today: date, path: str):
    if node["type"] == "LEAF":
        result = _eval_leaf(node, snap, today, path)
        return result["outcome"], [result]
    parts = [_eval(c, snap, today, f"{path}.children[{i}]") for i, c in enumerate(node["children"])]
    outcomes = [o for o, _ in parts]
    if node["type"] == "AND":
        return _combine_and(outcomes), [leaf for _, ls in parts for leaf in ls]
    combined = _combine_or(outcomes)
    if combined == PASS:  # failures in other OR branches are absorbed, so don't report them
        return PASS, [leaf for o, ls in parts if o == PASS for leaf in ls]
    return combined, [leaf for _, ls in parts for leaf in ls]


# ---------- the public function ----------

def _freshness(data_quality: str, last_verified_at: datetime | None, now: datetime) -> dict:
    age = (now - last_verified_at).days if last_verified_at else None
    return {
        "data_quality": data_quality,
        "last_verified_at": last_verified_at.isoformat() if last_verified_at else None,
        "age_days": age,
        "is_fresh": data_quality == "verified" and age is not None and age <= STALE_AFTER_DAYS,
    }


def _result(status: str, leaves: list[dict], freshness: dict, explanation: str) -> dict:
    return {
        "status": status,
        "requirements": leaves,
        "counts": {
            "satisfied": sum(l["outcome"] == PASS for l in leaves),
            "unsatisfied": sum(l["outcome"] == FAIL for l in leaves),
            "unknown": sum(l["outcome"] == UNKNOWN for l in leaves),
            "review": sum(l["outcome"] == REVIEW for l in leaves),
        },
        "explanation": explanation,
        "data_freshness": freshness,
        "engine_version": ENGINE_VERSION,
    }


def evaluate(
    snapshot: dict,
    tree: dict | None,
    *,
    data_quality: str,
    last_verified_at: datetime | None,
    now: datetime,
) -> dict:
    freshness = _freshness(data_quality, last_verified_at, now)
    if not tree:
        return _result(
            "needs_manual_verification", [], freshness,
            "This opportunity has no documented requirements yet, so eligibility "
            "cannot be assessed automatically.",
        )

    outcome, leaves = _eval(tree, snapshot, now.date(), "$")

    def names(kind: str) -> str:
        return "; ".join(l["label"] for l in leaves if l["outcome"] == kind)

    total = len(leaves)
    n_unknown = sum(l["outcome"] == UNKNOWN for l in leaves)

    if outcome == FAIL:
        return _result("ineligible", leaves, freshness, f"You do not meet: {names(FAIL)}.")
    if outcome == REVIEW:
        return _result(
            "needs_manual_verification", leaves, freshness,
            f"Some requirements need manual verification before eligibility can be "
            f"confirmed: {names(REVIEW)}.",
        )
    if outcome == UNKNOWN:
        if n_unknown * 2 > total:
            return _result(
                "insufficient_information", leaves, freshness,
                f"There is not enough information in your profile to assess this: "
                f"{n_unknown} of {total} requirements could not be checked.",
            )
        return _result(
            "potentially_eligible", leaves, freshness,
            f"No requirement was failed, but these could not be checked yet: "
            f"{names(UNKNOWN)}. Complete your profile to find out.",
        )

    if freshness["is_fresh"]:
        return _result(
            "eligible", leaves, freshness,
            f"You meet all {total} documented requirement(s), and the opportunity "
            f"data was verified {freshness['age_days']} day(s) ago.",
        )
    if freshness["data_quality"] == "verified":
        why = f"its data was last verified {freshness['age_days']} days ago"
    else:
        why = "its data is not currently verified"
    return _result(
        "likely_eligible", leaves, freshness,
        f"You meet all {total} documented requirement(s), but {why}. "
        f"Confirm the details on the official source before applying.",
    )