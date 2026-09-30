"""Live-session lifecycle around the existing Engine. SIMULATION / REPLAY ONLY.

Frames are consumed only when their video time has elapsed on the session
clock, so no future observation can be emitted. Each due frame goes to the
recorder first and then to the observation provider, so analysis failures or
slowness never remove frames from the recording ledger. Frames are never
skipped before the provider: if analysis is slower than real time, results age
and are marked stale instead. Not thread-safe; drive all methods from one
worker at a time.
"""
import time as _time
import uuid

from ..engine import Engine, INDICATORS
from .providers import ProviderError
from .recorder import FrameLedgerRecorder, RecorderError
from .source import SourceDisconnected, SourceEnded

SCHEMA_VERSION = 1
# Absorbs float error in clock subtraction (e.g. 100.2 - 100.0 < 0.2); far below one frame.
_DUE_TOLERANCE_S = 1e-6
TERMINAL_STATES = ("completed", "failed")
TRANSITIONS = {
    # Leaving before the session runs is a stop too, so abandoned sessions always end.
    "created": ("previewing", "stopping"),
    "previewing": ("target_selected", "stopping"),
    "target_selected": ("running", "stopping"),
    "running": ("paused", "stopping"),
    "paused": ("running", "stopping"),
    "stopping": ("completed",),
    "completed": (),
    "failed": (),
}


class InvalidTransition(Exception):
    """A lifecycle method was called from a state that does not allow it."""


def _percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))]


