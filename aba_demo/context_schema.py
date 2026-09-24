"""Strict provider-independent contract for visible VLM context.

The contract accepts only bounded enum-valued observations tied to timestamps
that were actually supplied to the model. It intentionally has no free-prose,
identity, diagnosis, behavioral-function, or treatment fields.
"""

import json
import math
import re


MODEL_OUTPUT_KEYS = frozenset({
    'activity_suggestion',
    'activity_status',
    'target_material_interaction',
    'adult_target_interaction_visible',
    'evidence_times',
})
ACTIVITY_SUGGESTIONS = frozenset({'table', 'movement', 'break', 'transition', 'unclear'})
ACTIVITY_STATUSES = frozenset({'supported', 'ambiguous', 'not_observable'})
INTERACTION_STATUSES = frozenset({'yes', 'no', 'ambiguous', 'not_observable'})
MODEL_OUTPUT_JSON_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {
        'activity_suggestion': {
            'type': 'string',
            'enum': ['table', 'movement', 'break', 'transition', 'unclear'],
        },
        'activity_status': {
            'type': 'string',
            'enum': ['supported', 'ambiguous', 'not_observable'],
        },
        'target_material_interaction': {
            'type': 'string',
            'enum': ['yes', 'no', 'ambiguous', 'not_observable'],
        },
        'adult_target_interaction_visible': {
            'type': 'string',
            'enum': ['yes', 'no', 'ambiguous', 'not_observable'],
        },
        'evidence_times': {
            'type': 'array',
            'items': {'type': 'number', 'minimum': 0},
            'maxItems': 4,
            'uniqueItems': True,
        },
    },
    'required': [
        'activity_suggestion', 'activity_status',
        'target_material_interaction', 'adult_target_interaction_visible',
        'evidence_times',
    ],
}
MAX_CONTEXT_FRAMES = 4
MAX_IMAGE_BYTES = 256 * 1024
MAX_MODEL_OUTPUT_CHARS = 8192
WINDOW_FRAME_KEYS = frozenset({
    'time', 'identity', 'scene_jpeg', 'target_crop_jpeg', 'target_box',
})
CONTEXT_SEGMENT_KEYS = frozenset({
    'segment_id', 'start_time', 'end_time',
    'activity_suggestion', 'activity_status',
    'target_material_interaction', 'adult_target_interaction_visible',
    'evidence_times', 'clinician_confirmation',
})
CLINICIAN_CONFIRMATIONS = frozenset({'pending', 'confirmed', 'rejected'})
CONTEXT_PROVENANCE_KEYS = frozenset({
    'adapter', 'source_sha256', 'requested_model', 'resolved_model', 'endpoint_provider',
    'external_processing', 'structured_outputs', 'data_collection',
    'zero_data_retention_required', 'sent_fields', 'consent',
})
CONTEXT_SENT_FIELDS = [
    'scene_jpeg', 'target_crop_jpeg', 'timestamps', 'target_box',
]
CONTEXT_DOCUMENT_KEYS = frozenset({
    'schema_version', 'source_sha256', 'tracking_candidate_sha256',
    'provenance', 'context_segments',
})
MAX_CONTEXT_SEGMENTS = 10000


def _finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _jpeg(value):
    return (isinstance(value, bytes) and 4 <= len(value) <= MAX_IMAGE_BYTES
            and value.startswith(b'\xff\xd8') and value.endswith(b'\xff\xd9'))


def validate_context_window(frames):
    """Validate one in-memory, target-grounded image window."""
    if not isinstance(frames, list) or not 1 <= len(frames) <= MAX_CONTEXT_FRAMES:
        raise ValueError('invalid_context_window')
    times = []
    for frame in frames:
        if not isinstance(frame, dict) or set(frame) != WINDOW_FRAME_KEYS:
            raise ValueError('invalid_context_window')
        time = frame['time']
        box = frame['target_box']
        if (not _finite_number(time) or time < 0 or (times and time <= times[-1])
                or frame['identity'] != 'confirmed'
                or not _jpeg(frame['scene_jpeg']) or not _jpeg(frame['target_crop_jpeg'])
                or not isinstance(box, list) or len(box) != 4
                or not all(_finite_number(value) and 0 <= value <= 1 for value in box)
                or not box[0] < box[2] or not box[1] < box[3]):
            raise ValueError('invalid_context_window')
        times.append(time)
    return times


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('invalid_context_output')
        result[key] = value
    return result


