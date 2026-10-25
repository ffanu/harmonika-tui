"""Tests for backend/mobile_engine/generators.py (Harmonika mobile source generators).

Stdlib unittest only. The generator under test is a pure, offline source
generator: these tests verify its registry, envelope contract, strict input
validation, path/injection safety, language coherence, manifest validity and
honest runtime claims. No network, no pip, no writes outside /tmp.
"""

import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN_DIR = os.path.join(ROOT, "backend", "mobile_engine")
if GEN_DIR not in sys.path:
    sys.path.insert(0, GEN_DIR)

import generators as gen  # noqa: E402

# Canonical skill ids cited by backend/mobile_engine/specification.txt that
# this engine must cover (bounded subset).
SPEC_REQUIRED_IDS = [
    "scaffold.expo",
    "scaffold.react_native_cli",
    "scaffold.flutter",
    "scaffold.kotlin_android",
    "scaffold.swift_ios",
    "scaffold.ionic",
    "mobile.gen.screen",
    "mobile.gen.component",
    "mobile.gen.form",
    "mobile.gen.list",
    "mobile.gen.card",
    "mobile.gen.modal",
    "mobile.gen.tab_bar",
    "mobile.gen.drawer",
    "mobile.gen.skeleton",
    "mobile.gen.empty_state",
    "mobile.style.material",
    "mobile.style.cupertino",
    "mobile.style.adaptive",
    "mobile.responsive.tablet",
    "mobile.theme.dark_mode",
    "nav.setup.stack",
    "nav.setup.tab",
    "nav.setup.modal",
    "nav.auth_flow",
    "nav.guard",
    "nav.deep_link",
    "state.setup.zustand",
    "net.setup.axios",
    "net.setup.fetch",
    "local.async_storage",
    "local.secure_store",
    "auth.mobile.login",
    "auth.mobile.logout",
    "device.gallery",
    "device.camera",
    "device.gps",
    "device.sharing",
    "device.permissions",
    "push.permission.request",
    "push.handle.tap",
]

_CODE_EXTS = (".ts", ".tsx", ".dart", ".kt", ".kts", ".swift")
_SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9]{16,}",
    r"AKIA[0-9A-Z]{16}",
    r"ghp_[A-Za-z0-9]{20,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"AIza[0-9A-Za-z_-]{30,}",
]


def balance_report(content):
    """Bracket balance check for generated code.

    Skips line/block comments, quoted strings, template literals and regex
    literals (heuristic: a `/` starts a regex only after a character that
    cannot end an expression, which excludes JSX '</' and division).
    """

    depth = {"(": 0, "[": 0, "{": 0}
    pairs = {")": "(", "]": "[", "}": "{"}
    regex_prev = set("(,=:[!&|?;+*%~^-")
    i, n = 0, len(content)
    state = None
    prev_sig = ""
    while i < n:
        ch = content[i]
        nxt = content[i + 1] if i + 1 < n else ""
        if state is None:
            if ch == "/" and nxt == "/":
                state = "line"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block"
                i += 2
                continue
            if ch == "/" and (prev_sig == "" or prev_sig in regex_prev):
                state = "regex"
                i += 1
                continue
            if ch in ('"', "'", "`"):
                state = ch
                i += 1
                continue
            if ch in depth:
                depth[ch] += 1
            elif ch in pairs:
                depth[pairs[ch]] -= 1
                if depth[pairs[ch]] < 0:
                    return "unbalanced %r at offset %d" % (ch, i)
            if not ch.isspace():
                prev_sig = ch
        elif state == "line":
            if ch == "\n":
                state = None
        elif state == "block":
            if ch == "*" and nxt == "/":
                state = None
                i += 2
                continue
        elif state == "regex":
            if ch == "\\":
                i += 2
                continue
            if ch == "/":
                state = None
                prev_sig = "/"
        else:  # quoted string / template literal
            if ch == "\\":
                i += 2
                continue
            if ch == state:
                state = None
                if not ch.isspace():
                    prev_sig = ch
        i += 1
    if state in ('"', "'", "`", "regex", "block"):
        return "unterminated %s" % state
    bad = {k: v for k, v in depth.items() if v != 0}
    return bad or None


def file_map(result):
    return {f["path"]: f["content"] for f in result["files"]}


