"""Harmonika master engine analyzers.

Stdlib-only, deterministic analyses over client-supplied bounded data.
No network, no host filesystem access, no subprocess, no SDK calls, no
execution of analyzed input. Handlers never make blanket safe/clean,
reputation, geo, or identity claims; every result carries limitations.
"""
import ast
import base64
import binascii
import hashlib
import ipaddress
import json
import math
from decimal import Decimal
import re
import struct
from datetime import datetime, timedelta, timezone

MAX_PAYLOAD_BYTES = 262144
MAX_DEPTH = 8
MAX_CODE_CHARS = 120000
MAX_TEXT_CHARS = 120000
MAX_LIST_ITEMS = 500
MAX_B64_DECODED = 262144
MAX_FINDINGS = 200

LIMIT_LOCAL = ("Client-supplied bounded data analyzed in-process only; no network, "
               "host files, shell, or execution of analyzed input.")
LIMIT_NO_CLAIMS = "No reputation, geolocation, identity, or safety verdict is claimed."


class ValidationError(ValueError):
    pass


def _f_str(required=False, max_len=MAX_TEXT_CHARS):
    return {"type": "str", "required": required, "max_len": max_len}


def _f_int(required=False, minimum=0, maximum=2**31, default=None):
    return {"type": "int", "required": required, "min": minimum, "max": maximum, "default": default}


def _f_list(required=False, max_items=MAX_LIST_ITEMS, items=None, allow_unknown=False):
    spec = {"type": "list", "required": required, "max_items": max_items}
    if items is not None:
        spec["items"] = items
    if allow_unknown:
        spec["allow_unknown"] = True
    return spec


def _f_dict(required=False, fields=None, allow_unknown=False):
    return {"type": "dict", "required": required, "fields": fields or {},
            "allow_unknown": allow_unknown}


def _check_value(value, key, spec):
    kind = spec["type"]
    if kind == "str":
        if not isinstance(value, str):
            raise ValidationError(f"{key} must be a string")
        if len(value) > spec.get("max_len", MAX_TEXT_CHARS):
            raise ValidationError(f"{key} exceeds max length {spec.get('max_len')}")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError(f"{key} must be an integer")
        if not (spec.get("min", -2**31) <= value <= spec.get("max", 2**31)):
            raise ValidationError(f"{key} out of range")
        return value
    if kind == "num":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationError(f"{key} must be a number")
        if not math.isfinite(value) or not 0 <= value <= 10**12:
            raise ValidationError(f"{key} must be a finite nonnegative number <= 1e12")
        return float(value)
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValidationError(f"{key} must be a boolean")
        return value
    if kind == "list":
        if not isinstance(value, list):
            raise ValidationError(f"{key} must be a list")
        if len(value) > spec.get("max_items", MAX_LIST_ITEMS):
            raise ValidationError(f"{key} exceeds max items {spec.get('max_items')}")
        items = spec.get("items")
        if items is None:
            return list(value)
        return [_check_value(v, f"{key}[{i}]", items) for i, v in enumerate(value)]
    if kind == "dict":
        if not isinstance(value, dict):
            raise ValidationError(f"{key} must be an object")
        fields = spec.get("fields", {})
        if not spec.get("allow_unknown"):
            unknown = sorted(set(value) - set(fields))
            if unknown:
                raise ValidationError(f"{key} has unknown fields: {unknown}")
        out = {}
        for k, child in fields.items():
            if child.get("required") and (k not in value or value[k] is None):
                raise ValidationError(f"{key}.{k} is required")
        for k, v in value.items():
            out[k] = _check_value(v, f"{key}.{k}", fields[k]) if k in fields else v
        return out
    raise ValidationError(f"{key} has unsupported schema type {kind}")


def _validate_payload(payload, schema):
    if not isinstance(payload, dict):
        raise ValidationError("payload must be a JSON object")
    unknown = sorted(set(payload) - set(schema))
    if unknown:
        raise ValidationError(f"unknown fields: {unknown}")
    out = {}
    for key, spec in schema.items():
        if key not in payload or payload[key] is None:
            if spec.get("required"):
                raise ValidationError(f"missing required field: {key}")
            out[key] = spec.get("default")
        else:
            out[key] = _check_value(payload[key], key, spec)
    return out


def _depth_of(value, depth=0):
    if isinstance(value, dict) and value:
        return max(_depth_of(v, depth + 1) for v in value.values())
    if isinstance(value, list) and value:
        return max(_depth_of(v, depth + 1) for v in value)
    return depth


def _ok(skill_id, findings=None, metrics=None, data=None, limitations=None):
    return {"status": "completed", "analysis_performed": True, "skill_id": skill_id,
            "findings": (findings or [])[:MAX_FINDINGS], "metrics": metrics or {},
            "data": data if data is not None else {}, "limitations": limitations or []}


def _reject(skill_id, errors, status="rejected"):
    return {"status": status, "analysis_performed": False, "skill_id": skill_id,
            "errors": errors[:20], "limitations": [LIMIT_LOCAL]}


def dispatch(skill_id, payload):
    skill = SKILLS.get(skill_id)
    if skill is None:
        return _reject(skill_id, ["unknown skill_id"])
    try:
        blob = json.dumps(payload, default=str)
    except (TypeError, ValueError):
        return _reject(skill_id, ["payload is not JSON-serializable"])
    if len(blob) > MAX_PAYLOAD_BYTES:
        return _reject(skill_id, [f"payload exceeds {MAX_PAYLOAD_BYTES} bytes"])
    if _depth_of(payload) > MAX_DEPTH:
        return _reject(skill_id, [f"payload nesting exceeds depth {MAX_DEPTH}"])
    try:
        value = _validate_payload(payload if isinstance(payload, dict) else {}, skill["input_schema"])
    except ValidationError as exc:
        return _reject(skill_id, [str(exc)])
    except Exception as exc:
        return _reject(skill_id,[f"validation failed: {type(exc).__name__}"],status="failed")
    try:
        result = skill["handler"](value)
    except ValidationError as exc:
        return _reject(skill_id, [str(exc)])
    except Exception as exc:
        return _reject(skill_id, [f"handler failed: {type(exc).__name__}"], status="failed")
    result.setdefault("skill_id", skill_id)
    return result


def _parse_ts(value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text[-1] in "Zz":
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    naive = moment.tzinfo is None
    if naive:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc), naive


def _iso(moment):
    return moment.isoformat().replace("+00:00", "Z")


def _decode_b64(value, field="data_base64"):
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ValidationError(f"{field} is not valid base64")
    if len(raw) > MAX_B64_DECODED:
        raise ValidationError(f"decoded data exceeds {MAX_B64_DECODED} bytes")
    return raw


def _add(findings, finding_id, **fields):
    entry = {"id": finding_id}
    entry.update(fields)
    findings.append(entry)


def _walk_own(node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        for child in ast.iter_child_nodes(current):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                                  ast.Lambda)):
                continue
            stack.append(child)


_MATCH_CASE = getattr(ast,"match_case",None)

def _cyclomatic(node):
    score = 1
    for sub in _walk_own(node):
        if isinstance(sub, (ast.If, ast.For, ast.While, ast.AsyncFor, ast.ExceptHandler,
                            ast.Assert, ast.IfExp)):
            score += 1
        elif isinstance(sub, ast.BoolOp):
            score += len(sub.values) - 1
        elif isinstance(sub, ast.comprehension):
            score += 1 + len(sub.ifs)
        elif _MATCH_CASE is not None and isinstance(sub, _MATCH_CASE):
            score += 1
    return score


def _h_static_ast_parse(v):
    findings, limitations = [], [LIMIT_LOCAL]
    metrics = {"code_chars": len(v["code"]), "parse_ok": False}
    try:
        tree = ast.parse(v["code"])
    except SyntaxError as exc:
        _add(findings, "parse.syntax_error", line=exc.lineno, offset=exc.offset,
             message=str(exc.msg or "invalid syntax")[:120])
        limitations.append("Source rejected by parser; no recovery parse attempted.")
        return _ok("static.ast.parse", findings, metrics, {"node_counts": {}}, limitations)
    except (RecursionError, MemoryError, ValueError):
        _add(findings, "parse.too_complex", message="parser bounds exceeded")
        limitations.append("Deeply nested source is refused to protect the analyzer.")
        return _ok("static.ast.parse", findings, metrics, {"node_counts": {}}, limitations)
    counts = {}
    for node in ast.walk(tree):
        name = type(node).__name__
        counts[name] = counts.get(name, 0) + 1
    metrics.update(parse_ok=True, total_nodes=sum(counts.values()), distinct_nodes=len(counts))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    data = {"node_counts": dict(sorted(counts.items())),
            "functions": [n.name for n in ast.walk(tree)
                          if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))],
            "classes": [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)],
            "imports": sorted(imports)}
    return _ok("static.ast.parse", findings, metrics, data, limitations)


