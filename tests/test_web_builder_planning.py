"""Tests for backend/web_builder/planning.py (pure planning module, worker scope).

Run from the repository root:
    python3 -m unittest discover -s tests -p "test_web_builder_planning.py" -v
"""

import json
import os
import re
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from backend.web_builder import planning  # noqa: E402

EXPECTED_OWNED_NUMBERS = set(range(1, 47)) | set(range(134, 152))
EXPECTED_ROOT_READERS = {
    "spec.read.markdown",
    "spec.read.txt",
    "spec.read.json",
    "spec.read.notation",
    "spec.read.obsidian",
    "spec.read.voice",
}
STACK_IDS = [
    "stack.analyze.requirements",
    "stack.select.frontend",
    "stack.select.backend",
    "stack.select.database",
    "stack.select.hosting",
    "stack.select.auth",
    "stack.select.payment",
    "stack.select.storage",
    "stack.select.cache",
    "stack.select.search",
    "stack.select.monitoring",
    "stack.recommend.combination",
]
DB_IDS = [f"db.engine.{e}" for e in planning.DB_ENGINE_PROFILES] + ["db.engine.match"]

SPEC_SAMPLE = planning.SPECIAL_DEFAULT_ARGS["text"]
SPEC_NEGATION = (
    "# Spec: Klinik Sehat\n\n"
    "## Deskripsi\nWebsite booking klinik.\n\n"
    "## Fitur\n- Booking appointment\n- User auth\n- Tanpa payment gateway\n"
    "- No blog\n- Katalog layanan\n"
)


class TestScope(unittest.TestCase):
    def test_owned_numbers_match_scope(self):
        numbers = {item["number"] for item in planning.owned_skills()}
        self.assertEqual(numbers, EXPECTED_OWNED_NUMBERS)

    def test_64_ids_total(self):
        self.assertEqual(len(planning.CATALOG_NUMBERS), 64)

    def test_catalog_source_alignment(self):
        with open(os.path.join(BASE, "backend", "web_builder", "catalog_source.json"), encoding="utf-8") as fh:
            catalog = json.load(fh)
        expected = {
            entry["number"]: entry["id"]
            for entry in catalog["skills"]
            if entry["number"] in EXPECTED_OWNED_NUMBERS
        }
        self.assertEqual(planning.CATALOG_NUMBERS, expected)

    def test_root_readers_marked(self):
        handled = {s["id"] for s in planning.owned_skills() if s["handled_by"] == "root"}
        self.assertEqual(handled, EXPECTED_ROOT_READERS)

    def test_handlers_cover_all_non_reader_ids(self):
        for skill_id in planning.CATALOG_NUMBERS.values():
            if skill_id in EXPECTED_ROOT_READERS:
                continue
            self.assertIn(skill_id, planning.HANDLERS, skill_id)


class TestSchema(unittest.TestCase):
    def test_schema_for_every_owned_id(self):
        for skill_id in planning.CATALOG_NUMBERS.values():
            sch = planning.schema(skill_id)
            self.assertEqual(sch["type"], "object", skill_id)
            self.assertIsInstance(sch["properties"], dict, skill_id)
            self.assertTrue(sch["required"], skill_id)
            for req in sch["required"]:
                self.assertIn(req, sch["properties"], skill_id)

    def test_schema_requires_reasonable_params(self):
        self.assertIn("text", planning.schema("spec.parse.requirements")["required"])
        self.assertIn("color", planning.schema("design.color.palette")["required"])
        self.assertIn("project_type", planning.schema("stack.select.frontend")["required"])
        self.assertIn("name", planning.schema("design.logo")["required"])
        self.assertIn("base_url", planning.schema("seo.gen.sitemap")["required"])

    def test_schema_reader_contract(self):
        for skill_id in EXPECTED_ROOT_READERS:
            sch = planning.schema(skill_id)
            self.assertEqual(sch["required"], ["text"], skill_id)
            self.assertIn("root", sch["description"].lower(), skill_id)

    def test_schema_unknown_raises(self):
        with self.assertRaises(KeyError):
            planning.schema("scaffold.nextjs")
        with self.assertRaises(KeyError):
            planning.schema("deploy.vercel")
        with self.assertRaises(KeyError):
            planning.schema("nonexistent")