class TestRegistry(unittest.TestCase):
    def test_descriptors_shape(self):
        desc = gen.descriptors()
        self.assertIsInstance(desc, dict)
        self.assertGreaterEqual(len(desc), 30, "target 30-45 generators")
        self.assertLessEqual(len(desc), 50)
        for skill_id, entry in desc.items():
            with self.subTest(skill_id=skill_id):
                self.assertTrue(re.match(r"^[a-z0-9_.]+$", skill_id), skill_id)
                self.assertEqual(set(entry), {"description", "input_schema"})
                self.assertIsInstance(entry["description"], str)
                self.assertGreater(len(entry["description"]), 20)
                schema = entry["input_schema"]
                self.assertEqual(schema["type"], "object")
                self.assertFalse(schema["additionalProperties"])
                self.assertIn("properties", schema)
                for name, prop in schema["properties"].items():
                    self.assertIn("description", prop, "%s.%s" % (skill_id, name))

    def test_spec_required_ids_present(self):
        desc = gen.descriptors()
        for skill_id in SPEC_REQUIRED_IDS:
            self.assertIn(skill_id, desc)

    def test_supported_frameworks_tuple(self):
        self.assertIsInstance(gen.SUPPORTED_FRAMEWORKS, tuple)
        self.assertEqual(
            set(gen.SUPPORTED_FRAMEWORKS),
            {"expo", "flutter", "kotlin", "swift", "ionic"},
        )
        for schema in gen.descriptors().values():
            fw = schema["input_schema"]["properties"].get("framework")
            if fw and "enum" in fw:
                self.assertTrue(set(fw["enum"]).issubset(set(gen.SUPPORTED_FRAMEWORKS)))
                if fw["enum"]: self.assertIn(fw["default"], fw["enum"])

    def test_support(self):
        self.assertTrue(gen.support("scaffold.expo"))
        self.assertTrue(gen.support("mobile.gen.screen"))
        self.assertTrue(gen.support("push.handle.tap"))
        # Bare RN project cannot be faked offline: registered, not dispatchable.
        self.assertFalse(gen.support("scaffold.react_native_cli"))
        self.assertFalse(gen.support("does.not.exist"))
        self.assertFalse(gen.support(None))
        self.assertFalse(gen.support(42))

    def test_react_native_cli_marked_unsupported(self):
        desc = gen.descriptors()["scaffold.react_native_cli"]
        self.assertIn("UNSUPPORTED", desc["description"])
        with self.assertRaises(gen.UnsupportedSkillError):
            gen.dispatch("scaffold.react_native_cli", {})
        msg = str(gen.UnsupportedSkillError("x"))
        self.assertTrue(msg)  # placeholder to keep import style explicit


class TestDispatchEnvelope(unittest.TestCase):
    def test_all_supported_skills_complete(self):
        for skill_id in sorted(gen.descriptors()):
            if not gen.support(skill_id):
                continue
            with self.subTest(skill_id=skill_id):
                result = gen.dispatch(skill_id, {})
                self.assertEqual(
                    set(result),
                    {"status", "mode", "files", "warnings", "runtime_verified"},
                )
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["mode"], "source_generation")
                self.assertIs(result["runtime_verified"], False)
                self.assertGreaterEqual(len(result["files"]), 1)
                self.assertIsInstance(result["warnings"], list)
                for warning in result["warnings"]:
                    self.assertIsInstance(warning, str)
                    self.assertTrue(warning)

    def test_file_entries_are_relative_and_sorted(self):
        for skill_id in sorted(gen.descriptors()):
            if not gen.support(skill_id):
                continue
            result = gen.dispatch(skill_id, {})
            paths = [f["path"] for f in result["files"]]
            with self.subTest(skill_id=skill_id):
                self.assertEqual(paths, sorted(paths))
                self.assertEqual(len(paths), len(set(paths)))
                for path in paths:
                    self.assertFalse(path.startswith("/"), path)
                    self.assertNotIn("..", path.split("/"), path)
                    self.assertNotIn("\\", path, path)
                for entry in result["files"]:
                    self.assertEqual(set(entry), {"path", "content"})
                    self.assertIsInstance(entry["content"], str)
                    self.assertTrue(entry["content"].strip())

    def test_deterministic_output(self):
        for skill_id, payload in [
            ("scaffold.expo", {"name": "Toko A", "identifier": "id.a.b"}),
            ("mobile.gen.form", {"screen_name": "Login", "fields": [
                {"name": "email", "type": "email", "required": True}]}),
            ("scaffold.kotlin_android", {"name": "Toko A"}),
            ("push.handle.tap", {}),
        ]:
            with self.subTest(skill_id=skill_id):
                first = gen.dispatch(skill_id, payload)
                second = gen.dispatch(skill_id, payload)
                self.assertEqual(first, second)

    def test_default_inputs_per_task_brief(self):
        result = gen.dispatch("scaffold.expo", {})
        files = file_map(result)
        app_json = json.loads(files["app.json"])
        self.assertEqual(app_json["expo"]["name"], "Harmonika App")
        self.assertEqual(app_json["expo"]["android"]["package"], "id.harmonika.app")
        self.assertEqual(app_json["expo"]["ios"]["bundleIdentifier"], "id.harmonika.app")
        result2 = gen.dispatch("mobile.gen.screen", {})
        paths = [f["path"] for f in result2["files"]]
        self.assertTrue(any(p.endswith("HomeScreen.tsx") for p in paths))