def _h_static_complexity(v):
    findings, limitations = [], [LIMIT_LOCAL]
    try:
        tree = ast.parse(v["code"])
    except (SyntaxError, RecursionError, MemoryError, ValueError):
        _add(findings, "parse.unparsable", message="source is not parsable Python")
        return _ok("static.complexity", findings, {"functions": 0}, {}, limitations)
    rows = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            score = _cyclomatic(node)
            grade = "low" if score <= 5 else "moderate" if score <= 10 else \
                "high" if score <= 20 else "very_high"
            rows.append({"name": node.name, "line": node.lineno, "complexity": score,
                         "grade": grade})
    rows.sort(key=lambda r: (-r["complexity"], r["name"], r["line"]))
    scores = [r["complexity"] for r in rows]
    mean = round(sum(scores) / len(scores), 2) if scores else 0.0
    metrics = {"functions": len(rows), "max_complexity": max(scores, default=0),
               "mean_complexity": mean,
               "functions_over_10": sum(1 for s in scores if s > 10)}
    if metrics["functions_over_10"]:
        _add(findings, "complexity.high_functions", count=metrics["functions_over_10"],
             threshold=10)
    return _ok("static.complexity", findings, metrics, {"functions": rows}, limitations)


def _h_static_duplication(v):
    findings, limitations = [], [LIMIT_LOCAL, "Window hashing on normalized lines; "
                                 "semantically different but textually equal code counts as duplicate."]
    lines = v["code"].splitlines()
    norm = [re.sub(r"\s+", " ", line.strip()) for line in lines]
    numbered = [(i + 1, text) for i, text in enumerate(norm) if text]
    size = max(4, min(int(v["min_block"] or 6), 20))
    buckets = {}
    for j in range(len(numbered) - size + 1):
        window = numbered[j:j + size]
        digest = hashlib.sha1("\n".join(t for _, t in window).encode("utf-8", "replace")).hexdigest()
        buckets.setdefault(digest, []).append(window[0][0])
    groups = []
    for starts in buckets.values():
        unique = []
        for start in sorted(starts):
            if not unique or start - unique[-1] >= size:
                unique.append(start)
        if len(unique) > 1:
            groups.append({"lines": size, "occurrences": unique})
    groups.sort(key=lambda g: (-len(g["occurrences"]), g["occurrences"]))
    rank={line:index for index,(line,_) in enumerate(numbered)}
    covered=set()
    for group in groups:
        for start in group["occurrences"][1:]:
            covered.update(range(rank[start],min(rank[start]+size,len(numbered))))
    duplicated = len(covered)
    metrics = {"total_nonblank_lines": len(numbered), "duplicate_groups": len(groups),
               "duplicated_lines_min_estimate": duplicated,
               "duplication_ratio": round(duplicated / len(numbered), 3) if numbered else 0.0,
               "block_size": size}
    for group in groups[:50]:
        _add(findings, "duplication.repeated_block", occurrences=len(group["occurrences"]),
             first_lines=group["occurrences"][:5], lines_per_block=size)
    return _ok("static.duplication", findings, metrics, {"groups": groups[:50]}, limitations)


_SECRET_PATTERNS = [
    ("secret.aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    ("secret.private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY( BLOCK)?-----"),
     "private key material"),
    ("secret.github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "GitHub token"),
    ("secret.slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "Slack token"),
    ("secret.google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "Google API key"),
    ("secret.stripe_key", re.compile(r"\b[sp]k_(?:live|test)_[0-9a-zA-Z]{10,}\b"), "Stripe key"),
    ("secret.jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{5,}\b"),
     "JWT"),
    ("secret.bearer_header", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._=+/-]{20,}"),
     "bearer token"),
    ("secret.keyword_assignment",
     re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd|auth)\b\s*[:=]\s*['\"][^'\"]{8,}['\"]"),
     "credential-looking assignment"),
]


def _h_code_secret_scan(v):
    findings, limitations = [], [
        LIMIT_LOCAL,
        "Matches are REDACTED by design; matched values and source excerpts are never returned.",
        "Regex heuristics only; absence of matches does not mean the code is secret-free."]
    hits = 0
    per_pattern = {}
    for lineno, line in enumerate(v["code"].splitlines(), start=1):
        for pattern_id, regex, label in _SECRET_PATTERNS:
            if regex.search(line):
                hits += 1
                per_pattern[pattern_id] = per_pattern.get(pattern_id, 0) + 1
                if len(findings) < MAX_FINDINGS:
                    _add(findings, pattern_id, line=lineno, kind=label, match="REDACTED",
                         recommendation="treat as compromised and rotate")
    metrics = {"lines_scanned": len(v["code"].splitlines()), "matches_redacted": hits,
               "patterns_matched": sorted(per_pattern)}
    return _ok("code.secret.scan", findings, metrics, {"note": "all matches redacted"},
               limitations)


def _h_malware_hash_check(v):
    raw = _decode_b64(v["data_base64"])
    limitations = [LIMIT_LOCAL, LIMIT_NO_CLAIMS,
                   "Local cryptographic digest of the supplied bytes only; no sandbox, "
                   "execution, unpacking, or reputation lookup is performed.",
                   "No verdict such as safe/clean/malicious is issued from a digest alone."]
    metrics = {"size_bytes": len(raw)}
    data = {"size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "sha1": hashlib.sha1(raw).hexdigest(),
            "md5": hashlib.md5(raw).hexdigest()}
    return _ok("malware.hash.check", [], metrics, data, limitations)


_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_DOMAIN_RE = re.compile(r"(?<![a-z0-9.-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.){1,20}[a-z]{2,24}(?![a-z0-9.-])", re.I)
_URL_RE = re.compile(r"\b(?:https?|ftp)://[^\s<>\"'`]+", re.I)
_EMAIL_RE = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}(?![A-Za-z0-9.-])")
_HASH_RES = [re.compile(r"\b[0-9a-f]{%d}\b" % n) for n in (64, 40, 32)]


def _defang(text, keep_at=False):
    out = text
    out = re.sub(r"(?i)\b(https?)://", lambda m: ("hxxps" if m.group(1).lower()=="https" else "hxxp") + "[://]", out)
    out = re.sub(r"(?i)\bftp://", "fxp[://]", out)
    if keep_at:
        out = out.replace("@", "[@]")
    return out.replace(".", "[.]")


def _harvest(text, regex, transform, keep_at=False):
    hits = []

    def replace(match):
        hits.append(transform(match.group(0)))
        return f"\x00{len(hits) - 1}\x00"

    return regex.sub(replace, text), hits


def _h_malware_ioc_extract(v):
    findings, limitations = [], [
        LIMIT_LOCAL, LIMIT_NO_CLAIMS,
        "Indicators are untrusted regex matches from supplied text, returned defanged; "
        "nothing is fetched, resolved, or scored."]
    text = v["text"].replace("\x00", "[NUL]")
    stored=[]
    def harvest(regex, transform):
        hits=[]
        def replace(m):
            value=transform(m.group(0));stored.append(value);hits.append(value)
            return "\x00%d\x00" % (len(stored)-1)
        nonlocal text
        text=regex.sub(replace,text)
        return hits
    urls=harvest(_URL_RE,lambda value:_defang(value))
    emails=harvest(_EMAIL_RE,lambda value:_defang(value,keep_at=True))
    ip_candidates=harvest(_IP_RE,lambda value:_defang(value))
    ips=[]
    for value in ip_candidates:
        try:ipaddress.ip_address(value.replace('[.]','.'));ips.append(value)
        except ValueError:_add(findings,'ioc.invalid_ip_candidate')
    domains=harvest(_DOMAIN_RE,lambda value:_defang(value))
    hashes_=[]
    for regex in _HASH_RES:hashes_.extend(harvest(regex,lambda value:value.lower()))
    restored=re.sub(r"\x00(\d+)\x00",lambda m:stored[int(m.group(1))],text)
    data={"ips":sorted(set(ips))[:200],"domains":sorted(set(domains))[:200],
          "urls":sorted(set(urls))[:200],"emails":sorted(set(emails))[:200],
          "hashes":sorted(set(hashes_))[:200],"defanged_text":restored[:20000]}
    counts = {k: len(data[k]) for k in ("ips", "domains", "urls", "emails", "hashes")}
    metrics = {"indicators_total": sum(counts.values())}
    if sum(counts.values()):
        _add(findings, "ioc.indicators_found", counts=counts,
             trust="untrusted, defanged")
    return _ok("malware.ioc.extract", findings, metrics, data, limitations)


