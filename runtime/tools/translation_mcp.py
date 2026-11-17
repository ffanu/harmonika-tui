"""Native MCP translation client. Fixed public gateway, no local text inference.

Install beside tool_firewall.py; extend the aggregator's TOOLS/HANDLERS before
its final firewall wrapping. Importing this module reads no credentials and
performs no network requests. Keys are read privately only when a tool runs.
"""
import json
import os
from pathlib import Path
import re
import socket
import stat
from urllib import error, request

from tool_firewall import scrub, validate_request

BASE = "https://chat.harmonika.id/api/tools/translation"
MAX_BYTES = 262144
MAX_TEXT = 40000
TIMEOUT = 135
TONES = ("neutral", "formal", "informal")
REGISTERS = ("neutral", "ngoko", "krama", "krama_inggil", "loma", "lemes", "andap", "alus")
SAFE_ERRORS = frozenset((
    "invalid_input", "invalid_json", "invalid_encoding", "json_required", "text_required",
    "invalid_language", "unsupported_language", "invalid_tone", "invalid_register",
    "register_language_mismatch", "invalid_glossary", "glossary_too_large", "invalid_batch",
    "batch_too_large", "text_too_large", "input_too_large", "too_many_chunks", "unsplittable_text",
    "credential_literal_denied", "tool_secret_reference_required", "invalid_tool_arguments",
    "tool_payload_complexity", "invalid_tool_payload", "translation_busy", "translation_timeout",
    "translation_unavailable", "translation_not_configured", "translation_output_limit",
    "inference_unavailable", "inference_output_limit", "invalid_inference_response",
    "invalid_detected_language", "empty_translation", "placeholder_mismatch", "url_mismatch",
    "number_mismatch", "name_mismatch", "gateway_access_denied", "gateway_unavailable",
    "gateway_transport", "gateway_timeout", "gateway_redirect_denied", "gateway_rejected",
    "gateway_output_limit", "gateway_invalid_response", "translation_sensitive_response",
    "translation_key_not_configured", "unsafe_key_file", "invalid_key_file",
    "translation_client_failed",
))


class ClientError(ValueError):
    pass


def require(ok, code="invalid_input"):
    if not ok:
        raise ClientError(code)


def _encode(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ClientError("invalid_input") from None


def _object(value, required=(), optional=()):
    require(type(value) is dict and set(required) <= set(value) <= set(required) | set(optional))


def _text(value):
    require(type(value) is str and bool(value.strip()), "text_required")
    require(len(value) <= MAX_TEXT, "text_too_large")
    return value


def _language(value, auto=False):
    require(type(value) is str and 0 < len(value.strip()) <= 100 and
            not any(ord(char) < 32 or ord(char) == 127 for char in value), "invalid_language")
    require(auto or value.strip().casefold() != "auto", "invalid_language")
    return value


def _translation(value):
    _object(value, ("text", "target"), ("source", "tone", "register", "glossary"))
    _text(value["text"])
    _language(value["target"])
    if "source" in value:
        _language(value["source"], auto=True)
    require("tone" not in value or type(value["tone"]) is str and value["tone"] in TONES, "invalid_tone")
    require("register" not in value or type(value["register"]) is str and value["register"] in REGISTERS, "invalid_register")
    if "glossary" in value:
        glossary = value["glossary"]
        require(type(glossary) is dict and len(glossary) <= 64, "invalid_glossary")
        for source, target in glossary.items():
            require(type(source) is str and type(target) is str and 0 < len(source.strip()) <= 160 and
                    0 < len(target.strip()) <= 160, "invalid_glossary")
        require(len(_encode(glossary)) <= 16384, "glossary_too_large")
    return dict(value)


def _validate(name, args):
    require(type(args) is dict)
    require(len(_encode(args)) <= MAX_BYTES, "input_too_large")
    if name == "translate_text":
        payload = _translation(args)
    elif name == "detect_language":
        _object(args, ("text",))
        payload = {"text": _text(args["text"])}
    elif name == "translation_languages":
        _object(args)
        payload = None
    elif name == "translate_batch":
        _object(args, ("items",))
        require(type(args["items"]) is list and 1 <= len(args["items"]) <= 16, "invalid_batch")
        items = [_translation(item) for item in args["items"]]
        require(sum(len(item["text"]) for item in items) <= MAX_TEXT, "batch_too_large")
        payload = {"items": items}
    else:
        raise ClientError("invalid_tool_arguments")
    # Enforce the same guard even when called without the aggregator wrapper.
    validate_request(name, args)
    return payload


def _private_file(path, max_bytes):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            require(stat.S_ISREG(info.st_mode) and
                    (os.name == "nt" or info.st_uid == os.getuid() and not info.st_mode & 0o077), "unsafe_key_file")
            raw = handle.read(max_bytes + 1)
        require(len(raw) <= max_bytes, "invalid_key_file")
        return raw.decode("utf-8").strip()
    except ClientError:
        raise
    except FileNotFoundError:
        raise ClientError("translation_key_not_configured") from None
    except (OSError, UnicodeError):
        raise ClientError("unsafe_key_file") from None


def _config_directory():
    return Path(os.environ.get("HARMONIKA_GLOBAL_CONFIG") or Path.home() / ".config/harmonika")


def _key():
    value = os.environ.get("HARMONIKA_API_KEY")
    if value is None or not value.strip():
        value = _private_file(_config_directory() / "harmonika-api-key", 256)
    value = value.strip()
    require(re.fullmatch(r"hai_[A-Za-z0-9_]{16,240}", value) is not None, "invalid_key_file")
    return value


def _device_headers():
    device = os.environ.get("HARMONIKA_DEVICE_ID", "")
    if not device:
        try:
            device = _private_file(_config_directory() / "device-id", 128)
        except ClientError:
            device = ""
    return {"X-Harmonika-Device-ID": device} if re.fullmatch(r"dev_[a-f0-9]{32}", device) else {}


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ClientError("gateway_redirect_denied")


def _decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "gateway_invalid_response")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ClientError("gateway_invalid_response")))
    except (ValueError, UnicodeError, RecursionError):
        raise ClientError("gateway_invalid_response") from None


