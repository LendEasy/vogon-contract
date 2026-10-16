"""JSON wire codec for the Turn API contract — Phase 1 (M1 item 1).

``to_wire`` / ``from_wire`` turn every ``contract.py`` dataclass into plain
JSON-safe dicts and back, so the same shapes that cross a function call in
Phase 0 cross HTTP in Phase 1 with no semantic change. Events are tagged by
their ``kind`` ClassVar on the wire (dataclasses.asdict does not emit
ClassVars, so ``to_wire`` adds it and ``from_wire`` dispatches on it).

Decoding is driven by the dataclass type hints — one recursive walker, no
per-type hand-written constructors to drift out of sync with the types.
Unknown wire fields are IGNORED (a newer peer may send fields an older peer
does not know; the contract is extend-only), and absent optional fields take
the dataclass defaults. A missing REQUIRED field raises — that is a real
contract violation, not a compatibility case.

Pure module: stdlib only, imports nothing from app except contract.
"""
from __future__ import annotations

import dataclasses
import typing
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints

from vogon_contract import types as _contract
from vogon_contract.types import (
    EVENT_TYPES,
    POST_CALL_FACT_TYPES,
    EndCallRequest,
    EndCallResponse,
    PostCallFact,
    ProcessEventsRequest,
    ProcessEventsResponse,
    RecordTransferRequest,
    RecordTransferResponse,
    StartCallRequest,
    StartCallResponse,
    TransportEvent,
)

_EVENT_BY_KIND: dict[str, type] = {cls.kind: cls for cls in EVENT_TYPES}
_POST_CALL_FACT_BY_KIND: dict[str, type] = {
    cls.kind: cls for cls in POST_CALL_FACT_TYPES}

# get_type_hints resolved once per class (they never change at runtime).
_HINTS_CACHE: dict[type, dict[str, Any]] = {}


def _hints(cls: type) -> dict[str, Any]:
    if cls not in _HINTS_CACHE:
        _HINTS_CACHE[cls] = get_type_hints(cls, vars(_contract))
    return _HINTS_CACHE[cls]


# ── encode ────────────────────────────────────────────────────────────────────