def _bounds_findings(raw, findings, fmt):
    if len(raw) < 4:
        _add(findings, "binary.truncated", detail=f"{fmt} input shorter than magic")
        return False
    return True


def _h_binary_inspect_pe(v):
    findings, limitations = [], [LIMIT_LOCAL,
                                 "Header/section table parsing only; no execution, unpacking, or disassembly."]
    raw = _decode_b64(v["data_base64"])
    metrics = {"size_bytes": len(raw)}
    data = {"format": "PE", "parsed": False}
    if len(raw) < 2 or raw[:2] != b"MZ":
        _add(findings, "pe.bad_dos_magic")
        return _ok("binary.inspect.pe", findings, metrics, data, limitations)
    if len(raw) < 0x40:
        _add(findings, "pe.truncated", detail="file shorter than DOS header")
        return _ok("binary.inspect.pe", findings, metrics, data, limitations)
    lfa = struct.unpack_from("<I", raw, 0x3C)[0]
    data["pe_offset"] = lfa
    if lfa + 24 > len(raw):
        _add(findings, "pe.truncated", detail="PE/COFF header beyond end of file")
        return _ok("binary.inspect.pe", findings, metrics, data, limitations)
    if raw[lfa:lfa + 4] != b"PE\x00\x00":
        _add(findings, "pe.bad_signature")
        return _ok("binary.inspect.pe", findings, metrics, data, limitations)
    machine, nsec, timestamp, _ptr, _nsyms, size_opt, chars = struct.unpack_from(
        "<HHIIIHH", raw, lfa + 4)
    machines = {0x14C: "i386", 0x8664: "x86_64", 0xAA64: "arm64", 0x1C0: "arm"}
    subsystems = {0:"unknown",1:"native",2:"gui",3:"console",5:"os2_console",7:"posix_console",8:"native_windows",9:"wince",10:"efi",11:"efi_boot_driver",12:"efi_runtime_driver",13:"efi_rom",14:"xbox",16:"boot"}
    opt_off = lfa + 24
    if opt_off + size_opt > len(raw):
        _add(findings,"pe.truncated",detail="optional header beyond end of file")
        return _ok("binary.inspect.pe",findings,metrics,data,limitations)
    opt_magic = struct.unpack_from("<H", raw, opt_off)[0] if size_opt >= 2 else None
    subsystem = struct.unpack_from("<H", raw, opt_off + 68)[0] if size_opt >= 70 else None
    if opt_magic not in (0x10B, 0x20B) and opt_magic is not None:
        _add(findings, "pe.unknown_optional_header_magic", magic=hex(opt_magic))
    stamp = None
    if 0 < timestamp < 0x7FFFFFFF:
        stamp = _iso(datetime.fromtimestamp(timestamp, timezone.utc))
    data.update(parsed=True, machine=machines.get(machine, hex(machine)),
                coff_characteristics=hex(chars), optional_header_magic=opt_magic,
                subsystem=subsystems.get(subsystem, subsystem), link_timestamp=stamp)
    sections, sec_off = [], opt_off + size_opt
    for i in range(min(nsec, 96)):
        if sec_off + 40 > len(raw):
            _add(findings, "pe.section_table_truncated", index=i, declared=nsec)
            break
        name, vsize, vaddr, rsize, rptr, _rel, _ln, _nrel, _nln, schars = struct.unpack_from(
            "<8sIIIIIIHHI", raw, sec_off)
        flags = [n for n, bit in (("code", 0x20), ("data", 0x40), ("executable", 0x20000000),
                                  ("readable", 0x40000000), ("writable", 0x80000000))
                 if schars & bit]
        if rptr + rsize > len(raw):
            _add(findings, "pe.section_data_beyond_eof", name=name.split(b"\x00")[0].decode("latin1"),
                 index=i)
        sections.append({"name": name.split(b"\x00")[0].decode("latin1"),
                         "virtual_size": vsize, "virtual_address": vaddr,
                         "raw_size": rsize, "raw_ptr": rptr,
                         "characteristics": hex(schars), "flags": flags})
        sec_off += 40
    if nsec > 96:
        _add(findings, "pe.section_list_capped", declared=nsec, cap=96)
    metrics["sections"] = len(sections)
    data["sections"] = sections
    return _ok("binary.inspect.pe", findings, metrics, data, limitations)


def _h_binary_inspect_elf(v):
    findings, limitations = [], [LIMIT_LOCAL,
                                 "ELF header parsing only; no execution, unpacking, or disassembly."]
    raw = _decode_b64(v["data_base64"])
    metrics = {"size_bytes": len(raw)}
    data = {"format": "ELF", "parsed": False}
    if len(raw) < 4 or raw[:4] != b"\x7fELF":
        _add(findings, "elf.bad_magic")
        return _ok("binary.inspect.elf", findings, metrics, data, limitations)
    if len(raw) < 16:
        _add(findings, "elf.truncated", detail="shorter than e_ident")
        return _ok("binary.inspect.elf", findings, metrics, data, limitations)
    ei_class, ei_data = raw[4], raw[5]
    if ei_class not in (1, 2) or ei_data not in (1, 2):
        _add(findings, "elf.invalid_ident", ei_class=ei_class, ei_data=ei_data)
        return _ok("binary.inspect.elf", findings, metrics, data, limitations)
    endian = "<" if ei_data == 1 else ">"
    is64 = ei_class == 2
    if len(raw) < (64 if is64 else 52):
        _add(findings, "elf.truncated", detail="shorter than fixed header")
        return _ok("binary.inspect.elf", findings, metrics, data, limitations)
    e_type, e_machine = struct.unpack_from(endian + "HH", raw, 16)
    if is64:
        entry, phoff, shoff = struct.unpack_from(endian + "QQQ", raw, 24)
        phentsize, phnum, shentsize, shnum = struct.unpack_from(endian + "HHHH", raw, 54)
    else:
        entry, phoff, shoff = struct.unpack_from(endian + "III", raw, 24)
        phentsize, phnum, shentsize, shnum = struct.unpack_from(endian + "HHHH", raw, 42)
    types = {1: "relocatable", 2: "executable", 3: "shared", 4: "core"}
    machines = {3: "x86", 8: "mips", 40: "arm", 62: "x86_64", 183: "aarch64"}
    data.update(parsed=True, bits=64 if is64 else 32, endian="little" if ei_data == 1 else "big",
                type=types.get(e_type, e_type), machine=machines.get(e_machine, e_machine),
                entry=hex(entry), program_header_entries=phnum, section_header_entries=shnum)
    for label, off, cnt, entsize in (("section", shoff, shnum, shentsize),
                                     ("program", phoff, phnum, phentsize)):
        if cnt and off + cnt * entsize > len(raw):
            _add(findings, "elf.header_table_beyond_eof", table=label,
                 declared_entries=cnt)
    metrics.update(bits=64 if is64 else 32, entry=hex(entry))
    return _ok("binary.inspect.elf", findings, metrics, data, limitations)


