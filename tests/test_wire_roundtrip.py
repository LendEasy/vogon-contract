"""Every contract type survives the wire, and an older peer's payload decodes."""
from __future__ import annotations

import dataclasses
from typing import Literal, Union, get_args, get_origin, get_type_hints

import vogon_contract as vc
from vogon_contract.codec import (
    end_call_request_from_wire,
    event_from_wire,
    process_events_request_from_wire,
    start_call_request_from_wire,
    to_wire,
)


def _sample(hint):
    """A non-default value of the hinted type, so a round trip has something to lose."""
    origin = get_origin(hint)
    if origin is Union:
        hint = [a for a in get_args(hint) if a is not type(None)][0]
        origin = get_origin(hint)
    if origin is Literal:
        return get_args(hint)[0]
    if origin is list:
        return [_sample(get_args(hint)[0])] if get_args(hint) else ["x"]
    if hint is dict or origin is dict:
        return {"k": "v"}
    if hint is bool:
        return True
    if hint is int:
        return 3
    if hint is float:
        return 0.5
    if dataclasses.is_dataclass(hint):
        return _instance(hint)
    return "x"


def _instance(cls):
    hints = get_type_hints(cls)
    return cls(**{f.name: _sample(hints[f.name]) for f in dataclasses.fields(cls)})


def test_every_transport_event_round_trips():
    for cls in vc.EVENT_TYPES:
        ev = _instance(cls)
        wire = to_wire(ev)
        assert wire["kind"] == cls.kind
        assert event_from_wire(wire) == ev, cls.__name__


def test_a_delivery_outcome_from_an_older_peer_decodes_without_heard_fraction():
    wire = to_wire(vc.DeliveryOutcome(audio_id="a1", status="truncated", heard_text="First sentence."))
    del wire["heard_fraction"]
    ev = event_from_wire(wire)
    assert ev.heard_text == "First sentence." and ev.heard_fraction is None


def test_requests_round_trip():
    req = vc.ProcessEventsRequest(call_id="c1", request_id="c1-1",
                                  events=[vc.UtteranceFinal(text="hi", confidence=0.9, low_conf_words=[]),
                                          vc.SilenceThreshold(threshold_id="alert", silent_for_sec=12.0)])
    assert process_events_request_from_wire(to_wire(req)) == req
    start = vc.StartCallRequest(call_id="c1", mode="text", payload={"k": "v"})
    assert start_call_request_from_wire(to_wire(start)) == start
    end = _instance(vc.EndCallRequest)
    assert end_call_request_from_wire(to_wire(end)) == end


def test_the_public_surface_is_the_contract():
    assert {"SpeakCommand", "DeliveryOutcome", "UtteranceFinal", "StartCallRequest",
            "ProcessEventsRequest", "EndCallRequest", "EVENT_TYPES"} <= set(vc.__all__)
    assert not any(name in vc.__all__ for name in ("Union", "Optional", "ClassVar", "dataclass"))