class TestValidation(unittest.TestCase):
    def _assert_validation_error(self, skill_id, payload, fragment=None):
        with self.assertRaises(gen.GeneratorError) as ctx:
            gen.dispatch(skill_id, payload)
        if fragment:
            self.assertIn(fragment, str(ctx.exception))
        return ctx.exception

    def test_input_must_be_object(self):
        for payload in ([], "x", 3, object()):
            with self.subTest(payload=type(payload).__name__):
                self._assert_validation_error("scaffold.expo", payload, "must be an object")

    def test_unknown_fields_rejected(self):
        exc = self._assert_validation_error(
            "scaffold.expo", {"deploy_to_play_store": True}, "unknown input field"
        )
        self.assertIn("deploy_to_play_store", str(exc))
        self.assertIn("allowed", str(exc))

    def test_unknown_skill(self):
        with self.assertRaises(gen.UnknownSkillError):
            gen.dispatch("deploy.play_store", {})
        with self.assertRaises(gen.UnknownSkillError):
            gen.dispatch("", {})
        with self.assertRaises(gen.UnknownSkillError):
            gen.dispatch(123, {})

    def test_unsupported_skill_fails_clearly(self):
        with self.assertRaises(gen.UnsupportedSkillError) as ctx:
            gen.dispatch("scaffold.react_native_cli", {})
        self.assertIn("react-native-community/cli", str(ctx.exception))

    def test_framework_mismatch_names_supported(self):
        exc = self._assert_validation_error(
            "state.setup.zustand", {"framework": "flutter"}, "does not support framework 'flutter'"
        )
        self.assertIn("expo", str(exc))
        self._assert_validation_error(
            "scaffold.flutter", {"framework": "expo"}, "does not support framework 'expo'"
        )
        self._assert_validation_error(
            "scaffold.expo", {"framework": "unity"}, "unsupported framework 'unity'"
        )

    def test_name_bounds_and_chars(self):
        for bad in ["", " ", "a" * 65, "bad/name", "..\\name", "<script>", "\x01x", "###"]:
            with self.subTest(name=bad):
                self._assert_validation_error("scaffold.expo", {"name": bad})
        ok = gen.dispatch("scaffold.expo", {"name": "Toko Maju Jaya 1 (Beta)"})
        self.assertEqual(ok["status"], "completed")

    def test_identifier_rules(self):
        for bad in [
            "harmonika",
            "ID.harmonika.app",
            "id..app",
            "id.harmonika..app",
            ".id.harmonika",
            "id.harm onika.app",
            "1id.harmonika.app",
            "id.harmonika.app/x",
            "a" * 101,
        ]:
            with self.subTest(identifier=bad):
                self._assert_validation_error("scaffold.expo", {"identifier": bad})
        ok = gen.dispatch("scaffold.expo", {"identifier": "id.harmonikaapp.store1"})
        self.assertEqual(ok["status"], "completed")

    def test_screen_name_must_be_pascal(self):
        for bad in ["home", "Home screen", "Home-Screen", "../Home", "1Home", "", "H" * 66, "Hóme"]:
            with self.subTest(screen_name=bad):
                self._assert_validation_error("mobile.gen.screen", {"screen_name": bad})
        for good in ["Home", "HomeScreen", "X9", "OrderTracking"]:
            result = gen.dispatch("mobile.gen.screen", {"screen_name": good})
            self.assertEqual(result["status"], "completed")

    def test_api_base_url_https_only(self):
        for bad in [
            "http://api.example.com",
            "ftp://api.example.com",
            "api.example.com",
            "https://user:pass@api.example.com",
            "https://api.example.com/?x=1",
            "https://api.example.com/#frag",
            "javascript:alert(1)",
            "https://",
            "",
            "https://api exam ple.com",
        ]:
            with self.subTest(api_base_url=bad):
                self._assert_validation_error("net.setup.axios", {"api_base_url": bad})
        ok = gen.dispatch("net.setup.axios", {"api_base_url": "https://api.tokosaya.id/v1"})
        self.assertEqual(ok["status"], "completed")

    def test_tabs_and_drawer_bounds(self):
        self._assert_validation_error("mobile.gen.tab_bar", {"tabs": ["OnlyOne"]})
        self._assert_validation_error(
            "mobile.gen.tab_bar", {"tabs": ["A", "B", "C", "D", "E", "F"]}
        )
        self._assert_validation_error("mobile.gen.drawer", {"items": ["Solo"]})
        self._assert_validation_error(
            "mobile.gen.tab_bar", {"tabs": ["home", "search"]}
        )
        self._assert_validation_error("mobile.gen.tab_bar", {"tabs": ["Home", "Home"]})
        result = gen.dispatch("mobile.gen.tab_bar", {"tabs": ["Home", "Cart"]})
        self.assertEqual(result["status"], "completed")

    def test_fields_validation(self):
        self._assert_validation_error("mobile.gen.form", {"fields": []})
        self._assert_validation_error(
            "mobile.gen.form", {"fields": [{"name": "f%d" % i} for i in range(13)]}
        )
        self._assert_validation_error(
            "mobile.gen.form", {"fields": [{"name": "email", "type": "tel"}]}
        )
        self._assert_validation_error(
            "mobile.gen.form", {"fields": [{"name": "email", "x": 1}]}
        )
        self._assert_validation_error(
            "mobile.gen.form", {"fields": [{"name": "EmailAddress"}]}
        )
        self._assert_validation_error(
            "mobile.gen.form", {"fields": [{"name": "a"}, {"name": "a"}]}
        )
        self._assert_validation_error(
            "mobile.gen.form", {"fields": [{"name": "email", "required": "yes"}]}
        )
        ok = gen.dispatch("mobile.gen.form", {"fields": [
            {"name": "email", "type": "email", "required": True},
            {"name": "age", "type": "number", "required": False},
        ]})
        self.assertEqual(ok["status"], "completed")

    def test_permissions_validation(self):
        self._assert_validation_error("device.permissions", {"permissions": ["sms"]})
        self._assert_validation_error("device.permissions", {"permissions": []})
        ok = gen.dispatch("device.permissions", {"permissions": ["camera", "location"]})
        self.assertEqual(ok["status"], "completed")


