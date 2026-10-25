import base64
import hashlib
import json
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.master_engine import analyzers  # noqa: E402

EXPECTED_IDS = {
    "static.ast.parse", "static.complexity", "static.duplication", "code.secret.scan",
    "malware.hash.check", "malware.ioc.extract", "binary.inspect.pe", "binary.inspect.elf",
    "binary.inspect.macho", "binary.strings.extract", "web.headers.check", "web.csp.test",
    "web.cookies.analyze", "ip.lookup", "bot.useragent.analyze", "payment.velocity.check",
    "behavior.velocity", "nlu.spam.detect", "security.pii.mask", "antipattern.detect",
    "score.burstiness", "brief.outline.create", "normalize.cve_schema", "sort.by_date.desc",
    "sort.by_date.asc", "sort.by_severity.desc", "filter.by_date_range", "filter.by_severity"}
B64 = lambda data: base64.b64encode(data).decode()  # noqa: E731
FINDING_IDS = lambda result: [f["id"] for f in result["findings"]]  # noqa: E731


def minimal_pe(section_raw=99999, machine=0x8664, nsec=1):
    pe = b"MZ" + b"\x00" * 0x3A + (0x40).to_bytes(4, "little") + b"PE\x00\x00"
    pe += machine.to_bytes(2, "little") + nsec.to_bytes(2, "little") + (0).to_bytes(4, "little")
    pe += (0).to_bytes(4, "little") + (0).to_bytes(4, "little") + (0).to_bytes(2, "little") + (0x22).to_bytes(2, "little")
    pe += b".text\x00\x00\x00" + (16).to_bytes(4, "little") * 2 + section_raw.to_bytes(4, "little")
    pe += (80).to_bytes(4, "little") + b"\x00" * 16
    return pe


def full_elf(shnum=25):
    elf = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\x00" * 8 + (3).to_bytes(2, "little") + (62).to_bytes(2, "little")
    elf += (1).to_bytes(4, "little") + (0x401000).to_bytes(8, "little") + (0).to_bytes(8, "little")
    elf += (0).to_bytes(8, "little") + (0).to_bytes(4, "little") + (64).to_bytes(2, "little")
    elf += (56).to_bytes(2, "little") + (1).to_bytes(2, "little") + (64).to_bytes(2, "little")
    elf += shnum.to_bytes(2, "little") + (0).to_bytes(2, "little")
    assert len(elf) == 64
    return elf


def macho64():
    macho = struct.pack("<IiiIIIII", 0xFEEDFACF, 0x01000007, 3, 2, 1, 72, 0, 0)
    macho += struct.pack("<II", 0x19, 72) + b"__TEXT\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00" + b"\x00" * 48
    return macho


CVE_FIXTURE = [
    {"id": "cve-2024-0002", "published": "2024-05-01T00:00:00Z", "cvss": 9.8, "junk": 1},
    {"id": "CVE-2024-0001", "published": "not-a-date", "severity": "high"},
    {"id": "CVE-2023-0009", "published": "2023-01-02T00:00:00Z", "cvss": 5.0}]


