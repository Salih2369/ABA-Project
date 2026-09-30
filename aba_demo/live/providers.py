"""Observation providers. Every provider declares its kind so output is never mislabeled.

Protocol: ``observe(frame) -> list[observation]``. A provider returns every
observation that became due at or before ``frame.video_time`` (possibly none),
in strictly increasing time order, and raises ``ProviderError`` when analysis
for that frame failed. Observations use the existing row shape
``{time, identity, signals, values}``; nothing here changes its meaning.
"""
import json
import math
from pathlib import Path
import re
import time as _time

from ..engine import INDICATORS

MAX_PRECOMPUTED_BYTES = 50 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DUE_TOLERANCE_S = 1e-6


class ProviderError(Exception):
    """Analysis failed for one frame; no observation may be fabricated for it."""


def _validate_signals(signals, where):
    if not isinstance(signals, dict) or set(signals) - set(INDICATORS) or any(
            value is not None and type(value) is not bool for value in signals.values()):
        raise ValueError(f"{where}: signals must map known indicators to true, false, or null")


class SyntheticProvider:
    """Deterministic scripted observations. SYNTHETIC: not model inference.

    ``script`` is a list of ``(start_time, identity, signals)`` segments with
    strictly increasing start times; the first must start at 0. Missing signal
    keys are ``None`` (unobservable). Uncertain identity always yields five
    ``None`` signals. ``fail_times`` lists video times whose frames raise
    ``ProviderError``. ``cost_s`` simulates per-frame analysis cost through
    ``sleep`` (injectable for tests).
    """
    kind = "synthetic"

    def __init__(self, script, fail_times=(), cost_s=0.0, sleep=_time.sleep):
        if not isinstance(script, (list, tuple)) or not script:
            raise ValueError("script must be a non-empty list of segments")
        previous = None
        segments = []
        for segment in script:
            if not isinstance(segment, (list, tuple)) or len(segment) != 3:
                raise ValueError("each segment must be (start_time, identity, signals)")
            start, identity, signals = segment
            if type(start) not in (int, float) or start < 0 or (previous is not None and start <= previous):
                raise ValueError("segment start times must be nonnegative and strictly increasing")
            if identity not in ("confirmed", "uncertain"):
                raise ValueError("identity must be confirmed or uncertain")
            _validate_signals(signals, "segment")
            segments.append((float(start), identity, dict(signals)))
            previous = start
        if segments[0][0] != 0:
            raise ValueError("the first segment must start at time 0")
        if type(cost_s) not in (int, float) or not math.isfinite(cost_s) or cost_s < 0:
            raise ValueError("cost_s must be a finite nonnegative number")
        self._segments = segments
        self._fail_times = {float(t) for t in fail_times}
        self._cost_s = float(cost_s)
        self._sleep = sleep

    def describe(self) -> dict:
        return {"kind": self.kind, "simulated_cost_ms": self._cost_s * 1000}

    def observe(self, frame) -> list:
        if self._cost_s:
            self._sleep(self._cost_s)
        if frame.video_time in self._fail_times:
            raise ProviderError("Synthetic provider failure injected")
        _, identity, signals = [s for s in self._segments if s[0] <= frame.video_time][-1]
        if identity == "uncertain":
            values = dict.fromkeys(INDICATORS)
        else:
            values = {key: signals.get(key) for key in INDICATORS}
        return [{"time": frame.video_time, "identity": identity, "signals": values, "values": {}}]


class PrecomputedProvider:
    """Replays rows from an existing precomputed observation export, causally.

    PRECOMPUTED: these rows were produced earlier by the recorded-video
    analysis; replaying them in real time is not live inference. A matching
    source SHA-256 proves byte identity with the video, not human review,
    publication provenance, or accuracy.
    """
    kind = "precomputed"

    def __init__(self, document: dict, video_sha256: str | None = None):
        if not isinstance(document, dict) or document.get("schema_version") != 1 \
                or document.get("mode") != "precomputed":
            raise ValueError("Expected a schema_version 1, mode 'precomputed' observation document")
        source = document.get("source")
        if not isinstance(source, dict) or not isinstance(source.get("sha256"), str) \
                or not _SHA256.match(source["sha256"]):
            raise ValueError("source.sha256 must be a lowercase SHA-256 hex digest")
        if video_sha256 is not None and video_sha256 != source["sha256"]:
            raise ValueError("The video does not match this observation document (SHA-256 differs)")
        rows = document.get("observations")
        if not isinstance(rows, list) or not rows:
            raise ValueError("observations must be a non-empty list")
        self._rows = [self._normalize(row, index, rows[index - 1]["time"] if index else None)
                      for index, row in enumerate(rows)]
        self.source = {key: source.get(key) for key in ("name", "sha256", "duration", "fps", "frame_count")}
        self._next = 0

    @staticmethod
    def _normalize(row, index, previous_time):
        where = f"observations[{index}]"
        if not isinstance(row, dict):
            raise ValueError(f"{where} must be an object")
        time = row.get("time")
        if isinstance(time, bool) or not isinstance(time, (int, float)) or not math.isfinite(time) \
                or time < 0 or (previous_time is not None and time <= previous_time):
            raise ValueError(f"{where}: time must be finite, nonnegative and strictly increasing")
        identity = row.get("identity")
        if identity not in ("confirmed", "uncertain"):
            raise ValueError(f"{where}: identity must be confirmed or uncertain")
        signals = row.get("signals")
        _validate_signals(signals, where)
        if set(signals) != set(INDICATORS):
            raise ValueError(f"{where}: signals must name exactly the five indicators")
        if identity == "uncertain" and any(value is not None for value in signals.values()):
            raise ValueError(f"{where}: uncertain identity must carry five null signals")
        values = row.get("values", {})
        if not isinstance(values, dict):
            raise ValueError(f"{where}: values must be an object")
        return {"time": float(time), "identity": identity, "signals": dict(signals), "values": values}

    @classmethod
    def from_file(cls, path, video_sha256: str | None = None) -> "PrecomputedProvider":
        path = Path(path)
        if not path.is_file():
            raise ValueError("Observation document must be an existing local file")
        if path.stat().st_size > MAX_PRECOMPUTED_BYTES:
            raise ValueError("Observation document is too large")

        def strict_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate JSON key")
                result[key] = value
            return result

        def reject_constant(value):
            raise ValueError("Non-finite JSON number")

        document = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=strict_object,
                              parse_constant=reject_constant)
        return cls(document, video_sha256)

    def describe(self) -> dict:
        return {"kind": self.kind, "rows": len(self._rows), "source": self.source}

    def observe(self, frame) -> list:
        due = []
        while self._next < len(self._rows) and self._rows[self._next]["time"] <= frame.video_time + _DUE_TOLERANCE_S:
            row = self._rows[self._next]
            due.append({**row, "signals": dict(row["signals"]), "values": dict(row["values"])})
            self._next += 1
        return due