class LiveSession:
    def __init__(self, source, provider, recorder=None, clock=_time.monotonic, session_id=None,
                 engine_config=None, stale_after_s=1.0, performance_interval_s=1.0):
        self.source = source
        self.provider = provider
        self.recorder = recorder or FrameLedgerRecorder()
        self.clock = clock
        self.session_id = session_id or "session-" + uuid.uuid4().hex[:12]
        self.engine = Engine(engine_config)
        self.stale_after_s = stale_after_s
        self.performance_interval_s = performance_interval_s
        self.state = "created"
        self.termination_reason = None
        self.recording_artifact = None
        self.provider_errors = 0
        self.stale_observations = 0
        self.last_video_time = None
        self._events = []
        self._sequence = 0
        self._pending = None
        self._elapsed = 0.0
        self._resumed_at = None
        self._identity = None
        self._cleaned_up = False
        self._window = {"frames": 0, "provider_ms": [], "max_age_s": 0.0, "stale": 0}
        self._window_start = None
        self._emit("session_state", state="created", source=source.kind, recording=self.recorder.kind)

    def snapshot(self) -> dict:
        """JSON-compatible summary of the current session state."""
        return {"session_id": self.session_id, "state": self.state,
                "provider": self.provider.kind, "source": self.source.kind,
                "recording": self.recorder.kind, "recorder": self.recorder.status(),
                "activity": self.engine.activity, "elapsed_s": self.elapsed(),
                "last_video_time": self.last_video_time,
                "termination_reason": self.termination_reason,
                "provider_errors": self.provider_errors,
                "stale_observations": self.stale_observations,
                "last_sequence": self._sequence}

    # Events -----------------------------------------------------------------

    def _emit(self, event_type, **payload):
        self._sequence += 1
        self._events.append({"schema_version": SCHEMA_VERSION, "event_type": event_type,
                             "session_id": self.session_id, "sequence": self._sequence,
                             "provider": self.provider.kind,
                             "monotonic_ms": self.clock() * 1000, **payload})

    def drain_events(self) -> list:
        """Return and clear pending events in sequence order."""
        events, self._events = self._events, []
        return events

    # Lifecycle --------------------------------------------------------------

    def _check(self, new_state):
        if new_state not in TRANSITIONS[self.state]:
            raise InvalidTransition(f"Cannot move from {self.state} to {new_state}")

    def _transition(self, new_state, **payload):
        self._check(new_state)
        self.state = new_state
        self._emit("session_state", state=new_state, **payload)

    def open(self) -> None:
        self._check("previewing")
        try:
            self.source.open()
        except Exception as exc:
            self._fail("source_disconnected", str(exc))
            return
        self._transition("previewing")

    def select_target(self) -> None:
        """Record the therapist's explicit target selection before the session starts."""
        self._transition("target_selected")

    def start(self) -> None:
        self._check("running")
        try:
            self.recorder.start()
        except RecorderError as exc:
            self._fail("recorder_failed", str(exc))
            return
        self._transition("running")
        self._emit("recording_status", **self.recorder.status())
        self._resumed_at = self.clock()

    def pause(self) -> None:
        # While paused no frame becomes due, so nothing is analysed or added to the ledger.
        # Whether a real recorder keeps capturing during pause is an open privacy decision.
        self._check("paused")
        self._elapsed = self.elapsed()
        self._resumed_at = None
        self._transition("paused")

    def resume(self) -> None:
        self._transition("running")
        self._resumed_at = self.clock()

    def stop(self, reason="user_stop") -> None:
        self._check("stopping")
        self._elapsed = self.elapsed()
        self._resumed_at = None
        self.termination_reason = reason
        self._transition("stopping", reason=reason)
        if self.recorder.state == "recording":
            try:
                self.recording_artifact = self.recorder.finalize()
            except RecorderError as exc:
                self._fail("recorder_failed", str(exc))
                return
        self._cleanup()
        # A ledger proves which frames reached the recorder; it is not a recording.
        self._transition("completed", reason=reason, simulation=True, recorded=False,
                         recording=self.recording_artifact)

    def set_activity(self, activity: str) -> None:
        """Therapist-declared activity context; closes open episodes via the Engine."""
        if self.state not in ("target_selected", "running", "paused"):
            raise InvalidTransition(f"Cannot change activity while {self.state}")
        self.engine.set_activity(activity)
        self._emit("activity", activity=self.engine.activity, video_time=self.last_video_time)

    def _fail(self, reason, detail):
        if self.state in TERMINAL_STATES:
            return
        self.termination_reason = reason
        self._resumed_at = None
        self.recorder.abort()
        self._emit("error", code=reason, detail=detail)
        self.state = "failed"
        self._emit("session_state", state="failed", reason=reason, simulation=True, recorded=False,
                   recording=None)
        self._cleanup()

    def _cleanup(self):
        if self._cleaned_up:
            return
        self._cleaned_up = True
        self._pending = None
        try:
            self.source.close()
        except Exception:
            pass

    def elapsed(self) -> float:
        """Session seconds spent running; pauses do not advance it."""
        running = 0.0 if self._resumed_at is None else self.clock() - self._resumed_at
        return self._elapsed + running

    # Processing -------------------------------------------------------------

    def tick(self, max_frames: int | None = None) -> int:
        """Process frames whose video time has elapsed (at most ``max_frames``). Returns frames processed."""
        if self.state != "running":
            return 0
        now = self.elapsed()
        processed = 0
        while self.state == "running" and (max_frames is None or processed < max_frames):
            if self._pending is None:
                try:
                    self._pending = self.source.read()
                except SourceEnded:
                    self.stop("source_eof")
                    break
                except SourceDisconnected as exc:
                    self._fail("source_disconnected", str(exc))
                    break
            if self._pending.video_time > now + _DUE_TOLERANCE_S:
                break
            # A file-as-live frame "arrives" when it becomes due, not when it is read ahead.
            frame, captured_ms = self._pending, self.clock() * 1000
            self._pending = None
            try:
                self.recorder.write(frame)
            except RecorderError as exc:
                self._fail("recorder_failed", str(exc))
                break
            self._process(frame, captured_ms)
            processed += 1
        return processed

    def _process(self, frame, captured_ms):
        started = self.clock()
        try:
            rows = self.provider.observe(frame)
            accepted = []
            for row in rows:
                if row["time"] > frame.video_time + _DUE_TOLERANCE_S:
                    raise ValueError("provider returned an observation from the future")
                if row["identity"] == "uncertain" and any(
                        row["signals"].get(key) is not None for key in INDICATORS):
                    raise ValueError("uncertain identity must carry five null signals")
                accepted.append((row, self.engine.update(row)))
        except ProviderError as exc:
            # No observation is fabricated for a failed frame; the engine sees a gap.
            self.provider_errors += 1
            self._emit("error", code="provider_failed", detail=str(exc),
                       source_frame_index=frame.index, video_time=frame.video_time)
            self._record_performance(frame, started, ages=())
            return
        except Exception as exc:
            self._fail("worker_failed", str(exc))
            return
        processed_ms = self.clock() * 1000
        ages = []
        for row, snapshot in accepted:
            age_s = max(0.0, self.elapsed() - row["time"])
            stale = age_s > self.stale_after_s
            self.stale_observations += stale
            ages.append((age_s, stale))
            self.last_video_time = row["time"]
            if row["identity"] != self._identity:
                self._identity = row["identity"]
                self._emit("identity_state", identity=self._identity, video_time=row["time"])
            self._emit("observation", source_frame_index=frame.index, video_time=row["time"],
                       captured_monotonic_ms=captured_ms, processed_monotonic_ms=processed_ms,
                       age_s=age_s, stale=stale, observation=row)
            self._emit("engine_state", video_time=row["time"], stale=stale, state=snapshot)
        self._record_performance(frame, started, ages)

    def _record_performance(self, frame, started, ages):
        window = self._window
        window["frames"] += 1
        window["provider_ms"].append((self.clock() - started) * 1000)
        for age_s, stale in ages:
            window["max_age_s"] = max(window["max_age_s"], age_s)
            window["stale"] += stale
        if self._window_start is None:
            self._window_start = frame.video_time
        if frame.video_time - self._window_start + _DUE_TOLERANCE_S < self.performance_interval_s:
            return
        costs = window["provider_ms"]
        self._emit("performance", video_time=frame.video_time, frames=window["frames"],
                   provider_ms_p50=_percentile(costs, 0.5), provider_ms_p95=_percentile(costs, 0.95),
                   provider_ms_max=max(costs), max_age_s=window["max_age_s"],
                   stale_observations=window["stale"], recorder=self.recorder.status())
        self._window = {"frames": 0, "provider_ms": [], "max_age_s": 0.0, "stale": 0}
        self._window_start = None
