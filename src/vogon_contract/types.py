"""Turn API contract types — voice-gateway/docs/voice-gateway-phase0.md §4.

The complete interface between the Voice Gateway (stateful audio half) and
the Turn Service (stateless reasoning half). In Phase 0 these cross a
function call; in Phase 1 the same shapes cross the network as JSON — so
everything here must stay JSON-representable (str/float/int/bool/list/dict).

Direction of knowledge (§2): events carry physical facts, gateway → backend;
speak commands carry meaning, backend → gateway. Every event carries `seq`
(gateway-assigned monotonic counter, per call) and `at` (the GATEWAY's
clock — authoritative for physical time, decision 2026-08-25). In-process,
`at`/`onset_at` are raw gateway monotonic values; ON THE WIRE they travel
as call-relative seconds and the receiver rebases them onto its own clock
(contract_codec.shift_event_times) — a clock value never leaves the process
that minted it. The backend never generates physical timestamps of its own,
and ORDER is never derived from time: the ledger orders by entry number.

Pure module: no I/O, no imports from anywhere else in app.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Literal, Optional, Union

# ── CallConfig (§4.1) ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Threshold:
    """One rung of a gateway clock ladder. `id` is an opaque label to the
    gateway; the backend maps it to an action (probe, farewell, log)."""

    id: str
    after_sec: float


@dataclass(frozen=True)
class FillerPolicy:
    """Gateway-side latency masking: play a phrase while a process_events
    request is outstanding. Cadence mirrors the historical handler loop
    (first at 1.1 s, then every 0.7 s).

    phrases: opener-style primaries ("Sure, one moment.") — one is picked at
    random for the first filler. continuations: follow-ups ("Almost there.")
    cycled in shuffled order for subsequent fillers.
    """

    first_after_ms: float
    interval_ms: float
    max: int
    phrases: list[str] = field(default_factory=list)
    continuations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RecordingPolicy:
    """Whether and how this call is recorded — the application decides IF
    (compliance is a domain question), the gateway decides HOW (storage,
    format). Absent policy means the gateway's tenant/deployment default."""

    enabled: bool = True
    channels: str = "stereo"      # stereo = caller L / agent R (today's WAV)


@dataclass(frozen=True)
class BargePolicy:
    """Barge-in semantics the application supplies. `safety_phrases` are the
    always-break-through utterances (compliance vocabulary is DOMAIN
    knowledge — it must never be hardcoded gateway-side).
    `floor_request_phrases` are short demands for the floor ("stop", "let me
    talk") that cut at 0 ms on a normal playout without waiting for the
    judge; unlike a safety phrase they do NOT break through a protected
    intro/farewell. `semantic_judge` gates the LLM barge-verdict call."""

    safety_phrases: list[str] = field(default_factory=list)
    floor_request_phrases: list[str] = field(default_factory=list)
    semantic_judge: bool = True


@dataclass(frozen=True)
class LineDetection:
    """Who-answered detection on the call's FIRST far-end utterance
    (voice-gateway/docs/VOICEMAIL_DETECTION_DESIGN.md). Numbers only: the gateway decides
    `human` / `machine` / `unknown` from the post-AEC transcript and reports
    it as a LineVerdict fact; what to say about it stays turn-side.

    ask_window_s: how long after the opening's playout ends the detector may
    still ask; quiet_ms / cap_s: the hold rule for a `play_after="line_quiet"`
    speak (the recording's end-of-turn plus quiet_ms of silence, or cap_s
    after the verdict, whichever first)."""

    enabled: bool = False
    min_words: int = 4
    machine_p: float = 0.60
    human_p: float = 0.30
    ask_window_s: float = 2.0
    quiet_ms: int = 700
    cap_s: float = 8.0