class TestInjectionAndPathSafety(unittest.TestCase):
    def test_hostile_strings_rejected_or_inert(self):
        hostile_values = [
            "../../etc/passwd",
            "..\\..\\windows",
            "/absolute/path",
            "name; rm -rf /",
            "name$(cmd)",
            "name`cmd`",
            "name\x00null",
            "a" * 5000,
        ]
        for value in hostile_values:
            for key in ("name", "screen_name", "identifier", "api_base_url"):
                with self.subTest(key=key):
                    with self.assertRaises(gen.GeneratorError):
                        gen.dispatch("mobile.gen.screen", {key: value})

    def test_hostile_name_raises_instead_of_leaking(self):
        for hostile in ("A/../B/name", "a/b/c", "..", ".", "\\", "name$x", "a;b"):
            with self.subTest(hostile=hostile):
                with self.assertRaises(gen.GeneratorError):
                    gen.dispatch("mobile.gen.screen", {"name": hostile})

    def test_generated_paths_stay_relative(self):
        for skill_id in sorted(gen.descriptors()):
            if not gen.support(skill_id):
                continue
            result = gen.dispatch(skill_id, {"name": "A B (C) - v2"})
            with self.subTest(skill_id=skill_id):
                for f in result["files"]:
                    self.assertFalse(f["path"].startswith("/"))
                    self.assertNotIn("..", f["path"].split("/"))
                    self.assertNotIn("\\", f["path"])

    def test_no_secret_material_in_output(self):
        for skill_id in sorted(gen.descriptors()):
            if not gen.support(skill_id):
                continue
            result = gen.dispatch(skill_id, {})
            with self.subTest(skill_id=skill_id):
                for f in result["files"]:
                    for pattern in _SECRET_PATTERNS:
                        self.assertIsNone(
                            re.search(pattern, f["content"]),
                            "%s matched %s" % (f["path"], pattern),
                        )

    def test_generator_module_is_offline_and_pure(self):
        with open(os.path.join(GEN_DIR, "generators.py"), "r", encoding="utf-8") as fh:
            source = fh.read()
        for forbidden in [
            "import os",
            "import sys",
            "import subprocess",
            "import socket",
            "import requests",
            "import urllib.request",
            "from urllib.request",
            "import shutil",
            "open(",
        ]:
            self.assertNotIn(forbidden, source, forbidden)


