"""How ready is a profile for matching? Pure Python."""

ALL = ["admission", "scholarship", "internship", "job"]

# (field, label shown to the user, test, categories it helps)
ITEMS = (
    ("country", "Country of residence", lambda p: bool(p["country"]), ALL),
    ("nationality", "Nationality", lambda p: bool(p["nationality"]), ["admission", "scholarship"]),
    ("education_level", "Education level", lambda p: bool(p["education_level"]), ALL),
    ("field_of_study", "Field of study", lambda p: bool(p["field_of_study"]), ALL),
    ("qualifications", "Qualifications and grades", lambda p: p["qualification_count"] > 0,
     ["admission", "scholarship"]),
    ("skills", "Skills", lambda p: p["skill_count"] > 0, ["internship", "job"]),
    ("career_interests", "Career interests", lambda p: p["interest_count"] > 0, ALL),
    ("location_preference", "Preferred locations or remote preference",
     lambda p: p["has_location_preference"], ["admission", "internship", "job"]),
    ("financial_aid", "Whether you need financial aid", lambda p: p["needs_financial_aid"] is not None,
     ["scholarship"]),
)


def assess(p: dict) -> dict:
    missing = [
        {"field": field, "label": label, "helps": helps}
        for field, label, test, helps in ITEMS
        if not test(p)
    ]
    percent = round(100 * (len(ITEMS) - len(missing)) / len(ITEMS))
    return {"percent": percent, "missing": missing}