def build_demo_args(skill_id):
    return planning._demo_args_for_schema(planning.schema(skill_id))


class TestDispatchEnvelope(unittest.TestCase):
    def test_all_owned_ids_dispatch(self):
        for number, skill_id in sorted(planning.CATALOG_NUMBERS.items()):
            args = build_demo_args(skill_id)
            result = planning.dispatch(skill_id, args)
            if skill_id in EXPECTED_ROOT_READERS:
                self.assertEqual(result["status"], "root_reader", skill_id)
                self.assertEqual(result["data"]["marker"], "root_reader", skill_id)
            else:
                self.assertEqual(result["status"], "completed", skill_id)
                self.assertIn(
                    result["execution_kind"],
                    ("analysis", "artifact_generation"),
                    skill_id,
                )
            self.assertIn("data", result, skill_id)
            self.assertIsInstance(result["data"], dict, skill_id)
            self.assertTrue(result["data"], skill_id)
            self.assertIsInstance(result["writes"], list, skill_id)
            self.assertIsInstance(result["limitations"], list, skill_id)
            self.assertTrue(result["limitations"], skill_id)
            self.assertIn(result["execution_kind"], ("analysis", "artifact_generation"), skill_id)

    def test_execution_kind_matches_contract(self):
        for skill_id in planning.HANDLERS:
            result = planning.dispatch(skill_id, build_demo_args(skill_id))
            expected = "artifact_generation" if skill_id in planning.ARTIFACT_IDS else "analysis"
            self.assertEqual(result["execution_kind"], expected, skill_id)

    def test_not_owned_statuses(self):
        for skill_id in ("scaffold.nextjs", "deploy.vercel", "ssh.connect", "monitor.uptime", "bogus.id"):
            result = planning.dispatch(skill_id, {})
            self.assertEqual(result["status"], "not_owned", skill_id)

    def test_missing_required_raises(self):
        with self.assertRaises(ValueError):
            planning.dispatch("design.logo", {})
        with self.assertRaises(ValueError):
            planning.dispatch("spec.parse.requirements", {})
        with self.assertRaises(ValueError):
            planning.dispatch("stack.select.frontend", {})

    def test_bad_enum_raises(self):
        with self.assertRaises(ValueError):
            planning.dispatch("stack.select.frontend", {"project_type": "spaceship"})
        with self.assertRaises(ValueError):
            planning.dispatch("design.color.palette", {"color": "not-a-color"})

    def test_args_must_be_dict(self):
        with self.assertRaises(ValueError):
            planning.dispatch("design.logo", None)


class TestWritesSafety(unittest.TestCase):
    def test_write_paths_relative_and_safe(self):
        for skill_id in planning.HANDLERS:
            result = planning.dispatch(skill_id, build_demo_args(skill_id))
            for write in result["writes"]:
                path = write["path"]
                self.assertFalse(os.path.isabs(path), f"{skill_id}: {path}")
                self.assertNotIn("..", path, f"{skill_id}: {path}")
                self.assertIsInstance(write["content"], str, f"{skill_id}: {path}")
                self.assertTrue(write["content"].strip(), f"{skill_id}: {path}")

    def test_demo_args_cover_writes(self):
        with_writes = [
            skill_id
            for skill_id in planning.HANDLERS
            if planning.dispatch(skill_id, build_demo_args(skill_id))["writes"]
        ]
        self.assertGreaterEqual(len(with_writes), 30)


