"""le-vogon-contract — the Turn API: the wire protocol between a vogon voice
gateway and the turn service it drives.

The gateway is the client. It opens a call (``StartCallRequest``), posts what
physically happened on the line as typed events (``ProcessEventsRequest`` —
utterances, delivery outcomes, clock thresholds, line verdicts) and performs
the typed response (``SpeakCommand``s and an end-call action). The turn
service is the server: it implements ``/turn/v1`` and returns the responses.
Both sides depend on this package and on nothing else of each other.

``vogon_contract.types`` holds the dataclasses; ``vogon_contract.codec`` the
JSON wire encoding (``to_wire`` / ``*_from_wire``) and the clock shift. Every
type is re-exported here.

Compatibility rule: fields are only ever ADDED, with defaults, so an older
peer ignores what it does not know (``DeliveryOutcome.heard_fraction``,
2026-10-10, is the pattern). ``heard_text`` on a delivery outcome is
normative; the sentence counts are informational.
"""
from vogon_contract.types import *  # noqa: F403 — the contract IS the public surface
from vogon_contract.types import __all__ as _types_all

__version__ = "0.1.0"
__all__ = list(_types_all)