def _h_binary_inspect_macho(v):
    findings, limitations = [], [LIMIT_LOCAL,
                                 "Mach-O header and bounded load-command walk only; no execution."]
    raw = _decode_b64(v["data_base64"])
    metrics = {"size_bytes": len(raw)}
    data = {"format": "Mach-O", "parsed": False}
    if len(raw) < 4:
        _add(findings, "macho.truncated", detail="shorter than magic")
        return _ok("binary.inspect.macho", findings, metrics, data, limitations)
    magic_le = struct.unpack_from("<I", raw, 0)[0]
    magics = {0xFEEDFACE: ("<", 32), 0xFEEDFACF: ("<", 64),
              0xCEFAEDFE: (">", 32), 0xCFFAEDFE: (">", 64)}
    if magic_le not in magics:
        _add(findings, "macho.bad_magic", magic=hex(magic_le))
        return _ok("binary.inspect.macho", findings, metrics, data, limitations)
    endian, bits = magics[magic_le]
    if len(raw)<(32 if bits==64 else 28):
        _add(findings,"macho.truncated",detail="shorter than fixed header")
        return _ok("binary.inspect.macho",findings,metrics,data,limitations)
    fields = struct.unpack_from(endian + "IiiIII" + ("I" if bits == 64 else ""), raw, 4)
    cputype, cpusub, ftype, ncmds, sizeofcmds = fields[0], fields[1], fields[2], fields[3], fields[4]
    cpus = {0x01000007: "x86_64", 7: "i386", 0x0100000C: "arm64", 12: "arm"}
    ftypes = {1:"object",2:"execute",3:"fvmlib",4:"core",5:"preload",6:"dylib",7:"dylinker",8:"bundle",9:"dylib_stub",10:"dsym",11:"kext_bundle",12:"fileset"}
    data.update(parsed=True, bits=bits, cpu=cpus.get(abs(cputype), cputype),
                filetype=ftypes.get(ftype, ftype), ncmds=ncmds, sizeofcmds=sizeofcmds)
    off = 32 if bits == 64 else 28
    end_commands=off+sizeofcmds
    if end_commands>len(raw):_add(findings,"macho.commands_beyond_eof")
    command_limit=min(len(raw),end_commands)
    segments, dylibs, cmd_counts = [], [], {}
    for i in range(min(ncmds, 256)):
        if off + 8 > command_limit:
            _add(findings, "macho.load_commands_truncated", index=i, declared=ncmds)
            break
        cmd, cmdsize = struct.unpack_from(endian + "II", raw, off)
        if cmdsize < 8 or off + cmdsize > command_limit:
            _add(findings, "macho.bad_cmdsize", index=i, cmd=hex(cmd), cmdsize=cmdsize)
            break
        cmd_counts[hex(cmd)] = cmd_counts.get(hex(cmd), 0) + 1
        if cmd in (0x1, 0x19):
            if cmdsize<(72 if cmd==0x19 else 56):
                _add(findings,"macho.truncated_segment_command");break
            name = raw[off + 8:off + 24].split(b"\x00")[0].decode("latin1", "replace")
            segments.append({"name": name, "bits": 64 if cmd == 0x19 else 32})
        elif cmd in (0xC, 0xD, 0x18, 0x1F) and len(dylibs) < 64:
            if cmdsize<24:
                _add(findings,"macho.truncated_dylib_command");break
            name_off = struct.unpack_from(endian + "I", raw, off + 8)[0]
            if not 24<=name_off<cmdsize:
                _add(findings,"macho.invalid_dylib_name_offset");break
            start = off + name_off
            chunk = raw[start:min(start + 256,off+cmdsize)].split(b"\x00")[0].decode("latin1", "replace")
            dylibs.append(chunk)
        off += cmdsize
    if ncmds > 256:
        _add(findings, "macho.command_walk_capped", declared=ncmds, cap=256)
    data.update(segments=segments[:64], dylibs=dylibs, load_command_histogram=cmd_counts)
    metrics.update(load_commands_walked=sum(cmd_counts.values()), segments=len(segments),
                   dylibs=len(dylibs))
    return _ok("binary.inspect.macho", findings, metrics, data, limitations)


def _h_binary_strings_extract(v):
    limitations = [LIMIT_LOCAL,
                   "Printable ASCII runs only; no decoding, deobfuscation, or interpretation."]
    raw = _decode_b64(v["data_base64"])
    min_len = max(3, min(v["min_length"] or 4, 64))
    matches = re.findall(rb"[\x20-\x7e]{%d,}" % min_len, raw)
    cap = min(v["max_strings"] or 500, 2000)
    strings = [m.decode("ascii") for m in matches[:cap]]
    metrics = {"size_bytes": len(raw), "min_length": min_len, "total_found": len(matches),
               "returned": len(strings), "truncated": len(matches) > cap}
    data = {"strings": strings, "truncated": metrics["truncated"]}
    return _ok("binary.strings.extract", [], metrics, data, limitations)


_SECURITY_HEADERS = [
    ("content-security-policy", "medium", "click/script injection mitigation"),
    ("x-content-type-options", "low", "MIME sniffing mitigation"),
    ("strict-transport-security", "medium", "downgrade/http MITM mitigation"),
    ("x-frame-options", "low", "clickjacking mitigation"),
    ("referrer-policy", "low", "referrer leakage control"),
    ("permissions-policy", "info", "powerful feature gating"),
]


def _h_web_headers_check(v):
    findings, limitations = [], [
        LIMIT_LOCAL,
        "Judges only the supplied header map; a real deployment may differ (ordering, "
        "duplicates, and set-cookie lists are not observable here)."]
    headers = {str(k).strip().lower(): str(val)[:400] for k, val in v["headers"].items()}
    present, missing = [], []
    for name, severity, why in _SECURITY_HEADERS:
        if name in headers:
            present.append(name)
            if name == "x-content-type-options" and headers[name].strip().lower() != "nosniff":
                _add(findings, "headers.xcto_wrong_value", value=headers[name][:60],
                     expected="nosniff", severity=severity)
            if name == "strict-transport-security" and "max-age" not in headers[name].lower():
                _add(findings, "headers.hsts_without_max_age", severity=severity)
        else:
            missing.append(name)
            _add(findings, "headers.missing", header=name, severity=severity, purpose=why)
    metrics = {"headers_supplied": len(headers), "present": len(present), "missing": len(missing)}
    data = {"headers_normalized": headers, "present": present, "missing": missing}
    return _ok("web.headers.check", findings, metrics, data, limitations)


def _h_web_csp_test(v):
    findings, limitations = [], [
        LIMIT_LOCAL,
        "Static CSP directive review only; does not verify enforcement or browser behavior."]
    directives = {}
    for part in v["csp"].split(";"):
        tokens = part.strip().split()
        if tokens:
            name=tokens[0].lower()
            if name in directives:
                _add(findings,"csp.duplicate_directive_ignored",directive=name)
                continue
            directives[name] = tokens[1:]
    weak = False
    for directive in ("default-src", "script-src"):
        sources = directives.get(directive)
        if sources is None:
            _add(findings, "csp.missing_directive", directive=directive,
                 severity="medium" if directive == "default-src" else "low")
            continue
        joined = " ".join(sources)
        if "'unsafe-inline'" in joined:
            _add(findings, "csp.unsafe_inline", directive=directive, severity="high")
            weak = True
        if "'unsafe-eval'" in joined:
            _add(findings, "csp.unsafe_eval", directive=directive, severity="medium")
            weak = True
        if "*" in sources or re.search(r"(?:^|\s)(?:https?:)?\*(?:\s|$)", joined):
            _add(findings, "csp.wildcard_source", directive=directive, severity="high")
            weak = True
    if "object-src" not in directives:
        _add(findings, "csp.object_src_missing", severity="low")
    if "frame-ancestors" not in directives:
        _add(findings, "csp.frame_ancestors_missing", severity="low",
             note="clickjacking mitigation relies on X-Frame-Options if present")
    metrics = {"directives": len(directives), "weak_config_detected": weak}
    data = {"directives": {k: val for k, val in sorted(directives.items())}}
    return _ok("web.csp.test", findings, metrics, data, limitations)


