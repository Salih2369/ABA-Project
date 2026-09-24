"""Pure-Python demo engine contract tests (no clinical validation)."""
import unittest

from aba_demo.engine import Engine, INDICATORS


def observation(time, identity="confirmed", **signals):
    return {"time": time, "identity": identity,
            "signals": {key: signals.get(key, False) for key in INDICATORS}}


class EngineTests(unittest.TestCase):
    def test_persistence_creates_one_episode_card(self):
        engine = Engine({"persistence_seconds": 2})
        first = engine.update(observation(0, orientation=True))
        self.assertEqual(first["indicators"]["orientation"]["state"], "candidate")
        self.assertIsNone(first["alert"])
        self.assertIsNone(engine.update(observation(1, orientation=True))["alert"])
        result = engine.update(observation(2, orientation=True))
        self.assertEqual(result["indicators"]["orientation"]["state"], "active")
        self.assertEqual(result["indicators"]["orientation"]["duration"], 2)
        self.assertEqual(result["indicators"]["orientation"]["count"], 1)
        self.assertEqual(result["alert"]["type"], "orientation")
        self.assertEqual(result["alert"]["evidence_time"], 2)
        self.assertEqual(result["events"][0]["start"], 0)
        self.assertIsNone(engine.update(observation(3, orientation=True))["alert"])
        self.assertEqual(len(engine.update(observation(4, orientation=True))["events"]), 1)

    def test_hysteresis_closes_after_observed_absence(self):
        engine = Engine({"persistence_seconds": 1, "release_seconds": 1})
        engine.update(observation(0, orientation=True))
        engine.update(observation(1, orientation=True))
        first_false = engine.update(observation(2))
        self.assertEqual(first_false["indicators"]["orientation"]["state"], "active")
        closed = engine.update(observation(3))
        self.assertEqual(closed["indicators"]["orientation"]["state"], "inactive")
        self.assertEqual(closed["events"][0]["end"], 2)
        self.assertEqual(closed["events"][0]["duration"], 1)
        self.assertEqual(engine.update(observation(4, orientation=True))["indicators"]["orientation"]["state"], "candidate")

    def test_unknown_closes_episode_without_counting_gap(self):
        engine = Engine({"persistence_seconds": 1})
        engine.update(observation(0, orientation=True))
        engine.update(observation(1, orientation=True))
        unknown = engine.update(observation(2, orientation=None))
        metric = unknown["indicators"]["orientation"]
        self.assertEqual(metric["state"], "unobservable")
        self.assertEqual(metric["duration"], 0)
        self.assertEqual(metric["observable_seconds"], 1)
        self.assertEqual(unknown["events"][0]["end"], 1)
        self.assertEqual(unknown["events"][0]["end_reason"], "unknown")
        resumed = engine.update(observation(3, orientation=True))
        self.assertEqual(resumed["indicators"]["orientation"]["duration"], 0)
        self.assertEqual(resumed["indicators"]["orientation"]["observable_seconds"], 1)
        self.assertEqual(resumed["indicators"]["orientation"]["state"], "candidate")

    def test_uncertain_identity_suppresses_and_restarts_all_signals(self):
        engine = Engine({"persistence_seconds": 1})
        positive = dict.fromkeys(INDICATORS, True)
        engine.update(observation(0, **positive))
        engine.update(observation(1, **positive))
        result = engine.update(observation(2, identity="uncertain", **positive))
        self.assertIsNone(result["alert"])
        self.assertTrue(all(item["state"] == "unobservable" for item in result["indicators"].values()))
        self.assertTrue(all(event["end_reason"] == "unknown" for event in result["events"]))
        resumed = engine.update(observation(3, **positive))
        self.assertTrue(all(item["duration"] == 0 for item in resumed["indicators"].values()))

    def test_activity_policy_and_boundary_reset(self):
        engine = Engine({"persistence_seconds": 1})
        positive = dict.fromkeys(INDICATORS, True)
        engine.update(observation(0, **positive))
        engine.update(observation(1, **positive))
        engine.set_activity("movement")
        result = engine.update(observation(2, **positive))
        self.assertEqual(result["activity"], "movement")
        for key in ("orientation", "out_of_seat"):
            self.assertEqual(result["indicators"][key]["state"], "not_applicable")
        self.assertEqual(result["indicators"]["body_motion"]["state"], "candidate")
        self.assertIsNone(engine.update(observation(3, **positive))["alert"])
        engine.set_activity("break")
        result = engine.update(observation(4, **positive))
        self.assertTrue(all(item["state"] == "not_applicable" for item in result["indicators"].values()))
        engine.set_activity("table")
        self.assertEqual(engine.update(observation(5, **positive))["indicators"]["orientation"]["duration"], 0)
        self.assertIsNotNone(engine.update(observation(6, **positive))["alert"])
        with self.assertRaises(ValueError):
            engine.set_activity("invalid")

    def test_invalid_time_rejected_without_mutating_state(self):
        engine = Engine()
        engine.update(observation(5, orientation=True))
        for time in (5, 4, float("nan"), float("inf"), -float("inf"), True, "6"):
            with self.subTest(time=time), self.assertRaises(ValueError):
                engine.update(observation(time, orientation=True))
        self.assertEqual(engine.update(observation(6, orientation=True))["indicators"]["orientation"]["duration"], 1)

    def test_oversized_numeric_inputs_raise_value_error(self):
        oversized = 10 ** 400
        with self.assertRaises(ValueError):
            Engine({"persistence_seconds": oversized})
        with self.assertRaises(ValueError):
            Engine().update(observation(oversized))

    def test_long_sampling_gap_resets_without_counting_missing_time(self):
        engine = Engine({"persistence_seconds": 1, "max_observed_delta": 1.5})
        engine.update(observation(0, body_motion=True))
        engine.update(observation(1, body_motion=True))
        result = engine.update(observation(10, body_motion=True))
        self.assertEqual(result["indicators"]["body_motion"]["duration"], 0)
        self.assertEqual(result["indicators"]["body_motion"]["observable_seconds"], 1)
        self.assertEqual(result["events"][0]["end"], 1)
        self.assertEqual(result["events"][0]["end_reason"], "unknown")
        self.assertIsNone(result["alert"])

    def test_seat_card_deduplicates_same_frame_body_and_posture(self):
        engine = Engine({"persistence_seconds": 1, "alert_posture_change": True,
                         "per_indicator": {"posture_change": {"persistence_seconds": 1}}})
        positive = {"out_of_seat": True, "body_motion": True, "posture_change": True}
        engine.update(observation(0, **positive))
        result = engine.update(observation(1, **positive))
        self.assertEqual(result["alert"]["type"], "out_of_seat")
        alerted = [event for event in result["events"] if event["alerted_at"] is not None]
        self.assertEqual(len(alerted), 1)
        self.assertEqual(alerted[0]["type"], "out_of_seat")
        self.assertIsNone(engine.update(observation(2, **positive))["alert"])

    def test_hand_and_posture_alerts_require_explicit_opt_in(self):
        for key in ("hand_motion", "posture_change"):
            with self.subTest(key=key):
                engine = Engine({"persistence_seconds": 1})
                engine.update(observation(0, **{key: True}))
                result = engine.update(observation(1, **{key: True}))
                self.assertIsNone(result["alert"])
                self.assertEqual(result["indicators"][key]["state"], "active")
                self.assertEqual(result["indicators"][key]["count"], 1)
                enabled = Engine({"persistence_seconds": 1, "alert_" + key: True,
                                  "per_indicator": {key: {"persistence_seconds": 1}}})
                enabled.update(observation(0, **{key: True}))
                self.assertEqual(enabled.update(observation(1, **{key: True}))["alert"]["type"], key)

    def test_cooldown_suppresses_episode_without_delayed_card(self):
        engine = Engine({"persistence_seconds": 1, "release_seconds": 0,
                         "cooldown_seconds": 5})
        engine.update(observation(0, orientation=True))
        self.assertIsNotNone(engine.update(observation(1, orientation=True))["alert"])
        engine.update(observation(2))
        engine.update(observation(3, orientation=True))
        self.assertIsNone(engine.update(observation(4, orientation=True))["alert"])
        self.assertIsNone(engine.update(observation(5, orientation=True))["alert"])
        self.assertIsNone(engine.update(observation(6, orientation=True))["alert"])
        engine.update(observation(7))
        engine.update(observation(8, orientation=True))
        self.assertIsNotNone(engine.update(observation(9, orientation=True))["alert"])
        engine.set_activity("break")
        engine.set_activity("table")
        engine.update(observation(10, orientation=True))
        self.assertIsNotNone(engine.update(observation(11, orientation=True))["alert"])

    def test_reset_clears_history_time_and_context_but_preserves_config(self):
        engine = Engine({"persistence_seconds": 1})
        engine.update(observation(10, orientation=True))
        engine.update(observation(11, orientation=True))
        engine.set_activity("break")
        engine.reset()
        result = engine.update(observation(0))
        self.assertEqual(result["activity"], "table")
        self.assertEqual(result["events"], [])
        self.assertTrue(all(item["count"] == 0 and item["observable_seconds"] == 0 for item in result["indicators"].values()))
        engine.update(observation(1, orientation=True))
        self.assertIsNotNone(engine.update(observation(2, orientation=True))["alert"])

    def test_invalid_observations_are_rejected_atomically(self):
        engine = Engine()
        engine.update(observation(0, orientation=True))
        bad = [observation(-1), observation(1, identity="missing"),
               observation(1, orientation=1), observation(1, body_motion="yes"),
               {"time": 1, "identity": "confirmed", "signals": []},
               {"time": 1, "identity": "confirmed", "signals": {}, "values": []},
               {}, None]
        for item in bad:
            with self.subTest(item=item), self.assertRaises(ValueError):
                engine.update(item)
        self.assertEqual(engine.update(observation(1, orientation=True))["indicators"]["orientation"]["duration"], 1)
        with self.assertRaises(ValueError):
            Engine().update(observation(-0.1))

    def test_per_indicator_thresholds_override_global_thresholds(self):
        engine = Engine({"persistence_seconds": 5, "release_seconds": 5,
                         "per_indicator": {"body_motion": {"persistence_seconds": 1, "release_seconds": 0}}})
        engine.update(observation(0, body_motion=True, orientation=True))
        result = engine.update(observation(1, body_motion=True, orientation=True))
        self.assertEqual(result["indicators"]["body_motion"]["state"], "active")
        self.assertEqual(result["indicators"]["orientation"]["state"], "candidate")
        self.assertEqual(engine.update(observation(2))["indicators"]["body_motion"]["state"], "inactive")

    def test_default_posture_pulses_count_separate_transitions(self):
        engine = Engine({"release_seconds": 10})
        first = engine.update(observation(0, posture_change=True))
        self.assertEqual(first["indicators"]["posture_change"]["count"], 1)
        self.assertIsNone(first["alert"])
        self.assertEqual(engine.update(observation(0.1))["indicators"]["posture_change"]["state"], "inactive")
        second = engine.update(observation(0.2, posture_change=True))
        self.assertEqual(second["indicators"]["posture_change"]["count"], 2)
        self.assertEqual(len(second["events"]), 2)
        self.assertEqual(second["events"][0]["end"], 0.1)
        enabled = Engine({"alert_posture_change": True})
        self.assertEqual(enabled.update(observation(0, posture_change=True))["alert"]["type"], "posture_change")

    def test_configuration_validation(self):
        invalid = [[], {"typo": 1}, {"persistence_seconds": -1},
                   {"release_seconds": float("nan")}, {"cooldown_seconds": True},
                   {"max_observed_delta": 0}, {"alert_hand_motion": "yes"},
                   {"per_indicator": []}, {"per_indicator": {"wrong": {}}},
                   {"per_indicator": {"orientation": {"persistence_seconds": -1}}},
                   {"per_indicator": {"orientation": {"typo": 1}}},
                   {"per_indicator": {"orientation": None}}]
        for config in invalid:
            with self.subTest(config=config), self.assertRaises(ValueError):
                Engine(config)

    def test_candidate_requires_uninterrupted_positive_evidence(self):
        engine = Engine({"persistence_seconds": 2, "release_seconds": 3})
        engine.update(observation(0, orientation=True))
        engine.update(observation(1, orientation=True))
        self.assertEqual(engine.update(observation(2))["indicators"]["orientation"]["state"], "inactive")
        self.assertEqual(engine.update(observation(3, orientation=True))["indicators"]["orientation"]["duration"], 0)
        self.assertIsNone(engine.update(observation(4, orientation=True))["alert"])
        self.assertIsNotNone(engine.update(observation(5, orientation=True))["alert"])

    def test_active_seat_suppresses_later_same_frame_motion_cards(self):
        engine = Engine({"persistence_seconds": 1, "alert_posture_change": True})
        engine.update(observation(0, out_of_seat=True))
        engine.update(observation(1, out_of_seat=True))
        engine.update(observation(2, out_of_seat=True, body_motion=True))
        result = engine.update(observation(3, out_of_seat=True, body_motion=True, posture_change=True))
        self.assertIsNone(result["alert"])
        self.assertEqual(result["indicators"]["posture_change"]["count"], 1)
        self.assertEqual(result["indicators"]["body_motion"]["count"], 1)

    def test_values_must_be_json_safe_before_state_changes(self):
        engine = Engine()
        engine.update(observation(0, orientation=True))
        for value in (float("nan"), float("inf"), {1, 2}):
            item = observation(1, orientation=True)
            item["values"] = {"orientation": value}
            with self.subTest(value=value), self.assertRaises(ValueError):
                engine.update(item)
        item = observation(1, orientation=True)
        item["values"] = {"orientation": {"angle": 42}}
        result = engine.update(item)
        self.assertEqual(result["indicators"]["orientation"]["value"], {"angle": 42})
        result["indicators"]["orientation"]["value"]["angle"] = 99
        self.assertEqual(item["values"]["orientation"]["angle"], 42)

    def test_deeply_nested_values_are_rejected_without_mutating_state(self):
        engine = Engine()
        engine.update(observation(0, orientation=True))
        nested = None
        for _ in range(600):
            nested = [nested]
        item = observation(1, orientation=True)
        item["values"] = {"orientation": nested}
        with self.assertRaises(ValueError):
            engine.update(item)
        result = engine.update(observation(1, orientation=True))
        self.assertEqual(result["indicators"]["orientation"]["duration"], 1)

    def test_unobservable_values_are_suppressed(self):
        for identity, signal, activity in (("uncertain", True, "table"),
                                           ("confirmed", None, "table"),
                                           ("confirmed", True, "break")):
            engine = Engine()
            engine.set_activity(activity)
            item = observation(0, identity=identity, orientation=signal)
            item["values"] = {"orientation": 42}
            self.assertIsNone(engine.update(item)["indicators"]["orientation"]["value"])


if __name__ == "__main__":
    unittest.main()
