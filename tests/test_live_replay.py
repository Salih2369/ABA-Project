"""Recorder, performance, precomputed replay and file-as-live tests.

SYNTHETIC ONLY: documents and videos are generated in a temporary directory
and deleted afterwards; no real recording or model output is involved.
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from aba_demo.engine import INDICATORS
from aba_demo.export import file_sha256
from aba_demo.live.providers import PrecomputedProvider, SyntheticProvider
from aba_demo.live.recorder import FrameLedgerRecorder
from aba_demo.live.session import LiveSession
from aba_demo.live.source import FileAsLiveSource, SourceEnded, SourceFrame, SyntheticFrameSource

HAS_CV2 = importlib.util.find_spec("cv2") is not None
QUIET = dict.fromkeys(INDICATORS, False)
SHA = "a" * 64


class FakeClock:
    def __init__(self, now=10.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def of_type(events, event_type):
    return [event for event in events if event["event_type"] == event_type]


def run(session, clock, seconds, step=0.2):
    session.open()
    session.select_target()
    session.start()
    session.tick()
    elapsed = 0.0
    while elapsed < seconds and session.state == "running":
        clock.advance(step)
        elapsed += step
        session.tick()


def document(rows, sha=SHA, **overrides):
    return {"schema_version": 1, "mode": "precomputed",
            "source": {"name": "synthetic.mp4", "sha256": sha, "duration": 3.0, "fps": 5.0},
            "observations": rows, **overrides}


def row(time, identity="confirmed", **signals):
    values = dict.fromkeys(INDICATORS) if identity == "uncertain" else dict(QUIET, **signals)
    return {"time": time, "identity": identity, "signals": values, "values": {}}


class RecorderTests(unittest.TestCase):
    def test_ledger_counts_every_due_frame_even_when_analysis_fails(self):
        clock = FakeClock()
        recorder = FrameLedgerRecorder()
        provider = SyntheticProvider([(0, "confirmed", QUIET)], fail_times=(0.4, 0.6))
        session = LiveSession(SyntheticFrameSource(5, 10), provider, recorder, clock=clock)
        run(session, clock, 5)
        events = session.drain_events()
        completed = of_type(events, "session_state")[-1]
        self.assertEqual((completed["state"], completed["reason"]), ("completed", "source_eof"))
        self.assertFalse(completed["recorded"])
        artifact = completed["recording"]
        self.assertEqual(artifact["frames"], 10)
        self.assertEqual(artifact["dropped_frames"], 0)
        self.assertTrue(artifact["verified"])
        self.assertFalse(artifact["pixels_stored"])
        self.assertEqual(len(of_type(events, "observation")), 8)

    def test_ledger_digest_is_deterministic(self):
        digests = []
        for _ in range(2):
            recorder = FrameLedgerRecorder()
            recorder.start()
            for index in range(5):
                recorder.write(SourceFrame(index, index / 5))
            digests.append(recorder.finalize()["sha256"])
        self.assertEqual(digests[0], digests[1])

    def test_gaps_are_counted_not_hidden(self):
        recorder = FrameLedgerRecorder()
        recorder.start()
        for index in (0, 1, 4):
            recorder.write(SourceFrame(index, index / 5))
        self.assertEqual(recorder.finalize()["dropped_frames"], 2)

    def test_write_failure_fails_session_without_completion(self):
        clock = FakeClock()
        session = LiveSession(SyntheticFrameSource(5, 50), SyntheticProvider([(0, "confirmed", QUIET)]),
                              FrameLedgerRecorder(fail_at_frame=3), clock=clock)
        run(session, clock, 5)
        events = session.drain_events()
        self.assertEqual((session.state, session.termination_reason), ("failed", "recorder_failed"))
        self.assertNotIn("completed", [e["state"] for e in of_type(events, "session_state")])
        self.assertEqual(len(of_type(events, "observation")), 3)  # frame 3 was never analysed
        self.assertIsNone(session.recording_artifact)

    def test_finalize_failure_fails_session(self):
        clock = FakeClock()
        session = LiveSession(SyntheticFrameSource(5, 50), SyntheticProvider([(0, "confirmed", QUIET)]),
                              FrameLedgerRecorder(fail_on_finalize=True), clock=clock)
        run(session, clock, 1)
        session.stop()
        self.assertEqual((session.state, session.termination_reason), ("failed", "recorder_failed"))

    def test_pause_adds_nothing_to_the_ledger(self):
        clock = FakeClock()
        recorder = FrameLedgerRecorder()
        session = LiveSession(SyntheticFrameSource(5, 50), SyntheticProvider([(0, "confirmed", QUIET)]),
                              recorder, clock=clock)
        run(session, clock, 1)
        frames = recorder.frames
        session.pause()
        clock.advance(10)
        session.tick()
        self.assertEqual(recorder.frames, frames)


class PerformanceTests(unittest.TestCase):
    def test_slow_analysis_ages_and_marks_results_stale_without_skipping(self):
        clock = FakeClock()
        provider = SyntheticProvider([(0, "confirmed", QUIET)], cost_s=0.3, sleep=clock.advance)
        session = LiveSession(SyntheticFrameSource(5, 40), provider, clock=clock, stale_after_s=1.0)
        run(session, clock, 4)
        events = session.drain_events()
        observations = of_type(events, "observation")
        indices = [e["source_frame_index"] for e in observations]
        self.assertEqual(indices, list(range(len(indices))))  # contiguous: nothing skipped
        ages = [e["age_s"] for e in observations]
        self.assertGreater(ages[-1], ages[1])
        self.assertTrue(observations[-1]["stale"])
        self.assertFalse(observations[0]["stale"])
        performance = of_type(events, "performance")
        self.assertTrue(performance)
        self.assertAlmostEqual(performance[0]["provider_ms_p95"], 300, delta=1)
        self.assertGreater(session.stale_observations, 0)

    def test_fast_analysis_is_never_stale(self):
        clock = FakeClock()
        session = LiveSession(SyntheticFrameSource(5, 20), SyntheticProvider([(0, "confirmed", QUIET)]),
                              clock=clock)
        run(session, clock, 5)
        self.assertEqual(session.stale_observations, 0)
        self.assertTrue(of_type(session.drain_events(), "performance"))

    def test_future_rows_fail_the_worker(self):
        class FutureProvider:
            kind = "synthetic"

            def observe(self, frame):
                return [row(frame.video_time + 1.0)]

        clock = FakeClock()
        session = LiveSession(SyntheticFrameSource(5, 10), FutureProvider(), clock=clock)
        run(session, clock, 1)
        self.assertEqual((session.state, session.termination_reason), ("failed", "worker_failed"))


class PrecomputedProviderTests(unittest.TestCase):
    def test_emits_rows_causally_and_can_batch(self):
        provider = PrecomputedProvider(document([row(0.0), row(0.2), row(0.4, out_of_seat=True), row(1.0)]))
        self.assertEqual([r["time"] for r in provider.observe(SourceFrame(0, 0.0))], [0.0])
        self.assertEqual(provider.observe(SourceFrame(1, 0.1)), [])
        self.assertEqual([r["time"] for r in provider.observe(SourceFrame(2, 0.5))], [0.2, 0.4])
        self.assertEqual(provider.observe(SourceFrame(3, 0.9)), [])
        rows = provider.observe(SourceFrame(4, 1.0))
        self.assertEqual(set(rows[0]), {"time", "identity", "signals", "values"})

    def test_rejects_invalid_documents(self):
        cases = [
            {**document([row(0)]), "mode": "live"},
            {**document([row(0)]), "schema_version": 2},
            document([row(0)], sha="ABC"),
            document([]),
            document([row(0.4), row(0.2)]),
            document([{**row(0), "identity": "uncertain"}]),  # uncertain with non-null signals
            document([{**row(0), "signals": {"orientation": False}}]),
            document([{**row(0), "time": float("nan")}]),
        ]
        for case in cases:
            with self.assertRaises(ValueError):
                PrecomputedProvider(case)

    def test_rejects_mismatched_video(self):
        with self.assertRaises(ValueError):
            PrecomputedProvider(document([row(0)]), video_sha256="b" * 64)

    def test_from_file_rejects_duplicates_and_non_finite_numbers(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic-observations.json"
            path.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")
            with self.assertRaises(ValueError):
                PrecomputedProvider.from_file(path)
            text = json.dumps(document([row(0.5)]))
            self.assertIn('"time": 0.5', text)
            path.write_text(text.replace('"time": 0.5', '"time": NaN'), encoding="utf-8")
            with self.assertRaises(ValueError):
                PrecomputedProvider.from_file(path)


def write_synthetic_video(path: Path, frames=15, fps=10):
    import cv2
    import numpy as np
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (64, 48))
    for index in range(frames):
        writer.write(np.full((48, 64, 3), (index * 16) % 255, dtype=np.uint8))
    writer.release()


@unittest.skipUnless(HAS_CV2, "file-as-live tests need OpenCV (opencv-python-headless)")
class FileAsLiveTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.video = Path(self.folder.name) / "SYNTHETIC_COLOR_PATTERN.avi"
        write_synthetic_video(self.video)

    def tearDown(self):
        self.folder.cleanup()

    def test_reads_every_frame_with_nominal_timestamps(self):
        source = FileAsLiveSource(self.video)
        source.open()
        frames = []
        with self.assertRaises(SourceEnded):
            while True:
                frames.append(source.read())
        source.close()
        self.assertEqual([f.index for f in frames], list(range(15)))
        self.assertAlmostEqual(frames[-1].video_time, 1.4)
        self.assertEqual(source.describe()["timestamp_basis"], "nominal_fps_cfr_only")

    def test_rejects_non_files(self):
        with self.assertRaises(ValueError):
            FileAsLiveSource(Path(self.folder.name) / "missing.mp4")

    def test_end_to_end_precomputed_replay_bound_to_video_hash(self):
        digest = file_sha256(self.video)
        rows = [row(round(t * 0.2, 1)) for t in range(8)]
        provider = PrecomputedProvider(document(rows, sha=digest), video_sha256=digest)
        clock = FakeClock()
        session = LiveSession(FileAsLiveSource(self.video), provider, clock=clock)
        run(session, clock, 3, step=0.1)
        events = session.drain_events()
        self.assertEqual((session.state, session.termination_reason), ("completed", "source_eof"))
        self.assertEqual([e["video_time"] for e in of_type(events, "observation")], [r["time"] for r in rows])
        self.assertTrue(all(e["provider"] == "precomputed" for e in events))
        self.assertEqual(of_type(events, "session_state")[-1]["recording"]["frames"], 15)

    def test_replay_scenario_validates_the_pair(self):
        from aba_demo.live.scenarios import ReplayScenario
        observations = Path(self.folder.name) / "synthetic-observations.json"
        observations.write_text(json.dumps(document([row(0.0)], sha=file_sha256(self.video))), encoding="utf-8")
        scenario = ReplayScenario(self.video, observations)
        self.assertEqual(scenario.describe()["kind"], "precomputed")
        source, provider, _ = scenario.build()
        self.assertEqual((source.kind, provider.kind), ("file", "precomputed"))
        observations.write_text(json.dumps(document([row(0.0)], sha="c" * 64)), encoding="utf-8")
        with self.assertRaises(ValueError):
            ReplayScenario(self.video, observations)


if __name__ == "__main__":
    unittest.main()
