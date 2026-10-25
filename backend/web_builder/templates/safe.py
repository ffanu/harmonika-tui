"""Validation and escaping helpers for generated source code.

Every user-provided string that ends up inside generated code must pass
through one of the validators here so that output paths stay inside the
owned workspace and interpolated identifiers/strings can never break out
of their syntactic context (no code injection via names, labels, or SQL
values).
"""

from __future__ import annotations

import html
import json
import re
from typing import Any, Dict, List, Sequence


class SkillInputError(ValueError):
    """Raised when dispatch arguments fail strict validation."""


SLUG_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
NPM_RE = re.compile(r"^(@[a-z0-9-]+/)?[a-z][a-z0-9._-]*$")
ROUTE_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

_PATH_PART_RE = re.compile(r"^[A-Za-z0-9_.+\-\[\]()]+$")
_DOTFILE_RE = re.compile(r"^\.[A-Za-z0-9_.+\-]+$")

PY_RESERVED = frozenset(
    "False None True and as assert async await break class continue def del elif else "
    "except finally for from global if import in is lambda nonlocal not or pass raise "
    "return try while with yield".split()
)
JS_RESERVED = frozenset(
    "break case catch class const continue debugger default delete do else enum export "
    "extends false finally for function if import in instanceof new null return super "
    "switch this throw true try typeof var void while with yield let static implements "
    "package protected interface private public await".split()
)
SQL_RESERVED = frozenset(
    "select insert update delete drop alter create table index view trigger database "
    "schema from where join left right inner outer on group by order having limit offset "
    "union all values into set as and or not null primary key foreign references unique "
    "default constraint begin commit rollback pragma vacuum explain case when then else end "
    "grant revoke procedure function returns return if exists cascade restrict".split()
)
RESERVED_DIRS = frozenset(
    {"node_modules", ".git", ".ssh", ".env", "vendor", "__pycache__", ".next", ".nuxt",
     ".turbo", "dist", ".cache", "coverage"}
)

MAX_NAME = 64
MAX_LABEL = 200
MAX_PATH_PARTS = 24
MAX_CONTENT_BYTES = 2 * 1024 * 1024


def _field(label: str, value: Any) -> str:
    return "{}: expected string, got {}".format(label, type(value).__name__) if not isinstance(value, str) else ""


def bounded(value: Any, label: str, max_len: int = MAX_NAME, min_len: int = 1) -> str:
    """Validate a bounded plain string (labels, descriptions, raw text)."""
    err = _field(label, value)
    if err:
        raise SkillInputError(err)
    if not (min_len <= len(value) <= max_len):
        raise SkillInputError("%s: length must be %d..%d characters" % (label, min_len, max_len))
    if "\x00" in value:
        raise SkillInputError("%s: NUL byte is not allowed" % label)
    return value


def slug(value: Any, label: str = "project") -> str:
    """Project/package slug used as the root directory of all writes."""
    err = _field(label, value)
    if err:
        raise SkillInputError(err)
    if not (2 <= len(value) <= 50):
        raise SkillInputError("%s: must be 2..50 chars" % label)
    if not SLUG_RE.match(value):
        raise SkillInputError("%s: must be lowercase kebab-case (a-z, 0-9, hyphens)" % label)
    if value in RESERVED_DIRS:
        raise SkillInputError("%s: %r is a reserved directory name" % (label, value))
    return value


def ident(value: Any, label: str, dialect: str = "py") -> str:
    """Validate an identifier usable in Python or JS source."""
    err = _field(label, value)
    if err:
        raise SkillInputError(err)
    if not (1 <= len(value) <= MAX_NAME) or not IDENT_RE.match(value):
        raise SkillInputError("%s: must match [A-Za-z_][A-Za-z0-9_]* (max %d)" % (label, MAX_NAME))
    reserved = PY_RESERVED if dialect == "py" else JS_RESERVED
    if value.lower() in reserved:
        raise SkillInputError("%s: %r is a reserved word" % (label, value))
    return value