class TestSpecParsing(unittest.TestCase):
    def test_features_and_negations(self):
        result = planning.dispatch("spec.parse.features", {"text": SPEC_NEGATION})
        data = result["data"]
        keys = set(data["feature_keys"])
        self.assertIn("booking", keys)
        self.assertIn("auth", keys)
        self.assertNotIn("payments", keys)
        self.assertNotIn("blog", keys)
        self.assertIn("payments", set(data["excluded_keys"]))
        self.assertIn("blog", set(data["excluded_keys"]))

    def test_requirements_include_exclusion_kind(self):
        result = planning.dispatch("spec.parse.requirements", {"text": SPEC_NEGATION})
        data = result["data"]
        kinds = {req["kind"] for req in data["requirements"]}
        self.assertIn("exclusion", kinds)
        self.assertEqual(data["project_type"], "booking")
        self.assertGreater(data["exclusion_count"], 0)

    def test_constraints_parse(self):
        result = planning.dispatch("spec.parse.constraints", {"text": SPEC_SAMPLE})
        data = result["data"]
        self.assertIn("mobile_first", data["markers"])
        self.assertIn("seo", data["markers"])
        self.assertTrue(data["constraints"]["budget_raw"])

    def test_design_pref_parse(self):
        result = planning.dispatch("spec.parse.design_pref", {"text": SPEC_SAMPLE})
        data = result["data"]
        self.assertIn("#2563EB", [c.split("=")[-1] for c in data["colors"] if "=" in c] + [c for c in data["colors"] if c.startswith("#")])
        self.assertIn("modern", data["tones"])
        self.assertIn("trustworthy", data["tones"])
        self.assertTrue(data["mobile_first"])

    def test_clarify_flags_payment_provider_gap(self):
        text = SPEC_SAMPLE.replace("Payment via Midtrans", "Payment gateway")
        result = planning.dispatch("spec.clarify", {"text": text})
        topics = {q["topic"] for q in result["data"]["questions"]}
        self.assertIn("payment provider", topics)

    def test_clarify_reports_exclusions(self):
        result = planning.dispatch("spec.clarify", {"text": SPEC_NEGATION})
        topics = {q["topic"] for q in result["data"]["questions"]}
        self.assertIn("confirmed exclusions", topics)

    def test_user_stories_exclude_negations(self):
        result = planning.dispatch("spec.parse.user_stories", {"text": SPEC_NEGATION, "language": "id"})
        features = {s["feature"] for s in result["data"]["stories"]}
        self.assertNotIn("payments", features)
        self.assertNotIn("blog", features)
        self.assertTrue(all(s["story"].startswith("Sebagai") for s in result["data"]["stories"]))

    def test_negation_drives_payment_selector(self):
        result = planning.dispatch(
            "stack.select.payment",
            {"project_type": "booking", "features": ["booking", "payments"], "excluded_features": ["payments"]},
        )
        self.assertFalse(result["data"]["included"])
        self.assertIn("excludes", result["data"]["reason"])

    def test_absent_feature_not_added(self):
        result = planning.dispatch("stack.select.payment", {"project_type": "landing", "features": ["portfolio"]})
        self.assertFalse(result["data"]["included"])
        self.assertIn("not request", result["data"]["reason"])