def _h_web_cookies_analyze(v):
    findings, limitations = [], [
        LIMIT_LOCAL,
        "Cookie values are never returned; only names and attributes are analyzed.",
        "Judges only the supplied Set-Cookie strings; no live requests are made."]
    cookies = []
    for raw_cookie in v["cookies"][:100]:
        first, _, rest = raw_cookie.partition(";")
        name = first.partition("=")[0].strip()[:100]
        attrs = [part.strip().lower() for part in rest.split(";") if part.strip()]
        secure = any(a == "secure" for a in attrs)
        httponly = any(a == "httponly" for a in attrs)
        samesite = None
        max_age = None
        for a in attrs:
            if a.startswith("samesite="):
                samesite = a.split("=", 1)[1].strip()
            elif a.startswith("max-age="):
                try:
                    max_age = int(a.split("=", 1)[1].strip())
                except ValueError:
                    pass
        if not secure:
            _add(findings, "cookie.missing_secure", name=name, severity="medium")
        if not httponly:
            _add(findings, "cookie.missing_httponly", name=name, severity="low")
        if samesite is None:
            _add(findings, "cookie.missing_samesite", name=name, severity="low")
        elif samesite == "none" and not secure:
            _add(findings, "cookie.samesite_none_insecure", name=name, severity="medium")
        if max_age is not None and max_age > 31536000:
            _add(findings, "cookie.overlong_max_age", name=name, max_age=max_age, severity="info")
        cookies.append({"name": name, "secure": secure, "httponly": httponly,
                        "samesite": samesite, "max_age": max_age,
                        "attribute_count": len(attrs)})
    metrics = {"cookies": len(cookies), "findings": len(findings)}
    data = {"cookies": cookies}
    return _ok("web.cookies.analyze", findings, metrics, data, limitations)


def _h_ip_lookup(v):
    limitations = [LIMIT_LOCAL, LIMIT_NO_CLAIMS,
                   "Classification uses Python ipaddress semantics only; no geo, ASN, "
                   "proxy, VPN, or reputation data is available or guessed."]
    try:
        address = ipaddress.ip_address(v["ip"].strip())
    except ValueError:
        raise ValidationError("ip is not a valid IPv4/IPv6 address")
    labels = []
    checks = {"is_loopback": address.is_loopback, "is_link_local": address.is_link_local,
              "is_multicast": address.is_multicast, "is_private": address.is_private,
              "is_reserved": address.is_reserved, "is_unspecified": address.is_unspecified,
              "is_global": address.is_global}
    if address.version == 6 and address.ipv4_mapped:
        checks["ipv4_mapped"] = address.ipv4_mapped.compressed
    for label in ("is_loopback", "is_link_local", "is_multicast", "is_private",
                  "is_reserved", "is_unspecified"):
        if checks[label]:
            labels.append(label[3:])
    scope = "global" if address.is_global else (labels[0] if labels else "other")
    findings = []
    if labels:
        _add(findings, "ip.non_global_scope", scope=scope,
             note="non-global addresses are not routable on the public internet")
    data = {"ip": address.compressed, "version": address.version, "scope": scope,
            "classification": checks}
    metrics = {"version": address.version, "scope": scope}
    return _ok("ip.lookup", findings, metrics, data, limitations)


_UA_BOT_TOKENS = ["bot", "crawler", "spider", "slurp", "scrape", "curl", "wget",
                  "python-requests", "python-urllib", "go-http-client", "java/",
                  "libwww", "okhttp", "axios", "httpclient", "phantomjs", "headless",
                  "selenium", "puppeteer", "playwright", "electron"]


def _h_bot_useragent_analyze(v):
    findings, limitations = [], [
        LIMIT_LOCAL, LIMIT_NO_CLAIMS,
        "Suspicion heuristics only; user agents are trivially spoofed, so no bot/human "
        "identity or verdict is claimed."]
    ua = v["user_agent"]
    lowered = ua.lower()
    signals = []
    if not ua.strip():
        signals.append(("ua.empty", 40))
    elif len(ua) < 20:
        signals.append(("ua.very_short", 25))
    matched_tokens = [tok for tok in _UA_BOT_TOKENS if tok in lowered]
    if matched_tokens:
        signals.append(("ua.automation_token", 35))
    if "()" in ua:
        signals.append(("ua.empty_token_parens", 10))
    if "/" not in ua:
        signals.append(("ua.no_version_separator", 10))
    score = min(100, sum(weight for _, weight in signals))
    for signal_id, _ in signals:
        _add(findings, signal_id, tokens=matched_tokens[:5] if signal_id == "ua.automation_token" else None)
    verdict = "high_suspicion" if score >= 60 else "moderate_suspicion" if score >= 30 else "low_suspicion"
    metrics = {"suspicion_score": score, "verdict": verdict, "signals": len(signals)}
    data = {"signals": [s[0] for s in signals], "matched_tokens": matched_tokens,
            "suspicion_score": score, "verdict": verdict}
    return _ok("bot.useragent.analyze", findings, metrics, data, limitations)


def _collect_events(v, findings, kind):
    events = []
    for index, event in enumerate(v["events"]):
        parsed = _parse_ts(event.get("timestamp", ""))
        if not parsed:
            _add(findings, "event.invalid_timestamp", index=index)
            continue
        if parsed[1]:
            _add(findings, "event.naive_timestamp_assumed_utc", index=index) if kind == "flag_naive" else None
        events.append((parsed[0], event))
    events.sort(key=lambda pair: pair[0])
    return events


def _window_extremes(events, window_seconds, amount_of):
    window = timedelta(seconds=window_seconds)
    best_count, best_amount, j, running = 0, Decimal(0), 0, Decimal(0)
    amounts = [Decimal(str(amount_of(event) or 0)) for _, event in events]
    for i, (moment, _event) in enumerate(events):
        running += amounts[i]
        while j <= i and moment - events[j][0] > window:
            running -= amounts[j]
            j += 1
        if i - j + 1 > best_count:
            best_count = i - j + 1
        best_amount=max(best_amount,running)
    return best_count, float(round(best_amount, 2))


def _velocity_result(skill_id, events, v, findings, limitations, scope_label, scope_key=None):
    total = len(events)
    if not total:
        _add(findings, "velocity.no_valid_events", note="cannot compute any window")
        return _ok(skill_id, findings, {"events_valid": 0, "events_invalid": len(v["events"])},
                   {}, limitations + ["Uncertainty: timestamp arithmetic on caller fixtures "
                                      "only; precision, clock skew, and timezone are not verified."])
    best_count, best_amount = _window_extremes(
        events if scope_key is None else events, v["window_seconds"],
        lambda e: e.get("amount"))
    exceeded = best_count >= v["threshold"]
    metrics = {"events_valid": total, "events_invalid": len(v["events"]) - total
               if scope_key is None else 0,
               "window_seconds": v["window_seconds"], "threshold": v["threshold"],
               "max_events_in_window": best_count, "max_amount_in_window": best_amount,
               "first": _iso(events[0][0]), "last": _iso(events[-1][0])}
    if exceeded:
        _add(findings, "velocity.threshold_exceeded", scope=scope_label,
             count=best_count, threshold=v["threshold"], window_seconds=v["window_seconds"])
    return _ok(skill_id, findings, metrics, {}, limitations + [
        "Uncertainty: timestamp arithmetic on caller fixtures only; ordering, precision, "
        "clock skew, and timezone assumptions are not verified against any authority."])


def _h_payment_velocity_check(v):
    findings, limitations = [], [LIMIT_LOCAL, LIMIT_NO_CLAIMS]
    events = _collect_events(v, findings, "flag_naive")
    return _velocity_result("payment.velocity.check", events, v, findings, limitations,
                            "all_events")


def _h_behavior_velocity(v):
    findings, limitations = [], [LIMIT_LOCAL, LIMIT_NO_CLAIMS]
    events = _collect_events(v, findings, "no_naive")
    if not events:
        _add(findings, "velocity.no_valid_events")
        return _ok("behavior.velocity", findings,
                   {"events_valid": 0, "events_invalid": len(v["events"])}, {}, limitations)
    groups = {}
    for moment, event in events:
        groups.setdefault(str(event.get("action", "unknown"))[:80], []).append(
            (moment, {"amount": None}))
    per_action = {}
    for action in sorted(groups):
        times = groups[action]
        best_count, _ = _window_extremes(times, v["window_seconds"], lambda e: None)
        per_action[action] = {"total": len(times), "max_in_window": best_count}
        if best_count >= v["threshold"] and len(findings) < MAX_FINDINGS:
            _add(findings, "velocity.threshold_exceeded", scope=action, count=best_count,
                 threshold=v["threshold"], window_seconds=v["window_seconds"])
    metrics = {"events_valid": len(events), "events_invalid": len(v["events"]) - len(events),
               "window_seconds": v["window_seconds"], "threshold": v["threshold"],
               "actions": per_action}
    return _ok("behavior.velocity", findings, metrics, {}, limitations + [
        "Uncertainty: timestamp arithmetic on caller fixtures only; per-action windows are "
        "computed independently and clocks are not verified."])