def _error_code(value, fallback):
    error_value = value.get("error") if type(value) is dict else None
    code = error_value.get("code") if type(error_value) is dict else error_value
    return code if type(code) is str and code in SAFE_ERRORS else fallback


def _request(method, path, body=None):
    require((method, path) in (("GET", "/languages"), ("POST", "/translate"),
                              ("POST", "/detect"), ("POST", "/batch")), "gateway_rejected")
    payload = _encode(body) if body is not None else None
    headers = {"Authorization": "Bearer " + _key(), "Accept": "application/json", **_device_headers()}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = request.Request(BASE + path, data=payload, headers=headers, method=method)
    try:
        # Proxy environment and redirects cannot move bearer credentials off-host.
        opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
        with opener.open(req, timeout=TIMEOUT) as response:
            raw = response.read(MAX_BYTES + 1)
            status = response.status
    except error.HTTPError as failure:
        status = failure.code
        try:
            raw = failure.read(MAX_BYTES + 1)
        finally:
            failure.close()
    except ClientError:
        raise
    except (socket.timeout, TimeoutError):
        raise ClientError("gateway_timeout") from None
    except Exception:
        raise ClientError("gateway_transport") from None
    require(len(raw) <= MAX_BYTES, "gateway_output_limit")
    value = _decode(raw)
    if status != 200:
        if status in (401, 403):
            raise ClientError("gateway_access_denied")
        fallback = "translation_busy" if status == 429 else "input_too_large" if status == 413 else "gateway_unavailable" if status >= 500 else "gateway_rejected"
        raise ClientError(_error_code(value, fallback))
    return value


def _translation_result(value):
    require(type(value) is dict and value.get("ok") is True, "gateway_invalid_response")
    fields = ("ok", "text", "source", "target", "detected_language", "needs_review", "quality", "translated", "chunks")
    require(all(key in value for key in fields), "gateway_invalid_response")
    require(type(value["text"]) is str and bool(value["text"].strip()) and
            all(type(value[key]) is str and 0 < len(value[key]) <= 100 for key in ("source", "target")) and
            (value["detected_language"] is None or type(value["detected_language"]) is str and 0 < len(value["detected_language"]) <= 100) and
            type(value["needs_review"]) is bool and type(value["translated"]) is bool and
            value["quality"] in ("model_self_estimated", "passthrough") and
            type(value["chunks"]) is int and 0 <= value["chunks"] <= 32, "gateway_invalid_response")
    return {key: value[key] for key in fields}


