"""Recorder boundary for the live-session shell. SIMULATED ONLY.

No pixels are stored anywhere. ``FrameLedgerRecorder`` keeps an in-memory
ledger of which source frames reached the recorder and hashes that ledger, so
completeness, gaps and failure handling can be exercised before any real
recording is authorized. A real recorder (codec, location, retention, access)
awaits the data-governance decision and must implement the same interface.
"""
import hashlib


class RecorderError(Exception):
    """Recording failed; the session must not claim a complete recording."""


class FrameLedgerRecorder:
    kind = "simulated_ledger"

    def __init__(self, fail_at_frame: int | None = None, fail_on_finalize: bool = False):
        self.fail_at_frame = fail_at_frame
        self.fail_on_finalize = fail_on_finalize
        self.state = "idle"
        self._digest = hashlib.sha256()
        self.frames = 0
        self.dropped_frames = 0
        self.first_time = None
        self.last_time = None
        self._last_index = None

    def status(self) -> dict:
        return {"kind": self.kind, "state": self.state, "frames": self.frames,
                "dropped_frames": self.dropped_frames, "pixels_stored": False}

    def start(self) -> None:
        if self.state != "idle":
            raise RecorderError(f"Recorder cannot start while {self.state}")
        self.state = "recording"

    def write(self, frame) -> None:
        if self.state != "recording":
            raise RecorderError(f"Recorder cannot write while {self.state}")
        if self.fail_at_frame is not None and frame.index == self.fail_at_frame:
            self.state = "failed"
            raise RecorderError("Simulated write failure: no space left on device")
        if self._last_index is not None and frame.index != self._last_index + 1:
            # Gaps are counted and reported, never hidden.
            self.dropped_frames += max(0, frame.index - self._last_index - 1)
        self._last_index = frame.index
        self._digest.update(f"{frame.index}:{frame.video_time:.6f}\n".encode("ascii"))
        self.frames += 1
        if self.first_time is None:
            self.first_time = frame.video_time
        self.last_time = frame.video_time

    def finalize(self) -> dict:
        if self.state != "recording":
            raise RecorderError(f"Recorder cannot finalize while {self.state}")
        if self.fail_on_finalize:
            self.state = "failed"
            raise RecorderError("Simulated finalize failure: output could not be verified")
        self.state = "finalized"
        return {**self.status(), "first_time": self.first_time, "last_time": self.last_time,
                "sha256": self._digest.hexdigest(), "verified": True}

    def abort(self) -> None:
        if self.state in ("idle", "recording"):
            self.state = "aborted"