def pascal(value: Any, label: str) -> str:
    """Convert kebab/snake input into a PascalCase JS/TS identifier."""
    base = ident_base(value, label)
    out = "".join(part[:1].upper() + part[1:] for part in re.split(r"[-_]", base) if part)
    if not IDENT_RE.match(out) or out[:1].isdigit():
        raise SkillInputError("%s: cannot derive a PascalCase identifier" % label)
    if out.lower() in JS_RESERVED:
        raise SkillInputError("%s: %r is a reserved word" % (label, out))
    return out


def snake(value: Any, label: str) -> str:
    """Convert kebab/camel input into a snake_case Python identifier."""
    base = ident_base(value, label)
    step1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", base)
    step2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", step1)
    out = re.sub(r"[-]+", "_", step2).lower()
    if not IDENT_RE.match(out):
        raise SkillInputError("%s: cannot derive a snake_case identifier" % label)
    if out in PY_RESERVED:
        raise SkillInputError("%s: %r is a reserved word" % (label, out))
    return out


def ident_base(value: Any, label: str) -> str:
    err = _field(label, value)
    if err:
        raise SkillInputError(err)
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "", value)
    if not cleaned or not (1 <= len(cleaned) <= MAX_NAME):
        raise SkillInputError("%s: must contain 1..%d letters/digits/hyphens" % (label, MAX_NAME))
    if not re.match(r"^[A-Za-z_]", cleaned):
        raise SkillInputError("%s: must start with a letter" % label)
    return cleaned


def route_segment(value: Any, label: str) -> str:
    """Single URL path segment (no slashes, no dots)."""
    err = _field(label, value)
    if err:
        raise SkillInputError(err)
    if not (1 <= len(value) <= 64) or not ROUTE_RE.match(value):
        raise SkillInputError("%s: must be a lowercase URL segment (a-z, 0-9, hyphens)" % label)
    return value


def npm_name(value: Any, label: str = "package") -> str:
    err = _field(label, value)
    if err:
        raise SkillInputError(err)
    if not (1 <= len(value) <= 100) or not NPM_RE.match(value):
        raise SkillInputError("%s: invalid npm package name" % label)
    return value


def semver(value: Any, label: str) -> str:
    err = _field(label, value)
    if err:
        raise SkillInputError(err)
    if not SEMVER_RE.match(value):
        raise SkillInputError("%s: expected semver like 1.2.3" % label)
    return value


def http_url(value: Any, label: str) -> str:
    err = _field(label, value)
    if err:
        raise SkillInputError(err)
    if len(value) > 300 or not re.match(r"^https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+$", value):
        raise SkillInputError("%s: expected an http(s) URL" % label)
    return value


def domain(value: Any, label: str) -> str:
    err = _field(label, value)
    if err:
        raise SkillInputError(err)
    if len(value) > 253 or not re.match(
        r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$", value.lower()
    ):
        raise SkillInputError("%s: expected a DNS domain name" % label)
    return value.lower()


def ipv4(value: Any, label: str) -> str:
    err = _field(label, value)
    if err:
        raise SkillInputError(err)
    parts = value.split(".")
    if len(parts) != 4:
        raise SkillInputError("%s: expected an IPv4 address" % label)
    for part in parts:
        if not part.isdigit() or not 0 <= int(part) <= 255:
            raise SkillInputError("%s: expected an IPv4 address" % label)
    return value


def email(value: Any, label: str) -> str:
    err = _field(label, value)
    if err:
        raise SkillInputError(err)
    if len(value) > 254 or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$", value):
        raise SkillInputError("%s: expected an email address" % label)
    return value