class TestStackSelectors(unittest.TestCase):
    def test_ecommerce_full_stack(self):
        args = {"project_type": "ecommerce", "features": ["catalog", "cart", "checkout", "payments", "auth", "admin"], "text": SPEC_SAMPLE}
        frontend = planning.dispatch("stack.select.frontend", args)["data"]
        backend = planning.dispatch("stack.select.backend", args)["data"]
        hosting = planning.dispatch("stack.select.hosting", args)["data"]
        payment = planning.dispatch("stack.select.payment", args)["data"]
        self.assertIn("Next.js", frontend["choice"])
        self.assertTrue(frontend["reasons"])
        self.assertTrue(backend["choice"])
        self.assertTrue(hosting["choice"])
        self.assertTrue(payment["included"])
        self.assertEqual(payment["choice"], "Midtrans")
        self.assertIn("webhook", " ".join(payment["mandatory_controls"]))

    def test_static_landing_gets_no_backend(self):
        args = {"project_type": "landing", "features": ["portfolio"]}
        backend = planning.dispatch("stack.select.backend", args)["data"]["choice"]
        self.assertIn("none", backend.lower())
        hosting = planning.dispatch("stack.select.hosting", args)["data"]["choice"]
        self.assertIn("static", hosting.lower())

    def test_realtime_pushes_vps(self):
        args = {"project_type": "social", "features": ["chat", "realtime", "auth"], "scale": "medium"}
        hosting = planning.dispatch("stack.select.hosting", args)["data"]
        self.assertIn("VPS", hosting["choice"])
        self.assertTrue(any("realtime" in r.lower() or "realtime" in r for r in hosting["reasons"]))

    def test_preferences_are_scoped_and_deterministic(self):
        args = {"project_type": "saas", "features": ["auth"], "preferences": {"frontend": "vue", "language": "python"}}
        frontend = planning.dispatch("stack.select.frontend", args)["data"]["choice"]
        backend = planning.dispatch("stack.select.backend", args)["data"]["choice"]
        self.assertIn("Nuxt", frontend)
        self.assertIn("FastAPI", backend)

    def test_auth_excluded_stays_excluded(self):
        result = planning.dispatch("stack.select.auth", {"project_type": "landing", "features": ["auth"], "excluded_features": ["auth"]})
        self.assertFalse(result["data"]["included"])

    def test_no_cost_and_no_pinned_year_versions(self):
        year_re = re.compile(r"\b(19\d\d|20[0-3]\d)\b")
        for skill_id in STACK_IDS:
            result = planning.dispatch(skill_id, build_demo_args(skill_id))
            blob = json.dumps(result["data"], ensure_ascii=False)
            for write in result["writes"]:
                blob += write["content"]
            self.assertNotIn("$", blob, skill_id)
            self.assertIsNone(year_re.search(blob), skill_id)

    def test_combination_document(self):
        result = planning.dispatch(
            "stack.recommend.combination",
            {"name": "Toko Maju Jaya", "project_type": "ecommerce", "features": ["catalog", "cart", "checkout", "payments", "auth"], "text": SPEC_SAMPLE},
        )
        self.assertEqual(result["execution_kind"], "artifact_generation")
        doc = result["writes"][0]["content"]
        self.assertIn("Stack recommendation: Toko Maju Jaya", doc)
        self.assertIn("Midtrans", doc)


class TestDbEngine(unittest.TestCase):
    def test_match_prefers_relational_for_ecommerce(self):
        result = planning.dispatch(
            "db.engine.match",
            {"project_type": "ecommerce", "features": ["catalog", "cart", "checkout", "payments", "orders"], "scale": "medium"},
        )
        self.assertEqual(result["data"]["recommendation"]["engine"], "postgresql")

    def test_match_explicit_preference_wins(self):
        result = planning.dispatch(
            "db.engine.match",
            {"features": ["catalog"], "preference": "sqlite"},
        )
        self.assertEqual(result["data"]["recommendation"]["engine"], "sqlite")
        self.assertIn("preference", result["data"]["basis"])

    def test_engine_plans_distinct(self):
        seen = set()
        for engine in planning.DB_ENGINE_PROFILES:
            result = planning.dispatch(f"db.engine.{engine}", {"name": "Toko Maju Jaya", "features": ["catalog", "auth"]})
            self.assertEqual(result["status"], "completed")
            content = result["writes"][0]["content"]
            self.assertIn(planning.DB_ENGINE_PROFILES[engine]["label"], content)
            seen.add(content)
        self.assertEqual(len(seen), len(planning.DB_ENGINE_PROFILES))

    def test_sqlite_caution_on_realtime(self):
        result = planning.dispatch(
            "db.engine.match",
            {"features": ["chat", "realtime"], "preference": "sqlite"},
        )
        self.assertTrue(result["data"]["caution"])


