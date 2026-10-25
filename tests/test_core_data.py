"""Stdlib unittest suite for backend/core_engine/data_tools.py.

Covers all six tool IDs, strict schema alignment (required/unknown fields),
CSV quoting / formula injection / duplicate headers, null + mixed numeric
analysis, extreme aggregates, rejection of host-path reads / eval-style
input, and data-only visualization specs.
"""

import copy
import inspect
import json
import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "backend", "core_engine"))

import data_tools  # noqa: E402

dispatch = data_tools.dispatch
descriptors = data_tools.descriptors

EXPECTED_IDS = {
    "data.read_csv", "data.read_json", "data.transform",
    "data.analyze", "data.visualize", "data.export",
}


def ok(result):
    assert result["status"] == "completed", result
    return result


def err(result):
    assert result["status"] == "error", result
    return result


def rows(*records):
    return [dict(record) for record in records]


class TestDescriptors(unittest.TestCase):
    def test_exactly_six_ids(self):
        self.assertEqual(set(descriptors()), EXPECTED_IDS)

    def test_descriptor_shape(self):
        for tool_id, desc in descriptors().items():
            self.assertTrue(desc["description"].strip(), tool_id)
            schema = desc["input_schema"]
            self.assertEqual(schema["type"], "object")
            self.assertFalse(schema["additionalProperties"], tool_id)
            self.assertIsInstance(schema["required"], list)
            self.assertTrue(schema["required"], tool_id)

    def test_unknown_tool(self):
        result = err(dispatch("data.read_excel", {}))
        self.assertEqual(result["error"]["code"], "unknown_tool")
        self.assertIn("data.read_csv", result["error"]["message"])

    def test_non_object_input(self):
        result = err(dispatch("data.analyze", None))
        self.assertEqual(result["error"]["code"], "invalid_input")


class TestReadCsv(unittest.TestCase):
    def test_basic_parse_no_type_guessing(self):
        result = ok(dispatch("data.read_csv",
                             {"text": "id,name\n007,Ada\n2.50,Bob\n"}))
        data = result["data"]
        self.assertEqual(data["columns"], ["id", "name"])
        self.assertEqual(data["row_count"], 2)
        self.assertEqual(data["rows"][0], {"id": "007", "name": "Ada"})
        self.assertEqual(data["rows"][1]["id"], "2.50")

    def test_quoted_fields(self):
        text = 'a,b\n"1,2","say ""hi"""\nplain,x\n'
        data = ok(dispatch("data.read_csv", {"text": text}))["data"]
        self.assertEqual(data["rows"][0]["a"], "1,2")
        self.assertEqual(data["rows"][0]["b"], 'say "hi"')

    def test_custom_delimiter(self):
        data = ok(dispatch("data.read_csv",
                           {"text": "a;b\n1;2\n", "delimiter": ";"}))["data"]
        self.assertEqual(data["rows"][0], {"a": "1", "b": "2"})

    def test_duplicate_header_rejected(self):
        result = err(dispatch("data.read_csv", {"text": "a,a\n1,2\n"}))
        self.assertIn("duplicate column name", result["error"]["message"])

    def test_empty_header_rejected(self):
        result = err(dispatch("data.read_csv", {"text": "a,\n1,2\n"}))
        self.assertIn("empty column name", result["error"]["message"])

    def test_malformed_row_rejected(self):
        result = err(dispatch("data.read_csv", {"text": "a,b\n1\n"}))
        self.assertIn("malformed row", result["error"]["message"])

    def test_no_header_rejected(self):
        result = err(dispatch("data.read_csv", {"text": ""}))
        self.assertEqual(result["error"]["code"], "invalid_input")

    def test_host_path_field_rejected(self):
        result = err(dispatch("data.read_csv",
                              {"text": "a\n1\n", "path": "/etc/passwd"}))
        self.assertIn("path: unknown field", result["error"]["message"])

    def test_input_size_limit(self):
        result = err(dispatch("data.read_csv",
                              {"text": "a\n" + "x" * 70000 + "\n"}))
        self.assertEqual(result["error"]["code"], "limit_exceeded")

    def test_row_limit(self):
        text = "a\n" + "\n".join(str(i) for i in range(501)) + "\n"
        result = err(dispatch("data.read_csv", {"text": text}))
        self.assertEqual(result["error"]["code"], "limit_exceeded")


