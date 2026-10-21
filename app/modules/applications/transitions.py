STATUSES = (
    "interested", "preparing", "ready_to_apply", "submitted", "under_review",
    "interview_assessment", "offer_received", "rejected", "withdrawn", "expired", "completed",
)

PRE_SUBMISSION = frozenset({"interested", "preparing", "ready_to_apply"})
INACTIVE = frozenset({"rejected", "withdrawn", "expired", "completed"})
USER_CANNOT_SET = frozenset({"expired"})  # set by the system when a deadline passes

_PRE = {"interested", "preparing", "ready_to_apply"}

TRANSITIONS: dict[str, frozenset[str]] = {
    # Before submitting, users can move freely between preparation stages.
    "interested": frozenset(_PRE - {"interested"} | {"submitted", "withdrawn"}),
    "preparing": frozenset(_PRE - {"preparing"} | {"submitted", "withdrawn"}),
    "ready_to_apply": frozenset(_PRE - {"ready_to_apply"} | {"submitted", "withdrawn"}),
    # After submitting, the process only moves forward.
    "submitted": frozenset({"under_review", "interview_assessment", "offer_received", "rejected", "withdrawn"}),
    "under_review": frozenset({"interview_assessment", "offer_received", "rejected", "withdrawn"}),
    "interview_assessment": frozenset({"offer_received", "rejected", "withdrawn"}),
    "offer_received": frozenset({"completed", "rejected", "withdrawn"}),
    # Final states.
    "completed": frozenset(),
    "rejected": frozenset(),
    "withdrawn": frozenset(),
    # Only reachable again if the deadline was extended (checked in the service).
    "expired": frozenset({"interested", "preparing"}),
}