def to_wire(obj: Any) -> Any:
    """Contract object → JSON-safe value. Events gain their ``kind`` tag;
    nested dataclasses, lists and dicts are converted recursively."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        out = {f.name: to_wire(getattr(obj, f.name))
               for f in dataclasses.fields(obj)}
        kind = getattr(type(obj), "kind", None)
        if isinstance(kind, str):
            out["kind"] = kind
        return out
    if isinstance(obj, list):
        return [to_wire(v) for v in obj]
    if isinstance(obj, dict):
        return {k: to_wire(v) for k, v in obj.items()}
    return obj


# ── decode ────────────────────────────────────────────────────────────────────


def _decode(hint: Any, value: Any) -> Any:
    """Reconstruct ``value`` (a JSON-decoded primitive/list/dict) as ``hint``."""
    # A forward reference inside a generic (list["ClientEvent"]) survives
    # get_type_hints as a bare string on 3.10 — resolve it against contract.
    if isinstance(hint, str | typing.ForwardRef):
        name = hint if isinstance(hint, str) else hint.__forward_arg__
        hint = getattr(_contract, name)
    origin = get_origin(hint)

    if hint is Any or hint is None or value is None:
        return value
    if origin is Union:  # Optional[X] and the TransportEvent union
        args = [a for a in get_args(hint) if a is not type(None)]
        if len(args) == 1:
            return _decode(args[0], value)
        if set(args) == set(get_args(TransportEvent)):
            return event_from_wire(value)
        if set(args) == set(get_args(PostCallFact)):
            return post_call_fact_from_wire(value)
        raise TypeError(f"undecodable union in contract hints: {hint}")
    if origin is Literal:
        return value
    if origin in (list, list):
        (item_hint,) = get_args(hint) or (Any,)
        return [_decode(item_hint, v) for v in value]
    if origin in (dict, dict) or hint is dict:
        return value
    if isinstance(hint, type) and dataclasses.is_dataclass(hint):
        return _decode_dataclass(hint, value)
    return value  # str / int / float / bool


def _decode_dataclass(cls: type, data: dict) -> Any:
    hints = _hints(cls)
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name in data:
            kwargs[f.name] = _decode(hints[f.name], data[f.name])
        # absent + has default → dataclass default; absent + required →
        # cls(**kwargs) raises TypeError, which is the contract violation.
    return cls(**kwargs)


def event_from_wire(data: dict) -> TransportEvent:
    """Wire dict → typed transport event, dispatched on ``kind``."""
    kind = data.get("kind")
    cls = _EVENT_BY_KIND.get(kind)  # type: ignore[arg-type]
    if cls is None:
        raise ValueError(f"unknown transport event kind: {kind!r}")
    return _decode_dataclass(cls, data)


def post_call_fact_from_wire(data: dict) -> PostCallFact:
    """Wire dict → typed post-call fact, dispatched on ``kind``."""
    kind = data.get("kind")
    cls = _POST_CALL_FACT_BY_KIND.get(kind)  # type: ignore[arg-type]
    if cls is None:
        raise ValueError(f"unknown post-call fact kind: {kind!r}")
    return _decode_dataclass(cls, data)


def from_wire(cls: type, data: dict) -> Any:
    """Wire dict → the given contract type (requests, responses, configs)."""
    return _decode_dataclass(cls, data)


# Named decoders for the five wire messages, so call sites read as protocol.
def start_call_request_from_wire(d: dict) -> StartCallRequest:
    return from_wire(StartCallRequest, d)


def start_call_response_from_wire(d: dict) -> StartCallResponse:
    return from_wire(StartCallResponse, d)


def process_events_request_from_wire(d: dict) -> ProcessEventsRequest:
    return from_wire(ProcessEventsRequest, d)


def process_events_response_from_wire(d: dict) -> ProcessEventsResponse:
    return from_wire(ProcessEventsResponse, d)


def end_call_request_from_wire(d: dict) -> EndCallRequest:
    return from_wire(EndCallRequest, d)


def end_call_response_from_wire(d: dict) -> EndCallResponse:
    return from_wire(EndCallResponse, d)


def record_transfer_request_from_wire(d: dict) -> RecordTransferRequest:
    return from_wire(RecordTransferRequest, d)


def record_transfer_response_from_wire(d: dict) -> RecordTransferResponse:
    return from_wire(RecordTransferResponse, d)


# ── Clock rebase (M1 item 7, plan §3.4) ───────────────────────────────────────
# The GATEWAY's clock is authoritative for physical events (decision
# 2026-08-25). On the wire, event times travel as CALL-RELATIVE seconds: the
# sender shifts by −its_epoch, the receiver by +its_epoch — so a clock value
# never leaves the process that minted it, and every difference between two
# event times is exact in both domains. 0.0 is the "absent" sentinel on the
# timestamp fields and is never shifted.
#
# Anchoring assumption: each side mints its epoch at the same call moment —
# the gateway just before sending start_call, the turn service on receiving
# it — so the two origins differ by one request latency (ms). Every rebased
# absolute time is off by that constant; every DELTA is exact.

_SHIFTED_FIELDS = ("at", "onset_at")


def shift_event_times(events, offset: float):
    """Return the events with at/onset_at/carry_forward.at shifted by
    ``offset`` (non-zero values only — 0.0 means absent). Pure; the inputs
    are frozen dataclasses and come back as new instances."""
    if not offset:
        return list(events)
    out = []
    for ev in events:
        changes = {}
        for name in _SHIFTED_FIELDS:
            value = getattr(ev, name, 0.0)
            if value:
                changes[name] = value + offset
        cf = getattr(ev, "carry_forward", None)
        if cf is not None and cf.at:
            changes["carry_forward"] = dataclasses.replace(cf, at=cf.at + offset)
        out.append(dataclasses.replace(ev, **changes) if changes else ev)
    return out
