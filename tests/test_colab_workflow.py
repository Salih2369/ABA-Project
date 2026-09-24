import base64
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from aba_demo import colab_workflow as workflow
from aba_demo.vision import SIGNALS


class FakeAnalyzer:
    """Synthetic decoder boundary, never actual video inference."""
    def __init__(self, path, config):
        self.fps, self.frame_count, self.duration = 10, 51, 5.1
        self.frame_index, self.target_id, self.closed = -1, None, False
        self.stop = config.get('fake_stop', 51)
        self.calls = []
    def preview(self):
        self.frame_index = max(0, self.frame_index)
        return {'image': '', 'time': self.frame_index / self.fps, 'boxes': []}
    def select_target(self, x, y):
        self.target_id = 2 if self.frame_index < 5 else 6
        return {'identity': 'confirmed', 'target_id': self.target_id}
    def analyze(self, time):
        index = round(time * self.fps)
        if index >= self.stop:
            raise EOFError('synthetic EOF')
        self.frame_index = index
        self.calls.append(index)
        confirmed = self.target_id == (2 if index < 5 else 6)
        return {'time': time, 'identity': 'confirmed' if confirmed else 'uncertain',
                'signals': dict.fromkeys(SIGNALS, False if confirmed else None),
                'values': {'identity_reason': None if confirmed else 'target_lost_reselect'},
                'boxes': [], 'target_box': None}
    def close(self):
        self.closed = True


class ContextFakeAnalyzer(FakeAnalyzer):
    """Fake with valid visual evidence only for context workflow tests."""
    def preview(self):
        preview = super().preview()
        preview['image'] = jpeg_base64()
        return preview

    def analyze(self, time):
        row = super().analyze(time)
        row['target_box'] = [.1, .2, .6, .9] if row['identity'] == 'confirmed' else None
        return row


class LongContextFakeAnalyzer(ContextFakeAnalyzer):
    def __init__(self, path, config):
        super().__init__(path, config)
        self.frame_count, self.duration = 101, 10.1
        self.stop = config.get('fake_stop', 101)


def jpeg_base64(size=(80, 60), color=(30, 120, 210)):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', size, color).save(buffer, format='JPEG', quality=95)
    return base64.b64encode(buffer.getvalue()).decode('ascii')


def context_provenance(source_sha256, provider='provider-name'):
    return {
        'adapter': 'openrouter', 'source_sha256': source_sha256,
        'requested_model': 'vendor/model:free', 'resolved_model': 'vendor/model:free',
        'endpoint_provider': provider, 'external_processing': True,
        'structured_outputs': True, 'data_collection': 'deny',
        'zero_data_retention_required': False,
        'sent_fields': ['scene_jpeg', 'target_crop_jpeg', 'timestamps', 'target_box'],
        'consent': {'required': False, 'granted': False, 'source_sha256': None},
    }


def context_observation(times):
    return {
        'activity_suggestion': 'table', 'activity_status': 'supported',
        'target_material_interaction': 'yes',
        'adult_target_interaction_visible': 'ambiguous',
        'evidence_times': list(times),
    }