_SPAM_KEYWORDS = ["free", "winner", "prize", "urgent", "act now", "limited time",
                  "click here", "buy now", "risk-free", "guarantee", "congratulations",
                  "exclusive deal", "loan approved", "forex", "crypto"]


def _h_nlu_spam_detect(v):
    findings, limitations = [], [
        LIMIT_LOCAL,
        "Signal-based scoring only; not a verdict, not identity, no sender reputation."]
    text = v["text"]
    lowered = text.lower()
    letters = [c for c in text if c.isalpha()]
    caps_ratio = round(sum(1 for c in letters if c.isupper()) / len(letters), 3) if letters else 0.0
    signals = []
    if len(letters) >= 20 and caps_ratio > 0.5:
        signals.append("caps_shouting")
    exclamations = text.count("!")
    if exclamations >= 3:
        signals.append("excessive_exclamation")
    if len(re.findall(r"(.)\1{3,}", text)) >= 2:
        signals.append("repeated_character_runs")
    url_count = len(_URL_RE.findall(text))
    if url_count >= 2:
        signals.append("multiple_urls")
    keywords = []
    for keyword in _SPAM_KEYWORDS:
        count = lowered.count(keyword)
        if count:
            keywords.append({"keyword": keyword, "count": count})
    if sum(k["count"] for k in keywords) >= 2:
        signals.append("spam_keywords")
    if re.search(r"(?i)\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b", text):
        signals.append("digit_sequence_pattern")
    score = min(100, 20 * len(signals) + 5 * sum(k["count"] for k in keywords))
    for signal in signals:
        _add(findings, "spam.signal_" + signal)
    verdict = "spam_signals_high" if score >= 60 else "mixed_signals" if score >= 30 else "low_signals"
    metrics = {"score": score, "verdict": verdict, "caps_ratio": caps_ratio,
               "exclamations": exclamations, "urls": url_count}
    data = {"signals": signals, "keywords": keywords[:10], "verdict": verdict}
    return _ok("nlu.spam.detect", findings, metrics, data, limitations)


_CARD_RE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
_PHONE_RE = re.compile(r"\+?\d(?:[\d(). -]{7,}\d)?")
_EMAIL_MASK_RE = _EMAIL_RE


def _luhn(digits):
    total, parity = 0, len(digits) % 2
    for i, digit in enumerate(digits):
        d = int(digit)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _h_security_pii_mask(v):
    findings, limitations = [], [
        LIMIT_LOCAL,
        "Regex-based masking; unusual formats may be missed and legitimate numbers may "
        "be masked. Masked output is for display, not reversible."]
    text = v["text"]
    counts = {"card": 0, "id_number": 0, "number": 0, "email": 0, "phone": 0}

    def mask_card(match):
        digits = re.sub(r"\D", "", match.group(0))
        if not 13 <= len(digits) <= 19:
            return match.group(0)
        if _luhn(digits):
            counts["card"] += 1
            return "[CARD]"
        if len(digits) == 16:
            counts["id_number"] += 1
            return "[ID-NUMBER]"
        counts["number"] += 1
        return "[NUMBER]"

    def mask_international_phone(match):
        digits=re.sub(r"\D","",match.group(0))
        if 9<=len(digits)<=15:
            counts["phone"]+=1
            return "[PHONE]"
        return match.group(0)
    text = re.sub(r"(?<![A-Za-z0-9])\+\d[\d(). -]{7,}\d",mask_international_phone,text)
    text = _CARD_RE.sub(mask_card, text)

    def mask_email(match):
        counts["email"] += 1
        return "[EMAIL]"

    text = _EMAIL_MASK_RE.sub(mask_email, text)

    def mask_phone(match):
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) < 9 or len(digits) > 15:
            return match.group(0)
        counts["phone"] += 1
        return "[PHONE]"

    text = _PHONE_RE.sub(mask_phone, text)
    masked_any = sum(counts.values())
    if masked_any:
        _add(findings, "pii.masked", counts=dict(counts),
             note="originals are not included in this result")
    metrics = {"replacements": masked_any, "text_chars": len(v["text"])}
    data = {"masked_text": text, "counts": counts}
    return _ok("security.pii.mask", findings, metrics, data, limitations)


_ANTIPATTERN_PHRASES = [
    "in today's fast-paced world", "in conclusion", "it is important to note",
    "it's important to note", "delve into", "dive deep into", "unlock the power",
    "game-changer", "game changer", "revolutionize", "seamlessly integrate",
    "cutting-edge", "in the realm of", "ever-evolving", "testament to",
    "navigate the complexities", "at the end of the day", "when all is said and done",
    "furthermore", "moreover", "additionally", "firstly", "secondly", "lastly"]
_TRANSITIONS = {"furthermore", "moreover", "additionally", "firstly", "secondly", "lastly"}


def _h_antipattern_detect(v):
    findings, limitations = [], [
        LIMIT_LOCAL,
        "Readability feedback only; explicitly no promise of defeating AI detectors or "
        "any guarantee about detector outcomes."]
    text = v["text"]
    lowered = text.lower()
    matches = []
    for phrase in _ANTIPATTERN_PHRASES:
        count = lowered.count(phrase)
        if count:
            matches.append({"phrase": phrase, "count": count})
    matches.sort(key=lambda m: (-m["count"], m["phrase"]))
    transition_total = sum(m["count"] for m in matches if m["phrase"] in _TRANSITIONS)
    signals = []
    if transition_total >= 3:
        signals.append("transition_overuse")
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    if len(sentences) >= 3 and sum(1 for s in sentences if s.endswith("?")) / len(sentences) > 0.3:
        signals.append("rhetorical_question_heavy")
    body_lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(body_lines) >= 4 and sum(1 for ln in body_lines if re.match(r"\s*(?:[-*]|\d+[.)])\s", ln)) / len(body_lines) >= 0.5:
        signals.append("listicle_structure")
    for signal in signals:
        _add(findings, "antipattern." + signal)
    metrics = {"phrase_matches": sum(m["count"] for m in matches), "distinct_phrases": len(matches),
               "signals": signals, "sentences": len(sentences)}
    data = {"matches": matches[:50], "signals": signals}
    return _ok("antipattern.detect", findings, metrics, data, limitations)


def _h_score_burstiness(v):
    findings, limitations = [], [
        LIMIT_LOCAL,
        "Approximation from sentence word-count variance; not an AI-detector verdict and "
        "no detector-evasion outcome is promised."]
    text = v["text"]
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    lengths = [len(s.split()) for s in sentences]
    if len(lengths) < 2:
        _add(findings, "burstiness.insufficient_sentences", sentences=len(lengths), minimum=2)
        return _ok("score.burstiness", findings, {"sentences": len(lengths)}, {}, limitations)
    n = len(lengths)
    mean = sum(lengths) / n
    stdev = (sum((x - mean) ** 2 for x in lengths) / n) ** 0.5
    cv = stdev / mean if mean else 0.0
    label = "low" if cv < 0.35 else "moderate" if cv < 0.7 else "high"
    metrics = {"sentences": n, "mean_words": round(mean, 2), "stdev_words": round(stdev, 2),
               "cv": round(cv, 3), "burstiness": label}
    data = {"sentence_lengths": lengths[:500], "truncated": n > 500}
    return _ok("score.burstiness", findings, metrics, data, limitations)


