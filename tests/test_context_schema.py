"""Strict provider-independent visual-context contract tests."""
import copy
import json
import unittest


class ContextSchemaTests(unittest.TestCase):
    def test_valid_model_output_is_normalized_without_free_prose(self):
        from aba_demo.context_schema import parse_model_output

        raw = json.dumps({
            'activity_suggestion': 'table',
            'activity_status': 'supported',
            'target_material_interaction': 'yes',
            'adult_target_interaction_visible': 'ambiguous',
            'evidence_times': [1.0, 1.5],
        })

        self.assertEqual(parse_model_output(raw, [1.0, 1.5, 2.0]), {
            'activity_suggestion': 'table',
            'activity_status': 'supported',
            'target_material_interaction': 'yes',
            'adult_target_interaction_visible': 'ambiguous',
            'evidence_times': [1.0, 1.5],
        })

    def test_model_output_rejects_duplicate_or_extra_keys(self):
        from aba_demo.context_schema import parse_model_output

        duplicate = (
            '{"activity_suggestion":"table","activity_suggestion":"movement",'
            '"activity_status":"supported","target_material_interaction":"yes",'
            '"adult_target_interaction_visible":"no","evidence_times":[1.0]}'
        )
        extra = json.dumps({
            'activity_suggestion': 'table',
            'activity_status': 'supported',
            'target_material_interaction': 'yes',
            'adult_target_interaction_visible': 'no',
            'evidence_times': [1.0],
            'description': 'free model prose is forbidden',
        })

        for raw in (duplicate, extra):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, 'invalid_context_output'):
                parse_model_output(raw, [1.0])

    def test_model_output_rejects_inconsistent_values_and_evidence(self):
        from aba_demo.context_schema import parse_model_output

        valid = {
            'activity_suggestion': 'table',
            'activity_status': 'supported',
            'target_material_interaction': 'yes',
            'adult_target_interaction_visible': 'no',
            'evidence_times': [1.0],
        }
        cases = [
            {**valid, 'activity_suggestion': 'attentive'},
            {**valid, 'activity_status': 'confident'},
            {**valid, 'target_material_interaction': 'purposeful'},
            {**valid, 'adult_target_interaction_visible': True},
            {**valid, 'evidence_times': []},
            {**valid, 'evidence_times': [True]},
            {**valid, 'evidence_times': [1.0, 1.0]},
            {**valid, 'evidence_times': [2.0, 1.0]},
            {**valid, 'evidence_times': [3.0]},
            {**valid, 'activity_suggestion': 'unclear'},
            {**valid, 'activity_status': 'ambiguous'},
            {**valid, 'activity_status': 'not_observable'},
        ]

        for value in cases:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'invalid_context_output'):
                parse_model_output(json.dumps(value), [1.0, 2.0])

    def test_model_output_rejects_malformed_fenced_or_oversized_documents(self):
        from aba_demo.context_schema import parse_model_output

        valid = json.dumps({
            'activity_suggestion': 'table',
            'activity_status': 'supported',
            'target_material_interaction': 'yes',
            'adult_target_interaction_visible': 'no',
            'evidence_times': [1.0],
        })
        cases = [None, b'{}', '', 'not-json', f'```json\n{valid}\n```',
                 'x' * 8193, '[]', 'null']

        for raw in cases:
            with self.subTest(raw_type=type(raw)), self.assertRaisesRegex(ValueError, 'invalid_context_output'):
                parse_model_output(raw, [1.0])

    def test_model_output_requires_a_valid_supplied_timestamp_contract(self):
        from aba_demo.context_schema import parse_model_output

        raw = json.dumps({
            'activity_suggestion': 'table',
            'activity_status': 'supported',
            'target_material_interaction': 'yes',
            'adult_target_interaction_visible': 'no',
            'evidence_times': [1.0],
        })
        cases = [None, [], [True], [-1], [float('nan')], [1.0, 1.0],
                 [2.0, 1.0], [1.0] * 5]

        for supplied_times in cases:
            with self.subTest(supplied_times=supplied_times), self.assertRaisesRegex(
                    ValueError, 'invalid_context_input'):
                parse_model_output(raw, supplied_times)

    def test_context_window_accepts_only_confirmed_aligned_jpegs(self):
        from aba_demo.context_schema import validate_context_window

        jpeg = b'\xff\xd8fixture\xff\xd9'
        frames = [
            {'time': 1.0, 'identity': 'confirmed', 'scene_jpeg': jpeg,
             'target_crop_jpeg': jpeg, 'target_box': [0.1, 0.2, 0.4, 0.9]},
            {'time': 1.5, 'identity': 'confirmed', 'scene_jpeg': jpeg,
             'target_crop_jpeg': jpeg, 'target_box': [0.2, 0.2, 0.5, 0.9]},
        ]

        self.assertEqual(validate_context_window(frames), [1.0, 1.5])

    def test_context_window_rejects_unconfirmed_unaligned_or_oversized_frames(self):
        from aba_demo.context_schema import validate_context_window

        jpeg = b'\xff\xd8fixture\xff\xd9'
        base = {'time': 1.0, 'identity': 'confirmed', 'scene_jpeg': jpeg,
                'target_crop_jpeg': jpeg, 'target_box': [0.1, 0.2, 0.4, 0.9]}
        cases = [
            [],
            [base] * 5,
            [{**base, 'private_note': 'forbidden'}],
            [{**base, 'identity': 'uncertain'}],
            [{**base, 'time': True}],
            [{**base, 'time': -1}],
            [{**base, 'time': float('nan')}],
            [base, dict(base)],
            [dict(base, time=2.0), dict(base, time=1.0)],
            [{**base, 'scene_jpeg': 'not-bytes'}],
            [{**base, 'scene_jpeg': b'not-jpeg'}],
            [{**base, 'target_crop_jpeg': b'not-jpeg'}],
            [{**base, 'scene_jpeg': b'\xff\xd8' + b'x' * (256 * 1024) + b'\xff\xd9'}],
            [{**base, 'target_box': None}],
            [{**base, 'target_box': [0, 0, 1]}],
            [{**base, 'target_box': [-0.1, 0, 1, 1]}],
            [{**base, 'target_box': [0, 0, 1.1, 1]}],
            [{**base, 'target_box': [0.5, 0, 0.5, 1]}],
            [{**base, 'target_box': [False, 0, 1, 1]}],
            [{**base, 'target_box': [0, 0, float('inf'), 1]}],
        ]

        for frames in cases:
            with self.subTest(frames=frames), self.assertRaisesRegex(ValueError, 'invalid_context_window'):
                validate_context_window(frames)

    def test_context_segment_accepts_typed_evidence_with_pending_human_review(self):
        from aba_demo.context_schema import validate_context_segment

        segment = {
            'segment_id': 'ctx-000001',
            'start_time': 1.0,
            'end_time': 2.0,
            'activity_suggestion': 'table',
            'activity_status': 'supported',
            'target_material_interaction': 'yes',
            'adult_target_interaction_visible': 'ambiguous',
            'evidence_times': [1.0, 1.5],
            'clinician_confirmation': 'pending',
        }

        self.assertEqual(validate_context_segment(segment, source_duration=3.0), segment)

    def test_context_segment_rejects_unbounded_untyped_or_unreviewable_values(self):
        from aba_demo.context_schema import validate_context_segment

        base = {
            'segment_id': 'ctx-000001',
            'start_time': 1.0,
            'end_time': 2.0,
            'activity_suggestion': 'table',
            'activity_status': 'supported',
            'target_material_interaction': 'yes',
            'adult_target_interaction_visible': 'ambiguous',
            'evidence_times': [1.0, 1.5],
            'clinician_confirmation': 'pending',
        }
        cases = [
            (None, 3.0),
            ({**base, 'free_prose': 'forbidden'}, 3.0),
            ({**base, 'segment_id': '../escape'}, 3.0),
            ({**base, 'segment_id': ''}, 3.0),
            ({**base, 'start_time': True}, 3.0),
            ({**base, 'start_time': -1}, 3.0),
            ({**base, 'end_time': 1.0}, 3.0),
            ({**base, 'end_time': 4.0}, 3.0),
            ({**base, 'evidence_times': [0.5]}, 3.0),
            ({**base, 'evidence_times': [2.5]}, 3.0),
            ({**base, 'evidence_times': [1.0, 1.0]}, 3.0),
            ({**base, 'clinician_confirmation': 'approved'}, 3.0),
            ({**base, 'activity_suggestion': 'attention'}, 3.0),
            (base, None),
            (base, float('inf')),
            (base, 0),
        ]

        for segment, duration in cases:
            with self.subTest(segment=segment, duration=duration), self.assertRaisesRegex(
                    ValueError, 'invalid_context_segment'):
                validate_context_segment(segment, source_duration=duration)

    def test_model_json_schema_is_strict_and_matches_the_parser_contract(self):
        from aba_demo.context_schema import MODEL_OUTPUT_JSON_SCHEMA

        self.assertEqual(MODEL_OUTPUT_JSON_SCHEMA['type'], 'object')
        self.assertFalse(MODEL_OUTPUT_JSON_SCHEMA['additionalProperties'])
        self.assertEqual(set(MODEL_OUTPUT_JSON_SCHEMA['required']), {
            'activity_suggestion', 'activity_status',
            'target_material_interaction', 'adult_target_interaction_visible',
            'evidence_times',
        })
        properties = MODEL_OUTPUT_JSON_SCHEMA['properties']
        self.assertEqual(properties['activity_suggestion']['enum'],
                         ['table', 'movement', 'break', 'transition', 'unclear'])
        self.assertEqual(properties['activity_status']['enum'],
                         ['supported', 'ambiguous', 'not_observable'])
        self.assertEqual(properties['target_material_interaction']['enum'],
                         ['yes', 'no', 'ambiguous', 'not_observable'])
        self.assertEqual(properties['adult_target_interaction_visible']['enum'],
                         ['yes', 'no', 'ambiguous', 'not_observable'])
        self.assertEqual(properties['evidence_times']['maxItems'], 4)
        self.assertTrue(properties['evidence_times']['uniqueItems'])

    def test_model_output_rejects_non_string_enum_types_stably(self):
        from aba_demo.context_schema import parse_model_output

        base = {
            'activity_suggestion': 'table',
            'activity_status': 'supported',
            'target_material_interaction': 'yes',
            'adult_target_interaction_visible': 'no',
            'evidence_times': [1.0],
        }
        cases = [
            {**base, 'activity_suggestion': {}},
            {**base, 'activity_status': []},
            {**base, 'target_material_interaction': None},
            {**base, 'adult_target_interaction_visible': True},
        ]

        for value in cases:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'invalid_context_output'):
                parse_model_output(json.dumps(value), [1.0])

    def test_context_segment_rejects_non_string_enum_types_stably(self):
        from aba_demo.context_schema import validate_context_segment

        base = {
            'segment_id': 'ctx-000001',
            'start_time': 1.0,
            'end_time': 2.0,
            'activity_suggestion': 'table',
            'activity_status': 'supported',
            'target_material_interaction': 'yes',
            'adult_target_interaction_visible': 'ambiguous',
            'evidence_times': [1.0],
            'clinician_confirmation': 'pending',
        }
        cases = [
            {**base, 'activity_suggestion': {}},
            {**base, 'activity_status': []},
            {**base, 'target_material_interaction': None},
            {**base, 'adult_target_interaction_visible': True},
            {**base, 'clinician_confirmation': {}},
        ]

        for segment in cases:
            with self.subTest(segment=segment), self.assertRaisesRegex(
                    ValueError, 'invalid_context_segment'):
                validate_context_segment(segment, source_duration=3.0)

    def test_context_document_rejects_extreme_numbers_as_validation_error(self):
        from aba_demo.context_schema import validate_context_segment

        segment = {
            'segment_id': 'ctx-extreme', 'start_time': 1.0, 'end_time': 2.0,
            'activity_suggestion': 'unclear', 'activity_status': 'not_observable',
            'target_material_interaction': 'not_observable',
            'adult_target_interaction_visible': 'not_observable',
            'evidence_times': [], 'clinician_confirmation': 'pending',
        }
        with self.assertRaisesRegex(ValueError, 'invalid_context_segment'):
            validate_context_segment(segment, source_duration=10 ** 400)

    def test_external_provenance_records_no_demo_consent_gate_and_privacy_filters(self):
        from aba_demo.context_schema import validate_context_provenance

        source_sha256 = 'a' * 64
        provenance = {
            'adapter': 'openrouter',
            'source_sha256': source_sha256,
            'requested_model': 'vendor/model:free',
            'resolved_model': 'vendor/model:free',
            'endpoint_provider': 'provider-name',
            'external_processing': True,
            'structured_outputs': True,
            'data_collection': 'deny',
            'zero_data_retention_required': False,
            'sent_fields': ['scene_jpeg', 'target_crop_jpeg', 'timestamps', 'target_box'],
            'consent': {
                'required': False,
                'granted': False,
                'source_sha256': None,
            },
        }

        self.assertEqual(validate_context_provenance(provenance, source_sha256), provenance)

    def test_external_provenance_rejects_false_consent_claims_or_malformed_policy(self):
        from aba_demo.context_schema import validate_context_provenance

        source_sha256 = 'a' * 64
        base = {
            'adapter': 'openrouter',
            'source_sha256': source_sha256,
            'requested_model': 'vendor/model:free',
            'resolved_model': 'vendor/model:free',
            'endpoint_provider': 'provider-name',
            'external_processing': True,
            'structured_outputs': True,
            'data_collection': 'deny',
            'zero_data_retention_required': False,
            'sent_fields': ['scene_jpeg', 'target_crop_jpeg', 'timestamps', 'target_box'],
            'consent': {'required': False, 'granted': False, 'source_sha256': None},
        }
        cases = [
            {**base, 'consent': {'required': True, 'granted': True,
                                 'source_sha256': source_sha256}},
            {**base, 'consent': {'required': False, 'granted': True,
                                 'source_sha256': None}},
            {**base, 'consent': None},
        ]

        for provenance in cases:
            with self.subTest(provenance=provenance), self.assertRaisesRegex(
                    ValueError, 'invalid_context_provenance'):
                validate_context_provenance(provenance, source_sha256)

    def test_external_provenance_rejects_weakened_privacy_or_secret_fields(self):
        from aba_demo.context_schema import validate_context_provenance

        source_sha256 = 'a' * 64
        base = {
            'adapter': 'openrouter',
            'source_sha256': source_sha256,
            'requested_model': 'vendor/model:free',
            'resolved_model': 'vendor/model:free',
            'endpoint_provider': 'provider-name',
            'external_processing': True,
            'structured_outputs': True,
            'data_collection': 'deny',
            'zero_data_retention_required': False,
            'sent_fields': ['scene_jpeg', 'target_crop_jpeg', 'timestamps', 'target_box'],
            'consent': {'required': False, 'granted': False, 'source_sha256': None},
        }
        cases = [
            {**base, 'api_key': 'must-never-be-recorded'},
            {**base, 'adapter': 'other'},
            {**base, 'requested_model': 'openrouter/free'},
            {**base, 'requested_model': ''},
            {**base, 'resolved_model': ''},
            {**base, 'endpoint_provider': ''},
            {**base, 'external_processing': False},
            {**base, 'structured_outputs': False},
            {**base, 'data_collection': 'allow'},
            {**base, 'zero_data_retention_required': True},
            {**base, 'sent_fields': ['scene_jpeg']},
        ]

        for provenance in cases:
            with self.subTest(provenance=provenance), self.assertRaisesRegex(
                    ValueError, 'invalid_context_provenance'):
                validate_context_provenance(provenance, source_sha256)

    def test_external_provenance_rejects_noncanonical_source_hash(self):
        from aba_demo.context_schema import validate_context_provenance

        source_sha256 = 'invalid'
        provenance = {
            'adapter': 'openrouter',
            'source_sha256': source_sha256,
            'requested_model': 'vendor/model:free',
            'resolved_model': 'vendor/model:free',
            'endpoint_provider': 'provider-name',
            'external_processing': True,
            'structured_outputs': True,
            'data_collection': 'deny',
            'zero_data_retention_required': False,
            'sent_fields': ['scene_jpeg', 'target_crop_jpeg', 'timestamps', 'target_box'],
            'consent': {'required': True, 'granted': True, 'source_sha256': 'invalid'},
        }

        with self.assertRaisesRegex(ValueError, 'invalid_context_provenance'):
            validate_context_provenance(provenance, source_sha256)

    def test_local_provenance_uses_the_same_contract_without_external_consent(self):
        from aba_demo.context_schema import validate_context_provenance

        source_sha256 = 'a' * 64
        provenance = {
            'adapter': 'local_transformers',
            'source_sha256': source_sha256,
            'requested_model': 'vendor/local-model',
            'resolved_model': 'vendor/local-model',
            'endpoint_provider': None,
            'external_processing': False,
            'structured_outputs': False,
            'data_collection': 'local',
            'zero_data_retention_required': False,
            'sent_fields': [],
            'consent': {'required': False, 'granted': False, 'source_sha256': None},
        }

        self.assertEqual(validate_context_provenance(provenance, source_sha256), provenance)

    def test_context_document_has_canonical_bytes_and_no_model_prose(self):
        from aba_demo.context_schema import encode_context_document, validate_context_document

        source_sha256 = 'a' * 64
        provenance = {
            'adapter': 'openrouter',
            'source_sha256': source_sha256,
            'requested_model': 'vendor/model:free',
            'resolved_model': 'vendor/model:free',
            'endpoint_provider': 'provider-name',
            'external_processing': True,
            'structured_outputs': True,
            'data_collection': 'deny',
            'zero_data_retention_required': False,
            'sent_fields': ['scene_jpeg', 'target_crop_jpeg', 'timestamps', 'target_box'],
            'consent': {'required': False, 'granted': False, 'source_sha256': None},
        }
        segment = {
            'segment_id': 'ctx-000001', 'start_time': 1.0, 'end_time': 2.0,
            'activity_suggestion': 'table', 'activity_status': 'supported',
            'target_material_interaction': 'yes',
            'adult_target_interaction_visible': 'ambiguous',
            'evidence_times': [1.0, 1.5], 'clinician_confirmation': 'pending',
        }
        document = {
            'schema_version': 1,
            'source_sha256': source_sha256,
            'tracking_candidate_sha256': 'b' * 64,
            'provenance': provenance,
            'context_segments': [segment],
        }

        self.assertEqual(validate_context_document(document, source_duration=3.0), document)
        encoded = encode_context_document(document, source_duration=3.0)
        self.assertIsInstance(encoded, bytes)
        self.assertEqual(encoded, json.dumps(
            document, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(',', ':')).encode('utf-8'))
        self.assertNotIn(b'description', encoded)

    def test_context_document_rejects_unbound_or_overlapping_segments(self):
        from aba_demo.context_schema import validate_context_document

        source_sha256 = 'a' * 64
        provenance = {
            'adapter': 'openrouter', 'requested_model': 'vendor/model:free',
            'source_sha256': source_sha256,
            'resolved_model': 'vendor/model:free', 'endpoint_provider': 'provider-name',
            'external_processing': True, 'structured_outputs': True,
            'data_collection': 'deny', 'zero_data_retention_required': False,
            'sent_fields': ['scene_jpeg', 'target_crop_jpeg', 'timestamps', 'target_box'],
            'consent': {'required': False, 'granted': False, 'source_sha256': None},
        }
        first = {
            'segment_id': 'ctx-000001', 'start_time': 1.0, 'end_time': 2.0,
            'activity_suggestion': 'table', 'activity_status': 'supported',
            'target_material_interaction': 'yes',
            'adult_target_interaction_visible': 'ambiguous',
            'evidence_times': [1.0], 'clinician_confirmation': 'pending',
        }
        second = {**first, 'segment_id': 'ctx-000002', 'start_time': 2.0,
                  'end_time': 3.0, 'evidence_times': [2.0]}
        base = {
            'schema_version': 1, 'source_sha256': source_sha256,
            'tracking_candidate_sha256': 'b' * 64,
            'provenance': provenance, 'context_segments': [first, second],
        }
        mismatched = copy.deepcopy(base)
        mismatched['provenance']['consent']['source_sha256'] = 'c' * 64
        cases = [
            None,
            {**base, 'free_prose': 'forbidden'},
            {**base, 'schema_version': 2},
            {**base, 'source_sha256': 'c' * 64},
            {**base, 'tracking_candidate_sha256': 'invalid'},
            {**base, 'context_segments': None},
            {**base, 'context_segments': [first, first]},
            {**base, 'context_segments': [first, {**second, 'start_time': 1.5}]},
            {**base, 'context_segments': [second, first]},
            mismatched,
        ]

        for document in cases:
            with self.subTest(document=document), self.assertRaisesRegex(
                    ValueError, 'invalid_context_document'):
                validate_context_document(document, source_duration=3.0)


if __name__ == '__main__':
    unittest.main()