class TestArchitectureArtifacts(unittest.TestCase):
    def test_schema_ddl_contains_derived_tables(self):
        result = planning.dispatch(
            "arch.design.database_schema",
            {"name": "Toko Maju Jaya", "project_type": "ecommerce", "features": ["catalog", "cart", "orders", "payments", "auth"]},
        )
        sql = next(w for w in result["writes"] if w["path"] == "db/schema.sql")["content"]
        for table in ("users", "products", "carts", "orders", "order_items", "payments"):
            self.assertIn(f"CREATE TABLE {table}", sql)
        self.assertIn("REFERENCES", sql)
        mermaid = next(w for w in result["writes"] if w["path"] == "docs/db/schema.md")["content"]
        self.assertIn("erDiagram", mermaid)

    def test_schema_respects_entities_arg(self):
        result = planning.dispatch(
            "arch.design.database_schema",
            {"name": "Rumah Sakit", "entities": [{"name": "pasien", "fields": ["nama", "umur:int"]}]},
        )
        sql = result["writes"][0]["content"]
        self.assertIn("CREATE TABLE pasien", sql)
        self.assertIn("umur INTEGER", sql)
        self.assertNotIn("CREATE TABLE products", sql)

    def test_api_openapi_and_auth_endpoints(self):
        result = planning.dispatch(
            "arch.design.api",
            {"name": "Toko Maju Jaya", "project_type": "ecommerce", "features": ["catalog", "auth", "payments"]},
        )
        openapi = json.loads(next(w for w in result["writes"] if w["path"].endswith("openapi.json"))["content"])
        self.assertEqual(openapi["openapi"], "3.1.0")
        self.assertIn("/auth/login", openapi["paths"])
        self.assertIn("/webhooks/payments", openapi["paths"])
        self.assertIn("/products/{id}", openapi["paths"])

    def test_component_tree_framework_variants(self):
        for framework, marker in (("react", ".tsx"), ("vue", ".vue"), ("svelte", ".svelte")):
            result = planning.dispatch(
                "arch.design.component_tree",
                {"name": "Toko Maju Jaya", "project_type": "ecommerce", "features": ["catalog", "auth", "admin"], "framework": framework},
            )
            doc = result["writes"][0]["content"]
            self.assertIn(marker, doc)
            self.assertIn("CatalogPage", doc)

    def test_folder_structure_templates(self):
        result_next = planning.dispatch("arch.design.folder_structure", {"name": "App", "framework": "next"})
        result_fast = planning.dispatch("arch.design.folder_structure", {"name": "App", "framework": "fastapi"})
        self.assertNotEqual(result_next["writes"][0]["content"], result_fast["writes"][0]["content"])
        self.assertIn("app/", result_next["writes"][0]["content"])
        self.assertIn("main.py", result_fast["writes"][0]["content"])

    def test_data_flow_and_system_diagrams(self):
        for skill_id, marker in (("arch.design.data_flow", "flowchart"), ("arch.design.system", "erDiagram")):
            result = planning.dispatch(skill_id, {"name": "Toko Maju Jaya", "project_type": "ecommerce", "features": ["catalog", "payments", "auth"]})
            doc = result["writes"][0]["content"]
            self.assertIn("```mermaid", doc)
            if skill_id == "arch.design.data_flow":
                self.assertIn("flowchart", doc)

    def test_auth_flow_variants(self):
        for strategy, marker in (("session_cookie", "session"), ("jwt", "refresh"), ("oauth", "oauth")):
            result = planning.dispatch("arch.design.auth_flow", {"name": "App", "strategy": strategy})
            doc = result["writes"][0]["content"]
            self.assertIn("sequenceDiagram", doc)
            self.assertIn(strategy, doc)

    def test_deployment_and_scaling_and_security(self):
        dep = planning.dispatch("arch.design.deployment", {"name": "App", "project_type": "saas", "features": ["auth"]})
        self.assertIn("staging", dep["writes"][0]["content"])
        scaling = planning.dispatch("arch.design.scaling", {"name": "App", "scale": "large"})
        self.assertIn("Stage", scaling["writes"][0]["content"])
        security = planning.dispatch("arch.design.security", {"name": "App", "project_type": "ecommerce", "features": ["payments", "upload", "auth"]})
        self.assertIn("webhook", security["writes"][0]["content"])
        self.assertIn("Uploads", security["writes"][0]["content"])

    def test_diagram_generate_kinds_and_formats(self):
        for kind in ("system", "data_flow", "er", "auth_sequence", "deployment", "component"):
            result = planning.dispatch(
                "arch.diagram.generate",
                {"name": "Toko Maju Jaya", "kind": kind, "features": ["catalog", "auth"], "project_type": "ecommerce"},
            )
            self.assertTrue(result["writes"][0]["content"].strip(), kind)
        mermaid = planning.dispatch("arch.diagram.generate", {"name": "App", "kind": "er", "entities": [{"name": "products"}]})
        plantuml = planning.dispatch("arch.diagram.generate", {"name": "App", "kind": "er", "format": "plantuml", "entities": [{"name": "products"}]})
        self.assertIn("erDiagram", mermaid["data"]["rendered"])
        self.assertIn("@startuml", plantuml["data"]["rendered"])

    def test_adr_generation(self):
        result = planning.dispatch(
            "arch.document.generate",
            {
                "title": "Use PostgreSQL for transactional data",
                "context": "Order and payment data need ACID guarantees.",
                "decision": "Use PostgreSQL 16 (latest stable at build time).",
                "options": ["PostgreSQL", "MySQL", "MongoDB"],
                "consequences": "Team needs migration discipline.",
                "number": 3,
            },
        )
        path = result["writes"][0]["path"]
        self.assertEqual(path, "docs/adr/ADR-0003-use-postgresql-for-transactional-data.md")
        content = result["writes"][0]["content"]
        self.assertIn("# ADR-0003", content)
        self.assertIn("## Decision", content)
        self.assertIn("- PostgreSQL", content)


