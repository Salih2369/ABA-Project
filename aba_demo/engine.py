"""Temporal demo rules. All thresholds are PROVISIONAL, not clinical cutoffs."""
from copy import deepcopy
from math import isfinite
import json

INDICATORS = ("orientation", "body_motion", "out_of_seat", "hand_motion", "posture_change")
_MAX_VALUE_NESTING = 100


class Engine:
    """Accumulate observed episodes; return JSON-compatible snapshot dictionaries.

    PROVISIONAL demo config (seconds, not validated clinical thresholds):
      persistence_seconds=2: uninterrupted positive evidence before activation.
      release_seconds=1: observed absence needed to close an active episode.
      cooldown_seconds=5: minimum interval between cards of the same type.
      max_observed_delta=2: larger sample gaps close episodes as unknown.
      alert_hand_motion=False, alert_posture_change=False: explicit opt-ins.
      per_indicator={name: {persistence_seconds: n, release_seconds: n}}.

    Posture transitions are already-confirmed pulses: their default persistence
    AND release are zero, even when global thresholds change. Override them only
    through per_indicator. Missing signal keys mean unknown, never absence.

    Duration sums intervals with positive observations at both endpoints, never
    unknown or release intervals. observable_seconds sums intervals with known,
    applicable observations at both endpoints. Counts/history are session-wide
    and include descriptive events. Activity changes close episodes and clear
    cooldowns, but retain counts/history; reset clears all and returns to table.

    Events appear when persistence is met, with start at candidate onset, end
    null while open, and evidence_time at activation. Unknown closes at the last
    observation; observed release closes at the first absent sample. Candidates
    that never persist are discarded. At most one card is returned per update,
    selected in seat/orientation/body/posture/hand order. Active seat suppresses
    body/posture cards. Suppressed cards are not queued for later replay.
    Movement events are descriptive only; break indicators are not applicable.

    Input validation precedes mutation and raises ValueError. Returned snapshots
    are detached copies. The engine performs no I/O and is not thread-safe.
    Treat config as construction-only; create a new Engine to change thresholds.
    """

    def __init__(self, config: dict | None = None):
        if config is not None and not isinstance(config, dict):
            raise ValueError("config must be a dict or None")
        self.config = {"persistence_seconds": 2.0, "release_seconds": 1.0,
                       "max_observed_delta": 2.0, "cooldown_seconds": 5.0,
                       "alert_hand_motion": False, "alert_posture_change": False,
                       "per_indicator": {}}
        if config and set(config) - set(self.config):
            raise ValueError("unknown configuration key")
        self.config.update(deepcopy(config or {}))
        for name in ("persistence_seconds", "release_seconds", "cooldown_seconds", "max_observed_delta"):
            self._validate_seconds(name, self.config[name])
        if self.config["max_observed_delta"] == 0:
            raise ValueError("max_observed_delta must be positive")
        for name in ("alert_hand_motion", "alert_posture_change"):
            if type(self.config[name]) is not bool:
                raise ValueError(name + " must be bool")
        overrides = self.config["per_indicator"]
        if not isinstance(overrides, dict) or set(overrides) - set(INDICATORS):
            raise ValueError("per_indicator must map known indicators to threshold dicts")
        for options in overrides.values():
            if not isinstance(options, dict) or set(options) - {"persistence_seconds", "release_seconds"}:
                raise ValueError("per_indicator accepts persistence_seconds and release_seconds only")
            for name, value in options.items():
                self._validate_seconds(name, value)
        self.reset()

    @staticmethod
    def _validate_seconds(name, value):
        try:
            invalid = type(value) not in (int, float) or not isfinite(value) or value < 0
        except OverflowError as exc:
            raise ValueError(name + " must be a finite nonnegative number") from exc
        if invalid:
            raise ValueError(name + " must be a finite nonnegative number")

    @staticmethod
    def _detach_values(values):
        message = "values must contain finite JSON-serializable data"
        try:
            json.dumps(values, allow_nan=False)
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            raise ValueError(message) from exc
        stack = [(values, 0)]
        deepest = {}
        while stack:
            value, depth = stack.pop()
            if depth > _MAX_VALUE_NESTING:
                raise ValueError("values nesting must not exceed 100 levels")
            if isinstance(value, (dict, list, tuple)):
                previous_depth = deepest.get(id(value), -1)
                if previous_depth >= depth:
                    continue
                deepest[id(value)] = depth
                children = value.values() if isinstance(value, dict) else value
                stack.extend((child, depth + 1) for child in children)
        try:
            return deepcopy(values)
        except Exception as exc:
            raise ValueError(message) from exc

    def _threshold(self, key, name):
        default = 0.0 if key == "posture_change" and name in (
            "persistence_seconds", "release_seconds") else self.config[name]
        return self.config.get("per_indicator", {}).get(key, {}).get(name, default)

    def reset(self) -> None:
        """Clear the session and return to table activity; retain configuration."""
        self.activity = "table"
        self._time = None
        self._events = []
        self._episodes = {key: None for key in INDICATORS}
        self._last_alert = dict.fromkeys(INDICATORS, None)
        self._counts = dict.fromkeys(INDICATORS, 0)
        self._observable = dict.fromkeys(INDICATORS, 0.0)
        self._previous = dict.fromkeys(INDICATORS, None)

    def set_activity(self, activity: str) -> None:
        """Change context; close episodes at the last observation boundary."""
        if activity not in ("table", "movement", "break"):
            raise ValueError("activity must be table, movement, or break")
        if activity != self.activity:
            for key in INDICATORS:
                self._close(key, self._time, "activity_change")
            self._previous = dict.fromkeys(INDICATORS, None)
            self._last_alert = dict.fromkeys(INDICATORS, None)
            self.activity = activity

    def _close(self, key, end, reason):
        episode = self._episodes[key]
        if episode is not None and episode["event"] is not None:
            episode["event"]["end"] = end
            episode["event"]["end_reason"] = reason
        self._episodes[key] = None

    def update(self, observation: dict) -> dict:
        """Consume {time, identity, signals, values?}; time is nonnegative seconds."""
        if not isinstance(observation, dict):
            raise ValueError("observation must be a dict")
        if observation.get("identity") not in ("confirmed", "uncertain"):
            raise ValueError("identity must be confirmed or uncertain")
        signals = observation.get("signals")
        if not isinstance(signals, dict) or any(
                value is not None and type(value) is not bool for value in signals.values()):
            raise ValueError("signals must map indicators to bool or None")
        values = observation.get("values", {})
        if not isinstance(values, dict):
            raise ValueError("values must be a dict")
        values = self._detach_values(values)
        time = observation.get("time")
        try:
            invalid_time = (isinstance(time, bool) or not isinstance(time, (int, float))
                            or not isfinite(time) or time < 0
                            or (self._time is not None and time <= self._time))
        except OverflowError as exc:
            raise ValueError("time must be finite and strictly increasing") from exc
        if invalid_time:
            raise ValueError("time must be finite and strictly increasing")
        delta = 0.0 if self._time is None else time - self._time
        if delta > self.config["max_observed_delta"]:
            for key in INDICATORS:
                self._close(key, self._time, "unknown")
            self._previous = dict.fromkeys(INDICATORS, None)
            delta = 0.0
        indicators = {}
        alert = None
        eligible = []
        for key in INDICATORS:
            signal = observation["signals"].get(key) if observation["identity"] == "confirmed" else None
            applicable = self.activity != "break" and not (
                self.activity == "movement" and key in ("orientation", "out_of_seat"))
            if not applicable:
                signal = None
            episode = self._episodes[key]
            if signal is not None and self._previous[key] is not None:
                self._observable[key] += delta
            self._previous[key] = signal
            if signal is None:
                self._close(key, self._time, "unknown")
                episode = None
                state = "unobservable" if applicable else "not_applicable"
            elif signal:
                if episode is None:
                    episode = {"start": time, "duration": 0.0, "event": None, "false_since": None}
                    self._episodes[key] = episode
                elif episode["false_since"] is None:
                    episode["duration"] += delta
                episode["false_since"] = None
                if episode["duration"] >= self._threshold(key, "persistence_seconds"):
                    if episode["event"] is None:
                        self._counts[key] += 1
                        event = {"id": len(self._events) + 1, "type": key,
                                 "start": episode["start"], "end": None,
                                 "duration": episode["duration"], "alerted_at": None,
                                 "evidence_time": time}
                        episode["event"] = event
                        self._events.append(event)
                        if self.activity == "table" and (
                                key not in ("hand_motion", "posture_change")
                                or self.config.get("alert_" + key, False)):
                            eligible.append(event)
                    episode["event"]["duration"] = episode["duration"]
                    state = "active"
                else:
                    state = "candidate"
            else:
                state = "inactive"
                if episode is not None and episode["event"] is None:
                    self._episodes[key] = episode = None
                if episode is not None:
                    if episode["false_since"] is None:
                        episode["false_since"] = time
                    if time - episode["false_since"] >= self._threshold(key, "release_seconds"):
                        if episode["event"] is not None:
                            episode["event"]["end"] = episode["false_since"]
                        self._episodes[key] = episode = None
                    else:
                        state = "active" if episode["event"] else "candidate"
            indicators[key] = {"state": state,
                               "duration": episode["duration"] if episode else 0.0,
                               "count": self._counts[key],
                               "observable_seconds": self._observable[key],
                               "value": values.get(key) if signal is not None else None}
        if indicators["out_of_seat"]["state"] == "active":
            eligible = [event for event in eligible
                        if event["type"] not in ("body_motion", "posture_change")]
        eligible = [event for event in eligible
                    if self._last_alert[event["type"]] is None
                    or time - self._last_alert[event["type"]] >= self.config["cooldown_seconds"]]
        if eligible:
            priority = ("out_of_seat", "orientation", "body_motion", "posture_change", "hand_motion")
            alert = min(eligible, key=lambda event: priority.index(event["type"]))
            alert["alerted_at"] = time
            self._last_alert[alert["type"]] = time
        self._time = time
        return deepcopy({"time": time, "activity": self.activity,
                         "identity": observation["identity"], "indicators": indicators,
                         "events": self._events, "alert": alert})