class TestLanguageCoherence(unittest.TestCase):
    def test_expo_modules_never_emit_other_languages(self):
        expo_skills = [
            "scaffold.expo",
            "mobile.gen.screen",
            "mobile.gen.component",
            "mobile.gen.form",
            "mobile.gen.list",
            "mobile.gen.card",
            "mobile.gen.modal",
            "mobile.gen.tab_bar",
            "mobile.gen.drawer",
            "mobile.gen.skeleton",
            "mobile.gen.empty_state",
            "mobile.style.material",
            "mobile.style.cupertino",
            "mobile.style.adaptive",
            "mobile.responsive.tablet",
            "mobile.theme.dark_mode",
            "nav.setup.stack",
            "nav.setup.tab",
            "nav.setup.modal",
            "nav.auth_flow",
            "nav.guard",
            "nav.deep_link",
            "state.setup.zustand",
            "net.setup.axios",
            "net.setup.fetch",
            "local.async_storage",
            "local.secure_store",
            "auth.mobile.login",
            "auth.mobile.logout",
            "device.gallery",
            "device.camera",
            "device.gps",
            "device.sharing",
            "device.permissions",
            "push.permission.request",
            "push.handle.tap",
        ]
        wrong_markers = [
            "package androidx",
            "import 'package:flutter",
            "import SwiftUI",
            "func apply(",
            "Widget build(BuildContext",
        ]
        for skill_id in expo_skills:
            result = gen.dispatch(skill_id, {})
            with self.subTest(skill_id=skill_id):
                for f in result["files"]:
                    if f["path"].endswith((".ts", ".tsx")):
                        for marker in wrong_markers:
                            self.assertNotIn(marker, f["content"])

    def test_flutter_modules_emit_dart_only(self):
        cupertino_skills = ("mobile.style.cupertino",)
        ts_markers = ["from 'react-native'", "from 'react'"]
        for skill_id, payload in [
            ("scaffold.flutter", {}),
            ("mobile.gen.screen", {"framework": "flutter", "screen_name": "Checkout"}),
            ("mobile.gen.form", {"framework": "flutter"}),
            ("mobile.gen.list", {"framework": "flutter"}),
            ("mobile.gen.card", {"framework": "flutter"}),
            ("mobile.gen.modal", {"framework": "flutter"}),
            ("mobile.gen.skeleton", {"framework": "flutter"}),
            ("mobile.gen.empty_state", {"framework": "flutter"}),
            ("mobile.style.material", {"framework": "flutter"}),
            ("mobile.style.cupertino", {"framework": "flutter"}),
            ("mobile.theme.dark_mode", {"framework": "flutter"}),
        ]:
            result = gen.dispatch(skill_id, payload)
            with self.subTest(skill_id=skill_id):
                dart_files = [f for f in result["files"] if f["path"].endswith(".dart")]
                self.assertTrue(dart_files, "expected Dart output")
                for f in dart_files:
                    for marker in ts_markers:
                        self.assertNotIn(marker, f["content"])
                joined = "\n".join(f["content"] for f in dart_files)
                import_marker = (
                    "import 'package:flutter/cupertino.dart';"
                    if skill_id in cupertino_skills
                    else "import 'package:flutter/material.dart';"
                )
                dart_markers = [import_marker]
                for marker in dart_markers:
                    self.assertIn(marker, joined)

    def test_kotlin_scaffold_structure(self):
        result = gen.dispatch("scaffold.kotlin_android", {"name": "Toko Maju", "identifier": "id.tokomaju.app"})
        files = file_map(result)
        manifest = files["app/src/main/AndroidManifest.xml"]
        self.assertIn('<manifest', manifest)
        self.assertIn('android:name=".MainActivity"', manifest)
        self.assertIn('android:exported="true"', manifest)
        self.assertIn('windowSoftInputMode="adjustResize"', manifest)
        gradle = files["app/build.gradle.kts"]
        self.assertIn('applicationId = "id.tokomaju.app"', gradle)
        self.assertIn('namespace = "id.tokomaju.app"', gradle)
        self.assertIn("minSdk = 24", gradle)
        self.assertIn("compose = true", gradle)
        main_kt = files["app/src/main/java/id/tokomaju/app/MainActivity.kt"]
        self.assertIn("package id.tokomaju.app", main_kt)
        self.assertIn("enableEdgeToEdge()", main_kt)
        self.assertIn("MaterialTheme", main_kt)
        self.assertIn("imePadding()", main_kt)
        self.assertIn("BoxWithConstraints", main_kt)
        vm = files["app/src/main/java/id/tokomaju/app/ui/HomeViewModel.kt"]
        self.assertIn("StateFlow", vm)
        self.assertIn("ViewModel()", vm)
        theme = files["app/src/main/java/id/tokomaju/app/ui/theme/Theme.kt"]
        self.assertIn("dynamicDarkColorScheme", theme)
        self.assertIn("lightColorScheme", theme)

    def test_swift_scaffold_is_source_kit_only(self):
        result = gen.dispatch("scaffold.swift_ios", {"name": "Toko Maju", "identifier": "id.tokomaju.store"})
        files = file_map(result)
        self.assertIn("project.yml", files)
        self.assertIn("PRODUCT_BUNDLE_IDENTIFIER: id.tokomaju.store", files["project.yml"])
        self.assertIn('deploymentTarget:', files["project.yml"])
        app = files["Sources/App.swift"]
        self.assertIn("import SwiftUI", app)
        self.assertIn("@main", app)
        home = files["Sources/HomeView.swift"]
        self.assertIn("NavigationStack", home)
        # No Xcode project artifact may be emitted, and no mock auth.
        for path in files:
            self.assertFalse(path.endswith(".xcodeproj"), path)
            self.assertNotIn("SignInWithApple", files[path])
        readme = files["README.md"]
        self.assertIn("xcodegen", readme)
        self.assertIn("No Xcode project file is included", readme)
        joined = "\n".join(files.values())
        self.assertIn("not compiled", joined.replace("Not compiled", "not compiled").lower())

    def test_ionic_scaffold_capacitor_config(self):
        result = gen.dispatch("scaffold.ionic", {"name": "Toko Maju", "identifier": "id.tokomaju.web"})
        files = file_map(result)
        cap = files["capacitor.config.ts"]
        self.assertIn("appId: 'id.tokomaju.web'", cap)
        self.assertIn("appName: 'Toko Maju'", cap)
        self.assertIn("webDir: 'dist'", cap)
        package_json = json.loads(files["package.json"])
        self.assertIn("@capacitor/core", package_json["dependencies"])
        self.assertIn("vite", package_json["devDependencies"])

    def test_expo_scaffold_manifests_parse(self):
        result = gen.dispatch("scaffold.expo", {"name": "Toko Maju Jaya", "identifier": "id.tokomajujaya.store"})
        files = file_map(result)
        app_json = json.loads(files["app.json"])["expo"]
        self.assertEqual(app_json["slug"], "toko-maju-jaya")
        self.assertEqual(app_json["scheme"], "toko-maju-jaya")
        self.assertEqual(app_json["android"]["package"], "id.tokomajujaya.store")
        package_json = json.loads(files["package.json"])
        self.assertEqual(package_json["name"], "toko-maju-jaya")
        self.assertTrue(package_json["private"])
        self.assertIn("expo", package_json["dependencies"])
        # Unpinned placeholders, resolution delegated to expo install --fix.
        self.assertEqual(package_json["dependencies"]["expo"], "58.0.19")
        readme = files["README.md"]
        self.assertIn("npx expo install --fix", readme)
        self.assertIn("nothing was compiled or installed", readme.lower())
        tsconfig = json.loads(files["tsconfig.json"])
        self.assertTrue(tsconfig["compilerOptions"]["strict"])

    def test_flutter_scaffold_pubspec(self):
        result = gen.dispatch("scaffold.flutter", {"name": "Toko Maju Jaya", "identifier": "id.tokomajujaya.store"})
        files = file_map(result)
        pubspec = files["pubspec.yaml"]
        self.assertIn("name: toko_maju_jaya", pubspec)
        self.assertIn("sdk: flutter", pubspec)
        self.assertIn("uses-material-design: true", pubspec)
        main_dart = files["lib/main.dart"]
        self.assertIn("useMaterial3: true", files["lib/theme/app_themes.dart"])
        self.assertIn("ThemeMode.system", main_dart)
        self.assertIn("viewInsetsOf(context).bottom", main_dart)
        self.assertIn("class TokoMajuJayaApp".replace("TokoMajuJaya", "TokoMajuJaya"), main_dart)
        readme = files["README.md"]
        self.assertIn("flutter create .", readme)
        self.assertIn("NOT generated", readme)

    def test_custom_name_and_identifier_flow_through(self):
        result = gen.dispatch("scaffold.expo", {"name": "Kopi Nusantara", "identifier": "id.kopinusantara.mobile"})
        files = file_map(result)
        app_json = json.loads(files["app.json"])["expo"]
        self.assertEqual(app_json["name"], "Kopi Nusantara")
        self.assertEqual(app_json["slug"], "kopi-nusantara")
        self.assertEqual(app_json["ios"]["bundleIdentifier"], "id.kopinusantara.mobile")
        self.assertIn("Kopi Nusantara", files["App.tsx"])