def _h_brief_outline_create(v):
    findings, limitations = [], [
        LIMIT_LOCAL,
        "Structural scaffold built only from the caller-supplied topic, audience, and "
        "points; no research, no invented facts, stories, or claims."]
    topic, audience = v["topic"].strip(), (v["audience"] or "").strip()
    points = [p.strip() for p in (v["points"] or []) if p.strip()][:12]
    sections = v["sections"] or 5
    outline = [{"level": 1, "title": topic, "bullets": []}]
    intro_bullets = []
    if audience:
        intro_bullets.append(f"Audience: {audience}")
    intro_bullets.append("State why this topic matters [add point]")
    outline.append({"level": 2, "title": "Introduction", "bullets": intro_bullets})
    body = max(sections - 2, 0)
    for i in range(body):
        if i < len(points):
            outline.append({"level": 2, "title": f"Section {i + 1}: {points[i][:60]}",
                            "bullets": [points[i]]})
        else:
            outline.append({"level": 2, "title": f"Section {i + 1} [to be supplied]",
                            "bullets": ["[add point for this section]"]})
    if len(points) > body:
        _add(findings, "outline.points_truncated", supplied=len(points), used=body)
    if not points:
        _add(findings, "outline.no_points_supplied",
             note="scaffold contains placeholders instead of content")
    outline.append({"level": 2, "title": "Conclusion",
                    "bullets": ["Summarize the supplied points", "Call to action [add point]"]})
    metrics = {"sections": len(outline), "points_supplied": len(points),
               "points_used": min(len(points), body)}
    data = {"outline": outline}
    return _ok("brief.outline.create", findings, metrics, data, limitations)


_CVE_FIELDS = ("id", "published", "severity", "cvss", "summary", "source")
_SEV_MAP = {"critical": "CRITICAL", "high": "HIGH", "important": "HIGH",
            "moderate": "MEDIUM", "medium": "MEDIUM", "low": "LOW",
            "negligible": "LOW", "none": "NONE", "info": "NONE", "informational": "NONE"}
_CVE_ID_RE = re.compile(r"^CVE-\d{4}-\d{4,}$", re.I)


def _norm_cve_record(record, findings):
    out = {"id": str(record.get("id", "")).strip().upper()[:60], "published": None,
           "severity": None, "cvss": None, "summary": "", "source": None}
    out["id_valid"] = bool(_CVE_ID_RE.fullmatch(out["id"]))
    if out["id"] and not out["id_valid"]:
        _add(findings, "cve.invalid_id", cve_id=out["id"])
    parsed = _parse_ts(record.get("published")) if isinstance(record.get("published"), str) else None
    if parsed:
        out["published"] = _iso(parsed[0])
    else:
        _add(findings, "cve.invalid_or_missing_date", cve_id=out["id"] or "?")
    cvss = record.get("cvss")
    if isinstance(cvss, (int, float)) and not isinstance(cvss, bool) and 0 <= float(cvss) <= 10:
        out["cvss"] = round(float(cvss), 1)
    elif cvss is not None:
        _add(findings, "cve.invalid_cvss", cve_id=out["id"] or "?", cvss=cvss)
    severity = str(record.get("severity") or "").strip().lower()
    normalized = _SEV_MAP.get(severity)
    if severity and not normalized:
        _add(findings, "cve.unknown_severity", cve_id=out["id"] or "?", severity=severity[:30])
    if not normalized and out["cvss"] is not None:
        normalized = ("CRITICAL" if out["cvss"] >= 9 else "HIGH" if out["cvss"] >= 7
                      else "MEDIUM" if out["cvss"] >= 4 else "LOW" if out["cvss"] > 0 else "NONE")
    out["severity"] = normalized
    out["summary"] = str(record.get("summary") or "")[:300]
    if record.get("source"):
        out["source"] = str(record["source"])[:60]
    dropped = sorted(set(record) - set(_CVE_FIELDS))
    if dropped:
        _add(findings, "cve.dropped_fields", cve_id=out["id"] or "?", fields=dropped[:10])
    return out


def _load_cve_records(v):
    findings = []
    records = [_norm_cve_record(r, findings) for r in v["records"]]
    return records, findings


_CVE_SCHEMA = _f_list(True, MAX_LIST_ITEMS, _f_dict(False, {}, allow_unknown=True))


def _h_normalize_cve_schema(v):
    limitations = [LIMIT_LOCAL, LIMIT_NO_CLAIMS,
                   "Normalizes only the supplied records; no live CVE feed is queried and "
                   "freshness/completeness is never claimed.",
                   "Severity derivation from CVSS uses common v3 qualitative ranges; "
                   "vendor scales may differ."]
    records, findings = _load_cve_records(v)
    metrics = {"records": len(records),
               "valid_ids": sum(1 for r in records if r["id_valid"]),
               "with_published_date": sum(1 for r in records if r["published"]),
               "with_severity": sum(1 for r in records if r["severity"])}
    data = {"records": records}
    return _ok("normalize.cve_schema", findings, metrics, data, limitations)


def _h_sort_by_date(v, descending):
    skill_id = "sort.by_date.desc" if descending else "sort.by_date.asc"
    limitations = [LIMIT_LOCAL, LIMIT_NO_CLAIMS,
                   "Sorts only supplied records; invalid or missing dates sort last by id, "
                   "which is a convention, not a claim about recency."]
    records, findings = _load_cve_records(v)
    valid = [r for r in records if r["published"]]
    invalid = sorted((r for r in records if not r["published"]), key=lambda r: r["id"])
    valid.sort(key=lambda r: _parse_ts(r["published"])[0], reverse=descending)
    metrics = {"records": len(records), "sorted_by_date": len(valid),
               "undated_last": len(invalid), "order": "newest_first" if descending else "oldest_first"}
    return _ok(skill_id, findings, metrics, {"records": valid + invalid}, limitations)


def _h_sort_by_severity_desc(v):
    limitations = [LIMIT_LOCAL, LIMIT_NO_CLAIMS,
                   "Orders supplied records only; unknown severity ranks lowest and is "
                   "flagged, never upgraded."]
    rank = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}
    records, findings = _load_cve_records(v)
    records.sort(key=lambda r: (-rank.get(r["severity"], -1), -(r["cvss"] if r["cvss"] is not None else -1.0), r["id"]))
    metrics = {"records": len(records),
               "critical": sum(1 for r in records if r["severity"] == "CRITICAL"),
               "unranked": sum(1 for r in records if r["severity"] is None)}
    return _ok("sort.by_severity.desc", findings, metrics, {"records": records}, limitations)


def _h_filter_by_date_range(v):
    limitations = [LIMIT_LOCAL, LIMIT_NO_CLAIMS,
                   "Range filter is inclusive on both ends using parsed UTC instants; "
                   "records without a valid published date are excluded and flagged."]
    start, end = _parse_ts(v["start"]), _parse_ts(v["end"])
    if not start or not end:
        raise ValidationError("start and end must be ISO 8601 timestamps")
    if start[0] > end[0]:
        raise ValidationError("start must not be after end")
    records, findings = _load_cve_records(v)

    def in_range(record):
        if not record["published"]:
            _add(findings, "filter.excluded_no_date", cve_id=record["id"] or "?")
            return False
        moment = _parse_ts(record["published"])[0]
        return start[0] <= moment <= end[0]

    kept = [r for r in records if in_range(r)]
    metrics = {"records": len(records), "kept": len(kept),
               "range_start": _iso(start[0]), "range_end": _iso(end[0])}
    return _ok("filter.by_date_range", findings, metrics, {"records": kept}, limitations)


def _h_filter_by_severity(v):
    limitations = [LIMIT_LOCAL, LIMIT_NO_CLAIMS,
                   "Severity filtering over supplied records only; unknown requested "
                   "levels and unranked records are flagged, not silently dropped."]
    wanted = sorted({_SEV_MAP.get(s.strip().lower()) for s in v["severities"] if _SEV_MAP.get(s.strip().lower())})
    unknown = sorted({s.strip() for s in v["severities"] if not _SEV_MAP.get(s.strip().lower())})
    records, findings = _load_cve_records(v)
    kept, unranked = [], 0
    for record in records:
        if record["severity"] in wanted:
            kept.append(record)
        elif record["severity"] is None:
            unranked += 1
    if unknown:
        _add(findings, "filter.unknown_severity_requested", levels=unknown[:10])
    if unranked:
        _add(findings, "filter.unranked_records_excluded", count=unranked)
    metrics = {"records": len(records), "kept": len(kept), "unranked_excluded": unranked,
               "wanted": wanted}
    return _ok("filter.by_severity", findings, metrics, {"records": kept}, limitations)


def _cve_sort_schema(descending):
    return {"records": _CVE_SCHEMA}


