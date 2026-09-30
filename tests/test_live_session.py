"""Live-session shell tests. SYNTHETIC ONLY: no camera, video, or model inference."""
import unittest

from aba_demo.engine import INDICATORS
from aba_demo.live.providers import SyntheticProvider
from aba_demo.live.session import InvalidTransition, LiveSession
from aba_demo.live.source import SyntheticFrameSource


class FakeClock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


ALL_FALSE = dict.fromkeys(INDICATORS, False)


def make_session(script=None, fps=5, frames=50, disconnect_at=None, fail_times=()):
    clock = FakeClock()
    source = SyntheticFrameSource(fps, frames, disconnect_at=disconnect_at)
    provider = SyntheticProvider(script or [(0, "confirmed", ALL_FALSE)], fail_times=fail_times)
    return LiveSession(source, provider, clock=clock, session_id="synthetic-test"), clock, source


def started(**kwargs):
    session, clock, source = make_session(**kwargs)
    session.open()
    session.select_target()
    session.start()
    return session, clock, source


def of_type(events, event_type):
    return [event for event in events if event["event_type"] == event_type]


class SyntheticSourceTests(unittest.TestCase):
    def test_rejects_invalid_configuration(self):
        for fps, frames in ((0, 10), (float("nan"), 10), (5, 0), (5, 1.5)):
            with self.assertRaises(ValueError):
                SyntheticFrameSource(fps, frames)

    def test_read_requires_open(self):
        with self.assertRaises(RuntimeError):
            SyntheticFrameSource(5, 10).read()


class SyntheticProviderTests(unittest.TestCase):
    def test_uncertain_segment_yields_five_nulls(self):
        provider = SyntheticProvider([(0, "confirmed", {"out_of_seat": True}),
                                      (1, "uncertain", {"out_of_seat": True})])
        frame = SyntheticFrameSource(5, 10)
        frame.open()
        rows = [provider.observe(frame.read())[0] for _ in range(10)]
        self.assertTrue(rows[0]["signals"]["out_of_seat"])
        self.assertIsNone(rows[0]["signals"]["orientation"])  # missing key = unobservable
        self.assertEqual(rows[5]["identity"], "uncertain")
        self.assertEqual(set(rows[5]["signals"].values()), {None})

    def test_rejects_invalid_scripts(self):
        for script in ([], [(1, "confirmed", {})], [(0, "maybe", {})],
                       [(0, "confirmed", {"unknown": True})], [(0, "confirmed", {}), (0, "confirmed", {})]):
            with self.assertRaises(ValueError):
                SyntheticProvider(script)


class LifecycleTests(unittest.TestCase):
    def test_normal_lifecycle_emits_ordered_states(self):
        session, clock, source = started()
        clock.advance(1)
        session.tick()
        session.stop()
        events = session.drain_events()
        states = [event["state"] for event in of_type(events, "session_state")]
        self.assertEqual(states, ["created", "previewing", "target_selected", "running",
                                  "stopping", "completed"])
        self.assertEqual([event["sequence"] for event in events], list(range(1, len(events) + 1)))
        self.assertTrue(all(event["provider"] == "synthetic" for event in events))
        completed = of_type(events, "session_state")[-1]
        self.assertTrue(completed["simulation"])
        self.assertFalse(completed["recorded"])
        self.assertEqual(session.termination_reason, "user_stop")
        self.assertTrue(source.closed)

    def test_disallowed_transitions_are_rejected(self):
        session, _, _ = make_session()
        for action in (session.select_target, session.start, session.pause, session.resume):
            with self.assertRaises(InvalidTransition):
                action()
        self.assertEqual(session.state, "created")
        session.open()
        with self.assertRaises(InvalidTransition):
            session.start()  # target must be selected first

    def test_stop_before_start_ends_without_observations(self):
        session, _, source = make_session()
        session.open()
        session.stop("user_left")
        self.assertEqual((session.state, session.termination_reason), ("completed", "user_left"))
        self.assertTrue(source.closed)
        self.assertEqual(of_type(session.drain_events(), "observation"), [])

    def test_no_observations_before_start(self):
        session, clock, _ = make_session()
        session.open()
        session.select_target()
        clock.advance(5)
        self.assertEqual(session.tick(), 0)
        self.assertEqual(of_type(session.drain_events(), "observation"), [])

    def test_terminal_states_allow_nothing(self):
        session, _, _ = started()
        session.stop()
        for action in (session.pause, session.resume, session.start, session.stop):
            with self.assertRaises(InvalidTransition):
                action()
        self.assertEqual(session.tick(), 0)