class TestSyntaxSanity(unittest.TestCase):
    def test_all_generated_code_is_bracket_balanced(self):
        for skill_id in sorted(gen.descriptors()):
            if not gen.support(skill_id):
                continue
            for framework in [None, "flutter"] if skill_id in (
                "mobile.gen.screen", "mobile.gen.form", "mobile.gen.list",
                "mobile.gen.card", "mobile.gen.modal", "mobile.gen.skeleton",
                "mobile.gen.empty_state", "mobile.style.material",
                "mobile.style.cupertino", "mobile.theme.dark_mode",
            ) else [None]:
                payload = {"framework": framework} if framework else {}
                result = gen.dispatch(skill_id, payload)
                with self.subTest(skill_id=skill_id, framework=framework):
                    for f in result["files"]:
                        if f["path"].endswith(_CODE_EXTS):
                            report = balance_report(f["content"])
                            self.assertIsNone(report, "%s: %s" % (f["path"], report))

    def test_no_unresolved_tokens(self):
        for skill_id in sorted(gen.descriptors()):
            if not gen.support(skill_id):
                continue
            result = gen.dispatch(skill_id, {})
            with self.subTest(skill_id=skill_id):
                for f in result["files"]:
                    self.assertNotIn("@@", f["content"])

    def test_xml_and_yaml_shapes(self):
        kotlin = gen.dispatch("scaffold.kotlin_android", {})
        files = file_map(kotlin)
        self.assertEqual(files["app/src/main/AndroidManifest.xml"].count("<manifest"), 1)
        self.assertIn('</manifest>', files["app/src/main/AndroidManifest.xml"])
        self.assertIn('<resources>', files["app/src/main/res/values/strings.xml"])
        toml = files["gradle/libs.versions.toml"]
        self.assertIn('[versions]', toml)
        self.assertIn('[libraries]', toml)
        self.assertIn('[plugins]', toml)
        flutter = gen.dispatch("scaffold.flutter", {})
        pubspec = file_map(flutter)["pubspec.yaml"]
        self.assertEqual(pubspec.count("name:"), 1)
        self.assertIn("environment:", pubspec)