def finish_context_session(session):
    session.select([.2, .4], expected_target_id=2, confirmed=True)
    session.advance(.5)
    session.select([.3, .4], expected_target_id=6, confirmed=True)
    return session.finish(max_uncertain_fraction=.1)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.video = self.root / 'authorized.mp4'
        self.video.write_bytes(b'synthetic-source-not-video')
    def tearDown(self):
        self.temp.cleanup()
    def session(self, **config):
        return workflow.ReviewSession(self.video, config, self.root / 'run', analyzer_factory=FakeAnalyzer)
    def context_session(self, **config):
        return workflow.ReviewSession(
            self.video, config, self.root / 'context-run', analyzer_factory=ContextFakeAnalyzer)

    def test_context_state_and_evidence_retain_target_box_only_outside_tracking(self):
        session = self.context_session()
        self.assertEqual(session.context_pending.name, 'context.pending.json')
        self.assertEqual(session.context_output.name, 'context.json')
        self.assertEqual(session.context_review_report.name, 'context-review-report.json')
        self.assertFalse(session.context_pending.exists())
        self.assertFalse(session.context_output.exists())
        self.assertFalse(session.context_review_report.exists())

        session.select([.2, .4], expected_target_id=2, confirmed=True)

        self.assertEqual(session.evidence[0]['target_box'], [.1, .2, .6, .9])
        self.assertNotIn('target_box', session.audit[0])
        self.assertNotIn('target_box', session.observations[0])
        session.close()

    def test_context_window_builds_one_and_four_target_grounded_jpegs_in_memory(self):
        from PIL import Image
        from aba_demo.context_schema import MAX_IMAGE_BYTES, validate_context_window

        evidence = [
            {'frame_index': index, 'time': index / 10, 'identity': 'confirmed',
             'image': jpeg_base64(color=(30 + index, 120, 210)),
             'target_box': [.25, .25, .75, .75]}
            for index in range(4)
        ]

        for rows in (evidence[:1], evidence):
            with self.subTest(count=len(rows)):
                before = set(self.root.rglob('*'))
                frames = workflow.build_context_window(rows)
                self.assertEqual(validate_context_window(frames), [row['time'] for row in rows])
                self.assertEqual([frame['target_box'] for frame in frames],
                                 [row['target_box'] for row in rows])
                self.assertEqual(set(self.root.rglob('*')), before)
                for frame in frames:
                    for field in ('scene_jpeg', 'target_crop_jpeg'):
                        self.assertLessEqual(len(frame[field]), MAX_IMAGE_BYTES)
                        image = Image.open(io.BytesIO(frame[field]))
                        self.assertEqual(image.mode, 'RGB')
                    self.assertEqual(Image.open(io.BytesIO(frame['target_crop_jpeg'])).size,
                                     (40, 30))

    def test_context_window_rejects_invalid_counts_order_identity_box_and_jpeg(self):
        base = {'frame_index': 0, 'time': 0.0, 'identity': 'confirmed',
                'image': jpeg_base64(), 'target_box': [.1, .2, .6, .9]}
        cases = [
            [],
            [dict(base, frame_index=index, time=index / 10) for index in range(5)],
            [dict(base, time=.2), dict(base, time=.1)],
            [{**base, 'identity': 'uncertain'}],
            [{**base, 'target_box': None}],
            [{**base, 'image': base64.b64encode(b'not-a-jpeg').decode('ascii')}],
            [{**base, 'image': '%%%'}],
        ]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaisesRegex(
                    ValueError, 'invalid_context_window'):
                workflow.build_context_window(rows)
        with mock.patch.object(workflow, 'MAX_IMAGE_BYTES', 100):
            with self.assertRaisesRegex(ValueError, 'invalid_context_window'):
                workflow.build_context_window([base])

    def test_context_candidate_is_canonical_and_bound_to_exact_tracking_bytes(self):
        from aba_demo.context_schema import encode_context_document, validate_context_document

        session = self.context_session()
        tracking_report = finish_context_session(session)
        tracking_bytes = session.pending.read_bytes()
        adapter = unittest.mock.Mock()
        adapter.analyze.return_value = {
            'observation': context_observation([0.0, 0.4]),
            'provenance': context_provenance(session.source_sha256),
        }

        report = session.build_context_candidate([
            {'segment_id': 'ctx-000001', 'frame_indices': [0, 4]},
        ], adapter=adapter)

        context_bytes = session.context_pending.read_bytes()
        document = json.loads(context_bytes)
        self.assertEqual(validate_context_document(document, session.analyzer.duration), document)
        self.assertEqual(context_bytes,
                         encode_context_document(document, session.analyzer.duration))
        self.assertEqual(document['tracking_candidate_sha256'],
                         tracking_report['candidate_sha256'])
        self.assertEqual(document['source_sha256'], session.source_sha256)
        self.assertEqual(document['context_segments'], [{
            'segment_id': 'ctx-000001', 'start_time': 0.0, 'end_time': 0.5,
            **context_observation([0.0, 0.4]), 'clinician_confirmation': 'pending',
        }])
        self.assertEqual(report, {
            'tracking_candidate_sha256': hashlib.sha256(tracking_bytes).hexdigest(),
            'context_candidate_sha256': hashlib.sha256(context_bytes).hexdigest(),
            'segment_count': 1,
            'run_id': session.run_id,
            'source_sha256': session.source_sha256,
        })
        self.assertEqual(json.loads(session.context_review_report.read_bytes()), report)
        self.assertEqual(session.pending.read_bytes(), tracking_bytes)
        adapter.analyze.assert_called_once()
        args, kwargs = adapter.analyze.call_args
        self.assertEqual(kwargs, {'source_sha256': session.source_sha256})
        self.assertEqual([frame['time'] for frame in args[0]], [0.0, 0.4])

    def test_every_window_is_strictly_preflighted_before_any_adapter_call(self):
        session = self.context_session()
        finish_context_session(session)
        tracking_bytes = session.pending.read_bytes()
        too_many = [
            {'segment_id': f'ctx-{index:06d}', 'frame_indices': [0]}
            for index in range(workflow.MAX_CONTEXT_WINDOWS + 1)
        ]
        cases = [
            [],
            too_many,
            [{'segment_id': 'ctx-extra', 'frame_indices': [0], 'extra': True}],
            [{'segment_id': 'ctx-empty', 'frame_indices': []}],
            [{'segment_id': 'ctx-five', 'frame_indices': [0, 4, 9, 13, 18]}],
            [{'segment_id': 'ctx-order', 'frame_indices': [4, 0]}],
            [{'segment_id': 'ctx-bool', 'frame_indices': [True]}],
            [{'segment_id': 'ctx-missing', 'frame_indices': [1]}],
            [{'segment_id': '../bad', 'frame_indices': [0]}],
            [{'segment_id': 'ctx-span', 'frame_indices': [0, 9]}],
            [{'segment_id': 'ctx-boundary', 'frame_indices': [5]}],
            [{'segment_id': 'ctx-first', 'frame_indices': [0, 4]},
             {'segment_id': 'ctx-overlap', 'frame_indices': [4]}],
            [{'segment_id': 'ctx-first', 'frame_indices': [0]},
             {'segment_id': 'ctx-first', 'frame_indices': [4]}],
            # The first window is valid: the invalid second window must still block all calls.
            [{'segment_id': 'ctx-first', 'frame_indices': [0]},
             {'segment_id': 'ctx-missing', 'frame_indices': [1]}],
        ]
        for windows in cases:
            with self.subTest(windows=windows):
                adapter = mock.Mock()
                with self.assertRaises(ValueError):
                    session.build_context_candidate(windows, adapter=adapter)
                adapter.analyze.assert_not_called()
                self.assertEqual(session.pending.read_bytes(), tracking_bytes)
                self.assertFalse(session.context_pending.exists())
                self.assertFalse(session.context_review_report.exists())
                self.assertIsNone(session.context_report)

        with mock.patch('aba_demo.openrouter_context.OpenRouterContextAdapter') as constructor:
            with self.assertRaises(ValueError):
                session.build_context_candidate([
                    {'segment_id': 'ctx-first', 'frame_indices': [0]},
                    {'segment_id': 'ctx-missing', 'frame_indices': [1]},
                ])
            constructor.assert_not_called()

    def test_context_requires_completed_quality_passing_bound_tracking_candidate(self):
        adapter = mock.Mock()
        unfinished = self.context_session()
        unfinished.select([.2, .4], expected_target_id=2, confirmed=True)
        with self.assertRaisesRegex(ValueError, 'quality gate'):
            unfinished.build_context_candidate([
                {'segment_id': 'ctx-000001', 'frame_indices': [0]},
            ], adapter=adapter)
        adapter.analyze.assert_not_called()
        unfinished.close()

        failed_quality = self.context_session()
        failed_quality.select([.2, .4], expected_target_id=2, confirmed=True)
        report = failed_quality.finish(max_uncertain_fraction=0)
        self.assertFalse(report['quality']['passed'])
        with self.assertRaisesRegex(ValueError, 'quality gate'):
            failed_quality.build_context_candidate([
                {'segment_id': 'ctx-000001', 'frame_indices': [0]},
            ], adapter=adapter)
        adapter.analyze.assert_not_called()

        tampered = self.context_session()
        finish_context_session(tampered)
        document = json.loads(tampered.pending.read_bytes())
        document['source']['duration'] = 9.9
        encoded = json.dumps(document, separators=(',', ':')).encode('utf-8')
        tampered.pending.write_bytes(encoded)
        tampered.report['candidate_sha256'] = hashlib.sha256(encoded).hexdigest()
        with self.assertRaisesRegex(ValueError, 'binding'):
            tampered.build_context_candidate([
                {'segment_id': 'ctx-000001', 'frame_indices': [0]},
            ], adapter=adapter)
        adapter.analyze.assert_not_called()

    def test_context_preflight_rejects_windows_longer_than_five_seconds(self):
        session = workflow.ReviewSession(
            self.video, {}, self.root / 'long-context-run',
            analyzer_factory=LongContextFakeAnalyzer)
        finish_context_session(session)
        self.assertTrue({9, 62}.issubset(session.evidence))
        adapter = mock.Mock()

        with self.assertRaisesRegex(ValueError, 'five seconds'):
            session.build_context_candidate([
                {'segment_id': 'ctx-too-long', 'frame_indices': [9, 62]},
            ], adapter=adapter)

        adapter.analyze.assert_not_called()
        self.assertFalse(session.context_pending.exists())

    def test_provider_failure_after_another_window_leaves_no_context_artifact(self):
        session = self.context_session()
        finish_context_session(session)
        tracking_bytes = session.pending.read_bytes()
        first = {
            'observation': context_observation([0.0]),
            'provenance': context_provenance(session.source_sha256),
        }
        adapter = mock.Mock()
        adapter.analyze.side_effect = [first, RuntimeError('private provider failure')]

        with self.assertRaisesRegex(RuntimeError, 'private provider failure'):
            session.build_context_candidate([
                {'segment_id': 'ctx-000001', 'frame_indices': [0]},
                {'segment_id': 'ctx-000002', 'frame_indices': [4]},
            ], adapter=adapter)

        self.assertEqual(adapter.analyze.call_count, 2)
        self.assertEqual(session.pending.read_bytes(), tracking_bytes)
        self.assertFalse(session.context_pending.exists())
        self.assertFalse(session.context_review_report.exists())
        self.assertIsNone(session.context_report)

    def test_provider_provenance_must_be_identical_across_calls(self):
        session = self.context_session()
        finish_context_session(session)
        adapter = mock.Mock()
        adapter.analyze.side_effect = [
            {'observation': context_observation([0.0]),
             'provenance': context_provenance(session.source_sha256, 'provider-a')},
            {'observation': context_observation([0.4]),
             'provenance': context_provenance(session.source_sha256, 'provider-b')},
        ]

        with self.assertRaisesRegex(ValueError, 'provenance changed'):
            session.build_context_candidate([
                {'segment_id': 'ctx-000001', 'frame_indices': [0]},
                {'segment_id': 'ctx-000002', 'frame_indices': [4]},
            ], adapter=adapter)

        self.assertEqual(adapter.analyze.call_count, 2)
        self.assertFalse(session.context_pending.exists())
        self.assertFalse(session.context_review_report.exists())
        self.assertIsNone(session.context_report)

    def test_publish_with_context_uses_one_review_and_exact_pair_of_artifacts(self):
        session = self.context_session()
        tracking_report = finish_context_session(session)
        adapter = mock.Mock(return_value=None)
        adapter.analyze.return_value = {
            'observation': context_observation([0.0, 0.4]),
            'provenance': context_provenance(session.source_sha256),
        }
        context_report = session.build_context_candidate([
            {'segment_id': 'ctx-000001', 'frame_indices': [0, 4]},
        ], adapter=adapter)
        tracking_bytes = session.pending.read_bytes()
        context_bytes = session.context_pending.read_bytes()

        published = session.publish_with_context(
            tracking_report['candidate_sha256'],
            context_report['context_candidate_sha256'],
            confirmed=True, reviewer=' clinician ')

        self.assertEqual(published, {
            'observations': session.output, 'context': session.context_output})
        self.assertEqual(session.output.read_bytes(), tracking_bytes)
        self.assertEqual(session.context_output.read_bytes(), context_bytes)
        self.assertFalse(session.pending.exists())
        self.assertFalse(session.context_pending.exists())
        self.assertEqual(json.loads((session.directory / 'human-review.json').read_bytes()), {
            'reviewer': 'clinician', 'confirmed': True,
            'tracking_candidate_sha256': tracking_report['candidate_sha256'],
            'context_candidate_sha256': context_report['context_candidate_sha256'],
            'source_sha256': session.source_sha256, 'run_id': session.run_id,
        })
        review_files = list(session.directory.glob('*human-review*.json'))
        self.assertEqual(review_files, [session.directory / 'human-review.json'])

    def test_publish_pair_revalidates_context_binding_before_any_rename(self):
        from aba_demo.context_schema import encode_context_document

        session = self.context_session()
        tracking_report = finish_context_session(session)
        adapter = mock.Mock()
        adapter.analyze.return_value = {
            'observation': context_observation([0.0]),
            'provenance': context_provenance(session.source_sha256),
        }
        context_report = session.build_context_candidate([
            {'segment_id': 'ctx-000001', 'frame_indices': [0]},
        ], adapter=adapter)
        tracking_bytes = session.pending.read_bytes()

        document = json.loads(session.context_pending.read_bytes())
        document['source_sha256'] = 'f' * 64
        document['provenance']['source_sha256'] = 'f' * 64
        context_bytes = encode_context_document(document, session.analyzer.duration)
        context_sha = hashlib.sha256(context_bytes).hexdigest()
        session.context_pending.write_bytes(context_bytes)
        session.context_report['context_candidate_sha256'] = context_sha
        session.context_review_report.write_text(
            json.dumps(session.context_report, separators=(',', ':')), encoding='utf-8')

        with mock.patch.object(workflow.os, 'replace') as replace:
            with self.assertRaisesRegex(ValueError, 'binding'):
                session.publish_with_context(
                    tracking_report['candidate_sha256'], context_sha,
                    confirmed=True, reviewer='clinician')
            replace.assert_not_called()
        self.assertEqual(session.pending.read_bytes(), tracking_bytes)
        self.assertFalse(session.output.exists())
        self.assertFalse(session.context_output.exists())
        self.assertFalse((session.directory / 'human-review.json').exists())
        self.assertNotEqual(context_report['context_candidate_sha256'], context_sha)

    def test_provenance_records_selected_tracker_profile(self):
        session = self.session(sample_hz=1, tracker_profile='botsort_reid')
        session.select([.2, .4], expected_target_id=2, confirmed=True)
        session.advance(.5)
        session.select([.3, .4], expected_target_id=6, confirmed=True)
        session.finish(max_uncertain_fraction=.99)
        data = json.loads(session.pending.read_text())
        self.assertEqual(data['provenance']['tracker'], 'botsort_reid.yaml')
        self.assertEqual(data['provenance']['config']['tracker_profile'], 'botsort_reid')

    def test_unsampled_loss_is_exported_and_gated_from_causal_audit(self):
        session = self.session(sample_hz=1)
        session.select([.2, .4], expected_target_id=2, confirmed=True)
        session.advance(.9)
        session.select([.3, .4], expected_target_id=6, confirmed=True)
        report = session.finish(max_uncertain_fraction=0)
        data = json.loads(session.pending.read_text())
        self.assertFalse(report['quality']['passed'])
        audit = data['provenance']['causal_audit']
        self.assertEqual(report['quality']['uncertain_fraction'],
                         sum(r['identity'] != 'confirmed' for r in audit) / len(audit))
        loss = [r for r in data['observations'] if r['identity'] != 'confirmed']
        self.assertEqual(loss[0]['time'], .5)
        self.assertTrue(all(v is None for r in loss for v in r['signals'].values()))

    def test_same_frame_reselection_cannot_erase_loss_boundary(self):
        session = self.session(sample_hz=1)
        session.select([.2, .4], expected_target_id=2, confirmed=True)
        session.advance(.5)
        session.select([.3, .4], expected_target_id=6, confirmed=True)
        report = session.finish(max_uncertain_fraction=0)
        self.assertFalse(report['quality']['passed'])
        self.assertEqual(next(r for r in session.observations if r['time'] == .5)['identity'], 'uncertain')
        self.assertEqual(next(r for r in session.observations if r['time'] == .6)['identity'], 'confirmed')
        self.assertEqual(len({r['time'] for r in session.observations}), len(session.observations))

    def test_unsampled_same_frame_reselection_preserves_loss_until_next_frame(self):
        session = self.session(sample_hz=5)
        session.select([.2, .4], expected_target_id=2, confirmed=True)
        session.advance(3.5)
        self.assertEqual(session.analyzer.frame_index, 35)
        self.assertEqual(session.audit[-1]['identity'], 'uncertain')
        self.assertFalse(any(row['time'] == 3.5 for row in session.observations))

        session.select([.3, .4], expected_target_id=6, confirmed=True)
        loss = next(row for row in session.observations if row['time'] == 3.5)
        self.assertEqual(loss['identity'], 'uncertain')
        self.assertEqual(loss['target_id'], 2)
        self.assertTrue(all(value is None for value in loss['signals'].values()))
        self.assertEqual(session.audit[-1]['identity'], 'uncertain')

        session.advance(3.6)
        recovery = next(row for row in session.observations if row['time'] == 3.6)
        self.assertEqual(recovery['identity'], 'confirmed')
        self.assertEqual(recovery['target_id'], 6)

    def test_source_change_during_open_review_closes_analyzer(self):
        session = self.session()
        self.video.write_bytes(b'different source')
        with self.assertRaisesRegex(ValueError, 'Video changed'):
            session.select([.2, .4], expected_target_id=2, confirmed=True)
        self.assertTrue(session.failed)
        self.assertTrue(session.analyzer.closed)

    def test_overlap_candidate_policy_is_explicit_not_publication_approval(self):
        session = self.session(candidate_overlap_policy='human_review_required')
        self.assertEqual(session.config['identity_overlap_threshold'], 1.0)
        self.assertFalse(session.finished)
        with self.assertRaisesRegex(ValueError, 'overlap policy'):
            self.session(candidate_overlap_policy='approved')
        strict = self.session()
        self.assertEqual(strict.config['identity_overlap_threshold'], .2)
        strict.close()
        session.close()

    def test_reselection_count_bounds_visual_evidence(self):
        session = self.session()
        session.select([.2, .4], expected_target_id=2, confirmed=True)
        for i in range(12):
            session.select([.2, .4], expected_target_id=2, confirmed=True)
        with self.assertRaisesRegex(ValueError, '12 reselections'):
            session.select([.2, .4], expected_target_id=2, confirmed=True)
        session.close()

    def test_pulses_do_not_bridge_uncertainty_or_reselection(self):
        for reset_kind in ('uncertain', 'reselect'):
            with self.subTest(reset=reset_kind):
                session = self.session(sample_hz=2.5)
                original = session.analyzer.analyze
                def pulse(time):
                    row = original(time)
                    row['signals']['posture_change'] = round(time * 10) == 1
                    if reset_kind == 'uncertain' and round(time * 10) == 2:
                        row['identity'] = 'uncertain'
                    return row
                session.analyzer.analyze = pulse
                session.select([.2, .4], expected_target_id=2, confirmed=True)
                session.advance(.2)
                if reset_kind == 'reselect':
                    session.select([.2, .4], expected_target_id=2, confirmed=True)
                session.advance(.4)
                self.assertFalse(session.observations[-1]['signals']['posture_change'])

    def test_limits_and_stale_run_isolation(self):
        from unittest.mock import patch
        with patch.object(workflow, 'MAX_CAUSAL_FRAMES', 50):
            with self.assertRaisesRegex(ValueError, 'causal frames'):
                self.session()
        first, second = self.session(), self.session()
        self.assertNotEqual(first.output, second.output)
        first.select([.2, .4], expected_target_id=2, confirmed=True)
        with patch.object(workflow, 'MAX_RUNTIME_SECONDS', -1):
            with self.assertRaisesRegex(ValueError, 'runtime limit'):
                first.advance(.1)
        self.assertTrue(first.failed)
        second.close()

    def test_progress_and_export_size_limit(self):
        from unittest.mock import patch
        session = self.session()
        session.select([.2, .4], expected_target_id=2, confirmed=True)
        progress = []
        with patch.object(workflow, 'MAX_EXPORT_BYTES', 10, create=True):
            with self.assertRaisesRegex(ValueError, '20 MB'):
                session.finish(progress=lambda done, total: progress.append((done, total)))
        self.assertTrue(progress)
        self.assertFalse(session.pending.exists())

    def test_posture_pulse_between_samples_is_preserved(self):
        session = self.session(sample_hz=2.5)
        original = session.analyzer.analyze
        def pulse(time):
            row = original(time)
            row['signals']['posture_change'] = round(time * 10) == 1
            return row
        session.analyzer.analyze = pulse
        session.select([.2, .4], expected_target_id=2, confirmed=True)
        session.advance(.4)
        self.assertTrue(session.observations[-1]['signals']['posture_change'])

    def test_advance_error_invalidates_and_closes(self):
        session = self.session(fake_stop=5)
        session.select([.2, .4], expected_target_id=2, confirmed=True)
        with self.assertRaises(EOFError):
            session.advance(1)
        self.assertTrue(session.failed)
        self.assertTrue(session.analyzer.closed)

    def test_preview_failure_closes_analyzer(self):
        created = []
        class BrokenPreview(FakeAnalyzer):
            def preview(self):
                created.append(self)
                raise RuntimeError('preview failed')
        with self.assertRaisesRegex(RuntimeError, 'preview failed'):
            workflow.ReviewSession(self.video, {}, self.root / 'broken', analyzer_factory=BrokenPreview)
        self.assertTrue(created[0].closed)

    def test_bad_sample_rate_rejected_before_analyzer(self):
        for value in (0, -1, True, float('nan')):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'sample_hz'):
                self.session(sample_hz=value)

    def test_effective_config_mutation_invalidates_and_closes(self):
        session = self.session(sample_hz=5, task_targets=[[.1, .1, .2, .2]])
        session.select([.2, .4], expected_target_id=2, confirmed=True)
        session.config['sample_hz'] = 100
        with self.assertRaisesRegex(ValueError, 'Configuration changed'):
            session.finish()
        self.assertTrue(session.failed)
        self.assertTrue(session.analyzer.closed)
        self.assertFalse(session.pending.exists())

    def test_non_json_config_mutation_also_invalidates_and_closes(self):
        session = self.session(sample_hz=5)
        session.select([.2, .4], expected_target_id=2, confirmed=True)
        session.config['injected'] = object()
        with self.assertRaisesRegex(ValueError, 'Configuration changed'):
            session.advance(.1)
        self.assertTrue(session.failed)
        self.assertTrue(session.analyzer.closed)

    def test_analyzer_config_mutation_invalidates_and_closes(self):
        session = self.session(sample_hz=5)
        session.select([.2, .4], expected_target_id=2, confirmed=True)
        session.analyzer.config = dict(session.config)
        session.analyzer.config['identity_overlap_threshold'] = .75
        with self.assertRaisesRegex(ValueError, 'Configuration changed'):
            session.advance(.1)
        self.assertTrue(session.failed)
        self.assertTrue(session.analyzer.closed)

    def test_exported_config_snapshot_mutation_invalidates_and_closes(self):
        session = self.session(sample_hz=5)
        session.select([.2, .4], expected_target_id=2, confirmed=True)
        session.analyzer.config = dict(session.config)
        session._effective_config['sample_hz'] = 100
        with self.assertRaisesRegex(ValueError, 'Configuration changed'):
            session.advance(.1)
        self.assertTrue(session.failed)
        self.assertTrue(session.analyzer.closed)
        self.assertFalse(session.pending.exists())

    def test_post_selection_analysis_error_invalidates_and_closes(self):
        session = self.session()
        def fail(_time):
            raise RuntimeError('post-selection analysis failed')
        session.analyzer.analyze = fail
        with self.assertRaisesRegex(RuntimeError, 'post-selection analysis failed'):
            session.select([.2, .4], expected_target_id=2, confirmed=True)
        self.assertTrue(session.failed)
        self.assertTrue(session.analyzer.closed)
        self.assertIsNone(session.initial_selection)
        self.assertFalse(session.pending.exists())

    def test_expected_id_mismatch_invalidates_session(self):
        session = self.session()
        with self.assertRaisesRegex(ValueError, 'expected target ID'):
            session.select([.2, .4], expected_target_id=6, confirmed=True)
        self.assertTrue(session.failed)

    def test_no_automatic_switch_and_quality_failure_quarantines(self):
        session = self.session()
        session.select([.2, .4], expected_target_id=2, confirmed=True)
        report = session.finish()
        self.assertFalse(report['quality']['passed'])
        self.assertEqual(session.events, [])
        self.assertTrue(all(v is None for r in session.observations if r['identity'] != 'confirmed' for v in r['signals'].values()))
        with self.assertRaisesRegex(ValueError, 'quality gate'):
            session.publish(report['candidate_sha256'], confirmed=True, reviewer='human')
        self.assertFalse(session.output.exists())
        self.assertTrue(session.pending.exists())

    def test_terminal_eof_flushes_last_decoded_row_and_posture_pulse(self):
        session = self.session(sample_hz=2.5, fake_stop=50)
        original = session.analyzer.analyze
        def pulse(time):
            row = original(time)
            row['signals']['posture_change'] = round(time * 10) == 49
            return row
        session.analyzer.analyze = pulse
        session.select([.2, .4], expected_target_id=2, confirmed=True)
        session.advance(.5)
        session.select([.3, .4], expected_target_id=6, confirmed=True)
        session.finish(max_uncertain_fraction=.7)
        self.assertEqual(session.observations[-1]['time'], 4.9)
        self.assertTrue(session.observations[-1]['signals']['posture_change'])
        self.assertFalse(session.posture_pulse)

    def test_terminal_eof_boundaries_and_early_probe_eof(self):
        for gap in (1, 2, 3, 4, 20):
            with self.subTest(gap=gap):
                session = self.session(fake_stop=51-gap)
                session.select([.2, .4], expected_target_id=2, confirmed=True)
                if gap <= 3:
                    session.finish()
                    source = json.loads(session.pending.read_text())['source']
                    self.assertEqual(source['trailing_unreadable_frames'], gap)
                else:
                    with self.assertRaises(EOFError):
                        session.finish()
                    self.assertFalse(session.pending.exists())
        session = self.session(fake_stop=5)
        session.select([.2, .4], expected_target_id=2, confirmed=True)
        with self.assertRaises(EOFError):
            session.advance(1)

    def test_publish_requires_current_source_exact_artifact_and_human(self):
        session = self.session()
        session.select([.2, .4], expected_target_id=2, confirmed=True)
        session.advance(.5)
        session.select([.3, .4], expected_target_id=6, confirmed=True)
        report = session.finish()
        with self.assertRaisesRegex(ValueError, 'Human'):
            session.publish(report['candidate_sha256'])
        with self.assertRaisesRegex(ValueError, 'exact reviewed'):
            session.publish('0'*64, confirmed=True, reviewer='human')
        session.pending.write_bytes(session.pending.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'Candidate changed'):
            session.publish(report['candidate_sha256'], confirmed=True, reviewer='human')
        self.video.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Video changed'):
            session.publish(report['candidate_sha256'], confirmed=True, reviewer='human')
        self.assertFalse(session.output.exists())
        self.assertFalse(session.pending.exists())

    def test_montage_is_bounded_and_reselection_is_current_frame(self):
        session = self.session()
        session.select([.2, .4], expected_target_id=2, confirmed=True)
        session.advance(3.49)
        event = session.select([.3, .4], expected_target_id=6, confirmed=True)
        self.assertEqual(event['frame_index'], 34)
        session.finish(max_uncertain_fraction=.7)
        self.assertLessEqual(len(session.evidence), 24)
        self.assertIn(47, session.evidence)
        self.assertEqual(session.evidence[34]['target_id'], 6)
        self.assertTrue(all(r['identity'] == 'uncertain' for r in session.audit[5:34]))

    def test_explicit_reselection_single_pass_and_exact_publish(self):
        self.assertTrue(hasattr(workflow, 'ReviewSession'), 'single-pass workflow missing')
        session = self.session()
        session.select([.2, .4], expected_target_id=2, confirmed=True)
        session.advance(3.5)
        session.select([.3, .4], expected_target_id=6, confirmed=True)
        report = session.finish(max_uncertain_fraction=.7)
        pending = session.pending.read_bytes()
        data = json.loads(pending)
        self.assertEqual(len(data['provenance']['causal_audit']), 51)
        self.assertEqual(data['provenance']['applied_target_reselections'][0]['target_id'], 6)
        self.assertTrue(report['quality']['passed'])
        published = session.publish(report['candidate_sha256'], confirmed=True, reviewer='human reviewer')
        self.assertEqual(published.read_bytes(), pending)
        self.assertFalse(session.pending.exists())
        self.assertEqual(len(set(session.analyzer.calls)), 51)
        self.assertEqual(session.analyzer.calls.count(0), 1)


if __name__ == '__main__':
    unittest.main()