class PacingTests(unittest.TestCase):
    def test_only_elapsed_frames_are_emitted(self):
        session, clock, _ = started(fps=5)
        self.assertEqual(session.tick(), 1)  # frame at t=0
        clock.advance(0.5)
        self.assertEqual(session.tick(), 2)  # t=0.2, 0.4
        times = [event["video_time"] for event in of_type(session.drain_events(), "observation")]
        self.assertEqual(times, [0.0, 0.2, 0.4])
        self.assertTrue(all(time <= session.elapsed() for time in times))

    def test_capture_time_is_when_frame_becomes_due(self):
        session, clock, _ = started(fps=5)
        session.tick()
        clock.advance(0.1)
        session.tick()  # frame at 0.2 is read ahead but not yet due
        clock.advance(0.1)
        session.tick()
        event = of_type(session.drain_events(), "observation")[-1]
        self.assertAlmostEqual(event["captured_monotonic_ms"], clock.now * 1000)

    def test_pause_freezes_video_time(self):
        session, clock, _ = started(fps=5)
        clock.advance(1)
        session.tick()
        session.pause()
        clock.advance(10)
        self.assertEqual(session.tick(), 0)
        self.assertAlmostEqual(session.elapsed(), 1)
        session.resume()
        clock.advance(0.2)
        session.tick()
        times = [event["video_time"] for event in of_type(session.drain_events(), "observation")]
        self.assertAlmostEqual(times[-1], 1.2)
        self.assertEqual(times, sorted(times))

    def test_envelope_wraps_observation_unchanged(self):
        session, _, _ = started()
        session.tick()
        event = of_type(session.drain_events(), "observation")[0]
        self.assertEqual(event["schema_version"], 1)
        self.assertEqual(event["session_id"], "synthetic-test")
        self.assertEqual(event["source_frame_index"], 0)
        # With a frozen clock a frame due now arrives and is processed at the same instant.
        self.assertEqual(event["captured_monotonic_ms"], event["processed_monotonic_ms"])
        self.assertEqual(set(event["observation"]), {"time", "identity", "signals", "values"})
        self.assertEqual(set(event["observation"]["signals"]), set(INDICATORS))


class EngineIntegrationTests(unittest.TestCase):
    def test_alert_comes_from_engine_after_persistence(self):
        session, clock, _ = started(script=[(0, "confirmed", dict(ALL_FALSE, out_of_seat=True))])
        clock.advance(1.8)
        session.tick()
        alerts = [e["state"]["alert"] for e in of_type(session.drain_events(), "engine_state")]
        self.assertEqual([a for a in alerts if a], [])  # true signal alone is not an alert
        clock.advance(0.2)
        session.tick()
        alerts = [e["state"]["alert"] for e in of_type(session.drain_events(), "engine_state")]
        self.assertEqual(alerts[-1]["type"], "out_of_seat")
        self.assertEqual(alerts[-1]["evidence_time"], 2.0)

    def test_identity_loss_is_unobservable_not_absent(self):
        session, clock, _ = started(script=[(0, "confirmed", ALL_FALSE), (1, "uncertain", ALL_FALSE)])
        clock.advance(1.2)
        session.tick()
        events = session.drain_events()
        self.assertEqual([e["identity"] for e in of_type(events, "identity_state")],
                         ["confirmed", "uncertain"])
        last = of_type(events, "engine_state")[-1]["state"]
        self.assertEqual({v["state"] for v in last["indicators"].values()}, {"unobservable"})


class FailureTests(unittest.TestCase):
    def test_eof_completes_with_source_eof(self):
        session, clock, source = started(fps=5, frames=3)
        clock.advance(5)
        session.tick()
        self.assertEqual(session.state, "completed")
        self.assertEqual(session.termination_reason, "source_eof")
        self.assertEqual(len(of_type(session.drain_events(), "observation")), 3)
        self.assertTrue(source.closed)

    def test_disconnect_fails_session_without_further_results(self):
        session, clock, source = started(fps=5, disconnect_at=2)
        clock.advance(5)
        session.tick()
        events = session.drain_events()
        self.assertEqual(session.state, "failed")
        self.assertEqual(session.termination_reason, "source_disconnected")
        self.assertEqual(len(of_type(events, "observation")), 2)
        self.assertFalse(of_type(events, "session_state")[-1]["recorded"])
        self.assertTrue(source.closed)
        clock.advance(5)
        self.assertEqual(session.tick(), 0)

    def test_provider_failure_emits_error_and_no_fabricated_row(self):
        session, clock, _ = started(fps=5, fail_times=(0.4,))
        clock.advance(0.8)
        session.tick()
        events = session.drain_events()
        times = [event["video_time"] for event in of_type(events, "observation")]
        self.assertNotIn(0.4, times)
        self.assertEqual(of_type(events, "error")[0]["code"], "provider_failed")
        self.assertEqual(session.provider_errors, 1)
        self.assertEqual(session.state, "running")

    def test_contract_violation_fails_worker(self):
        class BadProvider:
            kind = "synthetic"

            def observe(self, frame):
                return {"time": frame.video_time, "identity": "uncertain",
                        "signals": dict(ALL_FALSE, out_of_seat=True), "values": {}}

        clock = FakeClock()
        session = LiveSession(SyntheticFrameSource(5, 10), BadProvider(), clock=clock)
        session.open()
        session.select_target()
        session.start()
        session.tick()
        self.assertEqual(session.state, "failed")
        self.assertEqual(session.termination_reason, "worker_failed")
        self.assertEqual(of_type(session.drain_events(), "observation"), [])

    def test_source_open_failure_fails_session(self):
        class BrokenSource(SyntheticFrameSource):
            def open(self):
                raise OSError("synthetic open failure")

        session = LiveSession(BrokenSource(5, 10), SyntheticProvider([(0, "confirmed", {})]),
                              clock=FakeClock())
        session.open()
        self.assertEqual(session.state, "failed")
        self.assertEqual(session.termination_reason, "source_disconnected")


if __name__ == "__main__":
    unittest.main()