def validate_context_provenance(provenance, source_sha256):
    """Validate one provider record bound to its source video."""
    if (not isinstance(source_sha256, str)
            or re.fullmatch(r'[0-9a-f]{64}', source_sha256) is None
            or not isinstance(provenance, dict)
            or set(provenance) != CONTEXT_PROVENANCE_KEYS):
        raise ValueError('invalid_context_provenance')
    requested = provenance['requested_model']
    resolved = provenance['resolved_model']
    if (provenance['source_sha256'] != source_sha256
            or not isinstance(requested, str) or not 1 <= len(requested) <= 200 or '/' not in requested
            or not isinstance(resolved, str) or not 1 <= len(resolved) <= 200 or '/' not in resolved):
        raise ValueError('invalid_context_provenance')

    consent = provenance['consent']
    if not isinstance(consent, dict) or set(consent) != {'required', 'granted', 'source_sha256'}:
        raise ValueError('invalid_context_provenance')

    if provenance['adapter'] == 'openrouter':
        valid = (
            requested != 'openrouter/free'
            and resolved != 'openrouter/free'
            and isinstance(provenance['endpoint_provider'], str)
            and 1 <= len(provenance['endpoint_provider']) <= 128
            and provenance['external_processing'] is True
            and provenance['structured_outputs'] is True
            and provenance['data_collection'] == 'deny'
            and provenance['zero_data_retention_required'] is False
            and provenance['sent_fields'] == CONTEXT_SENT_FIELDS
            and consent['required'] is False
            and consent['granted'] is False
            and consent['source_sha256'] is None
        )
    elif provenance['adapter'] == 'local_transformers':
        valid = (
            provenance['endpoint_provider'] is None
            and provenance['external_processing'] is False
            and provenance['structured_outputs'] is False
            and provenance['data_collection'] == 'local'
            and provenance['zero_data_retention_required'] is False
            and provenance['sent_fields'] == []
            and consent['required'] is False
            and consent['granted'] is False
            and consent['source_sha256'] is None
        )
    else:
        valid = False
    if not valid:
        raise ValueError('invalid_context_provenance')
    return dict(provenance)


def validate_context_document(document, source_duration):
    """Validate one provider-neutral context sidecar document."""
    if (not isinstance(document, dict) or set(document) != CONTEXT_DOCUMENT_KEYS
            or document['schema_version'] != 1
            or not _finite_number(source_duration) or source_duration <= 0
            or not isinstance(document['source_sha256'], str)
            or re.fullmatch(r'[0-9a-f]{64}', document['source_sha256']) is None
            or not isinstance(document['tracking_candidate_sha256'], str)
            or re.fullmatch(r'[0-9a-f]{64}', document['tracking_candidate_sha256']) is None
            or not isinstance(document['context_segments'], list)
            or len(document['context_segments']) > MAX_CONTEXT_SEGMENTS):
        raise ValueError('invalid_context_document')
    try:
        provenance = validate_context_provenance(
            document['provenance'], document['source_sha256'])
        segments = [validate_context_segment(segment, source_duration)
                    for segment in document['context_segments']]
    except ValueError:
        raise ValueError('invalid_context_document') from None
    seen_ids = set()
    previous_end = 0.0
    for segment in segments:
        if segment['segment_id'] in seen_ids or segment['start_time'] < previous_end:
            raise ValueError('invalid_context_document')
        seen_ids.add(segment['segment_id'])
        previous_end = segment['end_time']
    normalized = dict(document)
    normalized['provenance'] = provenance
    normalized['context_segments'] = segments
    return normalized


