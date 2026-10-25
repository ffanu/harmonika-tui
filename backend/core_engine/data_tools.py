"""Bounded, pure-stdlib data tools for the Harmonika core engine.

Six deterministic handlers operate only on in-memory JSON input:

    data.read_csv, data.read_json, data.transform,
    data.analyze, data.visualize, data.export

Hard guarantees:
- No filesystem, network, SQL, shell, eval/exec, or code execution.
- Every input is JSON-schema validated (strict types, unknown fields
  rejected, booleans never count as numbers, non-finite rejected).
- Bounded: input <= 64 KiB, <= 500 rows, <= 30 columns, JSON nesting
  depth <= 12, read_json <= 10000 nodes, output <= 200 KiB.
- data.transform only runs an allowlist of deterministic row operations.
- data.visualize returns a Vega-Lite JSON spec only; nothing is rendered
  and no image is produced.
- data.export mitigates CSV spreadsheet formula injection for text cells;
  JSON numeric cells (including negatives) stay numeric and safe.
"""

import csv
import io
import json
import math
import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation, localcontext

MODE = "bounded_data_analysis"
MAX_INPUT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 200 * 1024
MAX_ROWS = 500
MAX_COLS = 30
MAX_DEPTH = 12
MAX_NODES = 10000
MAX_NAME_LEN = 256
MAX_OPS = 32
MAX_INT_ABS = 10 ** 308

_FILTER_OPS = ("eq", "ne", "gt", "gte", "lt", "lte")
_CHART_MARKS = {"bar": "bar", "line": "line", "scatter": "point"}
_INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")
_NUMERIC_TEXT = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")

_ROWS_SCHEMA = {"type": "array", "items": {"type": "object"}, "maxItems": MAX_ROWS}
_OPS_SCHEMA = {"type": "array", "items": {"type": "object"},
               "minItems": 1, "maxItems": MAX_OPS}

_DESCRIPTIONS = {
    "data.read_csv": (
        "Parse bounded CSV text into columns and rows from the provided text "
        "(never from host paths). Cell values stay strings; no type guessing. "
        "Rejects duplicate or empty column names and malformed rows."
    ),
    "data.read_json": (
        "Parse bounded JSON text. Rejects duplicate object keys, non-finite "
        "constants, nesting deeper than 12 and more than 10000 nodes."
    ),
    "data.transform": (
        "Apply allowlisted deterministic row operations: select, filter, sort, "
        "limit. No eval, no expressions, no mutation of the input rows. Cells "
        "must be JSON scalars."
    ),
    "data.analyze": (
        "Per-column counts, missing values, min, max and mean using bounded-precision "
        "Decimal aggregation (1000 digits internally; fractional JSON output may round). Numeric cells are JSON numbers or strictly "
        "formatted numeric text (booleans never count as numbers). Missing "
        "values are skipped; there is no imputation."
    ),
    "data.visualize": (
        "Return a bounded Vega-Lite JSON specification (bar, line or scatter) "
        "with the data inlined. Spec only: nothing is rendered, no image, no "
        "URLs to fetch, no scripts or HTML. The y column must be numeric."
    ),
    "data.export": (
        "Serialize rows to CSV or JSON text with mime type and filename. "
        "CSV text cells that could be read as spreadsheet formulas (=, +, -, "
        "@, tab, CR, also after leading whitespace) get a leading apostrophe; "
        "JSON numeric cells such as negative numbers stay numeric. Excel and "
        "other formats are not supported."
    ),
}