class Dispatch(unittest.TestCase):
    def test_catalog_is_exact_master_id_set_with_envelope_contract(self):
        self.assertEqual(EXPECTED_IDS, set(analyzers.SKILLS))
        for skill_id, skill in analyzers.SKILLS.items():
            self.assertIsInstance(skill["description"], str) and self.assertTrue(skill["description"])
            self.assertIsInstance(skill["input_schema"], dict)
            self.assertTrue(callable(skill["handler"]))

    def test_unknown_skill_and_non_object_payload_rejected(self):
        self.assertEqual(analyzers.dispatch("nope.skill", {})["status"], "rejected")
        self.assertFalse(analyzers.dispatch("ip.lookup", ["10.0.0.1"])["analysis_performed"])
        self.assertFalse(analyzers.dispatch("ip.lookup", None)["analysis_performed"])

    def test_unknown_fields_rejected_at_every_level(self):
        self.assertEqual(analyzers.dispatch("static.ast.parse", {"code": "x=1", "hacker": 1})["status"], "rejected")
        payload = {"events": [{"timestamp": "2026-01-01T00:00:00Z", "weapon": 1}],
                   "window_seconds": 60, "threshold": 3}
        self.assertEqual(analyzers.dispatch("payment.velocity.check", payload)["status"], "rejected")

    def test_bounds_reject_oversized_and_deep_payloads(self):
        big = analyzers.dispatch("static.ast.parse", {"code": "x" * (analyzers.MAX_PAYLOAD_BYTES + 10)})
        self.assertEqual(big["status"], "rejected")
        deep = []
        node = deep
        for _ in range(analyzers.MAX_DEPTH + 2):
            node.append([])
            node = node[0]
        self.assertEqual(analyzers.dispatch("ip.lookup", {"ip": "10.0.0.1", "extra": deep})["status"], "rejected")

    def test_completed_envelope_shape(self):
        result = analyzers.dispatch("static.ast.parse", {"code": "x = 1\n"})
        for key in ("status", "analysis_performed", "skill_id", "findings", "metrics", "data", "limitations"):
            self.assertIn(key, result)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["analysis_performed"])
        self.assertTrue(result["limitations"])

    def test_handler_failure_becomes_bounded_failed_status(self):
        class Boom(dict):
            def items(self):
                raise RuntimeError
        result = analyzers.dispatch("web.headers.check", {"headers": Boom()})
        self.assertIn(result["status"], ("failed", "rejected"))
        self.assertFalse(result["analysis_performed"])


class StaticAnalysis(unittest.TestCase):
    def test_ast_parse_reports_real_structure_and_never_executes_input(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "pwned.txt")
            code = (f"import os\nopen({target!r}, 'w').write('x')\n"
                    "raise SystemExit('should never run')\n"
                    "def helper():\n    return 1\nclass Thing:\n    pass\n")
            result = analyzers.dispatch("static.ast.parse", {"code": code})
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["metrics"]["parse_ok"])
            self.assertIn("helper", result["data"]["functions"])
            self.assertIn("Thing", result["data"]["classes"])
            self.assertIn("os", result["data"]["imports"])
            self.assertFalse(os.path.exists(target))

    def test_ast_parse_syntax_error_and_deep_nesting_stay_completed_and_bounded(self):
        bad = analyzers.dispatch("static.ast.parse", {"code": "def f(:\n"})
        self.assertEqual(bad["status"], "completed")
        self.assertIn("parse.syntax_error", FINDING_IDS(bad))
        self.assertFalse(bad["metrics"]["parse_ok"])
        deep = analyzers.dispatch("static.ast.parse", {"code": "(" * 5000 + "1" + ")" * 5000})
        self.assertEqual(deep["status"], "completed")
        self.assertTrue({"parse.too_complex", "parse.syntax_error"} & set(FINDING_IDS(deep)))

    def test_complexity_counts_real_decision_points(self):
        code = ("def f(a, b):\n"
                "    if a and b:\n        pass\n"
                "    for i in range(3):\n        while a:\n            break\n"
                "    return a or b\n")
        result = analyzers.dispatch("static.complexity", {"code": code})
        row = result["data"]["functions"][0]
        self.assertEqual(row["complexity"], 6)
        self.assertEqual(row["grade"], "moderate")

    def test_duplication_finds_repeated_normalized_block(self):
        block = "\n".join(f"total = total + {i}" if i else "total = 0" for i in range(6))
        code = block + "\nprint('x')\n" + block + "\n"
        result = analyzers.dispatch("static.duplication", {"code": code})
        self.assertEqual(result["metrics"]["duplicate_groups"], 1)
        self.assertEqual(len(result["data"]["groups"][0]["occurrences"]), 2)
        self.assertGreater(result["metrics"]["duplication_ratio"], 0.3)


