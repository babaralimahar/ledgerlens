"""Invoice status state machine. Every transition is checked here and audited by the caller."""

from __future__ import annotations

from typing import Literal

InvoiceStatus = Literal[
    "queued", "processing", "auto_approved", "needs_review", "approved", "rejected", "failed"
]

TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"processing"}),
    # processing -> processing: a crashed attempt restarted after the lock expired
    "processing": frozenset({"processing", "auto_approved", "needs_review", "rejected", "failed"}),
    "auto_approved": frozenset({"needs_review"}),  # reopened by an approver
    "needs_review": frozenset({"approved", "rejected", "needs_review"}),  # needs_review: corrected
    "approved": frozenset(),
    "rejected": frozenset(),
    "failed": frozenset({"queued"}),  # operator requeue
}
TERMINAL_AUTOMATED = frozenset({"auto_approved", "needs_review", "rejected"})


class InvalidTransition(Exception):
    def __init__(self, old: str, new: str) -> None:
        super().__init__(f"invalid invoice status transition {old} -> {new}")
        self.old, self.new = old, new


def assert_transition(old: str, new: str) -> None:
    if new not in TRANSITIONS.get(old, frozenset()):
        raise InvalidTransition(old, new)
