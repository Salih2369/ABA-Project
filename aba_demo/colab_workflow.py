"""Single causal pass, bounded visual evidence, and exact-artifact human publication.

Recorded-video development adapter, not live inference or clinical validation.
Heavy imports remain inside the vision adapter/display boundary.
"""
from collections import Counter
import base64
import binascii
import copy
import hashlib
import io
import json
import math
import os
from pathlib import Path
import tempfile
import time as clock
import uuid

from .context_schema import (
    MAX_IMAGE_BYTES,
    encode_context_document,
    validate_context_document,
    validate_context_segment,
    validate_context_window,
)
from .export import MAX_TRAILING_DECODE_GAP_FRAMES, file_sha256
from .vision import SIGNALS, VideoAnalyzer, resolve_tracker_profile, validate_config

MAX_EXPORT_BYTES = 20 * 1024 * 1024
MAX_OBSERVATIONS = 100_000
MAX_CAUSAL_FRAMES = 100_000
MAX_RUNTIME_SECONDS = 4 * 60 * 60
MAX_CONTEXT_SOURCE_BYTES = 32 * 1024 * 1024
MAX_CONTEXT_SOURCE_PIXELS = 40_000_000
MAX_CONTEXT_WINDOWS = 128


def _bounded_jpeg(image):
    """Encode an RGB image within the provider contract without disk I/O."""
    from PIL import Image

    image = image.convert('RGB')
    longest = max(image.size)
    if longest > 2048:
        scale = 2048 / longest
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS)
    for _ in range(16):
        for quality in (90, 75, 60, 45, 30, 20):
            output = io.BytesIO()
            image.save(output, format='JPEG', quality=quality, optimize=True)
            encoded = output.getvalue()
            if len(encoded) <= MAX_IMAGE_BYTES:
                return encoded
        if image.size == (1, 1):
            break
        image = image.resize(
            (max(1, image.width * 3 // 4), max(1, image.height * 3 // 4)),
            Image.Resampling.LANCZOS)
    raise ValueError('invalid_context_window')


def _decode_scene(value):
    """Decode one bounded base64 JPEG retained by the review session."""
    from PIL import Image

    if not isinstance(value, str) or len(value) > (MAX_CONTEXT_SOURCE_BYTES * 4 // 3 + 4):
        raise ValueError('invalid_context_window')
    try:
        raw = base64.b64decode(value, validate=True)
        if len(raw) > MAX_CONTEXT_SOURCE_BYTES:
            raise ValueError
        image = Image.open(io.BytesIO(raw))
        if image.format != 'JPEG' or image.width <= 0 or image.height <= 0:
            raise ValueError
        if image.width * image.height > MAX_CONTEXT_SOURCE_PIXELS:
            raise ValueError
        image.load()
        return image.convert('RGB')
    except (ValueError, TypeError, OSError, binascii.Error, Image.DecompressionBombError):
        raise ValueError('invalid_context_window') from None


def build_context_window(evidence_rows):
    """Build one validated, target-grounded context window entirely in memory."""
    if not isinstance(evidence_rows, list) or not 1 <= len(evidence_rows) <= 4:
        raise ValueError('invalid_context_window')
    frames = []
    previous_time = None
    for row in evidence_rows:
        if not isinstance(row, dict):
            raise ValueError('invalid_context_window')
        time = row.get('time')
        box = row.get('target_box')
        if (type(time) not in (int, float) or not math.isfinite(time) or time < 0
                or previous_time is not None and time <= previous_time
                or row.get('identity') != 'confirmed'
                or not isinstance(box, list) or len(box) != 4
                or not all(type(value) in (int, float) and math.isfinite(value)
                           and 0 <= value <= 1 for value in box)
                or not box[0] < box[2] or not box[1] < box[3]):
            raise ValueError('invalid_context_window')
        scene = _decode_scene(row.get('image'))
        left = math.floor(box[0] * scene.width)
        top = math.floor(box[1] * scene.height)
        right = math.ceil(box[2] * scene.width)
        bottom = math.ceil(box[3] * scene.height)
        crop = scene.crop((left, top, right, bottom))
        frames.append({
            'time': time,
            'identity': 'confirmed',
            'scene_jpeg': _bounded_jpeg(scene),
            'target_crop_jpeg': _bounded_jpeg(crop),
            'target_box': copy.deepcopy(box),
        })
        previous_time = time
    validate_context_window(frames)
    return frames


def _config_fingerprint(config):
    try:
        encoded = json.dumps(config, allow_nan=False, ensure_ascii=False,
                             sort_keys=True, separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError) as error:
        raise ValueError('Configuration must contain finite JSON values') from error
    return hashlib.sha256(encoded).hexdigest()


def _write_bytes(path, data):
    """Atomically replace one artifact with exact bytes."""
    if not isinstance(data, bytes):
        raise TypeError('data must be bytes')
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent,
                                         delete=False, suffix='.tmp') as handle:
            temporary = Path(handle.name)
            handle.write(data)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_json(path, data):
    encoded = json.dumps(data, allow_nan=False, ensure_ascii=False,
                         separators=(',', ':')).encode('utf-8')
    _write_bytes(path, encoded)


class ReviewSession:
    """Keep one analyzer alive from preview through human reselection to EOF.

    advance() processes EVERY intervening frame. select() applies only to the
    currently displayed frame and never backfills an uncertain interval.
    """
    def __init__(self, video, config, output_root, *, analyzer_factory=VideoAnalyzer):
        self.video = Path(video)
        self.config = copy.deepcopy(validate_config(config))
        policy = self.config.get('candidate_overlap_policy', 'strict')
        if policy not in ('strict', 'human_review_required'):
            raise ValueError('Unknown candidate overlap policy')
        self.config['candidate_overlap_policy'] = policy
        self.config['identity_overlap_threshold'] = 1.0 if policy == 'human_review_required' else .2
        sample_hz = self.config.get('sample_hz', 5)
        if type(sample_hz) not in (int, float) or not math.isfinite(sample_hz) or sample_hz <= 0:
            raise ValueError('sample_hz must be finite and positive')
        self.config['include_frame'] = False
        self._effective_config = copy.deepcopy(self.config)
        self._config_sha256 = _config_fingerprint(self._effective_config)
        self.started = clock.monotonic()
        self.source_sha256 = file_sha256(self.video)
        self.run_id = uuid.uuid4().hex
        self.directory = Path(output_root) / self.run_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.pending = self.directory / 'observations.pending.json'
        self.output = self.directory / 'observations.json'
        self.context_pending = self.directory / 'context.pending.json'
        self.context_output = self.directory / 'context.json'
        self.context_review_report = self.directory / 'context-review-report.json'
        self.analyzer = analyzer_factory(self.video, copy.deepcopy(self._effective_config))
        try:
            if self.analyzer.frame_count > MAX_CAUSAL_FRAMES:
                raise ValueError('Recording exceeds 100000 causal frames; use a shorter development clip')
            self.preview = self.analyzer.preview()
        except BaseException:
            self.analyzer.close()
            raise
        self.posture_pulse = False
        self.active_id = None
        self.initial_selection = None
        self.events, self.audit, self.observations = [], [], []
        self.last_decoded_row = None
        self.evidence = {}
        self.report = None
        self.context_report = None
        self.finished = False
        self.failed = False
        self.stride = max(1, round(self.analyzer.fps / self.config.get('sample_hz', 5)))
        # Dense early loss/re-entry evidence, uniform coverage, and safe decoded tail.
        last = max(0, self.analyzer.frame_count - 1 - MAX_TRAILING_DECODE_GAP_FRAMES)
        self.evidence_indices = {round(last * i / 11) for i in range(12)}
        self.evidence_indices.update(min(last, round(i * self.analyzer.fps / 2)) for i in range(9))
        self.evidence_indices.add(last)

    def _binding(self):
        analyzer_config = getattr(self.analyzer, 'config', self._effective_config)
        try:
            session_fingerprint = _config_fingerprint(self.config)
            analyzer_fingerprint = _config_fingerprint(analyzer_config)
            effective_fingerprint = _config_fingerprint(self._effective_config)
        except ValueError:
            session_fingerprint = analyzer_fingerprint = effective_fingerprint = None
        if (session_fingerprint != self._config_sha256
                or analyzer_fingerprint != self._config_sha256
                or effective_fingerprint != self._config_sha256):
            self.failed = True
            self.pending.unlink(missing_ok=True)
            self.analyzer.close()
            raise ValueError('Configuration changed during continuity audit')
        if file_sha256(self.video) != self.source_sha256:
            self.failed = True
            self.pending.unlink(missing_ok=True)
            self.analyzer.close()
            raise ValueError('Video changed during continuity audit')

    def _open(self):
        if self.failed or self.finished:
            raise RuntimeError('Session is closed or invalid; start a new run')
        self._binding()

    def select(self, click, *, expected_target_id, confirmed=False):
        self._open()
        if len(self.events) >= 12:
            raise ValueError('At most 12 reselections per bounded development review')
        if confirmed is not True or type(expected_target_id) is not int or expected_target_id < 0:
            raise ValueError('Explicit human confirmation and expected target ID are required')
        selection = self.analyzer.select_target(*click)
        if selection.get('identity') != 'confirmed' or selection.get('target_id') != expected_target_id:
            self.failed = True
            self.pending.unlink(missing_ok=True)
            self.analyzer.close()
            raise ValueError('Selection does not match expected target ID; restart and review')
        self.active_id = selection['target_id']
        event = {'applied_time': self.analyzer.frame_index / self.analyzer.fps,
                 'frame_index': self.analyzer.frame_index, 'click': list(click),
                 'target_id': self.active_id, 'expected_target_id': expected_target_id,
                 'human_confirmed': True}
        try:
            self._record(self.analyzer.analyze(event['applied_time']), event=True)
        except BaseException:
            self.failed = True
            self.pending.unlink(missing_ok=True)
            self.analyzer.close()
            raise
        if self.initial_selection is None:
            self.initial_selection = event
        else:
            self.events.append(event)
        return copy.deepcopy(event)

    def _record(self, row, *, event=False):
        if clock.monotonic() - self.started > MAX_RUNTIME_SECONDS:
            raise ValueError('Development run exceeded four-hour runtime limit')
        if len(self.observations) >= MAX_OBSERVATIONS:
            raise ValueError('Website observation limit is 100000')
        index = self.analyzer.frame_index
        row = copy.deepcopy(row)
        row.pop('frame', None)
        target_box = copy.deepcopy(row.pop('target_box', None))
        row['target_id'] = self.active_id
        if event or row['identity'] != 'confirmed':
            self.posture_pulse = False
        if row['identity'] != 'confirmed':
            row['signals'] = dict.fromkeys(SIGNALS)
        else:
            self.posture_pulse |= row['signals']['posture_change'] is True
        previous_decoded_row = copy.deepcopy(self.last_decoded_row)
        self.last_decoded_row = copy.deepcopy(row)
        identity_boundary = bool(self.audit and self.audit[-1]['identity'] != row['identity'])
        sampled = (index % self.stride == 0 or event or identity_boundary
                   or index == self.analyzer.frame_count - 1)
        if sampled and self.posture_pulse:
            row['signals']['posture_change'] = True
        if sampled:
            self.posture_pulse = False
        item = {'frame_index': index, 'time': row['time'], 'identity': row['identity'],
                'target_id': self.active_id, 'reason': row.get('values', {}).get('identity_reason'),
                'selection_event': event}
        # One timestamp cannot express both pre-click loss and post-click recovery.
        # Keep the conservative loss row; recovery starts at the next decoded frame.
        preserve_loss = bool(event and self.audit and self.audit[-1]['frame_index'] == index
                             and self.audit[-1]['identity'] != 'confirmed')
        if self.audit and self.audit[-1]['frame_index'] == index:
            if not preserve_loss:
                self.audit[-1] = item
        else:
            self.audit.append(item)
        if self.observations and self.observations[-1]['time'] == row['time']:
            if not preserve_loss:
                self.observations[-1] = row
        elif sampled:
            self.observations.append(previous_decoded_row if preserve_loss else row)
        if index in self.evidence_indices or event:
            self.evidence[index] = {
                **item, **self.analyzer.preview(), 'target_id': self.active_id,
                'target_box': target_box,
            }

    def _flush_last_decoded_observation(self):
        """Export the decoded tail once when bounded container metadata runs long."""
        if self.last_decoded_row is None:
            return
        row = copy.deepcopy(self.last_decoded_row)
        if self.observations and self.observations[-1]['time'] == row['time']:
            return
        if len(self.observations) >= MAX_OBSERVATIONS:
            raise ValueError('Website observation limit is 100000')
        if row['identity'] == 'confirmed' and self.posture_pulse:
            row['signals']['posture_change'] = True
        self.observations.append(row)
        self.posture_pulse = False

    def advance(self, time):
        self._open()
        if self.initial_selection is None:
            raise ValueError('Select the initial target first')
        if type(time) not in (int, float) or not math.isfinite(time):
            raise ValueError('Time must be finite')
        desired = int(math.floor(time * self.analyzer.fps + 1e-8))
        if desired < self.analyzer.frame_index or desired >= self.analyzer.frame_count:
            raise ValueError('Use a forward time inside the recording')
        try:
            for index in range(self.analyzer.frame_index + 1, desired + 1):
                self._record(self.analyzer.analyze(index / self.analyzer.fps))
            self.preview = self.analyzer.preview()
            return self.preview
        except BaseException:
            self.failed = True
            self.analyzer.close()
            raise

    def finish(self, *, max_uncertain_fraction=.10, progress=None):
        self._open()
        if (type(max_uncertain_fraction) not in (int, float)
                or not math.isfinite(max_uncertain_fraction) or not 0 <= max_uncertain_fraction < 1):
            raise ValueError('Quality threshold must be finite in [0, 1)')
        completion = 'complete'
        try:
            if progress:
                progress(self.analyzer.frame_index + 1, self.analyzer.frame_count)
            if self.initial_selection is None:
                raise ValueError('Select the initial target first')
            for index in range(self.analyzer.frame_index + 1, self.analyzer.frame_count):
                try:
                    self._record(self.analyzer.analyze(index / self.analyzer.fps))
                    if progress and (index % 100 == 0 or index == self.analyzer.frame_count - 1):
                        progress(index + 1, self.analyzer.frame_count)
                except EOFError:
                    gap = self.analyzer.frame_count - 1 - self.analyzer.frame_index
                    if not self.audit or not 1 <= gap <= MAX_TRAILING_DECODE_GAP_FRAMES:
                        raise
                    completion = 'trailing_eof_within_tolerance'
                    self._flush_last_decoded_observation()
                    break
            self._binding()
            source = {'name': self.video.name, 'sha256': self.source_sha256,
                      'duration': self.analyzer.duration, 'fps': self.analyzer.fps,
                      'frame_count': self.analyzer.frame_count,
                      'decoded_frame_count': self.analyzer.frame_index + 1,
                      'trailing_unreadable_frames': self.analyzer.frame_count - 1 - self.analyzer.frame_index,
                      'analysis_mode': 'precomputed'}
            uncertain = sum(row['identity'] != 'confirmed' for row in self.observations)
            uncertain_frames = sum(row['identity'] != 'confirmed' for row in self.audit)
            quality = {'uncertain_samples': uncertain, 'sample_count': len(self.observations),
                       'uncertain_frames': uncertain_frames, 'audited_frame_count': len(self.audit),
                       'uncertain_fraction': uncertain_frames / len(self.audit),
                       'uncertain_fraction_basis': 'causal_audit_frames',
                       'max_uncertain_fraction': max_uncertain_fraction,
                       'identity_reason_counts': dict(Counter(row['reason'] for row in self.audit if row['reason'])),
                       'observable_samples': {s: sum(row['signals'].get(s) is not None for row in self.observations) for s in SIGNALS}}
            quality['passed'] = quality['uncertain_fraction'] <= max_uncertain_fraction
            data = {'schema_version': 1, 'mode': 'precomputed', 'source': source,
                    'provenance': {'run_id': self.run_id, 'sha256': self.source_sha256,
                                   'analysis_mode': 'precomputed', 'adapter': 'aba_demo.vision.VideoAnalyzer',
                                   'weights': self._effective_config.get('weights', 'yolo11n-pose.pt'),
                                   'tracker': Path(resolve_tracker_profile(self._effective_config)).name,
                                   'config': self._effective_config,
                                   'config_sha256': self._config_sha256,
                                   'initial_selection': self.initial_selection,
                                   'applied_target_reselections': self.events, 'causal_audit': self.audit,
                                   'decode_completion': completion, 'quality': quality,
                                   'timestamp_basis': 'nominal_fps_cfr_only',
                                   'camera_stability': 'unchecked_fixed_camera_required',
                                   'clinical_validation': False}, 'observations': self.observations}
            if len(json.dumps(data, allow_nan=False, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > MAX_EXPORT_BYTES:
                raise ValueError('Candidate exceeds website 20 MB limit; use a shorter development clip')
            _write_json(self.pending, data)
            self.report = {'candidate_sha256': file_sha256(self.pending), 'quality': quality,
                           'run_id': self.run_id, 'source_sha256': self.source_sha256}
            _write_json(self.directory / 'review-report.json', self.report)
            self.finished = True
            return copy.deepcopy(self.report)
        except BaseException:
            self.failed = True
            self.pending.unlink(missing_ok=True)
            raise
        finally:
            self.analyzer.close()

    def _current_tracking_candidate(self):
        """Return exact pending tracking bytes after strict current-run binding."""
        self._binding()
        if (not self.finished or self.failed or not self.report
                or not isinstance(self.report.get('quality'), dict)
                or self.report['quality'].get('passed') is not True):
            raise ValueError('Current-run quality gate has not passed; pending artifact is quarantined')
        try:
            encoded = self.pending.read_bytes()
        except OSError:
            raise ValueError('Pending tracking candidate is unavailable') from None
        digest = hashlib.sha256(encoded).hexdigest()
        if digest != self.report.get('candidate_sha256'):
            raise ValueError('Candidate changed or digest is not the exact reviewed artifact')
        try:
            data = json.loads(encoded.decode('utf-8'))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ValueError('Pending tracking candidate is invalid') from None
        source = data.get('source') if isinstance(data, dict) else None
        provenance = data.get('provenance') if isinstance(data, dict) else None
        expected_source = {
            'name': self.video.name,
            'sha256': self.source_sha256,
            'duration': self.analyzer.duration,
            'fps': self.analyzer.fps,
            'frame_count': self.analyzer.frame_count,
        }
        if (data.get('schema_version') != 1 or data.get('mode') != 'precomputed'
                or not isinstance(source, dict) or not isinstance(provenance, dict)
                or any(source.get(key) != value for key, value in expected_source.items())
                or provenance.get('run_id') != self.run_id
                or provenance.get('sha256') != self.source_sha256
                or provenance.get('analysis_mode') != 'precomputed'
                or provenance.get('config_sha256') != self._config_sha256
                or provenance.get('config') != self._effective_config
                or provenance.get('quality') != self.report['quality']
                or provenance.get('quality', {}).get('passed') is not True
                or self.report.get('source_sha256') != self.source_sha256
                or self.report.get('run_id') != self.run_id):
            raise ValueError('Pending tracking candidate binding is invalid')
        return encoded, data, digest

    def _preflight_context_windows(self, windows):
        """Validate every local causal dependency before any provider call."""
        tracking_bytes, tracking_data, tracking_sha = self._current_tracking_candidate()
        if (self.context_pending.exists() or self.context_output.exists()
                or self.context_review_report.exists() or self.context_report is not None):
            raise ValueError('A context candidate already exists for this run')
        if not isinstance(windows, list) or not 1 <= len(windows) <= MAX_CONTEXT_WINDOWS:
            raise ValueError('Invalid context window specifications')
        source = tracking_data['source']
        duration, fps = source.get('duration'), source.get('fps')
        if (type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0
                or type(fps) not in (int, float) or not math.isfinite(fps) or fps <= 0):
            raise ValueError('Pending tracking candidate binding is invalid')
        audit = tracking_data['provenance'].get('causal_audit')
        if not isinstance(audit, list):
            raise ValueError('Pending tracking candidate binding is invalid')
        audit_by_index = {}
        for row in audit:
            index = row.get('frame_index') if isinstance(row, dict) else None
            if type(index) is not int or index < 0 or index in audit_by_index:
                raise ValueError('Pending tracking candidate binding is invalid')
            audit_by_index[index] = row

        prepared = []
        seen_ids = set()
        previous_end = None
        for specification in windows:
            if (not isinstance(specification, dict)
                    or set(specification) != {'segment_id', 'frame_indices'}):
                raise ValueError('Invalid context window specifications')
            identifier = specification['segment_id']
            indices = specification['frame_indices']
            if (not isinstance(indices, list) or not 1 <= len(indices) <= 4
                    or any(type(index) is not int or index < 0 for index in indices)
                    or any(current <= previous for previous, current in zip(indices, indices[1:]))):
                raise ValueError('Invalid context window specifications')
            if identifier in seen_ids:
                raise ValueError('Invalid context window specifications')
            if any(index not in self.evidence for index in indices):
                raise ValueError('Context frames must be retained review evidence')
            first, last = indices[0], indices[-1]
            for index in range(first, last + 1):
                row = audit_by_index.get(index)
                if row is None or row.get('identity') != 'confirmed':
                    raise ValueError('Context window is not causally identity-confirmed')
            evidence_rows = [copy.deepcopy(self.evidence[index]) for index in indices]
            for index, evidence_row in zip(indices, evidence_rows):
                audit_row = audit_by_index[index]
                if (evidence_row.get('frame_index') != index
                        or evidence_row.get('time') != audit_row.get('time')
                        or evidence_row.get('identity') != audit_row.get('identity')
                        or evidence_row.get('target_id') != audit_row.get('target_id')
                        or evidence_row.get('selection_event') != audit_row.get('selection_event')):
                    raise ValueError('Context evidence is not bound to the causal audit')
            frames = build_context_window(evidence_rows)
            start = frames[0]['time']
            end = min(frames[-1]['time'] + 1 / fps, duration)
            if end - start > 5:
                raise ValueError('Context window exceeds five seconds')
            placeholder = {
                'segment_id': identifier, 'start_time': start, 'end_time': end,
                'activity_suggestion': 'unclear', 'activity_status': 'not_observable',
                'target_material_interaction': 'not_observable',
                'adult_target_interaction_visible': 'not_observable',
                'evidence_times': [], 'clinician_confirmation': 'pending',
            }
            try:
                validate_context_segment(placeholder, duration)
            except ValueError:
                raise ValueError('Invalid context window specifications') from None
            if previous_end is not None and start < previous_end:
                raise ValueError('Context windows overlap or are out of order')
            seen_ids.add(identifier)
            previous_end = end
            prepared.append({'frames': frames, 'start': start, 'end': end,
                             'segment_id': identifier})
        return tracking_bytes, tracking_sha, duration, prepared

    def build_context_candidate(self, windows, adapter=None):
        """Invoke an explicit context adapter once per fully preflighted window."""
        tracking_bytes, tracking_sha, duration, prepared = self._preflight_context_windows(windows)
        if adapter is None:
            from .openrouter_context import OpenRouterContextAdapter
            adapter = OpenRouterContextAdapter()
        if not callable(getattr(adapter, 'analyze', None)):
            raise ValueError('Invalid context adapter')

        segments = []
        provenance = None
        for item in prepared:
            result = adapter.analyze(item['frames'], source_sha256=self.source_sha256)
            if not isinstance(result, dict) or set(result) != {'observation', 'provenance'}:
                raise ValueError('Invalid context adapter result')
            observation = result['observation']
            if not isinstance(observation, dict):
                raise ValueError('Invalid context adapter result')
            current_provenance = result['provenance']
            if provenance is None:
                provenance = copy.deepcopy(current_provenance)
            elif current_provenance != provenance:
                raise ValueError('Context provider provenance changed across windows')
            segment = {
                'segment_id': item['segment_id'],
                'start_time': item['start'],
                'end_time': item['end'],
                **copy.deepcopy(observation),
                'clinician_confirmation': 'pending',
            }
            try:
                segment = validate_context_segment(segment, duration)
            except ValueError:
                raise ValueError('Invalid context adapter result') from None
            segments.append(segment)

        current_tracking_bytes, _, current_tracking_sha = self._current_tracking_candidate()
        if current_tracking_bytes != tracking_bytes or current_tracking_sha != tracking_sha:
            raise ValueError('Pending tracking candidate changed during context analysis')
        document = {
            'schema_version': 1,
            'source_sha256': self.source_sha256,
            'tracking_candidate_sha256': tracking_sha,
            'provenance': provenance,
            'context_segments': segments,
        }
        try:
            document = validate_context_document(document, duration)
            encoded = encode_context_document(document, duration)
        except ValueError:
            raise ValueError('Invalid context adapter result') from None
        report = {
            'tracking_candidate_sha256': tracking_sha,
            'context_candidate_sha256': hashlib.sha256(encoded).hexdigest(),
            'segment_count': len(segments),
            'run_id': self.run_id,
            'source_sha256': self.source_sha256,
        }
        try:
            _write_bytes(self.context_pending, encoded)
            _write_json(self.context_review_report, report)
        except BaseException:
            self.context_pending.unlink(missing_ok=True)
            self.context_review_report.unlink(missing_ok=True)
            raise
        self.context_report = copy.deepcopy(report)
        return copy.deepcopy(report)

    def _current_context_candidate(self, tracking_sha):
        """Return exact canonical context bytes bound to the pending tracking artifact."""
        if not isinstance(self.context_report, dict):
            raise ValueError('Context candidate is unavailable')
        try:
            encoded = self.context_pending.read_bytes()
            recorded_report = json.loads(self.context_review_report.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
            raise ValueError('Context candidate is unavailable') from None
        digest = hashlib.sha256(encoded).hexdigest()
        if (recorded_report != self.context_report
                or digest != self.context_report.get('context_candidate_sha256')
                or tracking_sha != self.context_report.get('tracking_candidate_sha256')
                or self.context_report.get('run_id') != self.run_id
                or self.context_report.get('source_sha256') != self.source_sha256):
            raise ValueError('Context candidate changed or is not the exact reviewed artifact')
        try:
            document = json.loads(encoded.decode('utf-8'))
            document = validate_context_document(document, self.analyzer.duration)
            canonical = encode_context_document(document, self.analyzer.duration)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ValueError('Context candidate is invalid') from None
        if (canonical != encoded
                or document['source_sha256'] != self.source_sha256
                or document['tracking_candidate_sha256'] != tracking_sha
                or len(document['context_segments']) != self.context_report.get('segment_count')):
            raise ValueError('Context candidate binding is invalid')
        return encoded, digest

    def publish_with_context(self, tracking_sha, context_sha, *, confirmed=False, reviewer=''):
        """Publish one exactly reviewed tracking/context pair with one attestation."""
        if confirmed is not True or not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError('Human review confirmation and reviewer are required')
        tracking_bytes, _, current_tracking_sha = self._current_tracking_candidate()
        context_bytes, current_context_sha = self._current_context_candidate(current_tracking_sha)
        if (tracking_sha != current_tracking_sha
                or context_sha != current_context_sha):
            raise ValueError('Candidate pair is not the exact reviewed artifacts')
        if self.output.exists() or self.context_output.exists():
            raise ValueError('Published artifacts already exist for this run')

        review = {
            'reviewer': reviewer.strip(),
            'confirmed': True,
            'tracking_candidate_sha256': current_tracking_sha,
            'context_candidate_sha256': current_context_sha,
            'source_sha256': self.source_sha256,
            'run_id': self.run_id,
        }
        review_path = self.directory / 'human-review.json'
        _write_json(review_path, review)
        if json.loads(review_path.read_text(encoding='utf-8')) != review:
            raise RuntimeError('Human review record failed read-back verification')
        os.replace(self.pending, self.output)
        os.replace(self.context_pending, self.context_output)
        if (self.output.read_bytes() != tracking_bytes
                or self.context_output.read_bytes() != context_bytes):
            raise RuntimeError('Published artifacts failed read-back verification')
        return {'observations': self.output, 'context': self.context_output}

    def publish(self, reviewed_candidate_sha256, *, confirmed=False, reviewer=''):
        if confirmed is not True or not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError('Human review confirmation and reviewer are required')
        self._binding()
        if not self.finished or self.failed or not self.report or not self.report['quality']['passed']:
            raise ValueError('Current-run quality gate has not passed; pending artifact is quarantined')
        digest = file_sha256(self.pending)
        if reviewed_candidate_sha256 != digest or digest != self.report['candidate_sha256']:
            raise ValueError('Candidate changed or digest is not the exact reviewed artifact')
        _write_json(self.directory / 'human-review.json', {
            'reviewer': reviewer.strip(), 'confirmed': True, 'candidate_sha256': digest,
            'source_sha256': self.source_sha256, 'run_id': self.run_id})
        os.replace(self.pending, self.output)
        if file_sha256(self.output) != digest:
            raise RuntimeError('Published artifact failed read-back verification')
        return self.output

    def close(self):
        self.analyzer.close()
        if not self.finished:
            self.failed = True


def show_evidence(rows):
    """Render bounded evidence; images stay in notebook memory, never exported JSON."""
    import base64
    import io
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from PIL import Image
    rows = list(rows)
    if not rows:
        raise ValueError('No evidence frames')
    for start in range(0, len(rows), 12):
        batch = rows[start:start + 12]
        fig, axes = plt.subplots(math.ceil(len(batch) / 3), 3, figsize=(15, 4 * math.ceil(len(batch) / 3)), squeeze=False)
        for ax, row in zip(axes.flat, batch):
            ax.imshow(Image.open(io.BytesIO(base64.b64decode(row['image']))), extent=(0, 1, 1, 0))
            for box in row['boxes']:
                x1, y1, x2, y2 = box['xyxy']
                color = 'cyan' if box['id'] == row.get('target_id') else 'lime'
                ax.add_patch(Rectangle((x1, y1), x2 - x1, y2 - y1, fill=False, color=color))
                ax.text(x1, y1, str(box['id']), color='white', backgroundcolor='black')
            ax.set_title(f"{row['time']:.3f}s ID={row.get('target_id')} {row.get('identity', '')}\n{row.get('reason') or ''} {'RESELECT' if row.get('selection_event') else ''}")
            ax.set(xlim=(0, 1), ylim=(1, 0), xlabel='normalized x', ylabel='normalized y')
        for ax in list(axes.flat)[len(batch):]:
            ax.axis('off')
        fig.tight_layout()
        plt.show()
