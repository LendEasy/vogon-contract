# le-vogon-contract

The Turn API: the wire protocol between a [vogon](https://github.com/LendEasy/vogon)
voice gateway and the turn service it drives. Pure Python dataclasses and a JSON
codec, no dependencies.

```bash
pip install le-vogon-contract
```

The distribution is `le-vogon-contract`; the import is `vogon_contract`.

## Who speaks it

- **The gateway is the client.** It owns the audio: the phone or browser socket,
  speech-to-text, text-to-speech, barge-in, the silence clock, recording. It opens a
  call with `StartCallRequest`, posts what physically happened as typed events in a
  `ProcessEventsRequest` (`UtteranceFinal`, `UtteranceEager`, `DeliveryOutcome`,
  `SilenceThreshold`, `LineVerdict`, ...) and performs the typed response: each
  `SpeakCommand`, then the end-call action.
- **The turn service is the server.** It implements `POST /turn/v1/start_call`,
  `/process_events`, `/record_transfer` and `/end_call`, decides what to say, and
  returns `SpeakCommand`s. It needs to know nothing about the gateway beyond these
  types.

## Guarantees the gateway gives

- Events for one call are delivered in order, one outstanding request per call.
- Every request carries a `request_id`; a retried request returns the committed
  response rather than reprocessing the turn.
- `DeliveryOutcome.heard_text` is normative: what the caller heard, exact when the
  gateway paced by sentence, otherwise the gateway's own estimate. The sentence counts
  are informational.
- An eager end of turn (`UtteranceEager`) may be followed by a final with the same or a
  different text, or by `UtteranceResumed`. Speculating on the eager is the turn
  service's choice; the gateway never depends on it.

## Compatibility

Fields are only ever added, with defaults. A peer on an older version ignores fields
it does not know. Breaking changes bump the major version.

## Usage

```python
from vogon_contract import ProcessEventsRequest, SpeakCommand, UtteranceFinal
from vogon_contract.codec import process_events_request_from_wire, to_wire
```

Licensed under the Apache License, Version 2.0.