class TestDesignArtifacts(unittest.TestCase):
    def test_palette_math_deterministic_and_contrast(self):
        args = {"color": "#2563EB", "name": "Toko Maju Jaya"}
        first = planning.dispatch("design.color.palette", args)
        second = planning.dispatch("design.color.palette", args)
        self.assertEqual(json.dumps(first["data"], sort_keys=True), json.dumps(second["data"], sort_keys=True))
        self.assertAlmostEqual(first["data"]["palette"]["contrast"]["primary_vs_white"] + 0, 5.17, delta=1.5)
        tokens = json.loads(first["writes"][0]["content"])
        self.assertIn("primary", tokens["palette"])
        self.assertIn("semantic", tokens["palette"])

    def test_palette_accepts_color_names(self):
        result = planning.dispatch("design.color.palette", {"color": "biru"})
        self.assertEqual(result["data"]["palette"]["source"], "#2563EB")

    def test_typography_styles_distinct(self):
        outputs = set()
        for style in planning.TYPOGRAPHY_PAIRS:
            result = planning.dispatch("design.typography", {"style": style})
            outputs.add(result["writes"][0]["content"])
        self.assertEqual(len(outputs), len(planning.TYPOGRAPHY_PAIRS))

    def test_grid_presets(self):
        result = planning.dispatch("design.layout.grid", {"preset": "dashboard"})
        self.assertIn("--sidebar-width: 260px;", result["data"]["css"])
        self.assertIn("--bp-lg: 1024px;", result["data"]["css"])

    def test_icon_set_rules(self):
        result = planning.dispatch("design.icon.set", {"framework": "next", "tone": "playful"})
        self.assertEqual(result["data"]["choice"], "phosphor")
        self.assertEqual(result["data"]["license"], "MIT")

    def test_illustration_deterministic_svg(self):
        args = {"name": "Toko Maju Jaya", "style": "blobs"}
        a = planning.dispatch("design.illustration", args)
        b = planning.dispatch("design.illustration", args)
        self.assertEqual(a["writes"][0]["content"], b["writes"][0]["content"])
        self.assertIn("<svg", a["writes"][0]["content"])
        self.assertIn('aria-label', a["writes"][0]["content"])

    def test_logo_escapes_name(self):
        result = planning.dispatch("design.logo", {"name": 'A<"x">& B', "style": "wordmark"})
        svg = result["writes"][0]["content"]
        self.assertIn("A&lt;\"x\"&gt;&amp; B", svg)
        self.assertNotIn("<\"x\">", svg)

    def test_favicon_svg_and_limitation(self):
        result = planning.dispatch("design.favicon", {"name": "Toko Maju Jaya", "color": "#16A34A"})
        self.assertTrue(any("raster" in lim.lower() for lim in result["limitations"]))
        svg = result["writes"][0]["content"]
        self.assertIn("<svg", svg)
        self.assertIn("TM", svg)

    def test_og_image_1200x630_and_wrapping(self):
        result = planning.dispatch(
            "design.og_image",
            {"title": "Toko Maju Jaya elektronik terpercaya sejak lama untuk semua", "subtitle": "Promo aman", "brand": "Toko Maju Jaya"},
        )
        svg = result["writes"][0]["content"]
        self.assertIn('viewBox="0 0 1200 630"', svg)
        self.assertLessEqual(len(result["data"]["title_lines"]), 3)

    def test_theme_light_and_dark_css(self):
        result = planning.dispatch("design.theme", {"color": "#DC2626", "name": "App", "dark_mode": True})
        css = result["writes"][0]["content"]
        self.assertIn(":root", css)
        self.assertIn('[data-theme="dark"]', css)
        tokens = result["data"]["tokens"]
        self.assertIn("dark", tokens)

    def test_prototype_html_sections(self):
        result = planning.dispatch(
            "design.prototype",
            {"name": "Toko Maju Jaya", "features": ["catalog", "cart", "checkout"], "language": "id"},
        )
        html_doc = result["writes"][0]["content"]
        self.assertIn("<!DOCTYPE html>", html_doc)
        self.assertIn('href="#catalog"', html_doc)
        self.assertIn("Toko Maju Jaya", html_doc)
        self.assertIn("Placeholder", html_doc)