class TestReadJson(unittest.TestCase):
    def test_nested_value(self):
        data = ok(dispatch("data.read_json",
                           {"text": '{"a": [1, {"b": null}], "c": "x"}'}))["data"]
        self.assertEqual(data["value"], {"a": [1, {"b": None}], "c": "x"})
        self.assertEqual(data["depth"], 4)
        self.assertEqual(data["node_count"], 6)

    def test_duplicate_keys_rejected(self):
        result = err(dispatch("data.read_json", {"text": '{"a": 1, "a": 2}'}))
        self.assertIn("duplicate object key", result["error"]["message"])

    def test_depth_limit(self):
        deep = "[" * 13 + "1" + "]" * 13
        result = err(dispatch("data.read_json", {"text": deep}))
        self.assertEqual(result["error"]["code"], "limit_exceeded")

    def test_depth_twelve_allowed(self):
        ok_json = "[" * 11 + "1" + "]" * 11
        result = ok(dispatch("data.read_json", {"text": ok_json}))
        self.assertEqual(result["data"]["depth"], 12)

    def test_node_limit(self):
        text = "[" + ",".join("1" for _ in range(10001)) + "]"
        result = err(dispatch("data.read_json", {"text": text}))
        self.assertIn("nodes exceeds", result["error"]["message"])

    def test_nan_rejected(self):
        result = err(dispatch("data.read_json", {"text": '{"a": NaN}'}))
        self.assertIn("non-finite constant", result["error"]["message"])

    def test_overflow_float_rejected(self):
        result = err(dispatch("data.read_json", {"text": "[1e400]"}))
        self.assertIn("non-finite", result["error"]["message"])

    def test_url_field_rejected(self):
        result = err(dispatch("data.read_json",
                              {"text": "1", "url": "http://example.invalid"}))
        self.assertIn("url: unknown field", result["error"]["message"])


class TestTransform(unittest.TestCase):
    SRC = rows({"name": "a", "n": 3, "tag": "x"},
               {"name": "b", "n": 1, "tag": "y"},
               {"name": "c", "n": 2, "tag": None})

    def test_select_filter_sort_limit_pipeline(self):
        ops = [
            {"type": "select", "columns": ["name", "n"]},
            {"type": "filter", "column": "n", "op": "gte", "value": 2},
            {"type": "sort", "column": "n", "direction": "desc"},
            {"type": "limit", "count": 1},
        ]
        data = ok(dispatch("data.transform",
                           {"rows": self.SRC, "operations": ops}))["data"]
        self.assertEqual(data["rows"], [{"name": "a", "n": 3}])

    def test_input_not_mutated(self):
        original = copy.deepcopy(self.SRC)
        ops = [{"type": "select", "columns": ["n"]},
               {"type": "sort", "column": "n", "direction": "asc"}]
        ok(dispatch("data.transform", {"rows": self.SRC, "operations": ops}))
        self.assertEqual(self.SRC, original)

    def test_sort_nulls_last_both_directions(self):
        data = ok(dispatch("data.transform", {
            "rows": self.SRC,
            "operations": [{"type": "sort", "column": "tag", "direction": "desc"}],
        }))["data"]
        self.assertEqual([r["tag"] for r in data["rows"]], ["y", "x", None])

    def test_eq_matches_null(self):
        data = ok(dispatch("data.transform", {
            "rows": self.SRC,
            "operations": [{"type": "filter", "column": "tag", "op": "eq",
                            "value": None}],
        }))["data"]
        self.assertEqual(data["rows"], [{"name": "c", "n": 2, "tag": None}])

    def test_filter_type_strict_ordering(self):
        data = ok(dispatch("data.transform", {
            "rows": self.SRC,
            "operations": [{"type": "filter", "column": "n", "op": "gt",
                            "value": "1"}],
        }))["data"]
        self.assertEqual(data["rows"], [])

    def test_eval_style_op_rejected(self):
        result = err(dispatch("data.transform", {
            "rows": self.SRC,
            "operations": [{"type": "expression", "expr": "__import__('os')"}],
        }))
        self.assertIn("unknown operation", result["error"]["message"])

    def test_unknown_op_field_rejected(self):
        result = err(dispatch("data.transform", {
            "rows": self.SRC,
            "operations": [{"type": "limit", "count": 1, "callback": "js"}],
        }))
        self.assertIn("callback", result["error"]["message"])

    def test_unknown_column_rejected(self):
        result = err(dispatch("data.transform", {
            "rows": self.SRC,
            "operations": [{"type": "select", "columns": ["nope"]}],
        }))
        self.assertIn("unknown column", result["error"]["message"])

    def test_bool_count_rejected(self):
        result = err(dispatch("data.transform", {
            "rows": self.SRC,
            "operations": [{"type": "limit", "count": True}],
        }))
        self.assertIn("must be an integer", result["error"]["message"])

    def test_nested_cell_rejected(self):
        result = err(dispatch("data.transform", {
            "rows": rows({"a": {"nested": 1}}),
            "operations": [{"type": "limit", "count": 1}],
        }))
        self.assertIn("JSON scalar", result["error"]["message"])