@dataclass(frozen=True)
class CallConfig:
    """Everything the gateway consumes for one call — returned by start_call.

    Renamed from ``TransportConfig`` (Phase 1, M1 item 1): with three config
    layers in play, each name says whose config it is — GatewayConfig is the
    deployment's, TenantProfile is the tenant's, CallConfig is this call's.

    The gateway never sees the full assistant config; this is the complete
    list of what it is allowed to know.
    """

    persona_voice: str
    persona_name: str = ""    # who the widget says is speaking
    language: str = "en-US"
    # Resolved snake_case ambience values (enabled/room/typing/oneshots plus
    # gains and delays). Resolution against settings defaults happens on the
    # turn side; consumers read values, never defaults. The driver renames
    # six of these to the widget's camelCase keys for session_ready.
    ambience: Optional[dict] = None
    name_hint: Optional[str] = None       # STT keyword boost
    workflow_id: str = ""                 # opaque telemetry label, like agent_id
    silence_thresholds: list[Threshold] = field(default_factory=list)
    duration_thresholds: list[Threshold] = field(default_factory=list)
    # A second silence ladder for the span before the far end has answered
    # the opening (VOICEMAIL_DETECTION_DESIGN.md §12): shorter rungs, so a
    # call nobody answered ends in ~24 s instead of the default ladder's
    # 53 s. `silence_ladder` names the ladder in force at call start
    # ("default" | "opening"); ProcessEventsResponse.silence_ladder switches
    # it. Empty opening list = the default ladder is the only one.
    opening_silence_thresholds: list[Threshold] = field(default_factory=list)
    silence_ladder: str = "default"
    line_detection: Optional[LineDetection] = None
    filler: Optional[FillerPolicy] = None
    recording: Optional[RecordingPolicy] = None
    barge_policy: Optional[BargePolicy] = None
    # M2 failure policy: what the gateway says when the TURN SERVICE is
    # unreachable (retry ladder exhausted) before ending the call. The
    # platform owns the slot and a neutral default (driver-level speech);
    # the domain may override the wording here. Empty = the default.
    trouble_farewell: str = ""


# ── Escalation (warm transfer) — decided 2026-08-25 ──────────────────────────
# The TURN SERVICE composes the order (meaning: who to dial, what the whisper
# SAYS — an LLM summary of the call); the GATEWAY performs it (Twilio REST,
# conference, keypress) and reports what physically happened as a FACT. No
# fetch-back: the order IS the configuration handover.


@dataclass(frozen=True)
class EscalationTarget:
    number: str
    label: str = ""


@dataclass(frozen=True)
class EscalationOrder:
    """Everything the gateway needs to perform the warm transfer. Composed
    turn-side at the moment of the escalated finalize; opaque strings to the
    gateway (it plays them, never interprets them).

    ``escalation_id`` (minted turn-side) joins every later fact about this
    transfer — the initiation outcome and the post-call facts."""

    escalation_id: str = ""
    targets: list[EscalationTarget] = field(default_factory=list)
    whisper_text: str = ""
    agent_timeout_sec: float = 30.0
    hold_message: str = ""
    hold_music_url: str = ""
    voicemail_script: str = ""
    callback_message: str = ""


# ── Speak commands (§4.2) ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class SpeakCommand:
    """Backend → gateway: play this text as this audio_id.

    audio_id is minted by the backend and is the join key for every later
    physical fact about the playout (DeliveryOutcome, PlayoutFinished,
    DrainCleared, HeardHorizon membership).
    """

    audio_id: str
    text: str
    protected: bool = False   # play to completion; barge does not cut
    # Absorb-all barge policy (2026-08-02, the startup path): while this
    # playout runs, every caller-speech verdict except `safety` is ABSORBED —
    # logged, never queued. Used for the opening: a caller talking over the
    # greeting must not become a queued turn answered after it. Only
    # meaningful with protected=True.
    absorb_barge: bool = False
    # WHEN the gateway may start this playout. "line_quiet": hold it until
    # the far end has stopped talking (LineDetection.quiet_ms after its
    # end-of-turn, or LineDetection.cap_s after the line verdict) — the
    # voicemail message must not start over the greeting. "now" = today.
    play_after: Literal["now", "line_quiet"] = "now"
    # Opaque telemetry label: which agent composed this. The gateway tags its
    # own response-latency measurement with it and never interprets it — the
    # alternative was the backend stamping timestamps into gateway memory,
    # which stops meaning anything once the two halves have separate clocks.
    agent_id: str = ""