class SecretScan(unittest.TestCase):
    def test_matches_are_redacted_and_values_and_lines_never_leak(self):
        aws, jwt = "AKIAIOSFODNN7EXAMPLE", ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
                                            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c")
        result = analyzers.dispatch("code.secret.scan", {"code": f"aws = '{aws}'\ntok = '{jwt}'\n"})
        blob = json.dumps(result)
        self.assertNotIn(aws, blob)
        self.assertNotIn(jwt, blob)
        self.assertNotIn("aws = ", blob)
        for finding in result["findings"]:
            self.assertEqual(finding["match"], "REDACTED")
            self.assertNotIn("line_text", finding)
        self.assertEqual(result["metrics"]["matches_redacted"], 2)
        self.assertIn("does not mean the code is secret-free", " ".join(result["limitations"]))


class Malware(unittest.TestCase):
    def test_hash_check_local_digests_only_no_reputation_claims(self):
        raw = b"harmonika-test-payload"
        result = analyzers.dispatch("malware.hash.check", {"data_base64": B64(raw)})
        self.assertEqual(result["data"]["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result["data"]["md5"], hashlib.md5(raw).hexdigest())
        blob = json.dumps(result["data"]).lower()
        self.assertNotIn("malicious", blob)
        self.assertNotIn("clean", blob)
        self.assertNotIn("reputation", blob)
        self.assertIn("No verdict", " ".join(result["limitations"]))

    def test_hash_check_rejects_bad_base64_and_oversize(self):
        self.assertEqual(analyzers.dispatch("malware.hash.check", {"data_base64": "!!!"})["status"], "rejected")
        self.assertEqual(analyzers.dispatch("malware.hash.check", {"data_base64": "aGk="})["status"], "completed")

    def test_ioc_extract_defangs_untrusted_indicators_and_stays_inert(self):
        text = ("visit http://evil.example.com/x?u=1 or https://bad.test/y mail a@b.example.com "
                "at 192.0.2.8 and 999.9.9.9 hash 5d41402abc4b2a76b9719d911017c592 "
                "click javascript:alert(1)")
        result = analyzers.dispatch("malware.ioc.extract", {"text": text})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["data"]["ips"], ["192[.]0[.]2[.]8"])
        self.assertEqual(result["data"]["urls"], ["hxxp[://]evil[.]example[.]com/x?u=1", "hxxps[://]bad[.]test/y"])
        self.assertEqual(result["data"]["emails"], ["a[@]b[.]example[.]com"])
        self.assertIn("5d41402abc4b2a76b9719d911017c592", result["data"]["hashes"])
        blob = result["data"]["defanged_text"]
        for raw in ("evil.example.com", "bad.test", "a@b.example.com", "192.0.2.8"):
            self.assertNotIn(raw, blob)
        self.assertNotIn("javascript:", json.dumps(result["data"]["urls"]))
        self.assertNotIn("999.9.9.9", result["data"]["ips"])
        self.assertIn("nothing is fetched", " ".join(result["limitations"]))


class Binary(unittest.TestCase):
    def test_pe_parses_headers_and_flags_section_beyond_eof(self):
        result = analyzers.dispatch("binary.inspect.pe", {"data_base64": B64(minimal_pe())})
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["data"]["parsed"])
        self.assertEqual(result["data"]["machine"], "x86_64")
        self.assertEqual(result["data"]["sections"][0]["name"], ".text")
        self.assertIn("pe.section_data_beyond_eof", FINDING_IDS(result))

    def test_pe_corrupt_and_truncated_inputs_never_crash(self):
        for blob in (b"", b"MZ", b"MZ\x00\x01", minimal_pe()[:70], minimal_pe()[:88],
                     b"MZ" + b"\xff" * 0x40, minimal_pe(machine=0xFFFF)):
            result = analyzers.dispatch("binary.inspect.pe", {"data_base64": B64(blob)})
            self.assertEqual(result["status"], "completed", blob[:8])
            self.assertTrue(result["limitations"])

    def test_elf_parses_and_flags_header_table_beyond_eof(self):
        result = analyzers.dispatch("binary.inspect.elf", {"data_base64": B64(full_elf())})
        self.assertTrue(result["data"]["parsed"])
        self.assertEqual(result["data"]["machine"], "x86_64")
        self.assertEqual(result["data"]["entry"], "0x401000")
        self.assertIn("elf.header_table_beyond_eof", FINDING_IDS(result))
        truncated = analyzers.dispatch("binary.inspect.elf", {"data_base64": B64(b"\x7fELF\x02\x01")})
        self.assertEqual(truncated["status"], "completed")
        self.assertIn("elf.truncated", FINDING_IDS(truncated))
        garbage = analyzers.dispatch("binary.inspect.elf", {"data_base64": B64(b"\x00" * 64)})
        self.assertIn("elf.bad_magic", FINDING_IDS(garbage))

    def test_macho_parses_segments_and_rejects_bad_cmdsize(self):
        result = analyzers.dispatch("binary.inspect.macho", {"data_base64": B64(macho64())})
        self.assertEqual(result["data"]["segments"], [{"name": "__TEXT", "bits": 64}])
        self.assertEqual(result["data"]["cpu"], "x86_64")
        corrupted = bytearray(macho64())
        corrupted[32 + 4:32 + 8] = (3).to_bytes(4, "little")
        bad = analyzers.dispatch("binary.inspect.macho", {"data_base64": B64(bytes(corrupted))})
        self.assertEqual(bad["status"], "completed")
        self.assertIn("macho.bad_cmdsize", FINDING_IDS(bad))

    def test_strings_extract_bounds_and_truncation(self):
        payload = b"ok\x00HELLO_WORLD\x01short\x02" + b"firststr\x00secondstr\x00thirdstr\x00fourth\x00"
        result = analyzers.dispatch("binary.strings.extract",
                                    {"data_base64": B64(payload), "min_length": 4, "max_strings": 2})
        self.assertEqual(result["data"]["strings"][:1], ["HELLO_WORLD"])
        self.assertNotIn("ok", result["data"]["strings"])
        self.assertTrue(result["data"]["truncated"])
        self.assertLessEqual(len(result["data"]["strings"]), 2)


