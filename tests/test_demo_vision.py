"""Synthetic geometry/unit fixtures only; NOT observed children or validation."""
import copy
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from aba_demo import vision


def synthetic_pose():
    points = [[0.0, 0.0, 0.0] for _ in range(17)]
    for i, x, y in [(0, 100, 40), (3, 80, 40), (4, 120, 40),
                    (5, 80, 80), (6, 120, 80), (9, 70, 150),
                    (10, 130, 150), (11, 85, 180), (12, 115, 180),
                    (13, 85, 260), (14, 115, 260),
                    (15, 85, 340), (16, 115, 340)]:
        points[i] = [x, y, .99]
    return points


class FeatureTests(unittest.TestCase):
    def test_motion_is_torso_and_time_normalized_with_relative_hands(self):
        f = vision.PoseFeatures({})
        p = synthetic_pose()
        self.assertIsNone(f.observe(p, 0, (400, 400))['signals']['body_motion'])
        moved = [[x + 50, y, c] for x, y, c in p]
        result = f.observe(moved, .5, (400, 400))
        self.assertAlmostEqual(result['values']['body_speed'], 1.0)
        self.assertEqual(result['values']['body_motion'], result['values']['body_speed'])
        self.assertEqual(result['values']['hand_motion'], result['values']['hand_speed'])
        self.assertAlmostEqual(result['values']['hand_speed'], 0.0)
        self.assertTrue(result['signals']['body_motion'])
        self.assertFalse(result['signals']['hand_motion'])
        moved[9][0] += 100
        self.assertTrue(f.observe(moved, 1, (400, 400))['signals']['hand_motion'])
        f.observe(None, 1.5, (400, 400))
        self.assertIsNone(f.observe(moved, 2, (400, 400))['signals']['body_motion'])
        with self.assertRaises(ValueError):
            f.observe(p, 1, (400, 400))

    def test_orientation_requires_face_geometry_and_supported_task_direction(self):
        p = synthetic_pose()
        p[0][0] = 112  # Synthetic nose offset toward image-right.
        config = {'task_targets': [[.7, 0, 1, .4]]}
        self.assertFalse(vision.PoseFeatures(config).observe(p, 0, (400, 400))['signals']['orientation'])
        config['task_targets'] = [[0, 0, .1, .4]]
        self.assertTrue(vision.PoseFeatures(config).observe(p, 0, (400, 400))['signals']['orientation'])
        p[3][2] = .1
        self.assertIsNone(vision.PoseFeatures(config).observe(p, 0, (400, 400))['signals']['orientation'])
        self.assertIsNone(vision.PoseFeatures({}).observe(synthetic_pose(), 0, (400, 400))['signals']['orientation'])

    def test_seat_requires_leg_geometry_and_stable_posture_not_zone_exit(self):
        config = {'seat_roi': [0, 0, 1, 1], 'posture_stability_s': .4}
        f = vision.PoseFeatures(config)
        standing = synthetic_pose()
        self.assertIsNone(f.observe(standing, 0, (400, 400))['signals']['out_of_seat'])
        r = f.observe(standing, .5, (400, 400))
        self.assertIsNone(r['signals']['out_of_seat'])  # No seated baseline yet.
        self.assertEqual(r['values']['posture'], 'standing')
        sitting = copy.deepcopy(standing)
        for hip, knee, ankle in [(11, 13, 15), (12, 14, 16)]:
            sitting[knee] = [sitting[hip][0] + 80, 190, .99]
            sitting[ankle] = [sitting[hip][0] + 80, 270, .99]
        self.assertIsNone(f.observe(sitting, 1, (400, 400))['signals']['out_of_seat'])
        r = f.observe(sitting, 1.5, (400, 400))
        self.assertIs(r['signals']['out_of_seat'], False)
        self.assertIs(r['signals']['posture_change'], True)
        self.assertEqual(r['values']['posture_transition_count'], 1)
        self.assertIs(f.observe(sitting, 2, (400, 400))['signals']['posture_change'], False)
        f.observe(standing, 2.5, (400, 400))
        departed = f.observe(standing, 3, (400, 400))
        self.assertIs(departed['signals']['out_of_seat'], True)
        self.assertEqual(departed['values']['posture_transition_count'], 2)
        sitting[15][2] = 0
        self.assertIsNone(f.observe(sitting, 3.5, (400, 400))['signals']['out_of_seat'])

    def test_in_place_gross_movement_uses_non_wrist_joint_energy(self):
        p = synthetic_pose()
        p[7], p[8] = [60, 120, .99], [140, 120, .99]
        f = vision.PoseFeatures({})
        f.observe(p, 0, (400, 400))
        p[7][0] -= 100
        p[8][0] += 100
        r = f.observe(p, .5, (400, 400))
        self.assertIs(r['signals']['body_motion'], True)
        self.assertIs(r['signals']['hand_motion'], False)
        self.assertGreater(r['values']['body_speed'], .35)

    def test_config_rejects_nonfinite_inverted_or_unnormalized_regions(self):
        for config in ({'seat_roi': [-.1, 0, 1, 1]},
                       {'seat_roi': [0, 0, float('nan'), 1]},
                       {'task_targets': [[.8, 0, .2, 1]]},
                       {'task_targets': [[0, 0, 1]]}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                vision.PoseFeatures(config)

    def test_posture_does_not_bridge_unobserved_time(self):
        f = vision.PoseFeatures({'posture_stability_s': .4})
        p = synthetic_pose()
        f.observe(p, 0, (400, 400))
        self.assertIsNone(f.observe(p, 10, (400, 400))['signals']['posture_change'])
        self.assertNotIn('posture', f.observe(p, 10.1, (400, 400))['values'])

    def test_stable_transition_survives_brief_intermediate_pose(self):
        p = synthetic_pose()
        f = vision.PoseFeatures({'posture_stability_s': .4})
        f.observe(p, 0, (400, 400))
        f.observe(p, .5, (400, 400))
        for hip, knee, ankle in [(11, 13, 15), (12, 14, 16)]:
            p[knee] = [p[hip][0] + 80, 230, .99]
            p[ankle] = [p[hip][0] + 80, 310, .99]
        self.assertIsNone(f.observe(p, .7, (400, 400))['signals']['posture_change'])
        for knee, ankle in [(13, 15), (14, 16)]:
            p[knee][1], p[ankle][1] = 190, 270
        f.observe(p, .9, (400, 400))
        self.assertIs(f.observe(p, 1.4, (400, 400))['signals']['posture_change'], True)

    def test_missing_pose_is_unknown_not_absent(self):
        result = vision.PoseFeatures({}).observe(None, 0, (400, 400))
        self.assertEqual(result['signals'], dict.fromkeys(
            ['orientation', 'body_motion', 'out_of_seat', 'hand_motion', 'posture_change']))


class TrackerProfileTests(unittest.TestCase):
    def test_reviewed_profiles_are_portable_and_arbitrary_paths_are_rejected(self):
        self.assertEqual(vision.resolve_tracker_profile({'tracker_profile': 'bytetrack'}),
                         'bytetrack.yaml')
        self.assertEqual(vision.resolve_tracker_profile({'tracker_profile': 'botsort'}),
                         'botsort.yaml')
        reid = Path(vision.resolve_tracker_profile({'tracker_profile': 'botsort_reid'}))
        self.assertEqual(reid.name, 'botsort_reid.yaml')
        self.assertTrue(reid.is_file())
        self.assertIn('with_reid: true', reid.read_text(encoding='utf-8').lower())
        with self.assertRaisesRegex(ValueError, 'tracker_profile'):
            vision.resolve_tracker_profile({'tracker_profile': '../../arbitrary.yaml'})


class IdentityTests(unittest.TestCase):
    def test_brief_missing_frame_recovers_only_the_same_continuous_track(self):
        target = {'id': 4, 'xyxy': [.10, .10, .30, .80]}
        returned = {'id': 4, 'xyxy': [.11, .10, .31, .80]}
        guard = vision.TargetLock({'identity_recovery_max_gap_s': .3})
        guard.select(.2, .4, [target])

        self.assertEqual(guard.update([target], time=0), target)
        self.assertIsNone(guard.update([], time=.1))
        self.assertEqual(guard.identity, 'uncertain')
        self.assertEqual(guard.reason, 'target_temporarily_missing')
        self.assertEqual(guard.update([returned], time=.2), returned)
        self.assertEqual(guard.identity, 'confirmed')
        self.assertIsNone(guard.reason)

    def test_different_id_is_never_selected_while_same_id_gets_bounded_recovery(self):
        target = {'id': 4, 'xyxy': [.10, .10, .30, .80]}
        returned = {'id': 4, 'xyxy': [.11, .10, .31, .80]}
        other_id = {'id': 9, 'xyxy': [.11, .10, .31, .80]}
        guard = vision.TargetLock({
            'identity_recovery_max_gap_s': .3,
            'identity_overlap_threshold': 1.0,
        })
        guard.select(.2, .4, [target])

        self.assertEqual(guard.update([target], time=0), target)
        self.assertIsNone(guard.update([other_id], time=.1))
        self.assertEqual(guard.identity, 'uncertain')
        self.assertEqual(guard.reason, 'target_temporarily_missing')
        self.assertTrue(guard.recovery_pending)
        self.assertEqual(guard.target_id, 4)
        self.assertEqual(guard.update([returned, other_id], time=.2), returned)
        self.assertEqual(guard.identity, 'confirmed')
        self.assertEqual(guard.target_id, 4)

    def test_recovery_rejects_long_gap_and_discontinuous_return(self):
        target = {'id': 4, 'xyxy': [.10, .10, .30, .80]}
        nearby = {'id': 4, 'xyxy': [.11, .10, .31, .80]}
        jumped = {'id': 4, 'xyxy': [.70, .10, .90, .80]}

        long_gap = vision.TargetLock({'identity_recovery_max_gap_s': .3})
        long_gap.select(.2, .4, [target])
        long_gap.update([target], time=0)
        long_gap.update([], time=.1)
        self.assertIsNone(long_gap.update([nearby], time=.31))
        self.assertEqual(long_gap.reason, 'target_lost_reselect')
        self.assertFalse(long_gap.recovery_pending)

        discontinuous = vision.TargetLock({'identity_recovery_max_gap_s': .3})
        discontinuous.select(.2, .4, [target])
        discontinuous.update([target], time=0)
        discontinuous.update([], time=.1)
        self.assertIsNone(discontinuous.update([jumped], time=.2))
        self.assertEqual(discontinuous.reason, 'discontinuous_track_reselect')
        self.assertFalse(discontinuous.recovery_pending)

    def test_configured_half_second_gap_recovers_same_continuous_id(self):
        target = {'id': 4, 'xyxy': [.10, .10, .30, .80]}
        returned = {'id': 4, 'xyxy': [.11, .10, .31, .80]}
        guard = vision.TargetLock({'identity_recovery_max_gap_s': .5})
        guard.select(.2, .4, [target])
        self.assertEqual(guard.update([target], time=0), target)
        self.assertIsNone(guard.update([], time=.4))
        self.assertEqual(guard.update([returned], time=.45), returned)
        self.assertEqual(guard.identity, 'confirmed')

    def test_recovery_gap_is_bounded_to_one_second(self):
        for value in (-.01, 1.01, True, float('inf'), float('nan')):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, r'\[0, 1\.0\]'):
                vision.TargetLock({'identity_recovery_max_gap_s': value})

    def test_reviewed_overlap_override_keeps_loss_and_jump_fail_closed(self):
        target = {'id': 4, 'xyxy': [.1, .1, .3, .8]}
        crossing = {'id': 9, 'xyxy': [.15, .1, .4, .8]}
        jumped = {'id': 4, 'xyxy': [.8, .7, .9, .9]}

        loss_guard = vision.TargetLock({'identity_overlap_threshold': 1.0})
        loss_guard.select(.12, .4, [target, crossing])
        self.assertEqual(loss_guard.update([target, crossing]), target)
        self.assertIsNone(loss_guard.update([crossing]))
        self.assertEqual(loss_guard.reason, 'target_lost_reselect')

        jump_guard = vision.TargetLock({'identity_overlap_threshold': 1.0})
        jump_guard.select(.12, .4, [target, crossing])
        self.assertIsNone(jump_guard.update([jumped, crossing]))
        self.assertEqual(jump_guard.reason, 'discontinuous_track_reselect')

    def test_discontinuous_same_id_jump_latches_uncertainty(self):
        a = {'id': 4, 'xyxy': [.1, .1, .2, .3]}
        jumped = {'id': 4, 'xyxy': [.8, .7, .9, .9]}
        guard = vision.TargetLock({})
        guard.select(.15, .2, [a])
        self.assertIsNone(guard.update([jumped]))
        self.assertEqual(guard.identity, 'uncertain')
        self.assertIsNone(guard.update([a]))

    def test_explicit_selection_loss_and_crossing_latch_until_reselection(self):
        a = {'id': 4, 'xyxy': [.1, .1, .3, .8]}
        b = {'id': 9, 'xyxy': [.6, .1, .9, .9]}
        guard = vision.TargetLock({})
        self.assertIsNone(guard.update([a, b]))
        self.assertEqual(guard.identity, 'unselected')
        guard.select(.2, .4, [a, b])
        self.assertEqual(guard.update([a, b]), a)
        self.assertIsNone(guard.update([b]))
        self.assertEqual(guard.identity, 'uncertain')
        self.assertIsNone(guard.update([a, b]))  # Even same ID returning is NOT rebound.
        guard.select(.2, .4, [a, b])
        crossing = {'id': 9, 'xyxy': [.15, .1, .4, .8]}
        self.assertIsNone(guard.update([a, crossing]))
        self.assertIsNone(guard.update([a, b]))
        with self.assertRaises(ValueError):
            guard.select(.2, .4, [a, crossing])
        with self.assertRaises(ValueError):
            guard.select(1.2, .5, [a, b])