# ── Event payload fragments (§4.2) ────────────────────────────────────────────


@dataclass(frozen=True)
class HeardHorizon:
    """What had PLAYED when the caller started speaking (snapshot at onset,
    computed by the transport at event-enqueue time from its delivery ledger).

    `completed` is completion-ordered (oldest → newest); exchange resolution
    walks it from the end against the session state's `asks` map.
    `last_heard_text` / `unheard_text` serve horizon-anchored history rewriting
    (docs/full-duplex-turn-model.md §A) — they cannot be derived from the asks
    map alone because non-ask playouts are not in it.
    """

    completed: list[str] = field(default_factory=list)
    in_flight: Optional[str] = None
    in_flight_heard_text: str = ""
    last_heard_text: str = ""
    unheard_text: str = ""


@dataclass(frozen=True)
class AbsorbedUtterance:
    """A recently absorbed barge, carried forward into the next turn's
    annotations. Attached identically to the eager and the final of the
    same turn (no destructive read, no eager/final race)."""

    text: str
    verdict: str
    at: float = 0.0


# ── Transport events (§4.2) ───────────────────────────────────────────────────
# `kind` is the wire tag for Phase 1 JSON serialization; in Phase 0 dispatch
# is by class identity.


@dataclass(frozen=True)
class UtteranceFinal:
    """The committed transcript of one caller turn.

    ``text``, ``confidence`` and ``low_conf_words`` are REQUIRED: every
    producer knows all three at enqueue time (a transport without word
    confidences passes 1.0 / [] explicitly — a conscious statement, not a
    silent default), and the STT low-confidence gate reads them on every
    final. The remaining fields default to zero values because they are
    genuinely absent on some transports (text mode has no audio onset and no
    heard horizon).
    """

    kind: ClassVar[str] = "utterance_final"
    text: str
    confidence: float
    low_conf_words: list[str]
    onset_at: float = 0.0
    heard: HeardHorizon = field(default_factory=HeardHorizon)
    carry_forward: Optional[AbsorbedUtterance] = None
    seq: int = 0
    at: float = 0.0
    playback_generation: int | None = None  # gateway epoch at enqueue, preserved across queue delay


@dataclass(frozen=True)
class UtteranceEager:
    """A speculative EagerEndOfTurn transcript. Same required-field contract
    as ``UtteranceFinal`` — ``confidence`` and ``low_conf_words`` exist here
    so the STT gate's eager suppression (``should_suppress_eager``) can run
    on the typed event; without them a would-gate transcript would start a
    speculative pipeline the final then discards."""

    kind: ClassVar[str] = "utterance_eager"
    text: str
    confidence: float
    low_conf_words: list[str]
    onset_at: float = 0.0
    heard: HeardHorizon = field(default_factory=HeardHorizon)
    carry_forward: Optional[AbsorbedUtterance] = None
    seq: int = 0
    at: float = 0.0
    playback_generation: int | None = None


@dataclass(frozen=True)
class UtteranceResumed:
    kind: ClassVar[str] = "utterance_resumed"
    seq: int = 0
    at: float = 0.0


@dataclass(frozen=True)
class UtteranceAbsorbed:
    """Log-only; never drives a turn.

    ``phase`` is REQUIRED and disambiguates the two moments the transport
    tags an absorbed utterance: ``"eager"`` is the mid-utterance marker (the
    backend skips it — it exists so trigger/fact ordering and the metrics
    anchor behave exactly as an untagged eager would); ``"final"`` is the
    swallow decision, logged once as BARGE_ABSORBED. ``verdict`` and
    ``source`` are REQUIRED — an absorb decision always has both (echo guard,
    heuristic, or LLM judge). ``text`` is the barge-time transcript;
    ``final_text`` (final phase only) is the full final the swallow applied
    to. ``window_ms``/``audio_id`` complete the audit detail the call log
    stores verbatim.
    """

    kind: ClassVar[str] = "utterance_absorbed"
    text: str
    phase: Literal["eager", "final"]
    verdict: str
    source: str
    window_ms: float = 0.0
    audio_id: str = ""
    final_text: str = ""
    onset_at: float = 0.0
    seq: int = 0
    at: float = 0.0