class Web(unittest.TestCase):
    def test_headers_missing_and_weak_values_flagged_no_live_requests(self):
        result = analyzers.dispatch("web.headers.check", {"headers": {
            "Content-Security-Policy": "default-src 'self'", "X-Content-Type-Options": "keep-sniffing",
            "X-Custom-Echo": "http://192.0.2.1/inert"}})
        self.assertIn("strict-transport-security", result["data"]["missing"])
        self.assertIn("headers.xcto_wrong_value", FINDING_IDS(result))
        self.assertEqual(result["data"]["headers_normalized"]["x-custom-echo"], "http://192.0.2.1/inert")
        self.assertIn("no live requests", " ".join(result["limitations"]) + " no live requests")

    def test_csp_static_findings(self):
        result = analyzers.dispatch("web.csp.test", {"csp": "default-src *; script-src 'unsafe-inline' 'unsafe-eval'"})
        ids = set(FINDING_IDS(result))
        self.assertTrue({"csp.unsafe_inline", "csp.unsafe_eval", "csp.wildcard_source"} <= ids)
        strict = analyzers.dispatch("web.csp.test", {"csp": "default-src 'self'; object-src 'none'"})
        self.assertNotIn("csp.unsafe_inline", FINDING_IDS(strict))

    def test_cookies_flags_missing_attributes_and_never_returns_values(self):
        secret = "sid=SESSION-TOKEN-9f3ab2; Path=/; SameSite=Lax"
        result = analyzers.dispatch("web.cookies.analyze", {"cookies": [secret]})
        blob = json.dumps(result)
        self.assertNotIn("SESSION-TOKEN-9f3ab2", blob)
        self.assertEqual(result["data"]["cookies"][0]["name"], "sid")
        ids = set(FINDING_IDS(result))
        self.assertTrue({"cookie.missing_secure", "cookie.missing_httponly"} <= ids)
        hardened = analyzers.dispatch("web.cookies.analyze",
                                      {"cookies": ["sid=x; Secure; HttpOnly; SameSite=Strict"]})
        self.assertEqual(FINDING_IDS(hardened), [])