class TestAnalyze(unittest.TestCase):
    def test_null_and_mixed_numeric(self):
        data = ok(dispatch("data.analyze", {
            "rows": rows({"v": 1}, {"v": None}, {"v": "2.5"}, {"v": 4}),
        }))["data"]
        col = data["columns"]["v"]
        self.assertEqual(col["count"], 3)
        self.assertEqual(col["missing"], 1)
        self.assertTrue(col["numeric"])
        self.assertEqual(col["min"], 1)
        self.assertEqual(col["max"], 4)
        self.assertEqual(col["mean"], 2.5)
        self.assertIn("missing_values_skipped_no_imputation", result_warnings(
            dispatch("data.analyze",
                     {"rows": rows({"v": 1}, {"v": None})})))

    def test_decimal_exact_mean(self):
        data = ok(dispatch("data.analyze",
                           {"rows": rows({"v": 0.1}, {"v": 0.2})}))["data"]
        self.assertEqual(data["columns"]["v"]["mean"], 0.15)

    def test_extreme_float_aggregate(self):
        data = ok(dispatch("data.analyze", {
            "rows": rows({"v": 1e308}, {"v": 1e308}, {"v": -1e308}),
        }))["data"]
        col = data["columns"]["v"]
        self.assertEqual(col["min"], -10 ** 308)
        self.assertEqual(col["max"], 10 ** 308)
        self.assertAlmostEqual(float(col["mean"]) / 1e308, 1 / 3)
        self.assertTrue(col["mean_decimal"].startswith("3.333333333"))
        cancel = ok(dispatch("data.analyze", {
            "rows": rows({"v": 1e308}, {"v": -1e308}),
        }))["data"]
        self.assertEqual(cancel["columns"]["v"]["mean"], 0)

    def test_text_column_not_numeric(self):
        data = ok(dispatch("data.analyze", {
            "rows": rows({"s": "a"}, {"s": "b"}),
        }))["data"]
        self.assertFalse(data["columns"]["s"]["numeric"])
        self.assertNotIn("mean", data["columns"]["s"])

    def test_bool_never_numeric(self):
        data = ok(dispatch("data.analyze",
                           {"rows": rows({"b": True}, {"b": False})}))["data"]
        self.assertFalse(data["columns"]["b"]["numeric"])

    def test_mixed_text_breaks_numeric(self):
        data = ok(dispatch("data.analyze", {
            "rows": rows({"v": 1}, {"v": "2"}, {"v": "oops"}),
        }))["data"]
        self.assertFalse(data["columns"]["v"]["numeric"])

    def test_empty_rows(self):
        data = ok(dispatch("data.analyze", {"rows": []}))["data"]
        self.assertEqual(data["row_count"], 0)
        self.assertEqual(data["columns"], {})


def result_warnings(result):
    assert result["status"] == "completed", result
    return result["warnings"]


class TestVisualize(unittest.TestCase):
    ROWS = rows({"day": "mon", "temp": 21}, {"day": "tue", "temp": "22.5"})

    def test_bar_spec_data_only(self):
        result = ok(dispatch("data.visualize", {
            "rows": self.ROWS, "chart_type": "bar", "x": "day", "y": "temp"}))
        spec = result["data"]["spec"]
        self.assertEqual(spec["mark"], "bar")
        self.assertEqual(len(spec["data"]["values"]), 2)
        self.assertEqual(spec["encoding"]["x"]["field"], "day")
        self.assertIn("vega_lite_spec_only_no_image_rendered", result["warnings"])

    def test_scatter_mark_point(self):
        spec = ok(dispatch("data.visualize", {
            "rows": rows({"day": 1, "temp": 21}, {"day": 2, "temp": 22.5}), "chart_type": "scatter",
            "x": "day", "y": "temp"}))["data"]["spec"]
        self.assertEqual(spec["mark"], "point")

    def test_no_scripts_html_or_fetchable_urls(self):
        blob = json.dumps(ok(dispatch("data.visualize", {
            "rows": self.ROWS, "chart_type": "line",
            "x": "day", "y": "temp"}))["data"])
        for token in ("<script", "javascript:", "html", "src=", "fetch(", "eval("):
            self.assertNotIn(token, blob.lower())

    def test_non_numeric_y_rejected(self):
        bad = rows({"x": 1, "y": "hot"}, {"x": 2, "y": "cold"})
        result = err(dispatch("data.visualize", {
            "rows": bad, "chart_type": "bar", "x": "x", "y": "y"}))
        self.assertIn("numeric", result["error"]["message"])

    def test_missing_column_rejected(self):
        result = err(dispatch("data.visualize", {
            "rows": self.ROWS, "chart_type": "bar", "x": "nope", "y": "temp"}))
        self.assertIn("not found", result["error"]["message"])

    def test_bad_chart_type_rejected(self):
        result = err(dispatch("data.visualize", {
            "rows": self.ROWS, "chart_type": "pie", "x": "day", "y": "temp"}))
        self.assertIn("must be one of", result["error"]["message"])