@dataclass(frozen=True)
class DeliveryOutcome:
    """Per finished/failed playout attempt — replaces the synchronous
    DeliveryReport return value across the seam. Carries everything the
    delivery gate needs: ``heard_text`` is NORMATIVE — the gateway fills it
    on every outcome, exactly (the fully-sent sentences) when it paced by
    sentence, else its own proportional estimate — and ``heard_fraction`` is
    the gateway's audio_ms / estimated total, None when unknowable. The
    sentence counts are informational: they mean something only against the
    gateway's own segmentation. The turn side computes neither (2026-10-10:
    it used to re-run the gateway's words-to-time estimate)."""

    kind: ClassVar[str] = "delivery_outcome"
    audio_id: str
    status: Literal["complete", "truncated", "failed"]
    sentences_total: int = 0
    sentences_sent: int = 0
    heard_text: str = ""
    heard_fraction: Optional[float] = None
    first_chunk_ms: Optional[float] = None
    audio_ms: Optional[float] = None
    chunks_sent: int = 0
    mark_pending: bool = False
    seq: int = 0
    at: float = 0.0
    # Gateway-authored speech (the front model / a beat, 2026-09-19): the
    # turn side never composed it, so the outcome carries the INTENDED text
    # and names its origin. The executor writes the SPEAK_EMITTED row from
    # this fact — the only way a line the gateway chose reaches the call log.
    # Empty for every turn-side playout (additive; old peers ignore it).
    text: str = ""
    origin: str = ""          # "" = turn-side speech; "front" = the gateway's own


@dataclass(frozen=True)
class PlayoutFinished:
    """Terminal playback mark echoed; `at` is the completion time."""

    kind: ClassVar[str] = "playout_finished"
    audio_id: str
    seq: int = 0
    at: float = 0.0


@dataclass(frozen=True)
class DrainCleared:
    """Buffered tail cleared by a barge AFTER the playout's DeliveryOutcome
    already reported complete (Twilio drain path)."""

    kind: ClassVar[str] = "drain_cleared"
    audio_id: str
    seq: int = 0
    at: float = 0.0


@dataclass(frozen=True)
class EscalationOutcome:
    """What the gateway physically did with an EscalationOrder before
    teardown — the INITIATION outcome only (hold + first dial): the
    agent-leg lifecycle (answer, keypress, bridge, fallback) resolves via
    webhooks after this call ends. Rides EndCallRequest.events."""

    kind: ClassVar[str] = "escalation_outcome"
    status: Literal["initiated", "no_targets", "no_provider_call", "failed"]
    target: str = ""            # label/number of the first dialled target
    detail: str = ""
    seq: int = 0
    at: float = 0.0
    escalation_id: str = ""


# ── Post-call facts: a transfer's record (POST /record_transfer) ─────────────
# A warm transfer resolves AFTER end_call: the agent legs ring, answer and
# bridge while the caller is in the hold conference, long after the media
# stream and the session are gone. These facts reach the turn side on their
# own request, which it accepts only once the call is finalized, and it
# appends them to the call's ledger after CALL_ENDED. They never ride
# end_call (an older turn service would refuse an unknown kind there).
# Times are WALL clock (``at_utc``): there is no live call epoch to rebase on.