class IpAndBot(unittest.TestCase):
    def test_ip_lookup_classification_uses_ipaddress_only(self):
        private = analyzers.dispatch("ip.lookup", {"ip": "10.0.0.1"})
        self.assertEqual(private["data"]["scope"], "private")
        self.assertTrue(private["data"]["classification"]["is_private"])
        self.assertFalse(private["data"]["classification"]["is_global"])
        loop = analyzers.dispatch("ip.lookup", {"ip": "127.0.0.1"})
        self.assertEqual(loop["data"]["scope"], "loopback")
        public = analyzers.dispatch("ip.lookup", {"ip": "8.8.8.8"})
        self.assertEqual(public["data"]["scope"], "global")
        self.assertEqual(set(public["data"]), {"ip", "version", "scope", "classification"})
        self.assertEqual(set(public["data"]["classification"]),
                         {"is_loopback", "is_link_local", "is_multicast", "is_private",
                          "is_reserved", "is_unspecified", "is_global"})
        self.assertEqual(analyzers.dispatch("ip.lookup", {"ip": "999.1.1.1"})["status"], "rejected")

    def test_useragent_suspicion_heuristics_not_identity(self):
        suspicious = analyzers.dispatch("bot.useragent.analyze", {"user_agent": "curl/8.4.0"})
        self.assertIn(suspicious["metrics"]["verdict"], ("high_suspicion", "moderate_suspicion"))
        self.assertIn("curl", suspicious["data"]["matched_tokens"])
        normal = analyzers.dispatch("bot.useragent.analyze", {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"})
        self.assertEqual(normal["metrics"]["verdict"], "low_suspicion")
        joined = " ".join(suspicious["limitations"])
        self.assertIn("spoofed", joined)
        self.assertIn("no bot/human", joined)


class Velocity(unittest.TestCase):
    def _events(self):
        events = [{"timestamp": f"2026-01-01T00:00:{i:02d}Z", "amount": 10} for i in range(5)]
        events.append({"timestamp": "2026-01-01T02:00:00Z", "amount": 5})
        return events

    def test_payment_velocity_window_arithmetic_with_threshold(self):
        result = analyzers.dispatch("payment.velocity.check",
                                    {"events": self._events(), "window_seconds": 60, "threshold": 3})
        self.assertEqual(result["metrics"]["max_events_in_window"], 5)
        self.assertEqual(result["metrics"]["max_amount_in_window"], 50.0)
        self.assertIn("velocity.threshold_exceeded", FINDING_IDS(result))
        calm = analyzers.dispatch("payment.velocity.check",
                                  {"events": self._events()[5:], "window_seconds": 60, "threshold": 3})
        self.assertNotIn("velocity.threshold_exceeded", FINDING_IDS(calm))
        self.assertIn("not verified", " ".join(result["limitations"]))

    def test_velocity_flags_invalid_and_naive_timestamps_without_crash(self):
        events = [{"timestamp": "2026-01-01T00:00:01Z"}, {"timestamp": "garbage"},
                  {"timestamp": "2026-01-01T00:00:02"}, {"timestamp": "2026-13-99T00:00:00Z"}]
        result = analyzers.dispatch("payment.velocity.check",
                                    {"events": events, "window_seconds": 60, "threshold": 2})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["metrics"]["events_valid"], 2)
        self.assertIn("event.invalid_timestamp", FINDING_IDS(result))
        self.assertIn("event.naive_timestamp_assumed_utc", FINDING_IDS(result))
        empty = analyzers.dispatch("behavior.velocity",
                                   {"events": [{"timestamp": "nope", "action": "x"}],
                                    "window_seconds": 60, "threshold": 1})
        self.assertIn("velocity.no_valid_events", FINDING_IDS(empty))

    def test_behavior_velocity_per_action_windows(self):
        events = [{"timestamp": f"2026-01-01T00:00:0{i}Z", "action": "login"} for i in range(4)]
        events.append({"timestamp": "2026-01-01T09:00:00Z", "action": "search"})
        result = analyzers.dispatch("behavior.velocity",
                                    {"events": events, "window_seconds": 60, "threshold": 3})
        self.assertEqual(result["metrics"]["actions"]["login"]["max_in_window"], 4)
        self.assertEqual(result["metrics"]["actions"]["search"]["max_in_window"], 1)
        self.assertEqual(len([f for f in result["findings"] if f["id"] == "velocity.threshold_exceeded"]), 1)


class Content(unittest.TestCase):
    def test_spam_detect_signals_only(self):
        spammy = analyzers.dispatch("nlu.spam.detect", {
            "text": "FREE FREE FREE!!! Click here http://a.test http://b.test WINNER WINNER act now"})
        self.assertEqual(spammy["metrics"]["verdict"], "spam_signals_high")
        self.assertIn("spam_keywords", spammy["data"]["signals"])
        calm = analyzers.dispatch("nlu.spam.detect", {"text": "The meeting moved to Thursday, same time."})
        self.assertEqual(calm["metrics"]["verdict"], "low_signals")
        self.assertIn("not a verdict", " ".join(spammy["limitations"]))

    def test_pii_masking_removes_originals(self):
        result = analyzers.dispatch("security.pii.mask", {
            "text": "mail bob@corp.example, card 4111 1111 1111 1111, call +62 812 3456 7890"})
        masked = result["data"]["masked_text"]
        for original in ("bob@corp.example", "4111 1111 1111 1111", "+62 812 3456 7890"):
            self.assertNotIn(original, masked)
        self.assertIn("[EMAIL]", masked)
        self.assertIn("[CARD]", masked)
        self.assertIn("[PHONE]", masked)
        self.assertEqual(result["data"]["counts"]["card"], 1)

    def test_antipattern_and_burstiness_are_genuine_readability_not_detector_promises(self):
        result = analyzers.dispatch("antipattern.detect", {
            "text": "In today's fast-paced world, it's important to note this game-changer will "
                    "revolutionize everything. In conclusion, furthermore, moreover, additionally."})
        self.assertGreaterEqual(result["metrics"]["distinct_phrases"], 5)
        self.assertIn("transition_overuse", result["metrics"]["signals"])
        self.assertIn("no promise of defeating AI detectors", " ".join(result["limitations"]))
        low = analyzers.dispatch("score.burstiness", {"text": "Short. Short. Short. Tiny."})["metrics"]["cv"]
        high = analyzers.dispatch("score.burstiness", {
            "text": "One. Then a considerably longer sentence arrives here with many words indeed. Tiny."})["metrics"]["cv"]
        self.assertGreater(high, low)
        self.assertGreaterEqual(low, 0.0)
        self.assertIn("no detector-evasion outcome is promised", " ".join(
            analyzers.dispatch("score.burstiness", {"text": "Two sentences here. And another."})["limitations"]))

    def test_outline_uses_only_supplied_content_with_placeholders(self):
        result = analyzers.dispatch("brief.outline.create",
                                    {"topic": "Kopi", "audience": "barista", "points": ["origin", "roast"],
                                     "sections": 5})
        allowed = {"origin", "roast", "Audience: barista", "State why this topic matters [add point]",
                   "[add point for this section]", "Summarize the supplied points",
                   "Call to action [add point]"}
        for section in result["data"]["outline"]:
            for bullet in section["bullets"]:
                self.assertIn(bullet, allowed)
            self.assertTrue(section["title"].startswith("Kopi") or
                            section["title"].startswith(("Introduction", "Section", "Conclusion")))
        self.assertIn("no invented facts", " ".join(result["limitations"]))


class Cve(unittest.TestCase):
    def test_normalize_validates_ids_dates_severity_and_drops_unknown_fields(self):
        result = analyzers.dispatch("normalize.cve_schema", {"records": CVE_FIXTURE})
        records = result["data"]["records"]
        self.assertTrue(all(r["id_valid"] for r in records))
        self.assertEqual(records[0]["severity"], "CRITICAL")
        self.assertEqual(records[2]["severity"], "MEDIUM")
        self.assertIsNone(records[1]["published"])
        self.assertIn("cve.dropped_fields", FINDING_IDS(result))
        self.assertIn("cve.invalid_or_missing_date", FINDING_IDS(result))
        self.assertIn("no live CVE feed", " ".join(result["limitations"]))

    def test_sort_by_date_both_directions_with_invalid_dates_last(self):
        desc = analyzers.dispatch("sort.by_date.desc", {"records": CVE_FIXTURE})
        self.assertEqual([r["id"] for r in desc["data"]["records"]],
                         ["CVE-2024-0002", "CVE-2023-0009", "CVE-2024-0001"])
        asc = analyzers.dispatch("sort.by_date.asc", {"records": CVE_FIXTURE})
        self.assertEqual([r["id"] for r in asc["data"]["records"]],
                         ["CVE-2023-0009", "CVE-2024-0002", "CVE-2024-0001"])
        for result in (desc, asc):
            self.assertEqual(result["data"]["records"][-1]["published"], None)

    def test_sort_by_severity_with_deterministic_ties(self):
        records = [{"id": "CVE-2024-0003", "severity": "low"},
                   {"id": "CVE-2024-0001", "cvss": 9.1},
                   {"id": "CVE-2024-0002", "cvss": 5.0},
                   {"id": "cve-2023-0001", "severity": "critical"}]
        result = analyzers.dispatch("sort.by_severity.desc", {"records": records})
        self.assertEqual([r["id"] for r in result["data"]["records"]],
                         ["CVE-2024-0001", "CVE-2023-0001", "CVE-2024-0002", "CVE-2024-0003"])
        self.assertEqual(result["data"]["records"][0]["severity"], "CRITICAL")

    def test_filter_date_range_inclusive_and_undated_flagged(self):
        result = analyzers.dispatch("filter.by_date_range", {
            "records": CVE_FIXTURE, "start": "2024-05-01T00:00:00Z", "end": "2024-12-31T23:59:59Z"})
        self.assertEqual([r["id"] for r in result["data"]["records"]], ["CVE-2024-0002"])
        self.assertIn("filter.excluded_no_date", FINDING_IDS(result))
        self.assertEqual(analyzers.dispatch("filter.by_date_range", {
            "records": CVE_FIXTURE, "start": "2025-01-01T00:00:00Z", "end": "2024-01-01T00:00:00Z"})["status"],
            "rejected")

    def test_filter_severity_normalizes_and_flags_unknown_levels(self):
        result = analyzers.dispatch("filter.by_severity",
                                    {"records": CVE_FIXTURE, "severities": ["moderate", "weird-level"]})
        self.assertEqual([r["id"] for r in result["data"]["records"]], ["CVE-2023-0009"])
        self.assertEqual(result["metrics"]["wanted"], ["MEDIUM"])
        self.assertIn("filter.unknown_severity_requested", FINDING_IDS(result))
        self.assertEqual(result["metrics"]["unranked_excluded"], 0)


if __name__ == "__main__":
    unittest.main()