_SCHEMAS = {
    "data.read_csv": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "delimiter": {"type": "string"},
        },
        "required": ["text"],
        "additionalProperties": False,
    },
    "data.read_json": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    },
    "data.transform": {
        "type": "object",
        "properties": {"rows": _ROWS_SCHEMA, "operations": _OPS_SCHEMA},
        "required": ["rows", "operations"],
        "additionalProperties": False,
    },
    "data.analyze": {
        "type": "object",
        "properties": {"rows": _ROWS_SCHEMA},
        "required": ["rows"],
        "additionalProperties": False,
    },
    "data.visualize": {
        "type": "object",
        "properties": {
            "rows": _ROWS_SCHEMA,
            "chart_type": {"type": "string", "enum": ["bar", "line", "scatter"]},
            "x": {"type": "string"},
            "y": {"type": "string"},
        },
        "required": ["rows", "chart_type", "x", "y"],
        "additionalProperties": False,
    },
    "data.export": {
        "type": "object",
        "properties": {
            "rows": _ROWS_SCHEMA,
            "format": {"type": "string", "enum": ["csv", "json"]},
        },
        "required": ["rows", "format"],
        "additionalProperties": False,
    },
}


class ToolError(Exception):
    """Stable, typed rejection used across all data tools."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _error_result(code, message):
    return {"status": "error", "mode": MODE,
            "error": {"code": code, "message": message}, "warnings": []}


# ---------------------------------------------------------------------------
# validation helpers


def _type_ok(value, name):
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    return False


def _validate(value, schema, path):
    errors = []
    types = schema.get("type")
    if types:
        types = [types] if isinstance(types, str) else list(types)
        if not any(_type_ok(value, t) for t in types):
            errors.append("%s: expected type %s" % (path, "|".join(types)))
            return errors
    if "enum" in schema and value not in schema["enum"]:
        errors.append("%s: must be one of %s" % (path, schema["enum"]))
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append("%s.%s: required field missing" % (path, key))
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    errors.append("%s.%s: unknown field" % (path, key))
        for key, sub in value.items():
            if key in props:
                errors.extend(_validate(sub, props[key], "%s.%s" % (path, key)))
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append("%s: needs at least %d item(s)" % (path, schema["minItems"]))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append("%s: exceeds maximum of %d item(s)" % (path, schema["maxItems"]))
        items = schema.get("items")
        if isinstance(items, dict):
            for i, sub in enumerate(value):
                errors.extend(_validate(sub, items, "%s[%d]" % (path, i)))
    return errors


def _json_bytes(value, what):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise ToolError("invalid_input",
                        "%s: not JSON-serializable (non-finite or unsupported value)" % what)


def _scan(value, depth, path):
    if depth > MAX_DEPTH:
        raise ToolError("limit_exceeded",
                        "%s: nesting deeper than %d" % (path, MAX_DEPTH))
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ToolError("invalid_input", "%s: non-finite number" % path)
        return
    if isinstance(value, int):
        if abs(value) > MAX_INT_ABS:
            raise ToolError("invalid_input",
                            "%s: integer out of supported range" % path)
        return
    if isinstance(value, dict):
        for key, sub in value.items():
            _scan(sub, depth + 1, "%s.%s" % (path, key))
        return
    if isinstance(value, list):
        for i, sub in enumerate(value):
            _scan(sub, depth + 1, "%s[%d]" % (path, i))
        return
    raise ToolError("invalid_input",
                    "%s: unsupported type %s" % (path, type(value).__name__))


def _class_of(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "other"


def _check_names(names, where):
    seen = set()
    for name in names:
        if not isinstance(name, str) or not name.strip():
            raise ToolError("invalid_input", "%s: empty column name" % where)
        if len(name) > MAX_NAME_LEN:
            raise ToolError("limit_exceeded",
                            "%s: column name longer than %d" % (where, MAX_NAME_LEN))
        if name in seen:
            raise ToolError("invalid_input",
                            "%s: duplicate column name %r" % (where, name))
        seen.add(name)


def _check_rows(rows):
    if len(rows) > MAX_ROWS:
        raise ToolError("limit_exceeded",
                        "rows: %d exceeds limit %d" % (len(rows), MAX_ROWS))
    header = None
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ToolError("invalid_input", "rows[%d]: must be an object" % i)
        keys = list(row.keys())
        if header is None:
            if not keys:
                raise ToolError("invalid_input", "rows[0]: no columns")
            _check_names(keys, "rows[0]")
            if len(keys) > MAX_COLS:
                raise ToolError("limit_exceeded",
                                "rows[0]: %d columns exceeds limit %d" % (len(keys), MAX_COLS))
            header = keys
        elif set(keys) != set(header):
            raise ToolError("invalid_input",
                            "rows[%d]: columns differ from rows[0]" % i)
        for key, cell in row.items():
            if _class_of(cell) == "other":
                raise ToolError("invalid_input",
                                "rows[%d].%s: cell must be a JSON scalar" % (i, key))


def _as_decimal(cell):
    """Decimal for numeric cells (JSON number or strict numeric text), else None."""
    if cell is None or isinstance(cell, bool):
        return None
    if isinstance(cell, int):
        return Decimal(cell)
    if isinstance(cell, float):
        return Decimal(repr(cell))
    if isinstance(cell, str) and _NUMERIC_TEXT.match(cell):
        if len(cell)>128:raise ToolError("limit_exceeded", "numeric text exceeds precision limit")
        try:
            number = Decimal(cell)
        except InvalidOperation:
            return None
        if not number.is_finite():return None
        if number and not -324<=number.adjusted()<=308:raise ToolError("limit_exceeded", "numeric exponent outside supported range")
        return number
    return None


def _json_number(number):
    """Decimal back to a JSON-safe value; extreme magnitudes become text."""
    if number and number.adjusted()>308:return format(number,"E")
    if number == number.to_integral_value():
        try:
            whole = int(number)
        except (OverflowError, ValueError):
            return format(number, "E")
        if abs(whole) <= MAX_INT_ABS:
            return whole
        return format(number, "E")
    try:
        value = float(number)
    except (OverflowError, ValueError):
        return format(number, "E")
    return value if math.isfinite(value) and (value!=0 or number==0) else format(number, "E")


# ---------------------------------------------------------------------------
# tool handlers


def _tool_read_csv(params):
    text = params["text"]
    if text.startswith("\ufeff"):
        text = text[1:]
    delimiter = params.get("delimiter", ",")
    if (not isinstance(delimiter, str) or len(delimiter) != 1
            or delimiter in ("\r", "\n", '"')):
        raise ToolError("invalid_input",
                        "delimiter: must be a single character, not CR/LF/quote")
    try:
        records = [rec for rec in csv.reader(io.StringIO(text), delimiter=delimiter,
                                             strict=True) if rec != []]
    except csv.Error as exc:
        raise ToolError("invalid_input", "csv text: parse error: %s" % exc)
    if not records:
        raise ToolError("invalid_input", "csv text: no header row found")
    header = records[0]
    _check_names(header, "csv header")
    if len(header) > MAX_COLS:
        raise ToolError("limit_exceeded",
                        "csv header: %d columns exceeds limit %d" % (len(header), MAX_COLS))
    rows = []
    for i, record in enumerate(records[1:], start=2):
        if len(record) != len(header):
            raise ToolError("invalid_input",
                            "csv line %d: malformed row, expected %d fields, got %d"
                            % (i, len(header), len(record)))
        rows.append(dict(zip(header, record)))
    if len(rows) > MAX_ROWS:
        raise ToolError("limit_exceeded",
                        "csv rows: %d exceeds limit %d" % (len(rows), MAX_ROWS))
    return {"columns": header, "rows": rows, "row_count": len(rows)}, []


def _tool_read_json(params):
    def reject_constant(name):
        raise ValueError("non-finite constant not allowed: %s" % name)

    def pairs_hook(pairs):
        seen = set()
        for key, _ in pairs:
            if key in seen:
                raise ValueError("duplicate object key: %r" % key)
            seen.add(key)
        return dict(pairs)

    try:
        value = json.loads(params["text"], object_pairs_hook=pairs_hook,
                           parse_constant=reject_constant)
    except ValueError as exc:
        raise ToolError("invalid_input", "json text: invalid JSON: %s" % exc)
    except RecursionError:
        raise ToolError("limit_exceeded", "json text: nesting too deep")
    _scan(value, 1, "value")
    nodes = 0
    deepest = 0
    stack = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if depth > deepest:
            deepest = depth
        if isinstance(node, dict):
            for sub in node.values():
                stack.append((sub, depth + 1))
        elif isinstance(node, list):
            for sub in node:
                stack.append((sub, depth + 1))
    if nodes > MAX_NODES:
        raise ToolError("limit_exceeded",
                        "json value: %d nodes exceeds limit %d" % (nodes, MAX_NODES))
    return {"value": value, "depth": deepest, "node_count": nodes}, []


def _apply_op(rows, op, index):
    where = "operations[%d]" % index
    if not isinstance(op, dict):
        raise ToolError("invalid_input", "%s: must be an object" % where)
    op_type = op.get("type")
    if type(op_type) is not str:raise ToolError("invalid_input", "operation type must be a string")
    fields = {
        "select": {"columns"},
        "filter": {"column", "op", "value"},
        "sort": {"column", "direction"},
        "limit": {"count"},
    }.get(op_type)
    if fields is None:
        raise ToolError("invalid_input",
                        "%s.type: unknown operation %r; allowed: select, filter, "
                        "sort, limit" % (where, op_type))
    optional = {"direction"} if op_type == "sort" else set()
    unknown = sorted(set(op) - fields - optional - {"type"})
    if unknown:
        raise ToolError("invalid_input",
                        "%s: unknown field(s): %s" % (where, unknown))
    missing = sorted(fields - optional - set(op))
    if missing:
        raise ToolError("invalid_input",
                        "%s: missing field(s): %s" % (where, missing))
    if op_type in ('filter','sort'):
        column=op['column']
        if type(column) is not str or not 1<=len(column)<=MAX_NAME_LEN:raise ToolError('invalid_input','column must be a bounded string')
        if rows and column not in rows[0]:raise ToolError('invalid_input','unknown column')

    if op_type == "select":
        columns = op["columns"]
        if (not isinstance(columns, list) or not columns
                or not all(isinstance(c, str) for c in columns)):
            raise ToolError("invalid_input",
                            "%s.columns: must be a non-empty list of names" % where)
        if len(set(columns)) != len(columns):
            raise ToolError("invalid_input",
                            "%s.columns: duplicate column names" % where)
        if len(columns) > MAX_COLS:
            raise ToolError("limit_exceeded",
                            "%s.columns: exceeds %d columns" % (where, MAX_COLS))
        if not rows:
            return []
        known = set(rows[0])
        for name in columns:
            if name not in known:
                raise ToolError("invalid_input",
                                "%s.columns: unknown column %r" % (where, name))
        return [{name: row[name] for name in columns} for row in rows]

    if op_type == "filter":
        if op["op"] not in _FILTER_OPS:
            raise ToolError("invalid_input",
                            "%s.op: unknown filter op %r; allowed: %s"
                            % (where, op["op"], ", ".join(_FILTER_OPS)))
        if not isinstance(op["column"], str):
            raise ToolError("invalid_input", "%s.column: must be a string" % where)
        if _class_of(op["value"]) == "other":
            raise ToolError("invalid_input",
                            "%s.value: must be a JSON scalar" % where)
        fop, value = op["op"], op["value"]
        kept = []
        for row in rows:
            cell = row[op["column"]]
            same = _class_of(cell) == _class_of(value)
            if fop == "eq":
                keep = same and cell == value
            elif fop == "ne":
                keep = not (same and cell == value)
            elif same and _class_of(cell) in ("number", "string"):
                keep = {"gt": cell > value, "gte": cell >= value,
                        "lt": cell < value, "lte": cell <= value}[fop]
            else:
                keep = False
            if keep:
                kept.append(row)
        return kept

    if op_type == "sort":
        direction = op.get("direction", "asc")
        if direction not in ("asc", "desc"):
            raise ToolError("invalid_input",
                            "%s.direction: must be asc or desc" % where)
        if not rows:
            return []
        kinds = {_class_of(row[op["column"]]) for row in rows}
        kinds.discard("null")
        if len(kinds) > 1:
            raise ToolError("invalid_input",
                            "%s: mixed cell types in sort column %r"
                            % (where, op["column"]))
        tail = [row for row in rows if row[op["column"]] is None]
        body = [row for row in rows if row[op["column"]] is not None]
        body.sort(key=lambda row: row[op["column"]], reverse=direction == "desc")
        return body + tail

    count = op["count"]
    if not isinstance(count, int) or isinstance(count, bool):
        raise ToolError("invalid_input", "%s.count: must be an integer" % where)
    if not 0 <= count <= MAX_ROWS:
        raise ToolError("limit_exceeded",
                        "%s.count: %d outside 0..%d" % (where, count, MAX_ROWS))
    return rows[:count]


def _tool_transform(params):
    rows = [dict(row) for row in params["rows"]]
    for i, op in enumerate(params["operations"]):
        rows = _apply_op(rows, op, i)
    return {"rows": rows, "row_count": len(rows)}, []


def _tool_analyze(params):
    rows = params["rows"]
    header = list(rows[0].keys()) if rows else []
    columns = {}
    any_missing = False
    for name in header:
        values = [row[name] for row in rows]
        present = [v for v in values if v is not None]
        missing = len(values) - len(present)
        any_missing = any_missing or bool(missing)
        numbers = []
        numeric = True
        for cell in present:
            number = _as_decimal(cell)
            if number is None:
                numeric = False
                break
            numbers.append(number)
        entry = {"count": len(present), "missing": missing,
                 "numeric": numeric and bool(present)}
        if entry["numeric"]:
            with localcontext() as ctx:
                ctx.prec=1000
                total = Decimal(0)
                for number in numbers:
                    total += number
                entry["min"] = _json_number(min(numbers))
                entry["max"] = _json_number(max(numbers))
                mean = total / Decimal(len(numbers))
                entry["mean"] = _json_number(mean)
                entry["mean_decimal"] = format(mean,"E")
                entry["precision_digits"] = ctx.prec
                entry["mean_is_approximate"] = Decimal(str(entry["mean"])) != mean
        columns[name] = entry
    warnings = []
    if any_missing:
        warnings.append("missing_values_skipped_no_imputation")
    data = {"row_count": len(rows), "column_count": len(header), "columns": columns}
    return data, warnings


def _tool_visualize(params):
    rows = params["rows"]
    x_name, y_name = params["x"], params["y"]
    header = list(rows[0].keys()) if rows else []
    for name in (x_name, y_name):
        if name not in header:
            raise ToolError("invalid_input", "column %r not found" % name)
    numeric_fields=(x_name,y_name) if params['chart_type']=='scatter' else (y_name,)
    normalized=[]
    for row in rows:
        item=dict(row)
        for name in numeric_fields:
            number=_as_decimal(row[name])
            if number is None or abs(number)>2**53-1:raise ToolError('invalid_input','chart values require bounded numeric cells')
            plotted=float(number)
            if not math.isfinite(plotted) or (plotted==0 and number!=0):raise ToolError('invalid_input','chart number outside supported precision')
            item[name]=int(number) if number==number.to_integral_value() else plotted
        normalized.append(item)
    def field(name):
        return name.replace('\\','\\\\').replace('.','\\.').replace('[','\\[').replace(']','\\]')
    spec = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "description": "Bounded data-only Vega-Lite spec; not rendered here",
        "data": {"values": normalized},
        "mark": _CHART_MARKS[params["chart_type"]],
        "encoding": {"x": {"field": field(x_name),"type":"quantitative" if params['chart_type']=='scatter' else "nominal"}, "y": {"field": field(y_name),"type":"quantitative"}},
    }
    data = {"format": "vega-lite", "chart_type": params["chart_type"], "spec": spec}
    return data, ["vega_lite_spec_only_no_image_rendered"]


def _mitigate_text(cell):
    if cell[:1] in _INJECTION_PREFIXES or re.sub(r"\A[\s\ufeff]+", "", cell)[:1] in _INJECTION_PREFIXES:
        return "'" + cell, 1
    return cell, 0


def _tool_export(params):
    rows = params["rows"]
    fmt = params["format"]
    header = list(rows[0].keys()) if rows else []
    warnings = []
    if fmt == "csv":
        mitigated = 0
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        safe_header = []
        for name in header:
            cell, count = _mitigate_text(name)
            mitigated += count
            safe_header.append(cell)
        writer.writerow(safe_header)
        for row in rows:
            record = []
            for name in header:
                cell = row[name]
                if cell is None:
                    record.append("")
                elif isinstance(cell, bool):
                    record.append("true" if cell else "false")
                elif isinstance(cell, (int, float)):
                    record.append(str(cell))
                else:
                    cell, count = _mitigate_text(cell)
                    mitigated += count
                    record.append(cell)
            writer.writerow(record)
        content = buffer.getvalue()
        mime = "text/csv; charset=utf-8"
        ext = "csv"
        if mitigated:
            warnings.append("formula_injection_mitigated: %d cell(s) prefixed "
                            "with apostrophe" % mitigated)
    else:
        content = json.dumps({"columns": header, "rows": rows},
                             ensure_ascii=False, separators=(",", ":"))
        mime = "application/json; charset=utf-8"
        ext = "json"
    return {"content": content, "mime_type": mime,
            "filename": "harmonika-export." + ext,
            "byte_size": len(content.encode("utf-8"))}, warnings


_HANDLERS = {
    "data.read_csv": _tool_read_csv,
    "data.read_json": _tool_read_json,
    "data.transform": _tool_transform,
    "data.analyze": _tool_analyze,
    "data.visualize": _tool_visualize,
    "data.export": _tool_export,
}


# ---------------------------------------------------------------------------
# public interface


def descriptors():
    """Return {tool_id: {description, input_schema}} for all six data tools."""
    return {tool_id: {"description": _DESCRIPTIONS[tool_id],
                      "input_schema": deepcopy(_SCHEMAS[tool_id])}
            for tool_id in sorted(_HANDLERS)}


def dispatch(tool_id, params):
    """Run a bounded data tool. Never raises; returns a stable result dict."""
    try:
        if not isinstance(tool_id, str) or tool_id not in _HANDLERS:
            supported = ", ".join(sorted(_HANDLERS))
            raise ToolError("unknown_tool",
                            "unknown tool id %r; supported: %s" % (tool_id, supported))
        if not isinstance(params, dict):
            raise ToolError("invalid_input", "input: must be a JSON object")
        raw = _json_bytes(params, "input")
        if len(raw) > MAX_INPUT_BYTES:
            raise ToolError("limit_exceeded",
                            "input: %d bytes exceeds limit %d" % (len(raw), MAX_INPUT_BYTES))
        _scan(params, 1, "input")
        errors = _validate(params, _SCHEMAS[tool_id], "input")
        if errors:
            raise ToolError("invalid_input", "; ".join(errors[:5]))
        rows = params.get("rows")
        if isinstance(rows, list):
            _check_rows(rows)
        data, warnings = _HANDLERS[tool_id](params)
        out = _json_bytes(data, "output")
        if len(out) > MAX_OUTPUT_BYTES:
            raise ToolError("limit_exceeded",
                            "output: %d bytes exceeds limit %d" % (len(out), MAX_OUTPUT_BYTES))
        return {"status": "completed", "mode": MODE, "data": data,
                "warnings": warnings}
    except ToolError as exc:
        return _error_result(exc.code, exc.message)
    except RecursionError:
        return _error_result("limit_exceeded", "input: nesting too deep")