class ArrayStub:
    """Dependency-boundary stand-in, never exported as model observations."""
    def __init__(self, value):
        self.value = value
    def cpu(self):
        return self
    def tolist(self):
        return self.value
    def tobytes(self):
        return b'unit-test-jpeg-boundary'


class AdapterTests(unittest.TestCase):
    def test_video_adapter_passes_selected_reid_profile_to_ultralytics(self):
        calls = []
        class Capture:
            released = False
            def isOpened(self):
                return True
            def get(self, prop):
                return {1: 10, 2: 1, 3: 400, 4: 400, 5: 0}[prop]
            def read(self):
                return True, SimpleNamespace(shape=(400, 400, 3))
            def release(self):
                self.released = True
        capture = Capture()
        class Model:
            def __init__(self, weights):
                pass
            def track(self, frame, **kwargs):
                calls.append(kwargs)
                return [SimpleNamespace(
                    boxes=SimpleNamespace(xyxy=ArrayStub([]), id=None), keypoints=None)]
        cv = SimpleNamespace(VideoCapture=lambda _: capture, CAP_PROP_FPS=1,
                             CAP_PROP_FRAME_COUNT=2, CAP_PROP_FRAME_WIDTH=3,
                             CAP_PROP_FRAME_HEIGHT=4, CAP_PROP_POS_MSEC=5,
                             imencode=lambda *args: (True, ArrayStub(None)))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'portable-input.mp4'
            path.write_bytes(b'synthetic tracker dependency boundary')
            with patch.dict('sys.modules', {'cv2': cv, 'ultralytics': SimpleNamespace(YOLO=Model)}):
                analyzer = vision.VideoAnalyzer(path, {'tracker_profile': 'botsort_reid'})
                try:
                    analyzer.preview()
                finally:
                    analyzer.close()
        self.assertEqual(Path(calls[0]['tracker']).name, 'botsort_reid.yaml')

    def test_video_adapter_keeps_dropout_null_then_recovers_same_id(self):
        class Capture:
            index = -1
            def isOpened(self):
                return True
            def get(self, prop):
                return {1: 10, 2: 3, 3: 400, 4: 400, 5: max(0, self.index) * 100}[prop]
            def read(self):
                self.index += 1
                return (self.index < 3, SimpleNamespace(shape=(400, 400, 3)))
            def release(self):
                pass
        capture = Capture()
        class Model:
            calls = 0
            def __init__(self, weights):
                pass
            def track(self, frame, **kwargs):
                index = self.calls
                self.calls += 1
                if index == 1:
                    boxes = SimpleNamespace(xyxy=ArrayStub([]), id=None)
                    keypoints = None
                else:
                    boxes = SimpleNamespace(
                        xyxy=ArrayStub([[40 + index, 20, 160 + index, 380]]),
                        id=ArrayStub([7]))
                    keypoints = SimpleNamespace(data=ArrayStub([synthetic_pose()]))
                return [SimpleNamespace(boxes=boxes, keypoints=keypoints)]
        cv = SimpleNamespace(VideoCapture=lambda _: capture, CAP_PROP_FPS=1,
                             CAP_PROP_FRAME_COUNT=2, CAP_PROP_FRAME_WIDTH=3,
                             CAP_PROP_FRAME_HEIGHT=4, CAP_PROP_POS_MSEC=5,
                             imencode=lambda *args: (True, ArrayStub(None)))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'brief-dropout.mp4'
            path.write_bytes(b'synthetic brief dropout dependency boundary')
            with patch.dict('sys.modules', {'cv2': cv, 'ultralytics': SimpleNamespace(YOLO=Model)}):
                analyzer = vision.VideoAnalyzer(path, {'identity_recovery_max_gap_s': .3})
                try:
                    analyzer.preview()
                    analyzer.select_target(.2, .5)
                    self.assertEqual(analyzer.analyze(0)['identity'], 'confirmed')
                    missing = analyzer.analyze(.1)
                    self.assertEqual(missing['identity'], 'uncertain')
                    self.assertEqual(missing['values']['identity_reason'], 'target_temporarily_missing')
                    self.assertTrue(all(value is None for value in missing['signals'].values()))
                    self.assertEqual(analyzer.analyze(.2)['identity'], 'confirmed')
                finally:
                    analyzer.close()

    def test_video_adapter_reads_tracks_sequentially_and_closes(self):
        calls = []
        class Capture:
            index = -1
            released = False
            def isOpened(self):
                return True
            def get(self, prop):
                return {1: 10, 2: 4, 3: 400, 4: 400, 5: max(0, self.index) * 100}[prop]
            def read(self):
                self.index += 1
                return (self.index < 4, SimpleNamespace(shape=(400, 400, 3)))
            def release(self):
                self.released = True
        capture = Capture()
        class Model:
            def __init__(self, weights):
                self.weights = weights
            def track(self, frame, **kwargs):
                calls.append(kwargs)
                return [SimpleNamespace(
                    boxes=SimpleNamespace(xyxy=ArrayStub([[40, 20, 160, 380]]), id=ArrayStub([7])),
                    keypoints=SimpleNamespace(data=ArrayStub([synthetic_pose()])))]
        cv = SimpleNamespace(VideoCapture=lambda _: capture, CAP_PROP_FPS=1,
                             CAP_PROP_FRAME_COUNT=2, CAP_PROP_FRAME_WIDTH=3,
                             CAP_PROP_FRAME_HEIGHT=4, CAP_PROP_POS_MSEC=5,
                             imencode=lambda *args: (True, ArrayStub(None)))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'approved-local-video.mp4'
            path.write_bytes(b'unit test reader boundary; not a real video')
            with patch.dict('sys.modules', {'cv2': cv, 'ultralytics': SimpleNamespace(YOLO=Model)}):
                analyzer = vision.VideoAnalyzer(path, {})
                try:
                    preview = analyzer.preview()
                    self.assertEqual(preview['width'], 400)
                    self.assertTrue(preview['image'])
                    self.assertEqual(preview['boxes'][0]['xyxy'], [.1, .05, .4, .95])
                    self.assertEqual(analyzer.analyze(0)['identity'], 'uncertain')
                    analyzer.select_target(.2, .5)
                    first = analyzer.analyze(0)
                    self.assertEqual(first['identity'], 'confirmed')
                    result = analyzer.analyze(.3)
                    self.assertAlmostEqual(result['time'], .3)
                    self.assertEqual(len(calls), 4)
                    self.assertTrue(all(c['persist'] and c['tracker'] == 'bytetrack.yaml' for c in calls))
                    self.assertEqual(result['values']['camera_stability'], 'unchecked_fixed_camera_required')
                    with self.assertRaises(ValueError):
                        analyzer.analyze(.1)
                    with self.assertRaises(EOFError):
                        analyzer.analyze(.5)
                finally:
                    analyzer.close()
                self.assertTrue(capture.released)