SKILLS = {
    "static.ast.parse": {
        "description": "Parse client-supplied Python source into an AST and report node counts, functions, classes, and imports. No execution.",
        "input_schema": {"code": _f_str(True, MAX_CODE_CHARS)},
        "handler": _h_static_ast_parse,
    },
    "static.complexity": {
        "description": "Compute per-function cyclomatic complexity from the Python AST with low/moderate/high grading.",
        "input_schema": {"code": _f_str(True, MAX_CODE_CHARS)},
        "handler": _h_static_complexity,
    },
    "static.duplication": {
        "description": "Detect duplicated normalized line blocks in supplied source via bounded window hashing.",
        "input_schema": {"code": _f_str(True, MAX_CODE_CHARS),
                         "min_block": {**_f_int(False, 4, 20), "default": 6}},
        "handler": _h_static_duplication,
    },
    "code.secret.scan": {
        "description": "Regex scan for credential-shaped strings; every match is REDACTED and never echoed with source context.",
        "input_schema": {"code": _f_str(True, MAX_CODE_CHARS)},
        "handler": _h_code_secret_scan,
    },
    "malware.hash.check": {
        "description": "Compute local MD5/SHA1/SHA-256 digests of base64-supplied bytes. No reputation or verdict claims.",
        "input_schema": {"data_base64": _f_str(True, 400000)},
        "handler": _h_malware_hash_check,
    },
    "malware.ioc.extract": {
        "description": "Extract and DEFANG untrusted IPs, domains, URLs, emails, and hashes from supplied text. Nothing is fetched.",
        "input_schema": {"text": _f_str(True, MAX_TEXT_CHARS)},
        "handler": _h_malware_ioc_extract,
    },
    "binary.inspect.pe": {
        "description": "Bounded parse of PE/COFF headers and section table from base64 bytes; flags truncation instead of failing.",
        "input_schema": {"data_base64": _f_str(True, 400000)},
        "handler": _h_binary_inspect_pe,
    },
    "binary.inspect.elf": {
        "description": "Bounded parse of ELF identification and fixed header from base64 bytes; flags truncation and bad tables.",
        "input_schema": {"data_base64": _f_str(True, 400000)},
        "handler": _h_binary_inspect_elf,
    },
    "binary.inspect.macho": {
        "description": "Bounded parse of Mach-O header with capped load-command walk, segment names, and dylib ids.",
        "input_schema": {"data_base64": _f_str(True, 400000)},
        "handler": _h_binary_inspect_macho,
    },
    "binary.strings.extract": {
        "description": "Extract bounded printable-ASCII runs from base64 bytes with configurable minimum length and cap.",
        "input_schema": {"data_base64": _f_str(True, 400000),
                         "min_length": {**_f_int(False, 3, 64), "default": 4},
                         "max_strings": {**_f_int(False, 1, 2000), "default": 500}},
        "handler": _h_binary_strings_extract,
    },
    "web.headers.check": {
        "description": "Check supplied HTTP response headers for security headers and weak values. No live requests.",
        "input_schema": {"headers": _f_dict(True, None, True)},
        "handler": _h_web_headers_check,
    },
    "web.csp.test": {
        "description": "Static review of a supplied Content-Security-Policy string for unsafe-inline/eval, wildcards, and missing directives.",
        "input_schema": {"csp": _f_str(True, 8192)},
        "handler": _h_web_csp_test,
    },
    "web.cookies.analyze": {
        "description": "Analyze supplied Set-Cookie strings for Secure/HttpOnly/SameSite and lifetime; values are never returned.",
        "input_schema": {"cookies": _f_list(True, 100, _f_str(True, 4096))},
        "handler": _h_web_cookies_analyze,
    },
    "ip.lookup": {
        "description": "Classify a supplied IP with the ipaddress module (scope, loopback, private, multicast). No geo or reputation.",
        "input_schema": {"ip": _f_str(True, 45)},
        "handler": _h_ip_lookup,
    },
    "bot.useragent.analyze": {
        "description": "Score a supplied user-agent string with spoofable automation heuristics. Suspicion signals, not identity.",
        "input_schema": {"user_agent": _f_str(True, 512)},
        "handler": _h_bot_useragent_analyze,
    },
    "payment.velocity.check": {
        "description": "Sliding-window timestamp arithmetic over caller-supplied payment events with threshold findings and uncertainty notes.",
        "input_schema": {
            "events": _f_list(True, MAX_LIST_ITEMS, _f_dict(False, {
                "timestamp": _f_str(True, 40), "amount": {"type": "num", "required": False,"min":0,"max":10**12},
            }, allow_unknown=False)),
            "window_seconds": _f_int(True, 1, 2592000),
            "threshold": _f_int(True, 1, 10000)},
        "handler": _h_payment_velocity_check,
    },
    "behavior.velocity": {
        "description": "Per-action sliding-window counts over caller-supplied event fixtures; heuristic rate signals with uncertainty.",
        "input_schema": {
            "events": _f_list(True, MAX_LIST_ITEMS, _f_dict(False, {
                "timestamp": _f_str(True, 40), "action": _f_str(True, 80),
            }, allow_unknown=False)),
            "window_seconds": _f_int(True, 1, 2592000),
            "threshold": _f_int(True, 1, 10000)},
        "handler": _h_behavior_velocity,
    },
    "nlu.spam.detect": {
        "description": "Signal-based spam heuristics (caps, punctuation, repetition, keyword density) on supplied text. Signals only.",
        "input_schema": {"text": _f_str(True, MAX_TEXT_CHARS)},
        "handler": _h_nlu_spam_detect,
    },
    "security.pii.mask": {
        "description": "Mask emails, Luhn-valid cards, phone-like numbers, and long digit runs in supplied text with fixed placeholders.",
        "input_schema": {"text": _f_str(True, MAX_TEXT_CHARS)},
        "handler": _h_security_pii_mask,
    },
    "antipattern.detect": {
        "description": "Detect AI-content cliches, transition overuse, and listicle structure as readability feedback. No detector promises.",
        "input_schema": {"text": _f_str(True, MAX_TEXT_CHARS)},
        "handler": _h_antipattern_detect,
    },
    "score.burstiness": {
        "description": "Compute sentence-length variance (mean, stdev, coefficient of variation) as a burstiness indicator.",
        "input_schema": {"text": _f_str(True, MAX_TEXT_CHARS)},
        "handler": _h_score_burstiness,
    },
    "brief.outline.create": {
        "description": "Build a deterministic outline from the caller-supplied topic and points with placeholders; invents no content.",
        "input_schema": {"topic": _f_str(True, 200), "audience": _f_str(False, 200),
                         "points": _f_list(False, 12, _f_str(True, 200)),
                         "sections": {**_f_int(False, 3, 10), "default": 5}},
        "handler": _h_brief_outline_create,
    },
    "normalize.cve_schema": {
        "description": "Normalize supplied CVE records: validate ids and dates, map severities, derive severity from CVSS, drop unknown fields.",
        "input_schema": {"records": _CVE_SCHEMA},
        "handler": _h_normalize_cve_schema,
    },
    "sort.by_date.desc": {
        "description": "Sort supplied CVE records newest-first by valid published date; invalid dates go last, flagged deterministically.",
        "input_schema": {"records": _CVE_SCHEMA},
        "handler": lambda v: _h_sort_by_date(v, True),
    },
    "sort.by_date.asc": {
        "description": "Sort supplied CVE records oldest-first by valid published date; invalid dates go last, flagged deterministically.",
        "input_schema": {"records": _CVE_SCHEMA},
        "handler": lambda v: _h_sort_by_date(v, False),
    },
    "sort.by_severity.desc": {
        "description": "Order supplied CVE records by severity rank, then CVSS, then id; unknown severity ranks lowest and is flagged.",
        "input_schema": {"records": _CVE_SCHEMA},
        "handler": _h_sort_by_severity_desc,
    },
    "filter.by_date_range": {
        "description": "Keep supplied CVE records whose published date falls in an inclusive ISO 8601 range; undated records are flagged out.",
        "input_schema": {"records": _CVE_SCHEMA, "start": _f_str(True, 40), "end": _f_str(True, 40)},
        "handler": _h_filter_by_date_range,
    },
    "filter.by_severity": {
        "description": "Keep supplied CVE records matching normalized severity levels; unknown levels and unranked records are flagged.",
        "input_schema": {"records": _CVE_SCHEMA, "severities": _f_list(True, 10, _f_str(True, 30))},
        "handler": _h_filter_by_severity,
    },
}
