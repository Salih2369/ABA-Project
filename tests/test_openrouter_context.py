"""No-network contract tests for the OpenRouter context adapter."""
import base64
import copy
import json
import unittest
import urllib.error
from unittest.mock import patch


class OpenRouterContextAdapterTests(unittest.TestCase):
    def test_default_model_is_concrete_and_replaceable_by_one_setting(self):
        from aba_demo.openrouter_context import (
            DEFAULT_OPENROUTER_VLM_MODEL,
            OpenRouterContextAdapter,
        )

        self.assertEqual(DEFAULT_OPENROUTER_VLM_MODEL, 'nex-agi/nex-n2.5-pro:free')
        self.assertNotEqual(DEFAULT_OPENROUTER_VLM_MODEL, 'openrouter/free')
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(OpenRouterContextAdapter(api_key='fixture-key').model,
                             DEFAULT_OPENROUTER_VLM_MODEL)
        with patch.dict('os.environ', {'ABA_OPENROUTER_VLM_MODEL': 'vendor/other:free'},
                        clear=True):
            self.assertEqual(OpenRouterContextAdapter(api_key='fixture-key').model,
                             'vendor/other:free')
        self.assertEqual(OpenRouterContextAdapter(
            api_key='fixture-key', model='vendor/explicit').model, 'vendor/explicit')

    def test_demo_does_not_require_a_per_video_consent_gate(self):
        from aba_demo.openrouter_context import OpenRouterContextAdapter

        source_sha256 = 'a' * 64
        jpeg = b'\xff\xd8fixture\xff\xd9'
        frames = [{'time': 1.0, 'identity': 'confirmed', 'scene_jpeg': jpeg,
                   'target_crop_jpeg': jpeg, 'target_box': [0.1, 0.2, 0.4, 0.9]}]
        output = {
            'activity_suggestion': 'unclear', 'activity_status': 'not_observable',
            'target_material_interaction': 'not_observable',
            'adult_target_interaction_visible': 'not_observable', 'evidence_times': [],
        }
        envelope = {
            'model': 'vendor/model:free',
            'choices': [{'finish_reason': 'stop',
                         'message': {'role': 'assistant',
                                     'content': json.dumps(output)}}],
            'openrouter_metadata': {
                'requested': 'vendor/model:free',
                'endpoints': {'available': [{
                    'provider': 'fixture-provider', 'model': 'vendor/model:free',
                    'selected': True,
                }]},
            },
        }
        transport = unittest.mock.Mock(
            return_value=(200, json.dumps(envelope).encode('utf-8')))
        adapter = OpenRouterContextAdapter(
            api_key='fixture-key', model='vendor/model:free', transport=transport)

        result = adapter.analyze(frames, source_sha256=source_sha256)

        self.assertEqual(result['observation'], output)
        self.assertEqual(result['provenance']['consent'], {
            'required': False, 'granted': False, 'source_sha256': None,
        })
        transport.assert_called_once()

    def test_invalid_or_uncertain_context_window_never_reaches_transport(self):
        from aba_demo.openrouter_context import OpenRouterContextAdapter, OpenRouterContextError

        source_sha256 = 'a' * 64
        jpeg = b'\xff\xd8fixture\xff\xd9'
        base = {'time': 1.0, 'identity': 'confirmed', 'scene_jpeg': jpeg,
                'target_crop_jpeg': jpeg, 'target_box': [0.1, 0.2, 0.4, 0.9]}
        cases = [[], [{**base, 'identity': 'uncertain'}],
                 [{**base, 'target_box': [0, 0, 0, 1]}]]

        for frames in cases:
            with self.subTest(frames=frames):
                transport = unittest.mock.Mock()
                adapter = OpenRouterContextAdapter(
                    api_key='fixture-key', model='vendor/model:free',
                    transport=transport)
                with self.assertRaises(OpenRouterContextError) as caught:
                    adapter.analyze(frames, source_sha256=source_sha256)
                self.assertEqual(caught.exception.code, 'invalid_input')
                transport.assert_not_called()

    def test_invalid_source_hash_never_reaches_transport(self):
        from aba_demo.openrouter_context import OpenRouterContextAdapter, OpenRouterContextError

        jpeg = b'\xff\xd8fixture\xff\xd9'
        frames = [{'time': 1.0, 'identity': 'confirmed', 'scene_jpeg': jpeg,
                   'target_crop_jpeg': jpeg, 'target_box': [0.1, 0.2, 0.4, 0.9]}]
        transport = unittest.mock.Mock()
        adapter = OpenRouterContextAdapter(
            api_key='fixture-key', model='vendor/model:free',
            transport=transport)

        with self.assertRaises(OpenRouterContextError) as caught:
            adapter.analyze(frames, source_sha256='invalid')
        self.assertEqual(caught.exception.code, 'invalid_input')
        transport.assert_not_called()

    def test_missing_api_key_fails_before_transport(self):
        from aba_demo.openrouter_context import OpenRouterContextAdapter, OpenRouterContextError

        source_sha256 = 'a' * 64
        jpeg = b'\xff\xd8fixture\xff\xd9'
        frames = [{'time': 1.0, 'identity': 'confirmed', 'scene_jpeg': jpeg,
                   'target_crop_jpeg': jpeg, 'target_box': [0.1, 0.2, 0.4, 0.9]}]
        transport = unittest.mock.Mock()
        with patch.dict('os.environ', {}, clear=True):
            adapter = OpenRouterContextAdapter(transport=transport)
            with self.assertRaises(OpenRouterContextError) as caught:
                adapter.analyze(frames, source_sha256=source_sha256)
        self.assertEqual(caught.exception.code, 'provider_not_configured')
        transport.assert_not_called()

    def test_valid_request_uses_strict_schema_privacy_routing_and_configured_model(self):
        from aba_demo.context_schema import MODEL_OUTPUT_JSON_SCHEMA
        from aba_demo.openrouter_context import OpenRouterContextAdapter

        source_sha256 = 'a' * 64
        scene = b'\xff\xd8scene\xff\xd9'
        crop = b'\xff\xd8crop\xff\xd9'
        frames = [{'time': 1.0, 'identity': 'confirmed', 'scene_jpeg': scene,
                   'target_crop_jpeg': crop, 'target_box': [0.1, 0.2, 0.4, 0.9]}]
        model_output = {
            'activity_suggestion': 'table', 'activity_status': 'supported',
            'target_material_interaction': 'yes',
            'adult_target_interaction_visible': 'ambiguous',
            'evidence_times': [1.0],
        }
        envelope = {
            'id': 'gen-fixture', 'model': 'vendor/model:free',
            'choices': [{'finish_reason': 'stop',
                         'message': {'role': 'assistant',
                                     'content': json.dumps(model_output)}}],
            'openrouter_metadata': {
                'requested': 'vendor/model:free',
                'endpoints': {'available': [{
                    'provider': 'fixture-provider', 'model': 'vendor/model:free',
                    'selected': True,
                }]},
            },
        }
        transport = unittest.mock.Mock(return_value=(200, json.dumps(envelope).encode('utf-8')))

        result = OpenRouterContextAdapter(
            api_key='fixture-key', model='vendor/model:free',
            transport=transport,
        ).analyze(frames, source_sha256=source_sha256)

        self.assertEqual(result['observation'], model_output)
        self.assertEqual(result['provenance']['requested_model'], 'vendor/model:free')
        self.assertEqual(result['provenance']['resolved_model'], 'vendor/model:free')
        self.assertEqual(result['provenance']['endpoint_provider'], 'fixture-provider')
        self.assertFalse(result['provenance']['zero_data_retention_required'])
        self.assertEqual(result['provenance']['consent'], {
            'required': False, 'granted': False, 'source_sha256': None,
        })
        transport.assert_called_once()
        args, kwargs = transport.call_args
        self.assertEqual(args, ('https://openrouter.ai/api/v1/chat/completions',))
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer fixture-key')
        self.assertEqual(kwargs['headers']['X-OpenRouter-Metadata'], 'enabled')
        request = json.loads(kwargs['body'])
        self.assertEqual(request['model'], 'vendor/model:free')
        self.assertFalse(request['stream'])
        self.assertEqual(request['temperature'], 0)
        self.assertLessEqual(request['max_tokens'], 256)
        self.assertEqual(request['provider'], {
            'require_parameters': True, 'data_collection': 'deny',
            'allow_fallbacks': False,
        })
        self.assertNotIn('zdr', request['provider'])
        self.assertEqual(request['response_format'], {
            'type': 'json_schema',
            'json_schema': {'name': 'aba_visible_context', 'strict': True,
                            'schema': MODEL_OUTPUT_JSON_SCHEMA},
        })
        content = request['messages'][1]['content']
        self.assertEqual(json.loads(content[0]['text']), {
            'time': 1.0, 'target_box': [0.1, 0.2, 0.4, 0.9],
            'image_order': ['scene', 'target_crop'],
        })
        self.assertEqual(content[1]['image_url']['url'],
                         'data:image/jpeg;base64,' + base64.b64encode(scene).decode('ascii'))
        self.assertEqual(content[2]['image_url']['url'],
                         'data:image/jpeg;base64,' + base64.b64encode(crop).decode('ascii'))

    def test_invalid_key_or_model_configuration_never_reaches_transport(self):
        from aba_demo.openrouter_context import OpenRouterContextAdapter, OpenRouterContextError

        source_sha256 = 'a' * 64
        jpeg = b'\xff\xd8fixture\xff\xd9'
        frames = [{'time': 1.0, 'identity': 'confirmed', 'scene_jpeg': jpeg,
                   'target_crop_jpeg': jpeg, 'target_box': [0.1, 0.2, 0.4, 0.9]}]
        cases = [
            ('bad key', 'vendor/model:free'),
            ('fixture-key', ''),
            ('fixture-key', 'openrouter/free'),
            ('fixture-key', 'model-without-vendor'),
            ('fixture-key', 'vendor/model\n'),
        ]

        for api_key, model in cases:
            with self.subTest(api_key=api_key, model=model):
                transport = unittest.mock.Mock()
                adapter = OpenRouterContextAdapter(
                    api_key=api_key, model=model, transport=transport)
                with self.assertRaises(OpenRouterContextError) as caught:
                    adapter.analyze(frames, source_sha256=source_sha256)
                self.assertEqual(caught.exception.code, 'invalid_config')
                transport.assert_not_called()

    def test_provider_failures_have_stable_private_codes_without_retries(self):
        from aba_demo.openrouter_context import OpenRouterContextAdapter, OpenRouterContextError

        source_sha256 = 'a' * 64
        jpeg = b'\xff\xd8fixture\xff\xd9'
        frames = [{'time': 1.0, 'identity': 'confirmed', 'scene_jpeg': jpeg,
                   'target_crop_jpeg': jpeg, 'target_box': [0.1, 0.2, 0.4, 0.9]}]
        cases = [
            (401, 'provider_unauthorized'), (403, 'provider_unauthorized'),
            (402, 'provider_credit_exhausted'), (429, 'provider_rate_limited'),
            (400, 'provider_request_rejected'), (422, 'provider_request_rejected'),
            (404, 'provider_no_compatible_route'),
            (408, 'provider_timeout'), (504, 'provider_timeout'),
            (413, 'provider_request_too_large'),
            (500, 'provider_unavailable'), (503, 'provider_unavailable'),
        ]

        for status, code in cases:
            with self.subTest(status=status):
                transport = unittest.mock.Mock(return_value=(status, b'private provider body'))
                adapter = OpenRouterContextAdapter(
                    api_key='fixture-key', model='vendor/model:free',
                    transport=transport)
                with self.assertRaises(OpenRouterContextError) as caught:
                    adapter.analyze(frames, source_sha256=source_sha256)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(str(caught.exception), code)
                self.assertNotIn('private provider body', repr(caught.exception))
                transport.assert_called_once()

    def test_transport_exceptions_are_sanitized_as_provider_unavailable(self):
        from aba_demo.openrouter_context import OpenRouterContextAdapter, OpenRouterContextError

        source_sha256 = 'a' * 64
        jpeg = b'\xff\xd8fixture\xff\xd9'
        frames = [{'time': 1.0, 'identity': 'confirmed', 'scene_jpeg': jpeg,
                   'target_crop_jpeg': jpeg, 'target_box': [0.1, 0.2, 0.4, 0.9]}]
        for failure in (TimeoutError('private prompt'), OSError('private key'),
                        RuntimeError('private provider body')):
            with self.subTest(failure=type(failure).__name__):
                transport = unittest.mock.Mock(side_effect=failure)
                adapter = OpenRouterContextAdapter(
                    api_key='fixture-key', model='vendor/model:free',
                    transport=transport)
                with self.assertRaises(OpenRouterContextError) as caught:
                    adapter.analyze(frames, source_sha256=source_sha256)
                self.assertEqual(caught.exception.code, 'provider_unavailable')
                rendered = repr(caught.exception)
                self.assertNotIn(str(failure), rendered)
                transport.assert_called_once()

    def test_malformed_or_unbounded_provider_responses_fail_closed(self):
        from aba_demo.openrouter_context import OpenRouterContextAdapter, OpenRouterContextError

        source_sha256 = 'a' * 64
        jpeg = b'\xff\xd8fixture\xff\xd9'
        frames = [{'time': 1.0, 'identity': 'confirmed', 'scene_jpeg': jpeg,
                   'target_crop_jpeg': jpeg, 'target_box': [0.1, 0.2, 0.4, 0.9]}]
        valid_output = {
            'activity_suggestion': 'table', 'activity_status': 'supported',
            'target_material_interaction': 'yes',
            'adult_target_interaction_visible': 'ambiguous', 'evidence_times': [1.0],
        }
        valid_envelope = {
            'model': 'vendor/model:free',
            'choices': [{'finish_reason': 'stop',
                         'message': {'role': 'assistant',
                                     'content': json.dumps(valid_output)}}],
            'openrouter_metadata': {
                'requested': 'vendor/model:free',
                'endpoints': {'available': [{
                    'provider': 'fixture-provider', 'model': 'vendor/model:free',
                    'selected': True,
                }]},
            },
        }
        invalid_envelopes = [
            {},
            {**valid_envelope, 'choices': []},
            {**valid_envelope, 'choices': valid_envelope['choices'] * 2},
            {**valid_envelope, 'choices': [{'finish_reason': 'length',
                                            'message': {'content': json.dumps(valid_output)}}]},
            {**valid_envelope, 'choices': [{'finish_reason': 'stop', 'message': {
                'role': 'assistant', 'content': json.dumps(valid_output),
                'tool_calls': [{'name': 'x'}]}}]},
            {**valid_envelope, 'model': 'openrouter/free'},
            {**valid_envelope, 'choices': [{'finish_reason': 'stop',
                                            'message': {'role': 'assistant',
                                                        'content': 'not-json private body'}}]},
        ]
        replies = [(200, json.dumps(value).encode('utf-8')) for value in invalid_envelopes]
        replies.extend([('200', json.dumps(valid_envelope).encode('utf-8')),
                        (True, json.dumps(valid_envelope).encode('utf-8')),
                        (200, b'\xff'), (200, b'x' * 65537), (200, {})])

        for reply in replies:
            with self.subTest(reply_type=type(reply[1]).__name__):
                transport = unittest.mock.Mock(return_value=reply)
                adapter = OpenRouterContextAdapter(
                    api_key='fixture-key', model='vendor/model:free',
                    transport=transport)
                with self.assertRaises(OpenRouterContextError) as caught:
                    adapter.analyze(frames, source_sha256=source_sha256)
                self.assertEqual(caught.exception.code, 'provider_response_invalid')
                self.assertNotIn('private body', repr(caught.exception))
                transport.assert_called_once()

    def test_default_https_transport_is_bounded_and_disables_redirects_and_proxies(self):
        import urllib.request
        from aba_demo.openrouter_context import OpenRouterContextAdapter

        source_sha256 = 'a' * 64
        jpeg = b'\xff\xd8fixture\xff\xd9'
        frames = [{'time': 1.0, 'identity': 'confirmed', 'scene_jpeg': jpeg,
                   'target_crop_jpeg': jpeg, 'target_box': [0.1, 0.2, 0.4, 0.9]}]
        model_output = {
            'activity_suggestion': 'table', 'activity_status': 'supported',
            'target_material_interaction': 'yes',
            'adult_target_interaction_visible': 'ambiguous', 'evidence_times': [1.0],
        }
        envelope = {
            'model': 'vendor/model:free',
            'choices': [{'finish_reason': 'stop',
                         'message': {'role': 'assistant',
                                     'content': json.dumps(model_output)}}],
            'openrouter_metadata': {
                'requested': 'vendor/model:free',
                'endpoints': {'available': [{
                    'provider': 'fixture-provider', 'model': 'vendor/model:free',
                    'selected': True,
                }]},
            },
        }

        with patch('urllib.request.build_opener') as build:
            response = build.return_value.open.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = json.dumps(envelope).encode('utf-8')
            result = OpenRouterContextAdapter(
                api_key='fixture-key', model='vendor/model:free',
            ).analyze(frames, source_sha256=source_sha256)

        self.assertEqual(result['observation'], model_output)
        response.read.assert_called_once_with(65537)
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://openrouter.ai/api/v1/chat/completions')
        self.assertEqual(request.method, 'POST')
        self.assertEqual(request.get_header('Authorization'), 'Bearer fixture-key')
        self.assertEqual(build.return_value.open.call_args.kwargs, {'timeout': 90.0})
        handlers = build.call_args.args
        redirect = next(handler for handler in handlers
                        if isinstance(handler, urllib.request.HTTPRedirectHandler))
        self.assertIsNone(redirect.redirect_request(
            request, None, 302, 'redirect', {}, 'https://attacker.invalid'))
        proxy = next(handler for handler in handlers
                     if isinstance(handler, urllib.request.ProxyHandler))
        self.assertEqual(proxy.proxies, {})

    def test_router_metadata_is_strictly_bound_to_request_and_selected_endpoint(self):
        from aba_demo.openrouter_context import OpenRouterContextAdapter, OpenRouterContextError

        source_sha256 = 'a' * 64
        jpeg = b'\xff\xd8fixture\xff\xd9'
        frames = [{'time': 1.0, 'identity': 'confirmed', 'scene_jpeg': jpeg,
                   'target_crop_jpeg': jpeg, 'target_box': [0.1, 0.2, 0.4, 0.9]}]
        output = {
            'activity_suggestion': 'unclear', 'activity_status': 'not_observable',
            'target_material_interaction': 'not_observable',
            'adult_target_interaction_visible': 'not_observable', 'evidence_times': [],
        }
        selected = {
            'provider': 'selected-provider', 'model': 'vendor/resolved:free',
            'selected': True,
        }
        base = {
            'model': 'vendor/resolved:free',
            'provider': 'untrusted-top-level-provider',
            'choices': [{'finish_reason': 'stop', 'message': {
                'role': 'assistant', 'content': json.dumps(output),
            }}],
            'openrouter_metadata': {
                'requested': 'vendor/model:free',
                'endpoints': {'available': [selected]},
            },
        }

        valid_transport = unittest.mock.Mock(
            return_value=(200, json.dumps(base).encode('utf-8')))
        result = OpenRouterContextAdapter(
            api_key='fixture-key', model='vendor/model:free',
            transport=valid_transport,
        ).analyze(frames, source_sha256=source_sha256)
        self.assertEqual(result['provenance']['endpoint_provider'], 'selected-provider')
        self.assertEqual(result['provenance']['resolved_model'], 'vendor/resolved:free')

        cases = []
        missing_metadata = copy.deepcopy(base)
        del missing_metadata['openrouter_metadata']
        cases.append(missing_metadata)
        for requested in (None, '', 'vendor/other:free'):
            value = copy.deepcopy(base)
            value['openrouter_metadata']['requested'] = requested
            cases.append(value)
        for available in (None, [], [{**selected, 'selected': False}],
                          [{**selected, 'selected': 'true'}],
                          [selected, {**selected, 'provider': 'second-provider'}]):
            value = copy.deepcopy(base)
            value['openrouter_metadata']['endpoints']['available'] = available
            cases.append(value)
        for provider in (None, '', 'provider\nname', 'p' * 129):
            value = copy.deepcopy(base)
            value['openrouter_metadata']['endpoints']['available'][0]['provider'] = provider
            cases.append(value)
        for model in (None, '', 'model-without-vendor', 'openrouter/free', 'vendor/model\n'):
            value = copy.deepcopy(base)
            value['openrouter_metadata']['endpoints']['available'][0]['model'] = model
            value['model'] = model
            cases.append(value)
        mismatched_top_level = copy.deepcopy(base)
        mismatched_top_level['model'] = 'vendor/other:free'
        cases.append(mismatched_top_level)

        for envelope in cases:
            with self.subTest(envelope=envelope):
                transport = unittest.mock.Mock(
                    return_value=(200, json.dumps(envelope).encode('utf-8')))
                adapter = OpenRouterContextAdapter(
                    api_key='fixture-key', model='vendor/model:free',
                    transport=transport)
                with self.assertRaises(OpenRouterContextError) as caught:
                    adapter.analyze(frames, source_sha256=source_sha256)
                self.assertEqual(caught.exception.code, 'provider_response_invalid')
                transport.assert_called_once()

    def test_success_requires_assistant_message_without_error_refusal_or_tools(self):
        from aba_demo.openrouter_context import OpenRouterContextAdapter, OpenRouterContextError

        source_sha256 = 'a' * 64
        jpeg = b'\xff\xd8fixture\xff\xd9'
        frames = [{'time': 1.0, 'identity': 'confirmed', 'scene_jpeg': jpeg,
                   'target_crop_jpeg': jpeg, 'target_box': [0.1, 0.2, 0.4, 0.9]}]
        output = {
            'activity_suggestion': 'unclear', 'activity_status': 'not_observable',
            'target_material_interaction': 'not_observable',
            'adult_target_interaction_visible': 'not_observable', 'evidence_times': [],
        }
        base = {
            'model': 'vendor/model:free', 'provider': 'fixture-provider',
            'choices': [{'finish_reason': 'stop', 'message': {
                'role': 'assistant', 'content': json.dumps(output),
            }}],
            'openrouter_metadata': {
                'requested': 'vendor/model:free',
                'endpoints': {'available': [{
                    'provider': 'fixture-provider', 'model': 'vendor/model:free',
                    'selected': True,
                }]},
            },
        }
        choices = []
        for role in (None, 'user'):
            choice = copy.deepcopy(base['choices'][0])
            if role is None:
                del choice['message']['role']
            else:
                choice['message']['role'] = role
            choices.append(choice)
        for error in (None, {'code': 500}):
            choices.append({**copy.deepcopy(base['choices'][0]), 'error': error})
        for field, value in (('refusal', None), ('refusal', 'cannot comply'),
                             ('tool_calls', []), ('tool_calls', [{'name': 'x'}])):
            choice = copy.deepcopy(base['choices'][0])
            choice['message'][field] = value
            choices.append(choice)

        for choice in choices:
            with self.subTest(choice=choice):
                envelope = {**base, 'choices': [choice]}
                transport = unittest.mock.Mock(
                    return_value=(200, json.dumps(envelope).encode('utf-8')))
                adapter = OpenRouterContextAdapter(
                    api_key='fixture-key', model='vendor/model:free',
                    transport=transport)
                with self.assertRaises(OpenRouterContextError) as caught:
                    adapter.analyze(frames, source_sha256=source_sha256)
                self.assertEqual(caught.exception.code, 'provider_response_invalid')

    def test_duplicate_json_keys_anywhere_in_envelope_fail_closed(self):
        from aba_demo.openrouter_context import OpenRouterContextAdapter, OpenRouterContextError

        source_sha256 = 'a' * 64
        jpeg = b'\xff\xd8fixture\xff\xd9'
        frames = [{'time': 1.0, 'identity': 'confirmed', 'scene_jpeg': jpeg,
                   'target_crop_jpeg': jpeg, 'target_box': [0.1, 0.2, 0.4, 0.9]}]
        output = {
            'activity_suggestion': 'unclear', 'activity_status': 'not_observable',
            'target_material_interaction': 'not_observable',
            'adult_target_interaction_visible': 'not_observable', 'evidence_times': [],
        }
        envelope = {
            'model': 'vendor/model:free', 'provider': 'fixture-provider',
            'choices': [{'finish_reason': 'stop', 'message': {
                'role': 'assistant', 'content': json.dumps(output),
            }}],
            'openrouter_metadata': {
                'requested': 'vendor/model:free',
                'endpoints': {'available': [{
                    'provider': 'fixture-provider', 'model': 'vendor/model:free',
                    'selected': True,
                }]},
            },
        }
        raw = json.dumps(envelope, separators=(',', ':'))
        duplicate_documents = [
            raw.replace('"model":"vendor/model:free"',
                        '"model":"vendor/model:free","model":"vendor/model:free"', 1),
            raw.replace('"role":"assistant"',
                        '"role":"assistant","role":"assistant"', 1),
            raw.replace('"endpoints":{', '"endpoints":{},"endpoints":{', 1),
            raw.replace('"selected":true', '"selected":false,"selected":true', 1),
        ]

        for document in duplicate_documents:
            with self.subTest(document=document):
                transport = unittest.mock.Mock(return_value=(200, document.encode('utf-8')))
                adapter = OpenRouterContextAdapter(
                    api_key='fixture-key', model='vendor/model:free',
                    transport=transport)
                with self.assertRaises(OpenRouterContextError) as caught:
                    adapter.analyze(frames, source_sha256=source_sha256)
                self.assertEqual(caught.exception.code, 'provider_response_invalid')

    def test_default_transport_maps_http_error_status_without_reading_body(self):
        from aba_demo.openrouter_context import _http_transport

        private_body = unittest.mock.Mock()
        failure = urllib.error.HTTPError(
            'https://openrouter.ai/api/v1/chat/completions', 404,
            'private provider message', {}, private_body)
        with patch('urllib.request.build_opener') as build:
            build.return_value.open.side_effect = failure
            result = _http_transport(
                'https://openrouter.ai/api/v1/chat/completions',
                headers={'Authorization': 'Bearer fixture-key'}, body=b'{}',
                timeout=90.0, max_response_bytes=65536)

        self.assertEqual(result, (404, b''))
        private_body.read.assert_not_called()
        private_body.close.assert_called_once()
        build.return_value.open.assert_called_once()


if __name__ == '__main__':
    unittest.main()