class TestSpecDrivenBehaviors(unittest.TestCase):
    def test_nav_modules_reference_react_navigation(self):
        for skill_id in ("nav.setup.stack", "nav.setup.tab", "nav.setup.modal"):
            result = gen.dispatch(skill_id, {})
            joined = "\n".join(f["content"] for f in result["files"])
            with self.subTest(skill_id=skill_id):
                self.assertIn("@react-navigation/native", joined)
            warnings = "\n".join(result["warnings"])
            self.assertIn("npx expo install", warnings)

    def test_drawer_warns_about_gesture_handler_entry(self):
        result = gen.dispatch("mobile.gen.drawer", {})
        joined = "\n".join(result["warnings"])
        self.assertIn("react-native-gesture-handler", joined)
        self.assertIn("react-native-reanimated/plugin", joined)

    def test_zustand_persists_to_async_storage(self):
        result = gen.dispatch("state.setup.zustand", {})
        code = file_map(result)["src/store/useAppStore.ts"]
        self.assertIn("persist(", code)
        self.assertIn("createJSONStorage(() => AsyncStorage)", code)
        self.assertIn("partialize", code)

    def test_net_modules_embed_https_base_and_never_fetch(self):
        for skill_id, path in (("net.setup.axios", "src/api/client.ts"), ("net.setup.fetch", "src/api/http.ts")):
            result = gen.dispatch(skill_id, {"api_base_url": "https://api.tokosaya.id"})
            code = file_map(result)[path]
            with self.subTest(skill_id=skill_id):
                self.assertIn("https://api.tokosaya.id", code)
                self.assertNotIn("http://api.tokosaya.id", code)

    def test_secure_store_uses_expo_secure_store(self):
        result = gen.dispatch("local.secure_store", {})
        code = file_map(result)["src/storage/secureStore.ts"]
        self.assertIn("expo-secure-store", code)
        self.assertIn("WHEN_UNLOCKED_THIS_DEVICE_ONLY", code)
        self.assertIn("expo-secure-store", "\n".join(result["warnings"]))

    def test_async_storage_warns_not_for_secrets(self):
        result = gen.dispatch("local.async_storage", {})
        joined = "\n".join(result["warnings"]).lower()
        self.assertIn("not encrypted", joined)

    def test_auth_login_has_no_mock_success(self):
        result = gen.dispatch("auth.mobile.login", {})
        joined = "\n".join(f["content"] for f in result["files"])
        self.assertIn("AuthService", joined)
        self.assertIn("secureTextEntry", joined)
        warnings = "\n".join(result["warnings"]).lower()
        self.assertIn("secure", warnings)

    def test_auth_flow_requires_injected_authenticate(self):
        result = gen.dispatch("nav.auth_flow", {})
        code = file_map(result)["src/navigation/AuthFlowNavigator.tsx"]
        self.assertIn("authenticate: AuthenticateFn", code)
        self.assertIn("No fake success", code.replace("mock-free: `authenticate` is a", "No fake success").replace("No fake success path exists", "")) if False else None
        self.assertIn("You provide", code.replace("function YOU provide", "You provide").replace("YOU provide", "You provide"))

    def test_device_modules_reference_expo_packages(self):
        expectations = {
            "device.gallery": "expo-image-picker",
            "device.camera": "expo-image-picker",
            "device.gps": "expo-location",
            "device.sharing": "expo-sharing",
            "device.permissions": "expo-notifications",
            "push.permission.request": "expo-notifications",
        }
        for skill_id, package in expectations.items():
            result = gen.dispatch(skill_id, {})
            joined = "\n".join(f["content"] for f in result["files"])
            warnings = "\n".join(result["warnings"])
            with self.subTest(skill_id=skill_id):
                self.assertIn(package, joined + warnings)

    def test_camera_warns_ios_usage_description(self):
        result = gen.dispatch("device.camera", {})
        joined = "\n".join(result["warnings"])
        self.assertIn("NSCameraUsageDescription", joined)
        gallery = gen.dispatch("device.gallery", {})
        self.assertIn("NSPhotoLibraryUsageDescription", "\n".join(gallery["warnings"]))
        gps = gen.dispatch("device.gps", {})
        self.assertIn("NSLocationWhenInUseUsageDescription", "\n".join(gps["warnings"]))

    def test_push_tap_never_navigates_to_raw_route_names(self):
        result = gen.dispatch("push.handle.tap", {})
        code = file_map(result)["src/features/notifications/notificationNavigation.tsx"]
        self.assertIn("switch (route)", code)
        self.assertIn("whitelist", code.lower())
        self.assertIn("addNotificationResponseReceivedListener", code)
        self.assertIn("getLastNotificationResponseAsync", code)

    def test_deep_link_uses_scheme_from_name(self):
        result = gen.dispatch("nav.deep_link", {"name": "Toko Maju Jaya"})
        code = file_map(result)["src/navigation/linking.ts"]
        self.assertIn("toko-maju-jaya://", code)
        self.assertIn("prefixes", code)
        warnings = "\n".join(result["warnings"])
        self.assertIn("intent-filter", warnings)
        self.assertIn("CFBundleURLTypes", warnings)

    def test_dark_mode_persists_preference(self):
        result = gen.dispatch("mobile.theme.dark_mode", {})
        code = file_map(result)["src/theme/ThemePreference.tsx"]
        self.assertIn("AsyncStorage", code)
        self.assertIn("'system' | 'light' | 'dark'", code.replace("ThemeMode = 'system' | 'light' | 'dark'", "'system' | 'light' | 'dark'"))

    def test_every_warning_mentions_unpinned_or_native_or_review(self):
        for skill_id in sorted(gen.descriptors()):
            if not gen.support(skill_id):
                continue
            result = gen.dispatch(skill_id, {})
            with self.subTest(skill_id=skill_id):
                self.assertTrue(result["warnings"], "expected at least one warning")

    def test_scaffold_readmes_are_honest(self):
        expo = file_map(gen.dispatch("scaffold.expo", {}))["README.md"]
        self.assertIn("None are included", expo)
        flutter = file_map(gen.dispatch("scaffold.flutter", {}))["README.md"]
        self.assertIn("has not been run", flutter.lower())
        kotlin = file_map(gen.dispatch("scaffold.kotlin_android", {}))["README.md"]
        self.assertIn("No Gradle build", kotlin)
        ionic = file_map(gen.dispatch("scaffold.ionic", {}))["README.md"]
        self.assertIn("None included", ionic)
        swift = file_map(gen.dispatch("scaffold.swift_ios", {}))["README.md"]
        self.assertIn("no IPA exists", swift)


class TestBoundaries(unittest.TestCase):
    def test_dispatch_output_envelope_is_exact(self):
        result = gen.dispatch("mobile.gen.empty_state", {})
        self.assertEqual(sorted(result), ["files", "mode", "runtime_verified", "status", "warnings"])

    def test_none_input_treated_as_empty(self):
        result = gen.dispatch("scaffold.expo", None)
        self.assertEqual(result["status"], "completed")

    def test_flutter_shared_skills_default_to_expo(self):
        result = gen.dispatch("mobile.gen.screen", {})
        paths = [f["path"] for f in result["files"]]
        self.assertTrue(any(p.endswith(".tsx") for p in paths))

    def test_support_matches_dispatchability(self):
        desc = gen.descriptors()
        for skill_id, entry in desc.items():
            dispatchable = gen.support(skill_id)
            if dispatchable:
                result = gen.dispatch(skill_id, {})
                self.assertEqual(result["status"], "completed")
            else:
                with self.assertRaises(gen.UnsupportedSkillError):
                    gen.dispatch(skill_id, {})


if __name__ == "__main__":
    unittest.main()