@dataclass(frozen=True)
class EscalationLegEvent:
    """A FACT about one agent leg of a transfer."""

    kind: ClassVar[str] = "escalation_leg"
    escalation_id: str
    seq: int                    # per-transfer, gap-free from 1: the row order
    event: Literal[
        "dialled",              # the leg was placed (ringing)
        "dial_failed",          # Twilio refused the dial
        "answered",             # the agent picked up and is hearing the whisper
        "connected",            # the agent pressed a key: bridged to the caller
        "no_answer", "busy", "failed", "canceled",   # the leg's final status
        "no_keypress",          # answered, never pressed a key (or a voicemail took the whisper)
        "voicemail_left",       # fallback: the voicemail call to the last target was placed
        "voicemail_failed",     # fallback: that call could not be placed
    ]
    target: str = ""            # the target's label
    attempt: int = 0            # 0-based index into the order's targets
    agent_call_sid: str = ""
    detail: str = ""
    at_utc: float = 0.0


@dataclass(frozen=True)
class EscalationResolved:
    """A FACT: how the transfer ended — exactly one per transfer that got
    past the hold. ``connected`` resolves when the bridged call ends, so
    ``talk_sec`` is known."""

    kind: ClassVar[str] = "escalation_resolved"
    escalation_id: str
    seq: int
    result: Literal[
        "connected",            # an agent took the call
        "fallback_callback",    # nobody did: voicemail left, the caller told
        "caller_abandoned",     # the caller hung up on hold
        "failed",               # no target could be dialled
    ]
    connected_target: str = ""
    attempts: int = 0           # agent legs placed
    hold_sec: float = 0.0       # hold start → bridge (or → the end, unbridged)
    talk_sec: float = 0.0       # bridge → hang-up, when connected
    detail: str = ""
    at_utc: float = 0.0


PostCallFact = Union[EscalationLegEvent, EscalationResolved]  # noqa: UP007 — contract_codec matches typing.Union

POST_CALL_FACT_TYPES: tuple[type, ...] = PostCallFact.__args__  # type: ignore[attr-defined]


@dataclass(frozen=True)
class LineVerdict:
    """A FACT: who answered the line, decided gateway-side on the call's
    first far-end utterance (VOICEMAIL_DETECTION_DESIGN.md §5). Spooled like
    DeliveryOutcome; the turn side records it and, on the trigger it rides
    with, reads it as the ``answered_by`` annotation. `source` names the
    instrument ("jev" | "amd" | "tone" | "none"); `transcript` is what the
    verdict was formed on. Exactly one per call."""

    kind: ClassVar[str] = "line_verdict"
    answered_by: Literal["human", "machine", "unknown"]
    probability: float
    source: str
    transcript: str = ""
    latency_ms: float = 0.0
    seq: int = 0
    at: float = 0.0


@dataclass(frozen=True)
class SilenceThreshold:
    kind: ClassVar[str] = "silence_threshold"
    threshold_id: str
    silent_for_sec: float
    seq: int = 0
    at: float = 0.0


@dataclass(frozen=True)
class DurationThreshold:
    kind: ClassVar[str] = "duration_threshold"
    threshold_id: str
    elapsed_sec: float
    seq: int = 0
    at: float = 0.0


TransportEvent = Union[  # noqa: UP007 — contract_codec matches typing.Union (get_origin is Union)
    UtteranceFinal,
    UtteranceEager,
    UtteranceResumed,
    UtteranceAbsorbed,
    DeliveryOutcome,
    PlayoutFinished,
    DrainCleared,
    SilenceThreshold,
    DurationThreshold,
    EscalationOutcome,
    LineVerdict,
]

EVENT_TYPES: tuple[type, ...] = TransportEvent.__args__  # type: ignore[attr-defined]


# ── Turn API requests / responses (§4) ────────────────────────────────────────


@dataclass(frozen=True)
class RecordTransferRequest:
    """Gateway → turn service, after the call: the warm transfer's facts,
    which resolve once the session is gone (the only thing that outlives a
    call today; anything else would get its own verb). Idempotent — a fact
    is keyed by (escalation_id, seq), so a retried request appends nothing
    twice. The turn service answers 409 while the call is not yet finalized;
    the gateway retries."""

    call_id: str
    request_id: str
    events: list[PostCallFact] = field(default_factory=list)


@dataclass(frozen=True)
class RecordTransferResponse:
    appended: int = 0           # facts newly written (0 on a full retry)


