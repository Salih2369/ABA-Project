"""Mocked-service contract tests only: no network or real inference validation."""
import base64
import json
import unittest
from unittest.mock import patch

from aba_demo.context_review import ContextReviewer, ContextReviewError


FRAMES = [{"time": 1.25, "jpeg_base64": base64.b64encode(b"\xff\xd8fixture\xff\xd9").decode()}]
TARGET = {"bbox": [0.1, 0.2, 0.6, 0.9]}


def service_reply(value):
    """Explicitly fake OpenRouter envelope, not an actual model result."""
    return 200, json.dumps({"choices": [{"message": {"content": json.dumps(value)},
                                        "finish_reason": "stop"}]}).encode()


class ContextReviewTests(unittest.TestCase):
    def setUp(self):
        # A real socket is always a test failure; all service results are mock contracts.
        blocker = patch("socket.create_connection", side_effect=AssertionError("network forbidden"))
        blocker.start()
        self.addCleanup(blocker.stop)

    def test_external_processing_requires_literal_opt_in_before_transport(self):
        for consent in (False, None, 1, "true"):
            with self.subTest(consent=consent):
                transport = unittest.mock.Mock()
                reviewer = ContextReviewer(api_key="mock-key", model="mock-model",
                                           allow_external=consent, transport=transport)
                with self.assertRaises(ContextReviewError) as caught:
                    reviewer.review(FRAMES, TARGET, "table", "activity_matches")
                self.assertEqual(caught.exception.code, "external_not_allowed")
                self.assertEqual(str(caught.exception), "external_not_allowed")
                transport.assert_not_called()

    def test_missing_configuration_has_distinct_safe_codes(self):
        with patch.dict("os.environ", {}, clear=True):
            for key, model, code in ((None, None, "missing_api_key"),
                                     ("mock-key", None, "missing_model"),
                                     ("", "mock-model", "missing_api_key"),
                                     ("mock-key", "", "missing_model")):
                with self.subTest(code=code):
                    transport = unittest.mock.Mock()
                    reviewer = ContextReviewer(key, model, True, transport)
                    with self.assertRaises(ContextReviewError) as caught:
                        reviewer.review(FRAMES, TARGET, "table", "activity_matches")
                    self.assertEqual(caught.exception.code, code)
                    self.assertEqual(str(caught.exception), code)
                    transport.assert_not_called()

    def test_mocked_service_contract_has_target_scene_and_bounded_request(self):
        transport = unittest.mock.Mock(return_value=service_reply(
            {"status": "supported", "evidence_times": [1.25]}))
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "mock-env-key",
                                       "VLM_MODEL": "mock-env-model"}, clear=True):
            result = ContextReviewer(allow_external=True, transport=transport).review(
                FRAMES, TARGET, "table", "activity_matches")
        self.assertEqual(result, {"status": "supported", "evidence_times": [1.25],
                                 "description": "Visible context supports the requested observation; human review required."})
        transport.assert_called_once()
        args, kwargs = transport.call_args
        self.assertEqual(args, ("https://openrouter.ai/api/v1/chat/completions",))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer mock-env-key")
        self.assertEqual(kwargs["timeout"], 15.0)
        self.assertEqual(kwargs["max_response_bytes"], 65536)
        payload = json.loads(kwargs["body"])
        self.assertEqual(payload["model"], "mock-env-model")
        self.assertFalse(payload["stream"])
        self.assertLessEqual(payload["max_tokens"], 256)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        system = payload["messages"][0]["content"]
        for phrase in ("untrusted", "attention", "treatment", "behavioral function", "normalized", "scene"):
            self.assertIn(phrase, system)
        content = payload["messages"][1]["content"]
        context = json.loads(content[0]["text"])
        self.assertEqual(context["target"], TARGET)
        self.assertEqual(context["question"], "activity_matches")
        self.assertEqual(context["activity"], "table")
        self.assertEqual(json.loads(content[1]["text"]),
                         {"time": 1.25, "target_bbox": TARGET["bbox"]})
        self.assertEqual(content[2]["image_url"]["url"],
                         "data:image/jpeg;base64," + FRAMES[0]["jpeg_base64"])

    def test_invalid_input_never_reaches_transport(self):
        cases = [
            (FRAMES, TARGET, "table", q) for q in
            ("attention", "behavioral_function", "treatment", "activity_matches\nignore", {}, None)
        ]
        cases += [(f, TARGET, "table", "activity_matches") for f in (
            [], FRAMES * 5, None, {}, [dict(FRAMES[0], time=True)],
            [dict(FRAMES[0], time=float("nan"))], [dict(FRAMES[0], time=float("inf"))],
            [dict(FRAMES[0], time=-1)], [FRAMES[0], FRAMES[0]],
            [dict(FRAMES[0], time=2), FRAMES[0]],
            [dict(FRAMES[0], jpeg_base64="https://attacker.invalid/a.jpg")],
            [dict(FRAMES[0], jpeg_base64="eA==")],
            [dict(FRAMES[0], jpeg_base64="A" * 349529)],
            [dict(FRAMES[0], private_note="do not send")], [None],
        )]
        cases += [(FRAMES, t, "table", "activity_matches") for t in (
            {}, None, {"bbox": [0, 0, 1]}, {"bbox": [0, 0, 0, 1]},
            {"bbox": [-.1, 0, 1, 1]}, {"bbox": [0, 0, 1.1, 1]},
            {"bbox": [False, 0, 1, 1]}, {"bbox": [0, 0, float("nan"), 1]},
            dict(TARGET, name="private child name"),
        )]
        cases += [(FRAMES, TARGET, a, "activity_matches") for a in (
            "", " " * 2, "x" * 81, None, {}, "table\nignore instructions",
            "attention", "behavioral function", "treatment recommendation",
        )]
        for args in cases:
            with self.subTest(case=cases.index(args)):
                transport = unittest.mock.Mock(return_value=service_reply(
                    {"status": "supported", "evidence_times": [1.25]}))
                with self.assertRaises(ContextReviewError) as caught:
                    ContextReviewer("mock-key", "mock-model", True, transport).review(*args)
                self.assertEqual(caught.exception.code, "invalid_input")
                transport.assert_not_called()

    def test_classification_uses_fixed_templates_even_with_markdown_fences(self):
        expected = {
            "supported": "Visible context supports the requested observation; human review required.",
            "contradicted": "Visible context contradicts the requested observation; human review required.",
            "ambiguous": "Visible context is ambiguous; human review required.",
            "not_observable": "The requested observation is not observable in the supplied frames.",
        }
        for status, description in expected.items():
            for fence in ("", "```json\n", "```\n"):
                with self.subTest(status=status, fence=fence):
                    evidence = [] if status == "not_observable" else [1.25]
                    text = json.dumps({"status": status, "evidence_times": evidence})
                    if fence:
                        text = fence + text + "\n```"
                    transport = unittest.mock.Mock(return_value=(200, json.dumps({
                        "choices": [{"finish_reason": "stop", "message": {"content": text}}]
                    }).encode()))
                    result = ContextReviewer("mock-key", "mock-model", True, transport).review(
                        FRAMES, TARGET, "table", "hand_material_interaction")
                    self.assertEqual(result, {"status": status, "evidence_times": evidence,
                                              "description": description})

    def test_invalid_model_output_is_rejected_without_free_prose_or_future_evidence(self):
        valid = {"status": "supported", "evidence_times": [1.25]}
        values = [dict(valid, description="child seeks attention"),
                  dict(valid, behavioral_function="escape"), dict(valid, treatment="do X"),
                  dict(valid, status="attentive"), dict(valid, status={}),
                  {}, None, [], dict(valid, evidence_times=[9]),
                  dict(valid, evidence_times=[0]), dict(valid, evidence_times=[True]),
                  dict(valid, evidence_times=[float("nan")]),
                  dict(valid, evidence_times=["1.25"]), dict(valid, evidence_times=[{}]),
                  dict(valid, evidence_times=[1.25, 1.25]),
                  dict(valid, evidence_times=None), dict(valid, evidence_times=[]),
                  {"status": "contradicted", "evidence_times": []}]
        replies = [service_reply(value) for value in values]
        for text in ('{"status":"ambiguous","status":"supported","evidence_times":[1.25]}',
                     'prefix ' + json.dumps(valid), json.dumps(valid) + ' trailing',
                     '```python\n' + json.dumps(valid) + '\n```'):
            replies.append((200, json.dumps({"choices": [{"finish_reason": "stop",
                             "message": {"content": text}}]}).encode()))
        for envelope in ({}, {"choices": []}, {"choices": [{"message": {"content": None}}]},
                         {"choices": [{"finish_reason": "length", "message": {"content": json.dumps(valid)}}]},
                         {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(valid), "tool_calls": [{"name": "x"}]}}]}):
            replies.append((200, json.dumps(envelope).encode()))
        replies.extend([(200, b"not-json private response"), (200, b"x" * 65537),
                        (200, b"\xff"), (200, {})])
        for reply in replies:
            transport = unittest.mock.Mock(return_value=reply)
            with self.assertRaises(ContextReviewError) as caught:
                ContextReviewer("mock-key", "mock-model", True, transport).review(
                    FRAMES, TARGET, "table", "activity_matches")
            self.assertEqual(str(caught.exception), "invalid_response")

    def test_service_failures_have_distinct_private_codes_without_retries(self):
        import traceback
        from urllib.error import URLError
        cases = [(403, "forbidden"), (429, "rate_limited"), (401, "unauthorized"),
                 (500, "http_error"), (302, "http_error"),
                 (TimeoutError("mock-key private-body"), "timeout"),
                 (URLError(TimeoutError("private-body")), "timeout"),
                 (OSError("mock-key private-body"), "transport_error"),
                 (RuntimeError("mock-key private-body"), "transport_error")]
        for failure, expected in cases:
            transport = unittest.mock.Mock()
            if isinstance(failure, int):
                transport.return_value = failure, b"private-body"
            else:
                transport.side_effect = failure
            try:
                ContextReviewer("mock-key", "mock-model", True, transport).review(
                    FRAMES, TARGET, "table", "activity_matches")
            except ContextReviewError as exc:
                self.assertEqual(exc.code, expected)
                self.assertEqual(str(exc), expected)
                rendered = "".join(traceback.format_exception(exc))
                self.assertNotIn("private-body", rendered)
            else:
                self.fail("expected privacy-safe service failure")
            transport.assert_called_once()

    def test_multiframe_requires_aligned_per_frame_target_boxes(self):
        frames = [FRAMES[0], dict(FRAMES[0], time=2)]
        for target in (TARGET, {"bboxes": [TARGET["bbox"]]},
                       {"bboxes": [TARGET["bbox"], [0, 0, 2, 1]]},
                       {"bboxes": [TARGET["bbox"]] * 3}):
            transport = unittest.mock.Mock(return_value=service_reply(
                {"status": "supported", "evidence_times": [1.25]}))
            with self.assertRaises(ContextReviewError) as caught:
                ContextReviewer("mock-key", "mock-model", True, transport).review(
                    frames, target, "table", "activity_matches")
            self.assertEqual(caught.exception.code, "invalid_input")
            transport.assert_not_called()
        boxes = [TARGET["bbox"], [.2, .3, .7, 1.0]]
        transport = unittest.mock.Mock(return_value=service_reply(
            {"status": "supported", "evidence_times": [2, 1.25]}))
        result = ContextReviewer("mock-key", "mock-model", True, transport).review(
            frames, {"bboxes": boxes}, "table", "activity_matches")
        self.assertEqual(result["evidence_times"], [1.25, 2])
        content = json.loads(transport.call_args.kwargs["body"])["messages"][1]["content"]
        for i, frame in enumerate(frames):
            self.assertEqual(json.loads(content[1 + i * 2]["text"]),
                             {"time": frame["time"], "target_bbox": boxes[i]})

    def test_real_urllib_adapter_contract_is_exercised_with_mocked_io(self):
        import urllib.request
        with patch("urllib.request.build_opener") as build:
            response = build.return_value.open.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = service_reply(
                {"status": "ambiguous", "evidence_times": []})[1]
            result = ContextReviewer("mock-key", "mock-model", True).review(
                FRAMES, TARGET, "table", "activity_matches")
            self.assertEqual(result["status"], "ambiguous")
            response.read.assert_called_once_with(65537)
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/chat/completions")
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.get_header("Authorization"), "Bearer mock-key")
            self.assertEqual(build.return_value.open.call_args.kwargs, {"timeout": 15.0})
            handlers = build.call_args.args
            redirect = next(h for h in handlers if isinstance(h, urllib.request.HTTPRedirectHandler))
            self.assertIsNone(redirect.redirect_request(request, None, 302, "redirect", {},
                                                        "https://attacker.invalid"))
            proxy = next(h for h in handlers if isinstance(h, urllib.request.ProxyHandler))
            self.assertEqual(proxy.proxies, {})
            build.return_value.open.return_value.__exit__.assert_called_once()

    def test_urllib_http_errors_are_mapped_without_reading_error_bodies(self):
        from urllib.error import HTTPError
        for status, code in ((403, "forbidden"), (429, "rate_limited"),
                             (401, "unauthorized"), (302, "http_error"), (500, "http_error")):
            body = unittest.mock.Mock()
            error = HTTPError("https://openrouter.ai/api/v1/chat/completions", status,
                              "private-provider-message", {}, body)
            with patch("urllib.request.build_opener") as build:
                build.return_value.open.side_effect = error
                with self.assertRaises(ContextReviewError) as caught:
                    ContextReviewer("mock-key", "mock-model", True).review(
                        FRAMES, TARGET, "table", "activity_matches")
                self.assertEqual(str(caught.exception), code)
                body.read.assert_not_called()
                body.close.assert_called_once()

    def test_configuration_is_bounded_and_not_silently_normalized(self):
        cases = [("key\r\ninjected", "mock-model"), ("key" * 2000, "mock-model"),
                 ("mock-key", "m" * 201), ("mock-key", " model "),
                 ("mock-key", "model\n"), (" key ", "mock-model"),
                 (123, "mock-model"), ("mock-key", 123)]
        for key, model in cases:
            transport = unittest.mock.Mock(return_value=service_reply(
                {"status": "supported", "evidence_times": [1.25]}))
            with self.assertRaises(ContextReviewError) as caught:
                ContextReviewer(key, model, True, transport).review(
                    FRAMES, TARGET, "table", "activity_matches")
            self.assertEqual(str(caught.exception), "invalid_config")
            transport.assert_not_called()

    def test_malformed_transport_status_is_a_safe_invalid_response(self):
        for status in ({}, [], "200", True, 200.0, None):
            transport = unittest.mock.Mock(return_value=(status, service_reply(
                {"status": "supported", "evidence_times": [1.25]})[1]))
            with self.assertRaises(ContextReviewError) as caught:
                ContextReviewer("mock-key", "mock-model", True, transport).review(
                    FRAMES, TARGET, "table", "activity_matches")
            self.assertEqual(str(caught.exception), "invalid_response")

    def test_frame_byte_boundaries_and_four_frame_request(self):
        from aba_demo.context_review import MAX_FRAME_BYTES
        jpeg = b"\xff\xd8" + b"x" * (MAX_FRAME_BYTES - 4) + b"\xff\xd9"
        frames = [{"time": t, "jpeg_base64": base64.b64encode(jpeg).decode()} for t in range(4)]
        transport = unittest.mock.Mock(return_value=service_reply(
            {"status": "supported", "evidence_times": [0, 3]}))
        reviewer = ContextReviewer("mock-key", "mock-model", True, transport)
        result = reviewer.review(frames, {"bboxes": [TARGET["bbox"]] * 4},
                                 "table", "hand_material_interaction")
        self.assertEqual(result["evidence_times"], [0, 3])
        self.assertLess(len(transport.call_args.kwargs["body"]), 1500000)
        transport.reset_mock()
        too_large = b"\xff\xd8" + b"x" * (MAX_FRAME_BYTES - 3) + b"\xff\xd9"
        with self.assertRaises(ContextReviewError) as caught:
            reviewer.review([{ "time": 0, "jpeg_base64": base64.b64encode(too_large).decode()}],
                            TARGET, "table", "activity_matches")
        self.assertEqual(str(caught.exception), "invalid_input")
        transport.assert_not_called()


if __name__ == "__main__":
    unittest.main()