class ExportTests(unittest.TestCase):
    def test_export_rejects_video_that_differs_from_reviewed_sha256(self):
        from aba_demo import export

        class Boundary:
            fps, frame_count, duration = 10, 1, .1

            def __init__(self, *args):
                pass

            def preview(self):
                return {'boxes': []}

            def select_target(self, x, y):
                return None

            def analyze(self, t):
                return {'time': t, 'identity': 'uncertain',
                        'signals': dict.fromkeys(vision.SIGNALS), 'values': {}}

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as td:
            video, output = Path(td) / 'boundary.mp4', Path(td) / 'output.json'
            video.write_bytes(b'synthetic reviewed-video mismatch boundary')
            config = {'target_click': [.2, .3], 'reviewed_video_sha256': '0' * 64}
            with patch.object(export, 'VideoAnalyzer', Boundary):
                with self.assertRaisesRegex(ValueError, 'reviewed video SHA-256'):
                    export.export_video(video, config, output)
            self.assertFalse(output.exists())

    def test_export_rejects_selected_target_that_differs_from_reviewed_target(self):
        from aba_demo import export

        class Boundary:
            fps, frame_count, duration = 10, 1, .1

            def __init__(self, *args):
                self.closed = False

            def preview(self):
                return {'boxes': []}

            def select_target(self, x, y):
                return {'identity': 'confirmed', 'target_id': 999}

            def analyze(self, t):
                return {'time': t, 'identity': 'confirmed',
                        'signals': dict.fromkeys(vision.SIGNALS), 'values': {}}

            def close(self):
                self.closed = True

        with tempfile.TemporaryDirectory() as td:
            video, output = Path(td) / 'boundary.mp4', Path(td) / 'output.json'
            video.write_bytes(b'synthetic reviewed-target mismatch boundary')
            config = {'target_click': [.2, .3], 'reviewed_target_track_id': 2}
            with patch.object(export, 'VideoAnalyzer', Boundary):
                with self.assertRaisesRegex(ValueError, 'reviewed target track'):
                    export.export_video(video, config, output)
            self.assertFalse(output.exists())

    def test_export_records_and_tolerates_one_unreadable_trailing_frame(self):
        from aba_demo import export

        class Boundary:
            fps, frame_count, duration = 10, 4, .4

            def __init__(self, *args):
                self.frame_index = -1

            def preview(self):
                self.frame_index = 0
                return {'boxes': []}

            def select_target(self, x, y):
                pass

            def analyze(self, t):
                index = round(t * self.fps)
                if index == self.frame_count - 1:
                    self.frame_index = index - 1
                    raise EOFError('End of video or unreadable frame')
                self.frame_index = index
                return {'time': t, 'identity': 'uncertain',
                        'signals': dict.fromkeys(vision.SIGNALS), 'values': {}}

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as td:
            video, output = Path(td) / 'boundary.mp4', Path(td) / 'output.json'
            video.write_bytes(b'synthetic trailing decoder boundary')
            config = {'target_click': [.2, .3], 'sample_hz': 10,
                      'tracker_profile': 'botsort_reid'}
            with patch.object(export, 'VideoAnalyzer', Boundary):
                result = export.export_video(video, config, output)

            self.assertTrue(output.exists())
            self.assertEqual([o['time'] for o in result['observations']], [0, .1, .2])
            self.assertEqual(result['source']['frame_count'], 4)
            self.assertEqual(result['source']['decoded_frame_count'], 3)
            self.assertEqual(result['source']['trailing_unreadable_frames'], 1)
            self.assertEqual(result['provenance']['decode_completion'],
                             'trailing_eof_within_tolerance')
            self.assertEqual(result['provenance']['tracker'], 'botsort_reid.yaml')

    def test_export_tolerates_three_declared_but_undecodable_trailing_frames(self):
        from aba_demo import export

        class Boundary:
            fps, frame_count, duration = 10, 10, 1

            def __init__(self, *args):
                self.frame_index = -1

            def preview(self):
                self.frame_index = 0
                return {'boxes': []}

            def select_target(self, x, y):
                pass

            def analyze(self, t):
                if t:
                    self.frame_index = 6
                    raise EOFError('End of video or unreadable frame')
                self.frame_index = 0
                return {'time': t, 'identity': 'uncertain',
                        'signals': dict.fromkeys(vision.SIGNALS), 'values': {}}

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as td:
            video, output = Path(td) / 'boundary.mp4', Path(td) / 'output.json'
            video.write_bytes(b'synthetic three-frame trailing decoder boundary')
            config = {'target_click': [.2, .3], 'sample_hz': 1}
            with patch.object(export, 'VideoAnalyzer', Boundary):
                result = export.export_video(video, config, output)

            self.assertTrue(output.exists())
            self.assertEqual([o['time'] for o in result['observations']], [0])
            self.assertEqual(result['source']['decoded_frame_count'], 7)
            self.assertEqual(result['source']['trailing_unreadable_frames'], 3)
            self.assertEqual(result['provenance']['decode_completion'],
                             'trailing_eof_within_tolerance')

    def test_export_rejects_large_trailing_decode_gap_without_output(self):
        from aba_demo import export

        class Boundary:
            fps, frame_count, duration = 10, 10, 1

            def __init__(self, *args):
                self.frame_index = -1

            def preview(self):
                self.frame_index = 0
                return {'boxes': []}

            def select_target(self, x, y):
                pass

            def analyze(self, t):
                if t:
                    self.frame_index = 2
                    raise EOFError('End of video or unreadable frame')
                self.frame_index = 0
                return {'time': t, 'identity': 'uncertain',
                        'signals': dict.fromkeys(vision.SIGNALS), 'values': {}}

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as td:
            video, output = Path(td) / 'boundary.mp4', Path(td) / 'output.json'
            video.write_bytes(b'synthetic early decoder boundary')
            config = {'target_click': [.2, .3], 'sample_hz': 1}
            with patch.object(export, 'VideoAnalyzer', Boundary):
                with self.assertRaises(EOFError):
                    export.export_video(video, config, output)
            self.assertFalse(output.exists())

    def test_explicit_reselection_occurs_on_current_sample_then_reobserves(self):
        from aba_demo import export
        calls = []
        class Boundary:
            fps, frame_count, duration = 10, 4, .4
            def __init__(self, *args):
                self.identity = 'uncertain'
            def preview(self):
                calls.append('preview')
            def select_target(self, x, y):
                calls.append(('select', x, y))
                self.identity = 'confirmed'
            def analyze(self, t):
                calls.append(('analyze', t))
                return {'time': t, 'identity': self.identity, 'signals': dict.fromkeys(vision.SIGNALS), 'values': {}}
            def close(self):
                calls.append('close')
        with tempfile.TemporaryDirectory() as td:
            video, output = Path(td) / 'boundary.mp4', Path(td) / 'output.json'
            video.write_bytes(b'not video: explicit IO contract fixture')
            config = {'target_click': [.2, .3], 'target_reselections': [{'time': .1, 'click': [.7, .4]}]}
            with patch.object(export, 'VideoAnalyzer', Boundary):
                export.export_video(video, config, output)
            self.assertEqual(calls, ['preview', ('select', .2, .3), ('analyze', 0),
                                    ('analyze', .2), ('select', .7, .4), ('analyze', .2),
                                    ('analyze', .3), 'close'])
            for events in ([{'time': -1, 'click': [.2, .3]}],
                           [{'time': .1, 'click': [float('nan'), .3]}],
                           [{'time': .1, 'click': [.2, .3]}, {'time': .1, 'click': [.2, .3]}]):
                with self.subTest(events=events), patch.object(export, 'VideoAnalyzer', Boundary), self.assertRaises(ValueError):
                    export.export_video(video, {**config, 'target_reselections': events}, output)

    def test_export_cli_writes_bound_provenance_and_closes_reader(self):
        import hashlib
        import json
        from aba_demo import export
        created = []
        class AnalyzerBoundary:
            def __init__(self, path, config):
                self.fps, self.frame_count, self.duration = 10, 4, .4
                self.closed = False
                created.append(self)
            def preview(self):
                return {'boxes': []}
            def select_target(self, x, y):
                self.click = (x, y)
            def analyze(self, t):
                return {'time': t, 'identity': 'uncertain', 'signals': dict.fromkeys(vision.SIGNALS), 'values': {}}
            def close(self):
                self.closed = True
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            video = folder / 'boundary-not-a-real-video.mp4'
            video.write_bytes(b'synthetic dependency boundary only')
            config = folder / 'config.json'
            config.write_text(json.dumps({'target_click': [.2, .5]}))
            output = folder / 'unit-output.json'
            with patch.object(export, 'VideoAnalyzer', AnalyzerBoundary):
                self.assertEqual(export.main(['--video', str(video), '--config', str(config), '--output', str(output)]), 0)
            data = json.loads(output.read_text())
            self.assertEqual(data['schema_version'], 1)
            self.assertEqual(data['mode'], 'precomputed')
            self.assertEqual(data['source']['name'], video.name)
            self.assertEqual(data['source']['sha256'], hashlib.sha256(video.read_bytes()).hexdigest())
            self.assertEqual(data['source']['duration'], .4)
            self.assertTrue(created[0].closed)
            self.assertEqual(created[0].click, (.2, .5))
            times = [o['time'] for o in data['observations']]
            self.assertEqual(times, sorted(set(times)))
            self.assertEqual(times[0], 0)
            self.assertEqual(times[-1], .3)
            self.assertNotIn('frame', data['observations'][0])


if __name__ == '__main__':
    unittest.main()