@dataclass(frozen=True)
class StartCallRequest:
    """`payload` is the application's opaque call context, captured at call
    creation and passed through VERBATIM — the gateway never reads it. The
    application's identities (for codelexin: owner_id, workflow_id) live
    inside it; they are no longer contract fields, because they were the
    application's nouns, not the gateway's."""

    call_id: str
    mode: Literal["twilio", "widget", "text"]
    resume: bool = False
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class StartCallResponse:
    call_config: CallConfig
    speak: list[SpeakCommand] = field(default_factory=list)  # the opening
    state_version: int = 0
    client_events: list[ClientEvent] = field(default_factory=list)


@dataclass(frozen=True)
class ProcessEventsRequest:
    call_id: str
    request_id: str                        # gateway-minted, unique per request
    events: list[TransportEvent] = field(default_factory=list)  # occurrence order


@dataclass(frozen=True)
class ClientEvent:
    """A widget/text-mode frame the turn produced, delivered after the turn.

    Decision 2026-07-31: these are end-of-turn state changes (a field was
    collected, a booking confirmed, an error occurred), so the turn returns
    them rather than reaching into the gateway mid-turn. Progress indicators
    are NOT in this list — `server.thinking` drives the widget's typing
    animation and is emitted by the gateway when it dispatches, because
    arriving with the answer would defeat it.
    """

    type: str
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ProcessEventsResponse:
    speak: list[SpeakCommand] = field(default_factory=list)
    action: Literal["continue", "end_call"] = "continue"
    end_reason: Optional[str] = None
    client_events: list[ClientEvent] = field(default_factory=list)
    # Present when end_reason == "escalated": the gateway plays the speaks,
    # PERFORMS this order, then calls end_call carrying the initiation
    # outcome as a fact (the later agent-leg lifecycle resolves via the
    # gateway's escalation callbacks, after this call's ledger closes).
    escalation: Optional[EscalationOrder] = None
    # The reasoning side asked for quiet: the caller requested a hold and the
    # agent acknowledged it, so the silence ladder must not probe for this
    # many seconds after this turn's audio ends. Re-granted by the next hold;
    # cleared by caller activity. None = the ladder runs as configured.
    silence_grace_sec: Optional[float] = None
    # Switch the gateway's silence ladder ("default" | "opening",
    # CallConfig.silence_ladder): the turn side says when the far end has
    # answered the opening and the short pre-answer ladder no longer applies.
    silence_ladder: Optional[str] = None


@dataclass(frozen=True)
class EndCallRequest:
    call_id: str
    reason: str          # hangup | silence_exit | max_duration | error | backend_directed
    # Physical facts spooled after the last trigger — no later request will
    # carry them (M1 item 4: the spool is gateway custody, and this is its
    # final flush). Processed before finalize so their audit entries land
    # in the ledger ahead of CALL_ENDED.
    events: list[TransportEvent] = field(default_factory=list)
    # The gateway's analytics rows, as a LIST — per-call counters from the
    # transport plus the driver's per-playout latency rows (tts_first_chunk,
    # response_latency), which can repeat per turn and so cannot live in a
    # dict keyed by name. Row shape: {event_name, stage, latency_ms, metadata}.
    transport_analytics: list = field(default_factory=list)
    # The telephony provider's own id for this call (Twilio CallSid) — physical
    # call metadata the gateway holds; empty for widget/text calls. The turn
    # side needs it for call-end side effects that manipulate the phone call
    # itself (the escalation warm transfer).
    provider_call_id: str = ""


@dataclass(frozen=True)
class EndCallResponse:
    pass


# The public surface: every type and union declared here (not the typing
# helpers imported above), so ``from vogon_contract import *`` is exactly the
# contract.
__all__ = sorted(
    name for name, value in list(globals().items())
    if not name.startswith("_") and (
        getattr(value, "__module__", None) == __name__
        or name in {"TransportEvent", "PostCallFact", "EVENT_TYPES", "POST_CALL_FACT_TYPES"}))