def rel_path(*parts: str) -> str:
    """Join and validate a workspace-relative POSIX path."""
    if not parts or len(parts) > MAX_PATH_PARTS:
        raise SkillInputError("path: expected 1..%d segments" % MAX_PATH_PARTS)
    cleaned: List[str] = []
    for part in parts:
        if not isinstance(part, str) or not part or "\\" in part or "\x00" in part:
            raise SkillInputError("path: invalid segment %r" % (part,))
        if part in (".", "..") or part.startswith(".."):
            raise SkillInputError("path: traversal segment %r is blocked" % part)
        if part.startswith("/"):
            raise SkillInputError("path: absolute segment %r is blocked" % part)
        if part in RESERVED_DIRS:
            raise SkillInputError("path: reserved segment %r is blocked" % part)
        if not _PATH_PART_RE.match(part):
            raise SkillInputError("path: invalid characters in segment %r" % part)
        if part.startswith(".") and not _DOTFILE_RE.match(part):
            raise SkillInputError("path: invalid dotfile segment %r" % part)
        if set(part) == {"."}:
            raise SkillInputError("path: dot-only segment is blocked")
        cleaned.append(part)
    joined = "/".join(cleaned)
    if len(joined) > 512:
        raise SkillInputError("path: exceeds 512 characters")
    return joined


def js_str(value: str) -> str:
    """Return a safe double-quoted JavaScript/TypeScript string literal."""
    return json.dumps(bounded(value, "string", MAX_CONTENT_BYTES, 0)).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def py_str(value: str) -> str:
    """Return a safe double-quoted Python string literal."""
    return json.dumps(bounded(value, "string", MAX_LABEL * 4, 0), ensure_ascii=False)


def html_text(value: str) -> str:
    return html.escape(bounded(value, "text", MAX_LABEL * 2, 0), quote=True)


def shell_quote(value: str) -> str:
    """Quote a value for POSIX shell single-quote context."""
    return "'" + bounded(value, "shell-value", MAX_LABEL * 2).replace("'", "'\"'\"'") + "'"


def sql_ident(value: Any, label: str = "identifier") -> str:
    err = _field(label, value)
    if err:
        raise SkillInputError(err)
    if not (1 <= len(value) <= MAX_NAME) or not IDENT_RE.match(value):
        raise SkillInputError("%s: SQL identifiers must match [A-Za-z_][A-Za-z0-9_]*" % label)
    if value.lower() in SQL_RESERVED:
        raise SkillInputError("%s: %r is a reserved SQL word" % (label, value))
    return value


def sql_value(value: Any) -> str:
    """Render a validated scalar as a dialect-portable SQL literal."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise SkillInputError("seed value: NaN/infinity is not representable")
        return repr(value)
    if isinstance(value, str):
        if "\x00" in value or len(value) > 4000:
            raise SkillInputError("seed value: strings must be <= 4000 chars without NUL")
        escaped = value.replace("\\", "\\\\").replace("'", "''")
        return "'" + escaped + "'"
    raise SkillInputError("seed value: expected str/int/float/bool/None, got %s" % type(value).__name__)


def secret_ref(env_name: str) -> str:
    """Validate an environment variable name used for secret lookups."""
    if not re.match(r"^[A-Z][A-Z0-9_]{2,63}$", env_name or ""):
        raise SkillInputError("secret env name %r must be SCREAMING_SNAKE_CASE" % (env_name,))
    return env_name


def check_content(path: str, content: Any) -> None:
    if not isinstance(content, str):
        raise SkillInputError("artifact %s: content must be str" % path)
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise SkillInputError("artifact %s: content exceeds 2 MiB" % path)


def render(template: str, mapping: Dict[str, str]) -> str:
    """Substitute @@TOKEN@@ placeholders, failing loudly on leftovers.

    Used instead of f-strings for source templates that contain braces,
    so generated code can never be corrupted by interpolation.
    """
    out = template
    for key, value in mapping.items():
        out = out.replace("@@" + key + "@@", value)
    if "@@" in out:
        raise SkillInputError("internal: unresolved template token in output")
    return out


def writes_from(pairs: Sequence) -> List[Dict[str, str]]:
    """Normalize (path, content) pairs into the write manifest shape."""
    out: List[Dict[str, str]] = []
    seen = set()
    for pair in pairs:
        if len(pair) != 2:
            raise SkillInputError("internal: artifact pairs are (path, content)")
        path, content = pair
        if path in seen:
            raise SkillInputError("internal: duplicate artifact path %s" % path)
        seen.add(path)
        check_content(path, content)
        out.append({"path": path, "content": content})
    if not out:
        raise SkillInputError("internal: generator produced no artifacts")
    return out
