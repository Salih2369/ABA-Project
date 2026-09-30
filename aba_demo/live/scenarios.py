"""Named scenarios for the live-session shell.

Synthetic scenarios script identity and the five signals over video time to
exercise the interface and failure paths; they are not recordings, model
output, or examples of real child behavior. ``ReplayScenario`` replays an
existing precomputed export against its matching local video (file-as-live).
"""
from dataclasses import dataclass, field
from pathlib import Path

from ..engine import INDICATORS
from ..export import file_sha256
from .providers import PrecomputedProvider, SyntheticProvider
from .recorder import FrameLedgerRecorder
from .source import FileAsLiveSource, SyntheticFrameSource

FPS = 5
QUIET = dict.fromkeys(INDICATORS, False)


def _with(**signals):
    return dict(QUIET, **signals)


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    summary: str
    duration_s: int
    script: tuple
    exercises: tuple
    fail_frames: tuple = field(default_factory=tuple)
    disconnect_at: int | None = None
    cost_s: float = 0.0
    recorder_fail_at: int | None = None
    kind: str = "synthetic"

    def build(self):
        source = SyntheticFrameSource(FPS, self.duration_s * FPS, disconnect_at=self.disconnect_at)
        provider = SyntheticProvider(list(self.script), cost_s=self.cost_s,
                                     fail_times=tuple(index / FPS for index in self.fail_frames))
        return source, provider, FrameLedgerRecorder(fail_at_frame=self.recorder_fail_at)

    def describe(self) -> dict:
        return {"id": self.id, "title": self.title, "summary": self.summary, "kind": self.kind,
                "duration_s": self.duration_s, "fps": FPS, "exercises": list(self.exercises)}


class ReplayScenario:
    """File-as-live replay of an existing precomputed export. PRECOMPUTED, not live inference."""
    id = "precomputed-replay"
    kind = "precomputed"

    def __init__(self, video, observations):
        self.video = Path(video)
        self.observations = Path(observations)
        digest = file_sha256(self.video)
        # Validate once at startup so a mismatched pair fails fast.
        provider = PrecomputedProvider.from_file(self.observations, video_sha256=digest)
        self._digest = digest
        self._source_info = provider.source
        self.title = "Precomputed replay"

    def build(self):
        provider = PrecomputedProvider.from_file(self.observations, video_sha256=self._digest)
        return FileAsLiveSource(self.video), provider, FrameLedgerRecorder()

    def describe(self) -> dict:
        duration = self._source_info.get("duration")
        return {"id": self.id, "title": self.title, "kind": self.kind,
                "summary": (f"Replays precomputed rows for {self.video.name} in real time against the "
                            "matching video (SHA-256 verified). Earlier analysis, not live inference."),
                "duration_s": round(duration) if isinstance(duration, (int, float)) else None,
                "fps": self._source_info.get("fps"),
                "exercises": ["file-as-live source", "precomputed provider", "source hash binding"]}


SCENARIOS = {scenario.id: scenario for scenario in (
    Scenario(
        id="table-routine",
        title="Table routine",
        summary="Mostly settled table work with a sustained orientation change and a short burst of body motion.",
        duration_s=60,
        script=(
            (0, "confirmed", QUIET),
            (8, "confirmed", _with(orientation=True)),
            (13, "confirmed", QUIET),
            (24, "confirmed", _with(body_motion=True)),
            (28, "confirmed", QUIET),
            (38, "confirmed", _with(hand_motion=True)),
            (42, "confirmed", QUIET),
        ),
        exercises=("persistence", "candidate alerts", "cooldown", "opt-in hand motion"),
    ),
    Scenario(
        id="leaves-seat",
        title="Leaves seat",
        summary="Target stands and leaves the seat area; body and posture cards are suppressed while out of seat.",
        duration_s=45,
        script=(
            (0, "confirmed", QUIET),
            (6, "confirmed", _with(posture_change=True)),
            (6.2, "confirmed", _with(out_of_seat=True, body_motion=True)),
            (10, "confirmed", _with(out_of_seat=True)),
            (20, "confirmed", _with(posture_change=True)),
            (20.2, "confirmed", QUIET),
        ),
        exercises=("alert priority", "out-of-seat suppression", "posture pulses"),
    ),
    Scenario(
        id="brief-occlusion",
        title="Brief occlusion",
        summary="The target is hidden for several seconds. Every indicator becomes not observable, never absent.",
        duration_s=40,
        script=(
            (0, "confirmed", QUIET),
            (5, "confirmed", _with(body_motion=True)),
            (9, "uncertain", QUIET),
            (16, "confirmed", QUIET),
            (24, "confirmed", _with(orientation=True)),
            (29, "confirmed", QUIET),
        ),
        exercises=("identity uncertainty", "signal suppression", "episode closure"),
    ),
    Scenario(
        id="partial-visibility",
        title="Partial visibility",
        summary="Legs and face drop out of view at times, so some indicators are unobservable while others continue.",
        duration_s=40,
        script=(
            (0, "confirmed", QUIET),
            (6, "confirmed", _with(out_of_seat=None, posture_change=None)),
            (18, "confirmed", _with(orientation=None, hand_motion=True)),
            (24, "confirmed", _with(orientation=None)),
            (30, "confirmed", QUIET),
        ),
        exercises=("per-indicator observability", "null is not false"),
    ),
    Scenario(
        id="analysis-failures",
        title="Analysis failures",
        summary="The observation provider fails on a run of frames. Errors are surfaced and no rows are fabricated.",
        duration_s=35,
        script=(
            (0, "confirmed", QUIET),
            (4, "confirmed", _with(orientation=True)),
            (12, "confirmed", QUIET),
        ),
        fail_frames=tuple(range(40, 55)),
        exercises=("provider errors", "observation gaps", "engine unknown closure"),
    ),
    Scenario(
        id="source-disconnect",
        title="Source disconnect",
        summary="The video source drops mid-session. The session fails visibly instead of freezing on a stale result.",
        duration_s=60,
        script=(
            (0, "confirmed", QUIET),
            (6, "confirmed", _with(out_of_seat=True)),
            (12, "confirmed", QUIET),
        ),
        disconnect_at=90,
        exercises=("source failure", "terminal state", "stale result handling"),
    ),
    Scenario(
        id="slow-analysis",
        title="Slow analysis",
        summary="Each frame takes longer to analyse than real time allows. Results age and are flagged stale; no frame is skipped.",
        duration_s=30,
        script=(
            (0, "confirmed", QUIET),
            (4, "confirmed", _with(body_motion=True)),
            (9, "confirmed", QUIET),
        ),
        cost_s=0.28,
        exercises=("analysis lag", "stale marking", "performance telemetry"),
    ),
    Scenario(
        id="recorder-failure",
        title="Recorder failure",
        summary="The (simulated) recorder runs out of space mid-session. The session fails and never claims a complete recording.",
        duration_s=40,
        script=(
            (0, "confirmed", QUIET),
            (5, "confirmed", _with(orientation=True)),
        ),
        recorder_fail_at=60,
        exercises=("recorder failure", "no false completion", "failure surfacing"),
    ),
)}
