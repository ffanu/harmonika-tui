"""Native translation MCP fixtures: no real credentials, provider or HTTP calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime/tools"))
import translation_mcp as client
from tool_firewall import protect


def translation(text="Hello", **overrides):
    return {"ok": True, "text": text, "source": "id", "target": "en", "detected_language": "id",
            "needs_review": False, "quality": "model_self_estimated", "translated": True,
            "chunks": 1, **overrides}


def languages():
    return {"ok": True, "languages": [
        {"code": "jv", "name": "Javanese", "aliases": ["jawa"], "regional": True,
         "needs_review": True, "status": "registered", "quality": "model_self_estimated"}],
        "registration_is_not_verified_quality": True, "regional_review_required": True,
        "automatic_training": False}


class NativeTranslationTests(unittest.TestCase):
    def test_four_native_tools_have_strict_schemas_and_handlers(self):
        self.assertEqual(set(client.HANDLERS), {"translate_text", "detect_language", "translation_languages", "translate_batch"})
        self.assertEqual({tool["name"] for tool in client.TOOLS}, set(client.HANDLERS))
        for tool in client.TOOLS:
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
            self.assertTrue(tool["annotations"]["readOnlyHint"])

    def test_thin_translation_uses_shared_public_backend_and_text_field(self):
        args = {"text": "Selamat pagi {name} 42", "source": "Indonesia", "target": "jawa krama", "register": "krama", "tone": "formal", "glossary": {"morning": "enjing"}}
        expected = translation("Sugeng enjing {name} 42", target="jv", needs_review=True)
        with patch.object(client, "_request", return_value=expected) as request:
            result = client.translate_text(args)
        request.assert_called_once_with("POST", "/translate", args)
        self.assertEqual(result, expected)
        self.assertIn("text", result)
        self.assertNotIn("translated_text", result)

    def test_detection_and_registered_language_routes(self):
        detected = {"ok": True, "detected_language": "jv", "needs_review": True,
                    "quality": "model_self_estimated", "sampled_characters": 12, "complete_input_sampled": True}
        with patch.object(client, "_request", return_value=detected) as request:
            self.assertEqual(client.detect_language({"text": "Sugeng enjing"}), detected)
            request.assert_called_once_with("POST", "/detect", {"text": "Sugeng enjing"})
        with patch.object(client, "_request", return_value=languages()) as request:
            result = client.translation_languages({})
            request.assert_called_once_with("GET", "/languages", None)
            self.assertEqual(result, languages())

    def test_batch_is_single_bounded_backend_call(self):
        args = {"items": [{"text": "halo", "target": "en"}, {"text": "pagi", "target": "en"}]}
        output = {"ok": True, "items": [translation(), translation("Morning")], "quality": "model_self_estimated"}
        with patch.object(client, "_request", return_value=output) as request:
            self.assertEqual(client.translate_batch(args), output)
            request.assert_called_once_with("POST", "/batch", args)

    def test_bad_arguments_are_rejected_before_network_effects(self):
        cases = [
            (client.translate_text, None), (client.translate_text, []),
            (client.translate_text, {"text": "hello", "target": "en", "endpoint": "https://attacker.invalid"}),
            (client.translate_text, {"text": "hello", "target": "auto"}),
            (client.translate_text, {"text": "hello", "target": ["en"]}),
            (client.translate_text, {"text": "hello", "target": "en\r\nprivate"}),
            (client.translate_text, {"text": "hello", "target": "en", "tone": "root"}),
            (client.translate_text, {"text": "hello", "target": "en", "register": "root"}),
            (client.translate_text, {"text": "hello", "target": "en", "glossary": {"term": 8}}),
            (client.translate_text, {"text": " " * 20, "target": "en"}),
            (client.detect_language, {"text": "hi", "actor": "root"}),
            (client.translation_languages, {"ignored": True}),
            (client.translate_batch, {"items": []}),
            (client.translate_batch, {"items": [{"text": "x", "target": "en"}] * 17}),
            (client.translate_batch, {"items": [{"text": "x", "target": "en"}, {"text": "x", "target": "en", "model": "other"}]}),
        ]
        with patch.object(client, "_request") as request:
            for handler, args in cases:
                with self.subTest(args=args):
                    self.assertFalse(handler(args)["ok"])
            request.assert_not_called()

    def test_unicode_and_total_payload_bounds_fail_explicitly(self):
        with patch.object(client, "_request") as request:
            result = client.translate_text({"text": "x" * 40001, "target": "en"})
            self.assertEqual(result["error"], "text_too_large")
            result = client.translate_text({"text": "\ud800", "target": "en"})
            self.assertEqual(result["error"], "invalid_input")
            result = client.translate_text({"text": "hello", "target": "en", "extra": "x" * client.MAX_BYTES})
            self.assertEqual(result["error"], "input_too_large")
            result = client.translate_batch({"items": [{"text": "界" * 21000, "target": "en"}] * 2})
            self.assertEqual(result["error"], "batch_too_large")
            request.assert_not_called()

    def test_credential_literals_and_secret_paths_are_rejected_without_network(self):
        with patch.object(client, "_request") as request:
            for text in ("password=PRIVATE_FIXTURE", "Bearer PRIVATE_FIXTURE_KEY"):
                result = client.translate_text({"text": text, "target": "en"})
                self.assertEqual(result["error"], "tool_secret_reference_required")
                self.assertNotIn("PRIVATE_FIXTURE", repr(result))
            request.assert_not_called()

    def test_raw_backend_errors_are_never_exposed(self):
        for response, expected in (({"ok": False, "error": "PRIVATE_CUSTOMER_TEXT"}, "gateway_rejected"),
                                   ({"ok": False, "error": {"code": "translation_timeout", "detail": "PRIVATE_CUSTOMER_TEXT"}}, "translation_timeout")):
            with patch.object(client, "_request", return_value=response):
                result = client.translate_text({"text": "halo", "target": "en"})
            self.assertEqual(result, {"ok": False, "error": expected})
        with patch.object(client, "_request", side_effect=RuntimeError("PRIVATE_CUSTOMER_TEXT")):
            self.assertEqual(client.translate_text({"text": "halo", "target": "en"})["error"], "translation_client_failed")

    def test_malformed_output_cannot_become_fake_translation(self):
        cases = [
            {"ok": True, "translated_text": "invented"},
            translation(""), translation(chunks=True), translation(quality="verified"),
            translation(needs_review="yes"), translation(detected_language=[]),
        ]
        for response in cases:
            with patch.object(client, "_request", return_value=response):
                self.assertEqual(client.translate_text({"text": "halo", "target": "en"})["error"], "gateway_invalid_response")

    def test_sensitive_output_fails_instead_of_silently_redacting_translation(self):
        with patch.object(client, "_request", return_value=translation("password=PRIVATE_FIXTURE")):
            self.assertEqual(client.translate_text({"text": "halo", "target": "en"}), {"ok": False, "error": "translation_sensitive_response"})

    def test_additional_backend_fields_are_never_returned(self):
        response = {**translation("Google OpenAI Codex"), "debug": "PRIVATE_BACKEND_DETAILS"}
        with patch.object(client, "_request", return_value=response):
            result = client.translate_text({"text": "Google OpenAI Codex", "target": "en"})
        self.assertEqual(result["text"], "Google OpenAI Codex")
        self.assertNotIn("debug", result)

    def test_no_content_or_secret_logs_even_on_failure(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with patch.object(client, "_request", side_effect=RuntimeError("PRIVATE_FIXTURE")):
                client.translate_text({"text": "PRIVATE_CUSTOMER", "target": "en"})
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_aggregator_firewall_wrapper_preserves_handler_contract(self):
        wrapped = protect("translate_text", client.translate_text)
        with patch.object(client, "_request", return_value=translation()):
            self.assertEqual(wrapped({"text": "halo", "target": "en"})["text"], "Hello")
        self.assertTrue(wrapped._harmonika_firewall)


class FakeResponse:
    def __init__(self, value, status=200):
        self.raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, count):
        return self.raw[:count]


class NativeTranslationTransportTests(unittest.TestCase):
    KEY = "hai_" + "x" * 32

    def test_fixed_public_url_device_auth_and_no_proxy_or_retry(self):
        class Opener:
            def open(self, req, timeout):
                self.req, self.timeout = req, timeout
                return FakeResponse(translation())
        opener = Opener()
        with patch.dict(os.environ, {"HARMONIKA_API_KEY": self.KEY, "HARMONIKA_DEVICE_ID": "dev_" + "a" * 32}, clear=True):
            with patch.object(client.request, "build_opener", return_value=opener) as build:
                result = client.translate_text({"text": "halo", "target": "en"})
        self.assertTrue(result["ok"])
        self.assertEqual(opener.req.full_url, client.BASE + "/translate")
        self.assertEqual(opener.req.get_method(), "POST")
        self.assertEqual(opener.req.get_header("Authorization"), "Bearer " + self.KEY)
        self.assertEqual(opener.req.get_header("X-harmonika-device-id"), "dev_" + "a" * 32)
        self.assertNotIn(self.KEY, opener.req.data.decode())
        self.assertEqual(opener.timeout, 135)
        self.assertEqual(build.call_count, 1)
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertIsInstance(build.call_args.args[1], client._NoRedirect)

    def test_env_key_precedes_private_config_file_and_never_reads_reference_commands(self):
        with patch.dict(os.environ, {"HARMONIKA_API_KEY": self.KEY}, clear=True):
            with patch.object(client, "_private_file", side_effect=AssertionError("must not read file")):
                self.assertEqual(client._key(), self.KEY)
        with patch.dict(os.environ, {"HARMONIKA_API_KEY": "$(cat PRIVATE_FILE)"}, clear=True):
            with self.assertRaises(client.ClientError):
                client._key()

    def test_private_key_file_permissions_symlinks_and_size_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "harmonika-api-key"
            path.write_text(self.KEY)
            path.chmod(0o600)
            with patch.dict(os.environ, {"HARMONIKA_GLOBAL_CONFIG": directory}, clear=True):
                self.assertEqual(client._key(), self.KEY)
                path.chmod(0o644)
                with self.assertRaises(client.ClientError) as caught:
                    client._key()
                self.assertEqual(str(caught.exception), "unsafe_key_file")
                path.chmod(0o600)
                target = Path(directory) / "real-key"
                path.rename(target)
                path.symlink_to(target)
                with self.assertRaises(client.ClientError):
                    client._key()
                path.unlink()
                path.write_text("x" * 257)
                path.chmod(0o600)
                with self.assertRaises(client.ClientError):
                    client._key()

    def test_key_missing_or_invalid_never_reaches_network(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"HARMONIKA_GLOBAL_CONFIG": directory}, clear=True):
                with patch.object(client.request, "build_opener") as opener:
                    result = client.translation_languages({})
                    self.assertEqual(result["error"], "translation_key_not_configured")
                    opener.assert_not_called()

    def test_transport_redirect_timeout_and_raw_exceptions_are_fixed(self):
        for failure, expected in ((client.ClientError("gateway_redirect_denied"), "gateway_redirect_denied"),
                                  (socket.timeout("PRIVATE_FIXTURE"), "gateway_timeout"),
                                  (RuntimeError("PRIVATE_FIXTURE"), "gateway_transport")):
            with patch.object(client, "_key", return_value=self.KEY), patch.object(client, "_device_headers", return_value={}):
                with patch.object(client.request, "build_opener") as build:
                    build.return_value.open.side_effect = failure
                    self.assertEqual(client.translation_languages({}), {"ok": False, "error": expected})
                    self.assertEqual(build.return_value.open.call_count, 1)
        with self.assertRaises(client.ClientError):
            client._NoRedirect().redirect_request(None, None, 302, "", {}, "https://attacker.invalid")

    def test_http_error_codes_and_upstream_details_are_sanitized(self):
        for status, payload, expected in ((401, {"error": "PRIVATE_FIXTURE"}, "gateway_access_denied"),
                                          (429, {"error": "PRIVATE_FIXTURE"}, "translation_busy"),
                                          (502, {"error": "PRIVATE_FIXTURE"}, "gateway_unavailable"),
                                          (422, {"error": "unsupported_language"}, "unsupported_language")):
            failure = HTTPError(client.BASE, status, "PRIVATE_FIXTURE", {}, io.BytesIO(json.dumps(payload).encode()))
            with patch.object(client, "_key", return_value=self.KEY), patch.object(client, "_device_headers", return_value={}):
                with patch.object(client.request, "build_opener") as build:
                    build.return_value.open.side_effect = failure
                    self.assertEqual(client.translation_languages({}), {"ok": False, "error": expected})

    def test_oversized_duplicate_and_malformed_http_results_fail(self):
        for raw, expected in ((b"x" * (client.MAX_BYTES + 1), "gateway_output_limit"),
                              (b'{"ok":true,"ok":false}', "gateway_invalid_response"),
                              (b"PRIVATE_RESPONSE", "gateway_invalid_response")):
            with patch.object(client, "_key", return_value=self.KEY), patch.object(client, "_device_headers", return_value={}):
                with patch.object(client.request, "build_opener") as build:
                    build.return_value.open.return_value = FakeResponse(raw)
                    self.assertEqual(client.translation_languages({}), {"ok": False, "error": expected})

    def test_arbitrary_endpoint_and_methods_are_not_callable(self):
        with patch.object(client, "_key") as key:
            for method, path in (("GET", "https://attacker.invalid"), ("GET", "/translate"), ("POST", "/invoke")):
                with self.assertRaises(client.ClientError):
                    client._request(method, path)
            key.assert_not_called()


if __name__ == "__main__":
    unittest.main()