class TestContentAndSeo(unittest.TestCase):
    def test_placeholder_content_by_type(self):
        landing = planning.dispatch("content.gen.placeholder", {"name": "Acme", "project_type": "landing", "language": "en"})
        shop = planning.dispatch("content.gen.placeholder", {"name": "Acme", "project_type": "ecommerce", "language": "en"})
        self.assertNotEqual(landing["writes"][0]["content"], shop["writes"][0]["content"])
        self.assertIn("[Placeholder", shop["writes"][0]["content"])
        self.assertIn("product_card", shop["writes"][0]["content"])

    def test_copy_variants(self):
        result = planning.dispatch("content.gen.copy", {"name": "Toko Maju Jaya", "language": "id", "keywords": ["elektronik"]})
        data = result["data"]
        self.assertEqual(len(data["headlines"]), 3)
        self.assertIn("elektronik", data["headlines"][0]["text"])
        self.assertIn("review", " ".join(data["policy"].split()).lower())

    def test_meta_escaping_and_lengths(self):
        result = planning.dispatch(
            "seo.gen.meta",
            {"name": 'Toko <script>alert("x")</script>', "url": "https://contoh.test/", "description": 'Deskripsi "dengan" tanda & <br>'},
        )
        html_doc = result["writes"][0]["content"]
        self.assertNotIn("<script>", html_doc)
        self.assertIn("&lt;script&gt;", html_doc)
        self.assertIn("&amp;", html_doc)
        self.assertLessEqual(len(result["data"]["title"]), 60)
        self.assertLessEqual(len(result["data"]["description"]), 155)

    def test_sitemap_xml_shape_and_escaping(self):
        result = planning.dispatch(
            "seo.gen.sitemap",
            {"base_url": "https://contoh.test/", "routes": ["/", "/produk", "/produk", "/blog?a=1&b=2"]},
        )
        xml = result["writes"][0]["content"]
        self.assertIn('<?xml version="1.0"', xml)
        self.assertIn("urlset", xml)
        self.assertIn("&amp;b=2", xml)
        self.assertEqual(xml.count("<loc>"), 3)

    def test_sitemap_bad_url_raises(self):
        with self.assertRaises(ValueError):
            planning.dispatch("seo.gen.sitemap", {"base_url": "not-a-url"})

    def test_robots_disallows_admin_for_admin_feature(self):
        result = planning.dispatch(
            "seo.gen.robots",
            {"base_url": "https://contoh.test", "features": ["admin", "cart", "checkout"], "disallow": ["/private/"]},
        )
        txt = result["writes"][0]["content"]
        self.assertIn("Disallow: /admin/", txt)
        self.assertIn("Disallow: /api/", txt)
        self.assertIn("Sitemap: https://contoh.test/sitemap.xml", txt)

    def test_schema_jsonld_no_bogus_facts(self):
        result = planning.dispatch(
            "seo.gen.schema",
            {"name": "Toko Maju Jaya", "url": "https://toko.test", "project_type": "ecommerce"},
        )
        data = result["data"]
        payload = data["payload"]
        for forbidden in ("aggregateRating", "address", "telephone", "priceRange", "review"):
            self.assertNotIn(forbidden, payload)
        self.assertIn("address", " ".join(data["intentionally_omitted"]))
        html_doc = result["writes"][0]["content"]
        self.assertIn('type="application/ld+json"', html_doc)
        payload_blob = html_doc.replace("</script>", "").replace('<script type="application/ld+json">', "")
        self.assertNotIn("</", payload_blob)

    def test_og_tags_escaping(self):
        result = planning.dispatch(
            "seo.gen.og",
            {"url": "https://contoh.test/halaman?a=1&b=2", "title": 'Judul "Spesial" & <Hebat>', "site_name": "Contoh", "image_url": "https://contoh.test/og.png"},
        )
        doc = result["writes"][0]["content"]
        self.assertIn("&amp;", doc)
        self.assertIn("&quot;", doc)
        self.assertIn("summary_large_image", doc)

    def test_performance_plan_honest(self):
        result = planning.dispatch(
            "seo.optimize.performance",
            {"project_type": "ecommerce", "features": ["catalog", "dashboard"], "constraints": {"mobile_first": True}},
        )
        metrics = {item["metric"] for item in result["data"]["items"]}
        self.assertTrue({"LCP", "CLS", "INP"} <= metrics)
        blob = result["writes"][0]["content"]
        self.assertIn("measure", blob.lower())
        self.assertNotIn("Lighthouse score 95", blob)