def encode_context_document(document, source_duration):
    """Return deterministic UTF-8 bytes for one validated context document."""
    validated = validate_context_document(document, source_duration)
    return json.dumps(validated, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(',', ':')).encode('utf-8')


def validate_context_segment(segment, source_duration):
    """Validate one exported context segment."""
    if (not _finite_number(source_duration) or source_duration <= 0
            or not isinstance(segment, dict) or set(segment) != CONTEXT_SEGMENT_KEYS):
        raise ValueError('invalid_context_segment')
    identifier = segment['segment_id']
    start, end = segment['start_time'], segment['end_time']
    evidence = segment['evidence_times']
    enum_values = (
        (segment['activity_suggestion'], ACTIVITY_SUGGESTIONS),
        (segment['activity_status'], ACTIVITY_STATUSES),
        (segment['target_material_interaction'], INTERACTION_STATUSES),
        (segment['adult_target_interaction_visible'], INTERACTION_STATUSES),
        (segment['clinician_confirmation'], CLINICIAN_CONFIRMATIONS),
    )
    if (not isinstance(identifier, str)
            or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', identifier) is None
            or not _finite_number(start) or not _finite_number(end)
            or not 0 <= start < end <= source_duration
            or any(not isinstance(value, str) or value not in allowed
                   for value, allowed in enum_values)
            or not isinstance(evidence, list)
            or any(not _finite_number(time) or not start <= time <= end for time in evidence)
            or any(current <= previous for previous, current in zip(evidence, evidence[1:]))):
        raise ValueError('invalid_context_segment')
    activity_supported = segment['activity_status'] == 'supported'
    if activity_supported != (segment['activity_suggestion'] != 'unclear'):
        raise ValueError('invalid_context_segment')
    has_observation = (activity_supported
                       or segment['target_material_interaction'] in ('yes', 'no')
                       or segment['adult_target_interaction_visible'] in ('yes', 'no'))
    if has_observation and not evidence:
        raise ValueError('invalid_context_segment')
    return dict(segment)


def parse_model_output(raw, supplied_times):
    """Parse one exact model result; no transport/provider assumptions."""
    if (not isinstance(supplied_times, list)
            or not 1 <= len(supplied_times) <= MAX_CONTEXT_FRAMES
            or any(not _finite_number(time) or time < 0 for time in supplied_times)
            or any(current <= previous for previous, current in
                   zip(supplied_times, supplied_times[1:]))):
        raise ValueError('invalid_context_input')
    if not isinstance(raw, str) or not 1 <= len(raw) <= MAX_MODEL_OUTPUT_CHARS:
        raise ValueError('invalid_context_output')
    try:
        result = json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, TypeError, RecursionError):
        raise ValueError('invalid_context_output') from None
    if not isinstance(result, dict) or set(result) != MODEL_OUTPUT_KEYS:
        raise ValueError('invalid_context_output')
    enum_fields = (
        ('activity_suggestion', ACTIVITY_SUGGESTIONS),
        ('activity_status', ACTIVITY_STATUSES),
        ('target_material_interaction', INTERACTION_STATUSES),
        ('adult_target_interaction_visible', INTERACTION_STATUSES),
    )
    if any(not isinstance(result[field], str) or result[field] not in allowed
           for field, allowed in enum_fields):
        raise ValueError('invalid_context_output')
    evidence = result['evidence_times']
    if (not isinstance(evidence, list)
            or any(type(time) not in (int, float) or not math.isfinite(time)
                   for time in evidence)
            or any(current <= previous for previous, current in zip(evidence, evidence[1:]))):
        raise ValueError('invalid_context_output')
    allowed = set(supplied_times)
    if any(time not in allowed for time in evidence):
        raise ValueError('invalid_context_output')
    activity_supported = result['activity_status'] == 'supported'
    if activity_supported != (result['activity_suggestion'] != 'unclear'):
        raise ValueError('invalid_context_output')
    has_observation = (activity_supported
                       or result['target_material_interaction'] in ('yes', 'no')
                       or result['adult_target_interaction_visible'] in ('yes', 'no'))
    if has_observation and not evidence:
        raise ValueError('invalid_context_output')
    return result