def _result(name, value):
    require(type(value) is dict, "gateway_invalid_response")
    if value.get("ok") is False or value.get("error"):
        raise ClientError(_error_code(value, "gateway_rejected"))
    require(value.get("ok") is True, "gateway_invalid_response")
    require(len(_encode(value)) <= MAX_BYTES, "gateway_output_limit")
    if name == "translate_text":
        result = _translation_result(value)
    elif name == "translate_batch":
        require(type(value.get("items")) is list and 1 <= len(value["items"]) <= 16 and
                value.get("quality") == "model_self_estimated", "gateway_invalid_response")
        result = {"ok": True, "items": [_translation_result(item) for item in value["items"]],
                  "quality": "model_self_estimated"}
    elif name == "detect_language":
        require((value.get("detected_language") is None or type(value["detected_language"]) is str and
                 0 < len(value["detected_language"]) <= 100) and type(value.get("needs_review")) is bool and
                value.get("quality") == "model_self_estimated" and type(value.get("sampled_characters")) is int and
                1 <= value["sampled_characters"] <= 4000 and type(value.get("complete_input_sampled")) is bool,
                "gateway_invalid_response")
        result = {key: value[key] for key in ("ok", "detected_language", "needs_review", "quality", "sampled_characters", "complete_input_sampled")}
    else:
        rows = value.get("languages")
        require(type(rows) is list and 0 < len(rows) <= 1000 and
                value.get("registration_is_not_verified_quality") is True and
                value.get("regional_review_required") is True, "gateway_invalid_response")
        cleaned = []
        for row in rows:
            require(type(row) is dict and all(type(row.get(key)) is str and 0 < len(row[key]) <= 100 for key in ("code", "name")) and
                    type(row.get("aliases")) is list and len(row["aliases"]) <= 30 and
                    all(type(alias) is str and 0 < len(alias) <= 100 for alias in row["aliases"]) and
                    type(row.get("regional")) is bool and type(row.get("needs_review")) is bool and
                    row.get("status") == "registered" and row.get("quality") == "model_self_estimated" and
                    (not row["regional"] or row["needs_review"]), "gateway_invalid_response")
            cleaned.append({key: row[key] for key in ("code", "name", "aliases", "regional", "needs_review", "status", "quality")})
        result = {"ok": True, "languages": cleaned, "registration_is_not_verified_quality": True,
                  "regional_review_required": True, "automatic_training": False}
    require(scrub(result) == result, "translation_sensitive_response")
    return result


def _call(name, args):
    try:
        payload = _validate(name, args)
        method, path = {"translate_text": ("POST", "/translate"), "detect_language": ("POST", "/detect"),
                        "translation_languages": ("GET", "/languages"), "translate_batch": ("POST", "/batch")}[name]
        return _result(name, _request(method, path, payload))
    except Exception as failure:
        code = str(failure)
        return {"ok": False, "error": code if isinstance(failure, ValueError) and code in SAFE_ERRORS else "translation_client_failed"}


def translate_text(args):
    return _call("translate_text", args)


def detect_language(args):
    return _call("detect_language", args)


def translation_languages(args):
    return _call("translation_languages", args)


def translate_batch(args):
    return _call("translate_batch", args)


_TEXT = {"type": "string", "minLength": 1, "maxLength": MAX_TEXT}
_LANGUAGE = {"type": "string", "minLength": 1, "maxLength": 100}
_TRANSLATION = {"type": "object", "additionalProperties": False, "required": ["text", "target"],
    "properties": {"text": _TEXT, "source": {**_LANGUAGE, "description": "Language code/alias or auto (default)."},
                   "target": {**_LANGUAGE, "description": "Registered backend language code/alias; regional aliases preserve register."},
                   "tone": {"type": "string", "enum": list(TONES)}, "register": {"type": "string", "enum": list(REGISTERS)},
                   "glossary": {"type": "object", "maxProperties": 64, "additionalProperties": {"type": "string", "minLength": 1, "maxLength": 160}}}}
_LIMITS = "Authenticated shared Harmonika backend only; no local translation fallback or content logs. Credentials are denied. Language quality is model-estimated; regional outputs need review. "
TOOLS = [
    {"name": "translate_text", "description": _LIMITS + "Translate text up to 40000 characters. Preserve URLs, numbers and placeholders. Use target jv/su/ban and register for regional speech levels. Returns text, not translated_text.",
     "inputSchema": _TRANSLATION, "annotations": {"readOnlyHint": True}},
    {"name": "detect_language", "description": _LIMITS + "Estimate the language of a bounded text sample; no fabricated confidence score. Returns detection and whether the complete input was sampled.",
     "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"text": _TEXT}, "required": ["text"]}, "annotations": {"readOnlyHint": True}},
    {"name": "translation_languages", "description": _LIMITS + "Read registered language codes, names, aliases and regional flags; registration is not independent quality verification.",
     "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}}, "annotations": {"readOnlyHint": True}},
    {"name": "translate_batch", "description": _LIMITS + "Translate 1–16 items sharing a total limit of 40000 characters and one backend deadline. No automatic retry or partial-success fallback.",
     "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"items": {"type": "array", "minItems": 1, "maxItems": 16, "items": _TRANSLATION}}, "required": ["items"]}, "annotations": {"readOnlyHint": True}},
]
HANDLERS = {"translate_text": translate_text, "detect_language": detect_language,
            "translation_languages": translation_languages, "translate_batch": translate_batch}