class TestExport(unittest.TestCase):
    def test_csv_formula_injection_mitigated(self):
        result = ok(dispatch("data.export", {
            "rows": rows({"f": "=cmd()", "n": 5, "neg": -7, "ws": " +x",
                          "tab": "\tT", "plain": "ok"}),
            "format": "csv"}))
        content = result["data"]["content"]
        self.assertIn("'=cmd()", content)
        self.assertIn("' +x", content)
        self.assertIn("'\tT", content)
        self.assertIn(",5,", content)
        self.assertIn(",-7,", content)
        self.assertNotIn("'-7", content)
        self.assertEqual(result["data"]["mime_type"], "text/csv; charset=utf-8")
        self.assertTrue(result["data"]["filename"].endswith(".csv"))
        self.assertTrue(any("formula_injection_mitigated" in w
                            for w in result["warnings"]))

    def test_csv_negative_string_cell_mitigated(self):
        content = ok(dispatch("data.export", {
            "rows": rows({"v": "-5"}), "format": "csv"}))["data"]["content"]
        self.assertIn("'-5", content)

    def test_csv_header_mitigated_and_quoting(self):
        content = ok(dispatch("data.export", {
            "rows": rows({"=H": "a,b"}), "format": "csv"}))["data"]["content"]
        self.assertIn("'=H", content)
        self.assertIn('"a,b"', content)

    def test_json_roundtrip(self):
        source = rows({"a": 1, "b": None}, {"a": 2, "b": "x"})
        result = ok(dispatch("data.export", {"rows": source, "format": "json"}))
        self.assertEqual(result["data"]["mime_type"], "application/json; charset=utf-8")
        payload = json.loads(result["data"]["content"])
        self.assertEqual(payload["rows"], source)

    def test_excel_rejected(self):
        result = err(dispatch("data.export",
                              {"rows": rows({"a": 1}), "format": "xlsx"}))
        self.assertEqual(result["error"]["code"], "invalid_input")


class TestBoundsAndStrictness(unittest.TestCase):
    def test_row_limit_via_transform(self):
        many = rows(*({"a": i} for i in range(501)))
        result = err(dispatch("data.transform",
                              {"rows": many, "operations": [{"type": "limit", "count": 1}]}))
        self.assertEqual(result["error"]["code"], "invalid_input")
        self.assertIn("exceeds maximum of 500", result["error"]["message"])

    def test_nonfinite_cell_rejected(self):
        result = err(dispatch("data.analyze",
                              {"rows": rows({"a": float("nan")})}))
        self.assertEqual(result["error"]["code"], "invalid_input")

    def test_huge_int_rejected(self):
        result = err(dispatch("data.analyze",
                              {"rows": rows({"a": 10 ** 309})}))
        self.assertIn("out of supported range", result["error"]["message"])

    def test_inconsistent_columns_rejected(self):
        result = err(dispatch("data.analyze",
                              {"rows": rows({"a": 1, "b": 2}, {"a": 3, "c": 4})}))
        self.assertIn("columns differ", result["error"]["message"])

    def test_required_field_missing(self):
        result = err(dispatch("data.read_csv", {}))
        self.assertIn("required field missing", result["error"]["message"])

    def test_stable_errors(self):
        first = dispatch("data.read_csv", {"text": "a,a\n1,2\n"})
        second = dispatch("data.read_csv", {"text": "a,a\n1,2\n"})
        self.assertEqual(first, second)

    def test_result_shape_completed(self):
        result = ok(dispatch("data.read_json", {"text": "1"}))
        self.assertEqual(result["mode"], "bounded_data_analysis")
        self.assertIsInstance(result["data"], dict)
        self.assertIsInstance(result["warnings"], list)


class TestNoHostCapabilities(unittest.TestCase):
    def test_source_has_no_dangerous_calls(self):
        source = inspect.getsource(data_tools)
        for token in ("eval(", "exec(", "open(", "__import__", "subprocess",
                      "socket", "urllib", "requests", "os.system", "pandas",
                      "sqlite", "popen", "system("):
            self.assertNotIn(token, source, token)


if __name__ == "__main__":
    unittest.main(verbosity=2)