class TestDeterminismAndPurity(unittest.TestCase):
    def test_all_handlers_deterministic(self):
        for skill_id in planning.HANDLERS:
            args = build_demo_args(skill_id)
            first = planning.dispatch(skill_id, json.loads(json.dumps(args)))
            second = planning.dispatch(skill_id, json.loads(json.dumps(args)))
            self.assertEqual(
                json.dumps(first, sort_keys=True, ensure_ascii=False),
                json.dumps(second, sort_keys=True, ensure_ascii=False),
                skill_id,
            )

    def test_outputs_are_distinct_per_id(self):
        blobs = {}
        for skill_id in planning.HANDLERS:
            result = planning.dispatch(skill_id, build_demo_args(skill_id))
            blobs[skill_id] = json.dumps(result["data"], sort_keys=True, ensure_ascii=False) + "".join(w["content"] for w in result["writes"])
        self.assertEqual(len(set(blobs.values())), len(blobs))

    def test_module_has_no_io_imports(self):
        import ast

        with open(planning.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        forbidden_modules = {"os", "subprocess", "socket", "requests", "paramiko", "shutil", "pathlib", "urllib.request", "asyncio"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    self.assertNotIn(root, forbidden_modules, alias.name)
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                self.assertNotIn(root, forbidden_modules, node.module)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, {"open", "exec", "eval", "compile", "__import__"}, node.func.id)

    def test_no_status_unavailable_for_owned(self):
        for number, skill_id in sorted(planning.CATALOG_NUMBERS.items()):
            result = planning.dispatch(skill_id, build_demo_args(skill_id))
            self.assertNotEqual(result["status"], "unavailable", skill_id)


class TestContractNarrative(unittest.TestCase):
    def test_full_workflow_negation_story(self):
        parsed = planning.dispatch("spec.parse.requirements", {"text": SPEC_NEGATION})["data"]
        self.assertNotIn("payments", parsed["features"])
        self.assertIn("payments", parsed["excluded_features"])
        payment = planning.dispatch(
            "stack.select.payment",
            {"project_type": "booking", "features": parsed["features"], "excluded_features": parsed["excluded_features"]},
        )["data"]
        self.assertFalse(payment["included"])
        schema_result = planning.dispatch(
            "arch.design.database_schema",
            {"name": "Klinik Sehat", "project_type": "booking", "features": parsed["features"], "excluded_features": parsed["excluded_features"]},
        )
        sql = next(w for w in schema_result["writes"] if w["path"] == "db/schema.sql")["content"]
        self.assertNotIn("CREATE TABLE payments", sql)
        self.assertIn("CREATE TABLE bookings", sql)

    def test_ambiguities_are_explicit_not_guessed(self):
        text = "Website untuk toko.\n- Katalog produk\n- Payment\n"
        data = planning.dispatch("spec.parse.requirements", {"text": text})["data"]
        topics = {a["topic"] for a in data["ambiguities"]}
        self.assertIn("payment provider", topics)


if __name__ == "__main__":
    unittest.main()
