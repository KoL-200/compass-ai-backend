"""Job levels, shared by matching and career paths."""

LADDER = ("entry", "junior", "mid", "senior", "lead", "executive")

# Typical months of experience for each level: (low, high).
LEVEL_MONTHS = {
    "entry": (0, 12),
    "junior": (6, 36),
    "mid": (24, 72),
    "senior": (60, 180),
    "lead": (84, 240),
    "executive": (120, 600),
}


def level_for_months(months: int | None) -> str | None:
    """The highest level whose typical range has started. None when experience is unknown."""
    if months is None:
        return None
    current = None
    for level in LADDER:
        if months >= LEVEL_MONTHS[level][0]:
            current = level
    return current