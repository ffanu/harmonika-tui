"""Harmonika web_builder.planning - pure planning module (worker scope).

Scope: numbered skill IDs 1-46 and 134-151 from the Harmonika full-stack web
builder engine catalog (see backend/web_builder/catalog_source.json). Six of
these IDs are actual file/API/voice readers (spec.read.markdown, spec.read.txt,
spec.read.json, spec.read.notation, spec.read.obsidian, spec.read.voice); the
file/network access for them is owned by the root integration, and dispatch()
returns an explicit root-reader marker for them. Every other owned ID is
handled here as pure, deterministic planning over text and structured args.

Guarantees:
- No filesystem, network, or secret access; no host shell. `writes` entries are
  content payloads (relative path + string content) for the caller to write.
- Deterministic, rule-based heuristics only. No model inference, no cost
  estimates, no pinned dependency versions, and no claim that anything was
  built, deployed, executed, or tested.
- Standard library only.

Exact interface:
- dispatch(skill_id: str, args: dict | None) -> dict with keys
  status ("completed" | "root_reader" | "not_owned"), skill_id,
  execution_kind ("analysis" | "artifact_generation"), data (dict),
  writes (list of {"path": relative str, "content": str}), limitations (list).
- schema(skill_id: str) -> JSON Schema (dict) for one owned skill ID.
- owned_skills() -> list of {"number", "id", "execution_kind", "handled_by"}.
"""

import colorsys
import hashlib
import html
import json
import random
import re
import unicodedata
import urllib.parse

PLANNING_SCOPE = "backend/web_builder/planning.py"

ROOT_READER_IDS = (
    "spec.read.markdown",
    "spec.read.txt",
    "spec.read.json",
    "spec.read.notation",
    "spec.read.obsidian",
    "spec.read.voice",
)

GLOBAL_LIMITS = (
    "Pure planning: this module performs no filesystem, network, or secret access and runs no host shell.",
    "'writes' entries are content payloads for the caller to materialize; this module writes nothing itself.",
    "Planning output only: nothing has been built, deployed, executed, or tested against a live environment.",
)

NO_COST_NO_VERSION_LIMIT = (
    "No prices or cost estimates are provided. No dependency versions are pinned; pin the latest stable release at scaffold time and verify licenses.",
)


class SkillInputError(ValueError):
    pass


class SkillNotOwned(KeyError):
    pass


FEATURE_KEYWORDS = {
    "auth": ["auth", "login", "logout", "register", "sign up", "sign in", "daftar akun", "sesi", "user account"],
    "payments": ["payment", "pembayaran", "payment gateway", "midtrans", "xendit", "stripe", "paypal", "doku"],
    "cart": ["cart", "keranjang"],
    "checkout": ["checkout"],
    "catalog": ["catalog", "katalog", "produk", "product", "toko online", "online shop", "web store", "listing"],
    "orders": ["order", "pesanan", "order tracking", "tracking order"],
    "admin": ["admin", "back office", "backoffice", "user management", "manajemen user"],
    "dashboard": ["dashboard", "panel admin"],
    "blog": ["blog", "artikel", "article", "berita", "news", "postingan"],
    "search": ["search", "pencarian", "filter", "sorting"],
    "realtime": ["real-time", "realtime", "websocket", "live chat", "chat langsung"],
    "chat": ["chat", "messaging", "pesan antar pengguna"],
    "notifications": ["notification", "notifikasi", "email notification", "push notification"],
    "upload": ["upload", "unggah"],
    "reviews": ["review", "ulasan", "rating", "testimoni"],
    "wishlist": ["wishlist", "favorit", "favorite"],
    "booking": ["booking", "reservasi", "appointment", "jadwal", "schedule booking"],
    "forum": ["forum", "diskusi", "community", "komunitas"],
    "i18n": ["multi bahasa", "multilingual", "multi-language", "multi language", "i18n", "dua bahasa"],
    "analytics": ["analytics", "statistik", "laporan", "report", "grafik"],
    "api_only": ["api only", "api-only", "rest api", "backend service", "headless", "hanya api"],
    "dark_mode": ["dark mode", "mode gelap", "tema gelap"],
}

CONSTRAINT_MARKERS = {
    "mobile_first": ["mobile-first", "mobile first", "mobile friendly", "mobilefriendly", "responsif", "responsive"],
    "seo": ["seo"],
    "accessibility": ["wcag", "accessibility", "aksesibilitas", "a11y"],
    "gdpr": ["gdpr"],
    "pci": ["pci", "pci-dss"],
    "data_region": ["data residency", "lokasi data", "sovereign", "on-premise", "on premise"],
}

NEGATION_MARKERS = (
    "no ",
    "non-",
    "tanpa ",
    "without ",
    "tidak perlu",
    "tidak ada",
    "tidak usah",
    "ga perlu",
    "gak perlu",
    "jangan",
    "exclude",
    "except ",
    "doesn't need",
    "does not need",
    "dont add",
    "don't add",
    "skip ",
)

PROJECT_TYPE_KEYWORDS = {
    "ecommerce": ["toko online", "e-commerce", "ecommerce", "online shop", "web store", "toko"],
    "marketplace": ["marketplace", "multi vendor", "multi-vendor"],
    "saas": ["saas", "subscription app", "aplikasi langganan", "per langganan"],
    "dashboard": ["admin dashboard", "dashboard admin", "admin panel", "internal app", "aplikasi internal"],
    "landing": ["landing page", "company profile", "profil perusahaan", "promo site"],
    "portfolio": ["portfolio", "portofolio"],
    "blog": ["blog", "news site", "situs berita", "magazine", "majalah"],
    "docs": ["documentation site", "situs dokumentasi", "knowledge base", "wiki", "docs site"],
    "booking": ["booking", "reservasi", "appointment", "klinik", "salon", "agenda"],
    "api": ["api only", "api-only", "backend service", "rest api", "microservice"],
    "social": ["social network", "forum", "komunitas", "community site"],
    "erp_crm": ["erp", "crm", "inventory app", "inventori", "pos ", "kasir"],
}

PROJECT_TYPE_LABELS = {
    "ecommerce": ("online store", "toko online"),
    "marketplace": ("marketplace", "marketplace"),
    "saas": ("SaaS web app", "aplikasi web SaaS"),
    "dashboard": ("admin dashboard", "dashboard admin"),
    "landing": ("landing page", "landing page"),
    "portfolio": ("portfolio site", "situs portofolio"),
    "blog": ("blog / news site", "blog / situs berita"),
    "docs": ("documentation site", "situs dokumentasi"),
    "booking": ("booking app", "aplikasi booking"),
    "api": ("API-only backend", "backend API saja"),
    "social": ("community site", "situs komunitas"),
    "erp_crm": ("ERP/CRM internal app", "aplikasi internal ERP/CRM"),
}

FEATURE_LABELS = {
    "auth": ("user accounts (login/register)", "akun pengguna (login/daftar)"),
    "payments": ("payments", "pembayaran"),
    "cart": ("shopping cart", "keranjang belanja"),
    "checkout": ("checkout", "checkout"),
    "catalog": ("product catalog", "katalog produk"),
    "orders": ("order management", "manajemen pesanan"),
    "admin": ("admin back office", "back office admin"),
    "dashboard": ("dashboard", "dashboard"),
    "blog": ("blog / articles", "blog / artikel"),
    "search": ("search & filtering", "pencarian & filter"),
    "realtime": ("realtime updates", "pembaruan realtime"),
    "chat": ("chat", "chat"),
    "notifications": ("notifications", "notifikasi"),
    "upload": ("file/image upload", "unggah file/gambar"),
    "reviews": ("reviews & ratings", "ulasan & rating"),
    "wishlist": ("wishlist", "wishlist"),
    "booking": ("booking/scheduling", "booking/penjadwalan"),
    "forum": ("forum/discussion", "forum/diskusi"),
    "i18n": ("multi-language UI", "UI multi bahasa"),
    "analytics": ("analytics & reports", "analitik & laporan"),
    "api_only": ("API-only surface", "hanya API"),
    "dark_mode": ("dark mode", "mode gelap"),
}

COLOR_NAMES = {
    "blue": "#2563EB", "biru": "#2563EB",
    "red": "#DC2626", "merah": "#DC2626",
    "green": "#16A34A", "hijau": "#16A34A",
    "yellow": "#CA8A04", "kuning": "#CA8A04",
    "orange": "#EA580C", "jingga": "#EA580C", "oranye": "#EA580C",
    "purple": "#7C3AED", "ungu": "#7C3AED",
    "pink": "#DB2777", "merah muda": "#DB2777",
    "teal": "#0D9488", "tosca": "#0D9488",
    "cyan": "#0891B2",
    "navy": "#1E3A8A", "biru tua": "#1E3A8A",
    "black": "#111827", "hitam": "#111827",
    "white": "#F8FAFC", "putih": "#F8FAFC",
    "gray": "#6B7280", "grey": "#6B7280", "abu": "#6B7280",
    "gold": "#D97706", "emas": "#D97706",
    "brown": "#92400E", "coklat": "#92400E",
    "indigo": "#4F46E5",
    "violet": "#8B5CF6",
    "sky": "#0284C7",
    "emerald": "#059669", "zamrud": "#059669",
    "rose": "#E11D48",
    "lime": "#65A30D",
    "maroon": "#7F1D1D",
    "slate": "#475569",
}

TONE_KEYWORDS = {
    "modern": ["modern", "kekinian"],
    "professional": ["profesional", "professional"],
    "trustworthy": ["terpercaya", "trustworthy", "amanah"],
    "playful": ["playful", "ceria", "fun", "ramah"],
    "elegant": ["elegan", "elegant", "mewah", "luxury"],
    "minimal": ["minimal", "minimalis", "clean", "bersih"],
    "bold": ["bold", "berani"],
    "warm": ["hangat", "warm"],
    "corporate": ["corporate", "korporat", "enterprise"],
    "fresh": ["fresh", "segar"],
}

HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

SCALES = ("small", "medium", "large")

BREAKPOINTS = {"sm": 640, "md": 768, "lg": 1024, "xl": 1280, "2xl": 1536}


def _err(msg):
    raise SkillInputError(msg)


def _need_str(args, key):
    val = args.get(key)
    if not isinstance(val, str) or not val.strip():
        _err(f"argument '{key}' must be a non-empty string")
    return val.strip()


def _opt_str(args, key):
    val = args.get(key)
    if val is None:
        return None
    if not isinstance(val, str) or not val.strip():
        _err(f"argument '{key}' must be a non-empty string when provided")
    return val.strip()


def _opt_list(args, key, item_type=str):
    val = args.get(key)
    if val is None:
        return None
    if not isinstance(val, list) or not all(isinstance(v, item_type) for v in val):
        _err(f"argument '{key}' must be a list of {item_type.__name__}")
    return val


def _opt_dict(args, key):
    val = args.get(key)
    if val is None:
        return None
    if not isinstance(val, dict):
        _err(f"argument '{key}' must be an object")
    return val


def _opt_bool(args, key, default):
    val = args.get(key, default)
    if not isinstance(val, bool):
        _err(f"argument '{key}' must be a boolean")
    return val


def _opt_int(args, key, default, minimum=None, maximum=None):
    val = args.get(key, default)
    if isinstance(val, bool) or not isinstance(val, int):
        _err(f"argument '{key}' must be an integer")
    if minimum is not None and val < minimum:
        _err(f"argument '{key}' must be >= {minimum}")
    if maximum is not None and val > maximum:
        _err(f"argument '{key}' must be <= {maximum}")
    return val


def _opt_choice(args, key, choices, default=None):
    val = args.get(key, default)
    if val is None:
        return None
    if not isinstance(val, str) or val not in choices:
        _err(f"argument '{key}' must be one of {sorted(choices)}")
    return val


def _result(skill_id, execution_kind, data, writes=None, limitations=None):
    return {
        "status": "completed",
        "skill_id": skill_id,
        "execution_kind": execution_kind,
        "data": data,
        "writes": writes or [],
        "limitations": list(GLOBAL_LIMITS) + list(limitations or ()),
    }


def _root_reader_result(skill_id):
    return {
        "status": "root_reader",
        "skill_id": skill_id,
        "execution_kind": "analysis",
        "data": {
            "marker": "root_reader",
            "reason": "Actual file/API/voice access is handled by the root integration; this planning module never reads paths, networks, or secrets.",
            "contract": "Root resolves the source into plain text and passes it as args.text to spec.parse.* skills (IDs 7-12).",
            "next_planning_ids": [7, 8, 9, 10, 11, 12],
        },
        "writes": [],
        "limitations": list(GLOBAL_LIMITS) + [
            "This ID is a root-handled reader; no parsing was executed here.",
        ],
    }


def _not_owned_result(skill_id):
    return {
        "status": "not_owned",
        "skill_id": skill_id,
        "execution_kind": "analysis",
        "data": {
            "reason": "This skill ID belongs to another web_builder layer (scaffolding/build/deploy/test/monitor) or to root.",
            "owned_scope": "numbers 1-46 and 134-151, except the six root-handled spec.read.* IDs",
        },
        "writes": [],
        "limitations": list(GLOBAL_LIMITS),
    }


def _slug(text, fallback="item"):
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    out = re.sub(r"[^a-z0-9]+", "-", norm.lower()).strip("-")
    return out or fallback


def _truncate(text, limit):
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "\u2026"


def _esc_html(text):
    return html.escape(str(text), quote=True)


def _esc_text(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _jsonld_safe(payload):
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")


def _seed_int(*parts):
    digest = hashlib.sha256("::".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def _hex_to_rgb(value):
    raw = value.strip()
    if not HEX_RE.match(raw):
        _err(f"'{value}' is not a valid hex color like #2563EB")
    if len(raw) == 4:
        raw = "#" + "".join(ch * 2 for ch in raw[1:])
    raw = raw[1:]
    return tuple(int(raw[i : i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb):
    return "#%02X%02X%02X" % tuple(max(0, min(255, round(c))) for c in rgb)


def _adjust_lightness(rgb, lightness):
    h, _l, s = colorsys.rgb_to_hls(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)
    r, g, b = colorsys.hls_to_rgb(h, max(0.0, min(1.0, lightness)), s)
    return (r * 255, g * 255, b * 255)


def _rel_luminance(rgb):
    def chan(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (chan(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(a, b):
    la, lb = _rel_luminance(a), _rel_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _on_color(rgb):
    return "#FFFFFF" if _contrast_ratio(rgb, (255, 255, 255)) >= _contrast_ratio(rgb, (0, 0, 0)) else "#111111"


def _parse_color_input(value):
    raw = (value or "").strip().lower()
    if raw in COLOR_NAMES:
        return _hex_to_rgb(COLOR_NAMES[raw])
    return _hex_to_rgb(value)


def _build_palette(hex_color):
    base = _hex_to_rgb(hex_color)
    steps = (50, 100, 200, 300, 400, 500, 600, 700, 800, 900)
    lights = (0.97, 0.94, 0.88, 0.80, 0.70, 0.58, 0.48, 0.40, 0.32, 0.24)
    ramp = {}
    for key, light in zip(steps, lights):
        rgb = _adjust_lightness(base, light)
        ramp[str(key)] = _rgb_to_hex(rgb)
    h, _l, s = colorsys.rgb_to_hls(base[0] / 255, base[1] / 255, base[2] / 255)
    neutrals = {}
    for key, light in zip(steps, lights):
        r, g, b = colorsys.hls_to_rgb(h, light, min(s, 0.06))
        neutrals[str(key)] = _rgb_to_hex((r * 255, g * 255, b * 255))
    base_rgb = _hex_to_rgb(ramp["600"])
    return {
        "source": _rgb_to_hex(base),
        "primary": ramp,
        "neutral": neutrals,
        "semantic": {
            "success": "#16A34A",
            "warning": "#D97706",
            "danger": "#DC2626",
            "info": "#0284C7",
        },
        "contrast": {
            "on_primary": _on_color(base_rgb),
            "primary_vs_white": round(_contrast_ratio(base_rgb, (255, 255, 255)), 2),
            "primary_vs_black": round(_contrast_ratio(base_rgb, (0, 0, 0)), 2),
        },
        "method": "deterministic HSL ramp + WCAG relative-luminance contrast math; no model inference",
    }


def _md_document(title, sections):
    out = [f"# {title}", ""]
    for heading, body in sections:
        out.append(f"## {heading}")
        out.append("")
        if isinstance(body, str):
            out.append(body)
        else:
            out.extend(body)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _write(path, content):
    return {"path": path, "content": content}


def _mermaid_escape(label):
    return re.sub(r"[^A-Za-z0-9 _&/.\-]", "", str(label)).strip() or "node"


def _parse_spec(text):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    low_lines = [ln.lower() for ln in lines]
    title = None
    for ln in lines:
        if ln.startswith("#"):
            title = ln.lstrip("#").strip()
            break
    if title is None and lines:
        title = lines[0]
    name = None
    if title:
        if ":" in title:
            name = title.split(":", 1)[1].strip()
        else:
            name = title
    id_words = len(re.findall(r"\b(buatkan|buat|website|aplikasi|toko|produk|fitur|warna|wajib|tanpa|tidak|dengan|pengguna|layanan|harga|nama)\b", text.lower()))
    en_words = len(re.findall(r"\b(build|create|website|app|product|feature|color|must|without|with|user|service|price|name|store)\b", text.lower()))
    language = "id" if id_words > en_words else "en"
    positive = {}
    negative = {}
    # Contrasts start a new scope; exclusions anywhere remain authoritative.
    # A later mention must not silently reactivate an explicitly excluded feature.
    negation = re.compile(r"(?<![a-z])(?:tanpa|without|no|not|jangan|exclude|except|skip|tidak\s+(?:perlu|ada|usah)|ga[k]?\s+perlu|doesn.t\s+need|does\s+not\s+need|don.t\s+add)(?![a-z])")
    for idx, low in enumerate(low_lines):
        clauses = re.split(r"[,.;!?]|\b(?:tapi|tetapi|namun|but|however|sedangkan)\b", low)
        for clause in clauses:
            for key, keywords in FEATURE_KEYWORDS.items():
                for kw in keywords:
                    match = re.search(r"(?<![a-z0-9])"+re.escape(kw)+r"(?![a-z0-9])",clause)
                    if match is None:
                        continue
                    negated = negation.search(clause[:match.start()]) is not None
                    suffix = clause[match.end():]
                    if re.match(r"\s*(?:is\s+|are\s+|itu\s+)?(?:tidak\s+perlu|not\s+(?:required|needed)|dilarang)(?![a-z])",suffix):
                        negated = True
                    entry = {"feature": key, "keyword": kw, "line": idx + 1, "raw": lines[idx]}
                    (negative if negated else positive).setdefault(key,entry)
                    break
    features = [entry for key,entry in positive.items() if key not in negative]
    excluded = list(negative.values())
    type_scores = {}
    low_text = text.lower()
    for ptype, keywords in PROJECT_TYPE_KEYWORDS.items():
        score = sum(low_text.count(kw) for kw in keywords)
        if score:
            type_scores[ptype] = score
    colors = []
    for match in re.finditer(r"#[0-9a-fA-F]{6}\b", text):
        colors.append(match.group(0).upper())
    for cname, hexval in COLOR_NAMES.items():
        if re.search(r"(?<![a-z])" + re.escape(cname) + r"(?![a-z])", low_text):
            labeled = f"{cname}={hexval}"
            if labeled not in colors:
                colors.append(labeled)
    tones = [tone for tone, kws in TONE_KEYWORDS.items() if any(kw in low_text for kw in kws)]
    constraints = {}
    for key, markers in CONSTRAINT_MARKERS.items():
        hits = [lines[i] for i, low in enumerate(low_lines) if any(m in low for m in markers)]
        if hits:
            constraints[key] = hits
    perf = []
    for regex in (r"(?:load(?:ing)?(?: time)?|muat)\s*(?:waktu)?\s*(?:<|under|di bawah|kurang dari|less than)\s*(\d+)\s*(ms|detik|second|s\b)",):
        for match in re.finditer(regex, low_text):
            perf.append(match.group(0))
    if perf:
        constraints["performance"] = perf
    budget = re.findall(r"(?:budget|anggaran)[^\n]*|rp\s?[\d.,]+|\$\s?\d[\d.,]*", text, flags=re.IGNORECASE)
    if budget:
        constraints["budget_raw"] = [b.strip() for b in budget]
    deadline = re.findall(r"(?:deadline|tenggat|due)[^\n]*", text, flags=re.IGNORECASE)
    if deadline:
        constraints["deadline_raw"] = [d.strip() for d in deadline]
    description = None
    for i, ln in enumerate(lines):
        head = ln.lower().lstrip("#").strip()
        if head in ("deskripsi", "description") and i + 1 < len(lines):
            description = lines[i + 1]
            break
    return {
        "lines": lines,
        "title": title,
        "name": name,
        "language": language,
        "description": description,
        "features": features,
        "excluded": excluded,
        "conflicting_features":sorted(set(positive)&set(negative)),
        "type_scores": type_scores,
        "colors": colors,
        "tones": tones,
        "constraints": constraints,
    }


def _feature_keys(parsed):
    keys = []
    for entry in parsed["features"]:
        if entry["feature"] not in keys:
            keys.append(entry["feature"])
    return keys


def _excluded_keys(parsed):
    keys = []
    for entry in parsed["excluded"]:
        if entry["feature"] not in keys:
            keys.append(entry["feature"])
    return keys


def _detect_project_type(parsed):
    scores = parsed["type_scores"]
    if not scores:
        return None, [], []
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    top_score = ranked[0][1]
    tied = [t for t, s in ranked if s == top_score]
    chosen = tied[0] if len(tied) == 1 else None
    reasons = [f"keyword hits: {', '.join(f'{t}={s}' for t, s in ranked[:4])}"]
    return chosen, tied, reasons


def _normalize_ctx(args, require_name=True):
    text = _opt_str(args, "text")
    parsed = _parse_spec(text) if text else None
    name = _need_str(args, "name") if require_name else (_opt_str(args, "name") or (parsed["name"] if parsed else None))
    ctx = {
        "name": name,
        "text": text,
        "parsed": parsed,
        "language": _opt_choice(args, "language", ("en", "id")) or (parsed["language"] if parsed else "en"),
    }
    project_type = _opt_choice(args, "project_type", PROJECT_TYPE_KEYWORDS.keys())
    type_source = "argument"
    if project_type is None:
        ctx_type = _opt_dict(args, "context")
        if ctx_type and ctx_type.get("project_type"):
            project_type = ctx_type["project_type"]
            type_source = "context"
    if project_type is None and parsed:
        detected, tied, reasons = _detect_project_type(parsed)
        if detected:
            project_type = detected
            type_source = "detected from spec text (" + "; ".join(reasons) + ")"
        elif tied:
            type_source = "ambiguous: tied candidates " + ", ".join(tied)
    ctx["project_type"] = project_type
    ctx["project_type_source"] = type_source

    features_arg = _opt_list(args, "features")
    excluded_arg = _opt_list(args, "excluded_features") or []
    if features_arg is None and parsed:
        features_arg = _feature_keys(parsed)
        excluded_arg = list(dict.fromkeys(excluded_arg + _excluded_keys(parsed)))
    ctx["features"] = [str(f) for f in (features_arg or [])]
    ctx["excluded_features"] = [str(f) for f in excluded_arg]

    constraints = _opt_dict(args, "constraints") or {}
    if parsed:
        for key, val in parsed["constraints"].items():
            constraints.setdefault(key, val)
    ctx["constraints"] = constraints
    ctx["scale"] = _opt_choice(args, "scale", SCALES, default=None) or "small"
    ctx["preferences"] = _opt_dict(args, "preferences") or {}
    ctx["context"] = _opt_dict(args, "context") or {}
    return ctx


def _effective_features(ctx):
    excluded = set(ctx["excluded_features"])
    return [f for f in ctx["features"] if f not in excluded]


def _has_feature(ctx, key):
    return key in _effective_features(ctx)


SPECIAL_DEFAULT_ARGS = {
    "name": "Toko Maju Jaya",
    "title": "Toko Maju Jaya",
    "color": "#2563EB",
    "url": "https://toko-majujaya.example/",
    "base_url": "https://toko-majujaya.example/",
    "text": (
        "# Spec Website: Toko Maju Jaya\n\n"
        "## Deskripsi\nToko online untuk menjual produk elektronik.\n\n"
        "## Fitur Utama\n- Catalog produk (kategori, search, filter)\n- Shopping cart\n- Checkout\n"
        "- Payment via Midtrans\n- User auth (login, register)\n- Admin dashboard\n- Order tracking\n\n"
        "## Brand Identity\n- Warna: Biru (#2563EB) + Putih\n- Tone: Modern, profesional, terpercaya\n\n"
        "## Budget\n- Hosting: Rp 200.000/bln\n\n"
        "## Constraint\n- Mobile-first wajib\n- Loading < 3 detik\n- SEO friendly\n- Tanpa blog\n"
    ),
    "features": ["catalog", "cart", "checkout", "payments", "auth", "admin", "orders", "search"],
    "entities": [{"name": "products", "fields": ["name", "price_cents:int", "category_id"]}],
    "constraints": {"mobile_first": True},
    "preferences": {},
    "context": {},
    "routes": ["/", "/products"],
    "disallow": ["/admin/"],
    "keywords": ["elektronik"],
    "decision": "Gunakan PostgreSQL untuk data transaksional.",
    "description": "Toko elektronik online untuk konsumen Indonesia.",
}


def _demo_args_for_schema(sch):
    args = {}
    props = sch.get("properties", {})
    for key, prop in props.items():
        if key in sch.get("required", []):
            args[key] = _demo_value_for_prop(key, prop)
    for key in sch.get("required", []):
        if key not in args:
            args[key] = _demo_value_for_prop(key, props.get(key, {}))
    if "anyOf" in sch:
        for sub in sch["anyOf"]:
            for key in sub.get("required", []):
                args.setdefault(key, _demo_value_for_prop(key, props.get(key, {})))
    return args


def _demo_value_for_prop(key, prop):
    if key == "context" and prop.get("type") == "string":
        return "Kami butuh database transaksional untuk pesanan."
    if key in SPECIAL_DEFAULT_ARGS:
        return SPECIAL_DEFAULT_ARGS[key]
    if "enum" in prop:
        return prop["enum"][0]
    ptype = prop.get("type", "string")
    if ptype == "array":
        return ["catalog"]
    if ptype == "object":
        return {}
    if ptype == "integer":
        return 1
    if ptype == "boolean":
        return True
    return "Toko Maju Jaya"




def _ambiguities_from_parse(parsed, name, project_type):
    ambiguities = []
    if not name:
        ambiguities.append(
            {
                "topic": "project name",
                "question": "What is the project/site name to use across design and SEO artifacts?",
                "options": [],
            }
        )
    if project_type is None:
        ambiguities.append(
            {
                "topic": "project type",
                "question": "Which project type is this spec? (keyword detection found no clear winner)",
                "options": sorted(PROJECT_TYPE_KEYWORDS.keys()),
            }
        )
    keys = set(_feature_keys(parsed))
    excluded = set(_excluded_keys(parsed))
    if "payments" in keys and not any(k in parsed["raw_text"].lower() for k in ("midtrans", "xendit", "stripe", "paypal", "doku")):
        ambiguities.append(
            {
                "topic": "payment provider",
                "question": "Payments are requested but no provider is named. Which gateway should be planned?",
                "options": ["Midtrans", "Xendit", "Stripe", "other"],
            }
        )
    if "auth" in keys and not any(k in parsed["raw_text"].lower() for k in ("oauth", "google login", "sso", "email", "password")):
        ambiguities.append(
            {
                "topic": "auth method",
                "question": "Auth is requested but the method is unspecified. Email+password, OAuth, or both?",
                "options": ["email+password", "oauth", "both"],
            }
        )
    if ({"orders", "catalog", "booking"} & keys) and "database" not in parsed["raw_text"].lower():
        ambiguities.append(
            {
                "topic": "database",
                "question": "The spec stores data (catalog/orders/booking) but names no database. Any preference or expected scale?",
                "options": ["default recommendation", "self-hosted VPS", "managed cloud"],
            }
        )
    if "upload" in keys and not any(k in parsed["raw_text"].lower() for k in ("s3", "cloudinary", "minio", "storage")):
        ambiguities.append(
            {
                "topic": "file storage",
                "question": "Uploads are requested but no storage target is named. Self-hosted (MinIO) or managed (S3/Cloudinary)?",
                "options": ["self-hosted", "managed", "undecided"],
            }
        )
    if "i18n" in keys:
        ambiguities.append(
            {
                "topic": "languages",
                "question": "Multi-language UI is requested. Which locales exactly?",
                "options": ["id", "en", "id+en", "other"],
            }
        )
    if excluded:
        ambiguities.append(
            {
                "topic": "confirmed exclusions",
                "question": "The spec explicitly excludes: " + ", ".join(sorted(excluded)) + ". Confirm these are intentional so builders do not add them.",
                "options": ["confirm exclusions"],
            }
        )
    return ambiguities


def spec_parse_requirements(args):
    text = _need_str(args, "text")
    parsed = _parse_spec(text)
    parsed["raw_text"] = text
    name = _opt_str(args, "name") or parsed["name"]
    project_type = _opt_choice(args, "project_type", PROJECT_TYPE_KEYWORDS.keys())
    detected, tied, type_reasons = _detect_project_type(parsed)
    if project_type is None:
        project_type = detected
    requirements = []
    counter = 0
    for entry in parsed["features"]:
        counter += 1
        requirements.append(
            {
                "id": f"R{counter}",
                "kind": "functional",
                "text": entry["raw"],
                "feature": entry["feature"],
                "source_line": entry["line"],
            }
        )
    nrc = 0
    for key, hits in parsed["constraints"].items():
        for hit in hits:
            nrc += 1
            requirements.append({"id": f"NR{nrc}", "kind": "non_functional", "text": hit, "constraint": key})
    for entry in parsed["excluded"]:
        counter += 1
        requirements.append(
            {
                "id": f"R{counter}",
                "kind": "exclusion",
                "text": entry["raw"],
                "excluded_feature": entry["feature"],
                "source_line": entry["line"],
            }
        )
    ambiguities = _ambiguities_from_parse(parsed, name, project_type)
    if parsed.get("conflicting_features"):
        ambiguities.append({"topic":"conflicting_spec","question":"Fitur disebut diperlukan sekaligus dikecualikan: "+", ".join(parsed["conflicting_features"])+". Mohon konfirmasi; pengecualian dipertahankan sampai jelas.","options":["confirm exclusions","revise requirements"]})
    data = {
        "name": name,
        "project_type": project_type,
        "project_type_reasons": type_reasons,
        "language": parsed["language"],
        "summary": parsed["description"] or parsed["title"],
        "requirements": requirements,
        "functional_count": sum(1 for r in requirements if r["kind"] == "functional"),
        "non_functional_count": sum(1 for r in requirements if r["kind"] == "non_functional"),
        "exclusion_count": sum(1 for r in requirements if r["kind"] == "exclusion"),
        "features": _feature_keys(parsed),
        "excluded_features": _excluded_keys(parsed),
        "constraints": parsed["constraints"],
        "ambiguities": ambiguities,
        "assumptions": [],
        "method": "deterministic keyword/negation parsing of the supplied text; nothing inferred beyond matched keywords",
    }
    data["assumptions"] = (
        []
        if project_type is None
        else [f"project_type '{project_type}' taken from explicit keywords: {type_reasons[0]}"]
    )
    return _result(
        "spec.parse.requirements",
        "analysis",
        data,
        limitations=[
            "Ambiguities are returned explicitly instead of being guessed; run spec.clarify for a question list.",
        ]
        + ([] if not ambiguities else [f"{len(ambiguities)} ambiguity topic(s) need user confirmation."]),
    )


def spec_parse_user_stories(args):
    text = _need_str(args, "text")
    language = _opt_choice(args, "language", ("en", "id")) or _parse_spec(text)["language"]
    parsed = _parse_spec(text)
    parsed["raw_text"] = text
    actors = {
        "catalog": ("visitor", "pengunjung"),
        "search": ("visitor", "pengunjung"),
        "cart": ("shopper", "pembeli"),
        "checkout": ("shopper", "pembeli"),
        "payments": ("shopper", "pembeli"),
        "orders": ("customer", "pelanggan"),
        "auth": ("member", "anggota"),
        "admin": ("admin", "admin"),
        "dashboard": ("staff", "staf"),
        "blog": ("reader", "pembaca"),
        "booking": ("customer", "pelanggan"),
        "forum": ("member", "anggota"),
        "chat": ("member", "anggota"),
        "reviews": ("customer", "pelanggan"),
        "wishlist": ("member", "anggota"),
        "upload": ("content editor", "editor konten"),
        "analytics": ("owner", "pemilik"),
        "notifications": ("member", "anggota"),
        "i18n": ("visitor", "pengunjung"),
        "realtime": ("user", "pengguna"),
        "api_only": ("API client", "klien API"),
    }
    benefit = {
        "catalog": ("browse the offering", "melihat penawaran"),
        "search": ("find items quickly", "menemukan item dengan cepat"),
        "cart": ("collect items before paying", "mengumpulkan item sebelum bayar"),
        "checkout": ("complete a purchase", "menyelesaikan pembelian"),
        "payments": ("pay securely", "membayar dengan aman"),
        "orders": ("follow my purchases", "memantau pembelian"),
        "auth": ("keep my data private", "menjaga data saya tetap privat"),
        "admin": ("manage the site content", "mengelola konten situs"),
        "dashboard": ("monitor operations", "memantau operasional"),
        "blog": ("read updates", "membaca pembaruan"),
        "booking": ("reserve a slot", " memesan slot"),
        "forum": ("discuss with others", "berdiskusi dengan pengguna lain"),
        "chat": ("talk to other users", "berkomunikasi dengan pengguna lain"),
        "reviews": ("judge quality before buying", "menilai kualitas sebelum membeli"),
        "wishlist": ("save items for later", "menyimpan item untuk nanti"),
        "upload": ("add media to content", "menambah media ke konten"),
        "analytics": ("see what works", "melihat apa yang berhasil"),
        "notifications": ("get timely updates", "mendapat kabar tepat waktu"),
        "i18n": ("use the site in my language", "memakai situs dalam bahasa saya"),
        "realtime": ("see changes instantly", "melihat perubahan seketika"),
        "api_only": ("integrate programmatically", "integrasi secara programatik"),
        "dark_mode": ("browse comfortably at night", "nyaman menjelajah malam hari"),
    }
    stories = []
    for i, entry in enumerate(parsed["features"], start=1):
        key = entry["feature"]
        actor = actors.get(key, ("user", "pengguna"))
        want_en, want_id = FEATURE_LABELS.get(key, (key, key))
        ben_en, ben_id = benefit.get(key, ("achieve the goal", "mencapai tujuan"))
        story = {
            "id": f"US{i}",
            "feature": key,
            "source_line": entry["line"],
            "acceptance_criteria": [
                f"Given the {actor[0]} is authenticated where required",
                f"When the {actor[0]} uses {want_en}",
                f"Then the outcome supports: {ben_en}",
            ],
        }
        if language == "id":
            story["story"] = f"Sebagai {actor[1]}, saya ingin {want_id}, sehingga saya bisa {ben_id.strip()}."
        else:
            story["story"] = f"As a {actor[0]}, I want {want_en}, so that I can {ben_en}."
        stories.append(story)
    excluded = _excluded_keys(parsed)
    return _result(
        "spec.parse.user_stories",
        "analysis",
        {
            "language": language,
            "stories": stories,
            "excluded_features": excluded,
            "note": "excluded features produce no stories (negations from the spec are honored)",
            "method": "deterministic actor/benefit mapping per canonical feature keyword",
        },
    )


def spec_parse_features(args):
    text = _need_str(args, "text")
    parsed = _parse_spec(text)
    parsed["raw_text"] = text
    matched_lines = {entry["line"] for entry in parsed["features"] + parsed["excluded"]}
    unmatched = [
        {"line": i + 1, "raw": ln}
        for i, ln in enumerate(parsed["lines"])
        if (ln.startswith(("-", "*", "\u2022")) or re.match(r"^\d+[.)]", ln)) and (i + 1) not in matched_lines and not ln.lstrip("-*0123456789.) ").startswith("#")
    ]
    return _result(
        "spec.parse.features",
        "analysis",
        {
            "features": parsed["features"],
            "excluded_features": parsed["excluded"],
            "feature_keys": _feature_keys(parsed),
            "excluded_keys": _excluded_keys(parsed),
            "conflicting_features":parsed.get("conflicting_features",[]),
            "unrecognized_candidate_lines": unmatched,
            "note": "features and exclusions come only from matched keywords; negated keywords become exclusions, never features",
            "method": "deterministic clause-scoped keyword matching; explicit exclusions override positive mentions",
        },
        limitations=(
            ["Some bullet lines matched no known feature keyword; map them manually or extend the keyword table."]
            if unmatched
            else []
        ),
    )


def spec_parse_constraints(args):
    text = _need_str(args, "text")
    parsed = _parse_spec(text)
    parsed["raw_text"] = text
    return _result(
        "spec.parse.constraints",
        "analysis",
        {
            "constraints": parsed["constraints"],
            "markers": sorted({k for k, hits in parsed["constraints"].items() if hits}),
            "budget_note": "budget text is echoed raw only; this module never estimates costs",
            "deadline_note": "deadline text is echoed raw only; schedule math is out of scope for planning",
            "method": "deterministic regex + marker scanning of the supplied text",
        },
    )


def spec_parse_design_pref(args):
    text = _need_str(args, "text")
    parsed = _parse_spec(text)
    parsed["raw_text"] = text
    dark_mode = "dark_mode" in _feature_keys(parsed)
    mobile_first = "mobile_first" in parsed["constraints"]
    return _result(
        "spec.parse.design_pref",
        "analysis",
        {
            "colors": parsed["colors"],
            "tones": parsed["tones"],
            "dark_mode": dark_mode,
            "mobile_first": mobile_first,
            "ui_language": parsed["language"],
            "brand_name": parsed["name"],
            "note": "colors and tones come from explicit keywords/hex codes found in the text; no style is invented",
            "method": "deterministic hex regex + bilingual color/tone keyword tables",
        },
    )


def spec_clarify(args):
    text = _need_str(args, "text")
    parsed = _parse_spec(text)
    parsed["raw_text"] = text
    ctx_extra = _opt_dict(args, "context") or {}
    name = ctx_extra.get("name") or parsed["name"]
    project_type = ctx_extra.get("project_type")
    if project_type is None:
        detected, _tied, _r = _detect_project_type(parsed)
        project_type = detected
    ambiguities = _ambiguities_from_parse(parsed, name, project_type)
    if parsed.get("conflicting_features"):
        ambiguities.append({"topic":"conflicting_spec","question":"Fitur disebut diperlukan sekaligus dikecualikan: "+", ".join(parsed["conflicting_features"])+". Mohon konfirmasi; pengecualian dipertahankan sampai jelas.","options":["confirm exclusions","revise requirements"]})
    questions = []
    for i, amb in enumerate(ambiguities, start=1):
        questions.append(
            {
                "id": f"Q{i}",
                "topic": amb["topic"],
                "question": amb["question"],
                "options": amb["options"],
                "blocks": _ambiguity_blocks(amb["topic"]),
            }
        )
    return _result(
        "spec.clarify",
        "analysis",
        {
            "questions": questions,
            "features": _feature_keys(parsed),
            "excluded_features": _excluded_keys(parsed),
            "policy": "nothing is assumed for ambiguous topics; builders must not add features beyond the spec (anti-hallucination rule)",
            "method": "deterministic gap detection over matched keywords, exclusions, and missing provider/database/storage names",
        },
        limitations=["Question list is generated deterministically; ask the user before acting on blocked topics."],
    )


def _ambiguity_blocks(topic):
    mapping = {
        "payment provider": ["stack.select.payment", "arch.design.database_schema", "arch.design.api"],
        "auth method": ["stack.select.auth", "arch.design.auth_flow"],
        "database": ["stack.select.database", "db.engine.match"],
        "file storage": ["stack.select.storage"],
        "project type": ["stack.analyze.requirements", "stack.select.frontend"],
        "project name": ["design.logo", "seo.gen.meta", "content.gen.copy"],
        "languages": ["content.gen.placeholder", "design.prototype"],
        "confirmed exclusions": ["stack.select.payment", "arch.design.api"],
    }
    return mapping.get(topic, [])




def _dynamic_features(ctx):
    eff = _effective_features(ctx)
    return {
        "needs_server": ctx["project_type"] in {"api","erp_crm","marketplace","ecommerce","saas","booking","social"} or bool({"auth", "payments", "orders", "cart", "checkout", "booking", "admin", "dashboard", "api_only", "realtime", "chat", "forum"} & set(eff)),
        "needs_db": ctx["project_type"] in {"erp_crm","marketplace","ecommerce","saas","booking","social"} or bool({"auth", "payments", "orders", "cart", "checkout", "booking", "catalog", "blog", "admin", "forum", "analytics", "reviews", "wishlist"} & set(eff)),
        "needs_seo_ssr": bool({"catalog", "blog", "landing"} & set(eff)) or ctx["project_type"] in {"landing", "portfolio", "blog", "docs", "ecommerce", "marketplace"},
        "needs_realtime": bool({"realtime", "chat"} & set(eff)),
        "needs_queue": bool({"notifications", "analytics", "payments"} & set(eff)),
        "needs_search": "search" in eff,
        "needs_upload": "upload" in eff or "catalog" in eff or "blog" in eff,
        "needs_payments": "payments" in eff,
        "needs_auth": "auth" in eff,
        "api_only": "api_only" in eff,
        "content_only": not bool({"auth", "payments", "orders", "cart", "checkout", "booking", "admin", "dashboard", "api_only", "realtime", "chat", "forum", "analytics"} & set(eff)),
    }


def stack_analyze_requirements(args):
    ctx = _normalize_ctx(args, require_name=False)
    if ctx["project_type"] is None:
        _err("project_type is required (directly, via context, or detectable from text)")
    dyn = _dynamic_features(ctx)
    complexity = sum(
        [
            1 if dyn["needs_server"] else 0,
            1 if dyn["needs_db"] else 0,
            1 if dyn["needs_realtime"] else 0,
            1 if dyn["needs_queue"] else 0,
            1 if dyn["needs_upload"] else 0,
            1 if ctx["scale"] == "medium" else 0,
            2 if ctx["scale"] == "large" else 0,
            2 if ctx["project_type"] in {"erp_crm", "marketplace", "saas"} else 0,
        ]
    )
    drivers = [k for k, v in dyn.items() if v] + [f"project_type={ctx['project_type']}", f"scale={ctx['scale']}"]
    risks = []
    if dyn["needs_realtime"]:
        risks.append("realtime features need persistent connections; serverless-only hosting is a poor fit")
    if dyn["needs_payments"]:
        risks.append("payments introduce webhook + reconciliation flows; plan idempotency and signature verification")
    if ctx["excluded_features"]:
        risks.append("spec explicitly excludes: " + ", ".join(sorted(set(ctx["excluded_features"]))) + "; builders must not add them")
    data = {
        "project_type": ctx["project_type"],
        "project_type_source": ctx["project_type_source"],
        "features": _effective_features(ctx),
        "excluded_features": sorted(set(ctx["excluded_features"])),
        "signals": dyn,
        "complexity_score": complexity,
        "complexity_band": "simple" if complexity <= 2 else ("moderate" if complexity <= 5 else "high"),
        "drivers": drivers,
        "risks": risks,
        "next_steps": [
            "stack.select.frontend",
            "stack.select.backend",
            "stack.select.database",
            "stack.select.hosting",
            "stack.select.auth" if dyn["needs_auth"] else None,
            "stack.select.payment" if dyn["needs_payments"] else None,
            "stack.recommend.combination",
        ],
        "method": "deterministic feature-to-signal mapping; no model inference, no cost estimates",
    }
    data["next_steps"] = [s for s in data["next_steps"] if s]
    return _result("stack.analyze.requirements", "analysis", data, limitations=[NO_COST_NO_VERSION_LIMIT[0]])


def stack_select_frontend(args):
    ctx = _normalize_ctx(args, require_name=False)
    if ctx["project_type"] is None:
        _err("project_type is required (directly, via context, or detectable from text)")
    dyn = _dynamic_features(ctx)
    pref = str(ctx["preferences"].get("frontend", "")).lower()
    reasons = []
    if pref in {"react", "vue", "svelte"}:
        mapping = {"react": "Next.js (React meta-framework)", "vue": "Nuxt (Vue meta-framework)", "svelte": "SvelteKit"}
        choice = mapping[pref]
        reasons.append(f"explicit preference frontend={pref} (scoped deterministic preference, honored as-is)")
    elif ctx["project_type"] in {"docs"}:
        choice = "Astro (content-focused, islands)"
        reasons.append("documentation sites are content-heavy; Astro ships minimal JS by default")
    elif ctx["project_type"] in {"landing", "portfolio"} and dyn["content_only"]:
        choice = "Astro (static-first) or Next.js static export"
        reasons.append("content-only landing/portfolio needs no client runtime; static-first reduces JS payload")
    elif ctx["project_type"] in {"dashboard", "erp_crm"} and not dyn["needs_seo_ssr"]:
        choice = "Next.js (React meta-framework) or Vite + React SPA"
        reasons.append("internal dashboards rarely need SEO; an SPA variant removes server-rendering overhead")
    else:
        choice = "Next.js (React meta-framework)"
        reasons.append("default for feature-rich web apps: SSR/SSG for SEO-heavy pages plus API layer in one deployable")
        if dyn["needs_seo_ssr"]:
            reasons.append("spec includes SEO-relevant pages (catalog/blog/landing); server rendering helps crawling")
    alternatives = [
        {"option": "Nuxt (Vue)", "when": "team prefers the Vue ecosystem"},
        {"option": "SvelteKit", "when": "team prefers Svelte and wants a small runtime"},
        {"option": "Astro", "when": "site is mostly content with light interactivity"},
        {"option": "Vite + React SPA", "when": "pure internal tool where SEO is irrelevant"},
    ]
    triggers = [
        "if realtime chat becomes core, keep the framework but move hosting to a persistent server (see stack.select.hosting)",
        "if heavy editor-style dashboards appear, add a dedicated SPA route group and lazy-load it",
    ]
    return _result(
        "stack.select.frontend",
        "analysis",
        {
            "project_type": ctx["project_type"],
            "choice": choice,
            "reasons": reasons,
            "alternatives": alternatives,
            "triggers": triggers,
            "version_policy": "no version pinned here; use the latest stable at scaffold time",
            "method": "deterministic decision table over project_type + preference + feature signals",
        },
        limitations=[NO_COST_NO_VERSION_LIMIT[0]],
    )


def stack_select_backend(args):
    ctx = _normalize_ctx(args, require_name=False)
    if ctx["project_type"] is None:
        _err("project_type is required (directly, via context, or detectable from text)")
    dyn = _dynamic_features(ctx)
    lang = str(ctx["preferences"].get("language", "")).lower()
    reasons = []
    heavy = ctx["project_type"] in {"erp_crm", "marketplace", "saas"} or ctx["scale"] == "large" or dyn["needs_realtime"] or dyn["needs_queue"]
    if not dyn["needs_server"] and not dyn["needs_db"]:
        choice = "none (static site); use build-time data only"
        reasons.append("spec has no server-side features; adding a backend would violate spec-driven scoping")
    elif dyn["api_only"] or ctx["project_type"] == "api":
        mapping = {
            "python": "FastAPI",
            "php": "Laravel (API mode)",
            "go": "Go (Fiber or Gin)",
            "node": "NestJS (structured) or Express (minimal)",
        }
        choice = mapping.get(lang, "NestJS (structured) or FastAPI")
        reasons.append("API-only surface: pick by team language; structure over a minimal framework pays off once resources grow")
    elif not dyn["needs_server"] and dyn["needs_db"] is False:
        choice = "none (static site)"
        reasons.append("content-only spec; no backend required")
    elif heavy and lang == "python":
        choice = "Django (batteries included) or FastAPI + services"
        reasons.append("heavy domain (admin/queues/realtime signals) with Python preference; Django ships admin, auth, ORM")
    elif heavy:
        choice = "NestJS"
        reasons.append("heavy domain signals (realtime/queue/enterprise type) favor a structured, modular backend with DI and WebSockets support")
    elif lang == "python":
        choice = "FastAPI"
        reasons.append("explicit Python preference for a light-to-moderate API")
    elif lang == "php":
        choice = "Laravel"
        reasons.append("explicit PHP preference for a light-to-moderate API")
    else:
        choice = "Next.js API routes / server actions (same deployable as frontend)"
        reasons.append("light-to-moderate CRUD next to a Next.js frontend; one deployable reduces operational surface")
        reasons.append("move to NestJS/FastAPI when domain logic outgrows route handlers (see triggers)")
    alternatives = [
        {"option": "NestJS", "when": "domain logic grows or multiple teams share the API"},
        {"option": "FastAPI", "when": "Python team or ML-adjacent services"},
        {"option": "Django", "when": "batteries-included admin/auth/ORM is wanted"},
        {"option": "Laravel", "when": "PHP team"},
    ]
    triggers = [
        "introduce a queue worker (and a separate process) once notifications/payments reconciliation arrive",
        "split backend out of the frontend deployable when >~1 team works on it or WebSocket scale matters",
    ]
    return _result(
        "stack.select.backend",
        "analysis",
        {
            "project_type": ctx["project_type"],
            "choice": choice,
            "reasons": reasons,
            "alternatives": alternatives,
            "triggers": triggers,
            "version_policy": "no version pinned here; use the latest stable at scaffold time",
            "method": "deterministic decision table over project_type + language preference + feature signals",
        },
        limitations=[NO_COST_NO_VERSION_LIMIT[0]],
    )


DB_ENGINE_PROFILES = {
    "postgresql": {
        "label": "PostgreSQL",
        "kind": "relational",
        "weights": {"relational": 3, "transactions": 3, "fulltext_search": 1, "analytics": 1, "document_flexible": 1},
        "best_for": ["transactional multi-entity data", "complex relational queries", "JSONB hybrid needs"],
        "avoid_when": ["simple embedded single-file app", "pure cache workload"],
        "ops": ["logical backups (dump) plus WAL archiving", "connection pooling at app tier", "migrations reviewed and applied manually"],
        "modeling": "normalized relational schema; JSONB for genuinely flexible attributes",
        "backup": "scheduled dumps + WAL; restore rehearsed before go-live",
        "metrics": ["connections", "replication lag", "cache hit ratio", "slow queries"],
        "pitfalls": ["unbounded connection growth without pooling", "storing large blobs in-row"],
    },
    "mysql": {
        "label": "MySQL",
        "kind": "relational",
        "weights": {"relational": 3, "transactions": 2, "fulltext_search": 1},
        "best_for": ["straightforward relational CRUD", "wide hosting familiarity", "read-heavy replication setups"],
        "avoid_when": ["advanced relational features (e.g., partial indexes, rich JSONB) are required"],
        "ops": ["dump-based backups + binlog", "read replicas for read scale", "manual reviewed migrations"],
        "modeling": "normalized relational schema; strict SQL mode on",
        "backup": "scheduled dumps + binlog; verify restore path",
        "metrics": ["connections", "replica lag", "slow query log"],
        "pitfalls": ["silent type coercion with strict mode off", "utf8 vs utf8mb4 confusion"],
    },
    "mongodb": {
        "label": "MongoDB",
        "kind": "document",
        "weights": {"document_flexible": 3, "relational": 1},
        "best_for": ["document-shaped content", "rapidly evolving attributes", "embed-heavy read models"],
        "avoid_when": ["multi-entity transactional integrity dominates (orders, payments)"],
        "ops": ["dump-based backups", "schema validation rules in the DB", "manual reviewed migrations"],
        "modeling": "model around read patterns; embed what is read together, reference what is shared",
        "backup": "scheduled dumps + oplog tailing for point-in-time",
        "metrics": ["opcache/working set", "replica lag", "slow operations"],
        "pitfalls": ["unbounded arrays in documents", "treating it as schemaless with no validation"],
    },
    "redis": {
        "label": "Redis",
        "kind": "in-memory key-value",
        "weights": {"cache": 3, "realtime": 2, "queue": 2},
        "best_for": ["cache", "session store", "rate limiting", "lightweight queues/pub-sub"],
        "avoid_when": ["primary durable record system of truth"],
        "ops": ["eviction policy chosen explicitly (e.g., allkeys-lru for pure cache)", "persistence (AOF/RDB) only when durability is needed", "maxmemory set before go-live"],
        "modeling": "key naming convention per domain; TTLs mandatory for cache keys",
        "backup": "RDB snapshots only if durability is required; cache data may be treated as disposable",
        "metrics": ["memory", "hit ratio", "evicted keys", "latency"],
        "pitfalls": ["blocking commands on large key spaces", "no TTL on cache keys"],
    },
    "elasticsearch": {
        "label": "Elasticsearch",
        "kind": "search/analytic engine",
        "weights": {"fulltext_search": 3, "analytics": 2, "log": 2},
        "best_for": ["full-text search with faceting", "log/analytics retention", "typo-tolerant search at scale"],
        "avoid_when": ["system of truth for transactional data", "small catalog where Postgres FTS is enough"],
        "ops": ["index/shard plan sized to data volume", "snapshot-based backups to shared storage", "alias-based reindex flow"],
        "modeling": "denormalized search documents synced from the source of truth",
        "backup": "snapshots; index can be rebuilt from primary DB",
        "metrics": ["query latency", "heap usage", "shard health", "indexing lag"],
        "pitfalls": ["too many tiny shards", "treating search index as the source of truth"],
    },
    "sqlite": {
        "label": "SQLite",
        "kind": "embedded relational",
        "weights": {"embedded_small": 3, "relational": 2, "transactions": 1},
        "best_for": ["single-node apps", "prototypes and MVPs", "read-heavy small sites", "local tooling"],
        "avoid_when": ["multiple write nodes", "high-concurrency writes", "managed multi-tenant scale"],
        "ops": ["file-based backups (with WAL checkpoint)", "enable WAL mode for concurrent readers", "single writer discipline"],
        "modeling": "same relational discipline as server engines; keep writes serialized",
        "backup": "scheduled file copies after checkpoint; test restore",
        "metrics": ["write contention", "db file size", "checkpoint duration"],
        "pitfalls": ["assuming network file systems are safe", "long write transactions blocking readers"],
    },
    "duckdb": {
        "label": "DuckDB",
        "kind": "embedded OLAP",
        "weights": {"analytics": 3, "embedded_small": 2},
        "best_for": ["in-process analytics over columnar files", "reporting/BI batches", "local data crunching"],
        "avoid_when": ["transactional OLTP app backend"],
        "ops": ["file-based artifacts under versioned paths", "nightly batch jobs only (no OLTP role)"],
        "modeling": "star-ish read models; columnar-friendly types",
        "backup": "copy database files after batch completion",
        "metrics": ["query duration", "file sizes"],
        "pitfalls": ["using it as a concurrent OLTP store", "very frequent small writes"],
    },
    "influxdb": {
        "label": "InfluxDB",
        "kind": "time-series",
        "weights": {"time_series": 3},
        "best_for": ["metrics/IoT telemetry", "downsampling and retention windows"],
        "avoid_when": ["relational business records"],
        "ops": ["retention policy per measurement", "continuous downsampling tasks", "snapshot backups"],
        "modeling": "measurement/tag/field design kept narrow; tags bounded in cardinality",
        "backup": "native export + snapshots; retention makes most data reproducible/ephemeral by design",
        "metrics": ["ingest rate", "cardinality", "query latency"],
        "pitfalls": ["unbounded tag cardinality", "keeping raw series forever without retention"],
    },
    "surrealdb": {
        "label": "SurrealDB",
        "kind": "multi-model",
        "weights": {"multi_model": 3, "document_flexible": 2, "relational": 1},
        "best_for": ["multi-model prototypes mixing documents, graph, and records"],
        "avoid_when": ["team wants boring, well-trodden operational tooling for critical transactional data"],
        "ops": ["import/export based backups", "single-node start; verify HA story before committing"],
        "modeling": "mix record links and graph edges deliberately; document access rules",
        "backup": "scheduled export; rehearse import path",
        "metrics": ["store size", "query latency"],
        "pitfalls": ["younger operational ecosystem than mainstream engines", "unclear fit when only one model is actually needed"],
    },
}

DB_SIGNAL_FROM_FEATURE = {
    "auth": "relational",
    "orders": "relational",
    "payments": "transactions",
    "catalog": "relational",
    "cart": "relational",
    "checkout": "transactions",
    "booking": "relational",
    "search": "fulltext_search",
    "blog": "fulltext_search",
    "realtime": "realtime",
    "chat": "realtime",
    "notifications": "queue",
    "analytics": "analytics",
    "reviews": "relational",
}


def _db_signals(ctx, explicit_signals):
    signals = {k: 0 for k in ("relational", "document_flexible", "cache", "fulltext_search", "time_series", "analytics", "embedded_small", "multi_model", "transactions", "realtime", "queue", "log")}
    for feature in _effective_features(ctx):
        sig = DB_SIGNAL_FROM_FEATURE.get(feature)
        if sig:
            signals[sig] = min(3, signals[sig] + (2 if feature in {"payments", "search", "realtime", "chat"} else 1))
    if ctx["scale"] == "small":
        signals["embedded_small"] = max(signals["embedded_small"], 1)
    if ctx["preferences"].get("self_host"):
        signals["embedded_small"] = max(signals["embedded_small"], 1)
    for key, val in (explicit_signals or {}).items():
        if key not in signals:
            _err(f"unknown db signal '{key}'; valid: {sorted(signals)}")
        if not isinstance(val, int) or isinstance(val, bool) or not 0 <= val <= 3:
            _err(f"db signal '{key}' must be an integer 0..3")
        signals[key] = max(signals[key], val)
    return signals


def db_engine_match(args):
    ctx = _normalize_ctx(args, require_name=False)
    features = _opt_list(args, "features")
    if features is None and ctx["text"] is None and not ctx["features"]:
        _err("db.engine.match needs 'features', 'text', or 'signals' to score engines")
    explicit = _opt_dict(args, "signals")
    if explicit is None and not ctx["features"] and ctx["text"]:
        parsed = ctx["parsed"]
        parsed["raw_text"] = ctx["text"]
        ctx["features"] = _feature_keys(parsed)
    signals = _db_signals(ctx, explicit)
    preference = _opt_str(args, "preference")
    ranked = []
    for engine_id, profile in DB_ENGINE_PROFILES.items():
        score = sum(profile["weights"].get(sig, 0) * val for sig, val in signals.items())
        matched = {sig: val for sig, val in signals.items() if val and profile["weights"].get(sig)}
        ranked.append({"engine": engine_id, "label": profile["label"], "score": score, "matched_signals": matched})
    ranked.sort(key=lambda r: (-r["score"], r["engine"]))
    top = ranked[0]
    recommendation = top
    basis = "highest weighted signal match"
    if preference:
        pref = preference.lower()
        hit = next((r for r in ranked if r["engine"] == pref), None)
        if hit:
            recommendation = hit
            basis = f"explicit preference '{preference}' overrides scoring (deterministic scoped preference)"
        else:
            _err(f"preference '{preference}' is not a known engine")
    caution = []
    if recommendation["engine"] == "sqlite" and (signals["realtime"] or ctx["scale"] == "large"):
        caution.append("SQLite plan assumes a single write node; verify fit for realtime/large scale")
    if recommendation["engine"] in {"duckdb", "influxdb"} and "payments" in _effective_features(ctx):
        caution.append("analytic/time-series engines are not transactional systems of truth; pair with a relational store")
    return _result(
        "db.engine.match",
        "analysis",
        {
            "signals": signals,
            "ranking": ranked,
            "recommendation": recommendation,
            "basis": basis,
            "caution": caution,
            "method": "deterministic weighted scoring; explicit preference wins when provided",
        },
        limitations=[NO_COST_NO_VERSION_LIMIT[0]],
    )


def db_engine_plan(engine_id):
    def handler(args):
        profile = DB_ENGINE_PROFILES[engine_id]
        ctx = _normalize_ctx(args, require_name=True)
        sections = [
            ("Role in the stack", [f"Engine: {profile['label']} ({profile['kind']}).", "", "Best for:", *[f"- {b}" for b in profile["best_for"]]]),
            ("When not to use it", [f"- {w}" for w in profile["avoid_when"]]),
            ("Modeling strategy", [profile["modeling"]]),
            ("Operational plan", [f"- {op}" for op in profile["ops"]]),
            ("Backup and restore", [profile["backup"]]),
            ("Monitoring", [f"- {m}" for m in profile["metrics"]]),
            ("Known pitfalls", [f"- {p}" for p in profile["pitfalls"]]),
        ]
        notes = _opt_str(args, "notes")
        if notes:
            sections.append(("Project notes", [notes]))
        features = _effective_features(ctx)
        if features:
            sections.insert(1, ("Features this plan was derived from", [", ".join(features)]))
        doc = _md_document(f"{ctx['name']}: {profile['label']} planning notes", sections)
        return _result(
            f"db.engine.{engine_id}",
            "artifact_generation",
            {
                "engine": engine_id,
                "label": profile["label"],
                "kind": profile["kind"],
                "features": features,
                "summary": f"Planning profile for {profile['label']}; provisioning/execution is a root-layer concern.",
                "when_not_to_use": profile["avoid_when"],
                "plan_path": f"docs/db/plans/{engine_id}.md",
            },
            writes=[_write(f"docs/db/plans/{engine_id}.md", doc)],
            limitations=[
                "This is a provisioning plan document; no engine was installed, started, or connected.",
                "Verify the engine still fits with db.engine.match if requirements change.",
            ],
        )

    return handler




def _hosting_choice(ctx):
    dyn = _dynamic_features(ctx)
    constraints = ctx["constraints"]
    region_lock = bool(constraints.get("data_region")) or bool(ctx["preferences"].get("self_host"))
    if region_lock:
        return (
            "VPS or on-premise (Docker), in the required region",
            ["spec/constraints mention data residency or on-premise; managed regions may not comply"],
            [{"option": "managed cloud in-region", "when": "compliance sign-off allows it"}],
            ["confirm the exact region requirement with the user before provisioning"],
        )
    if dyn["needs_realtime"]:
        return (
            "VPS (persistent WebSocket-capable server, e.g., Docker + reverse proxy)",
            ["realtime/chat features need persistent connections; serverless platforms fit poorly"],
            [{"option": "managed realtime add-on", "when": "team prefers not to operate servers"}],
            ["budget vertical headroom for WebSocket connection counts"],
        )
    if not dyn["needs_server"]:
        return (
            "static hosting (e.g., Vercel/Netlify/Cloudflare Pages)",
            ["no server-side features detected; static hosting is the smallest operational surface"],
            [],
            [],
        )
    if dyn["needs_queue"] or ctx["project_type"] in {"erp_crm", "marketplace"}:
        return (
            "VPS (Docker) or a platform with background-worker support",
            ["queues/cron and heavier domains need a long-running process, not only request handlers"],
            [{"option": "serverless + separate worker platform", "when": "team avoids server ops"}],
            ["plan worker process and queue backend together"],
        )
    return (
        "managed platform (e.g., Vercel) or VPS (Docker)",
        ["standard full-stack app; both a managed platform and a VPS are reasonable at this scope"],
        [{"option": "VPS", "when": "full control or pre-existing server is required"}],
        [],
    )


def stack_select_database(args):
    ctx = _normalize_ctx(args, require_name=False)
    if ctx["project_type"] is None:
        _err("project_type is required (directly, via context, or detectable from text)")
    match_args = {
        "project_type": ctx["project_type"],
        "features": ctx["features"],
        "excluded_features": ctx["excluded_features"],
        "scale": ctx["scale"],
        "preferences": ctx["preferences"],
        "text": ctx["text"],
    }
    preference = ctx["preferences"].get("database")
    if preference:
        match_args["preference"] = str(preference)
    matched = db_engine_match(match_args)["data"]
    recommendation = matched["recommendation"]
    reasons = [
        f"weighted signal match: {recommendation['matched_signals'] or {}}" if recommendation["matched_signals"] else "no strong signals; lowest-commitment engine preferred",
        "deterministic scoring via db.engine.match rules",
    ]
    if matched.get("basis", "").startswith("explicit preference"):
        reasons.insert(0, matched["basis"])
    return _result(
        "stack.select.database",
        "analysis",
        {
            "project_type": ctx["project_type"],
            "choice": recommendation["label"],
            "engine": recommendation["engine"],
            "reasons": reasons,
            "alternatives": [
                {"option": r["label"], "score": r["score"]}
                for r in matched["ranking"][1:4]
            ],
            "caution": matched["caution"],
            "exclusions": sorted(set(ctx["excluded_features"])),
            "method": "delegates to db.engine.match scoring; explicit preference wins",
        },
        limitations=[NO_COST_NO_VERSION_LIMIT[0]],
    )


def stack_select_hosting(args):
    ctx = _normalize_ctx(args, require_name=False)
    if ctx["project_type"] is None:
        _err("project_type is required (directly, via context, or detectable from text)")
    choice, reasons, alternatives, triggers = _hosting_choice(ctx)
    return _result(
        "stack.select.hosting",
        "analysis",
        {
            "project_type": ctx["project_type"],
            "choice": choice,
            "reasons": reasons,
            "alternatives": alternatives,
            "triggers": triggers,
            "environments": ["staging", "production"],
            "note": "no costs estimated; confirm platform pricing with the user before provisioning",
            "method": "deterministic rules: residency > realtime > static > queue/heavy > default",
        },
        limitations=[NO_COST_NO_VERSION_LIMIT[0]],
    )


def stack_select_auth(args):
    ctx = _normalize_ctx(args, require_name=False)
    if ctx["project_type"] is None:
        _err("project_type is required (directly, via context, or detectable from text)")
    eff = set(_effective_features(ctx))
    excluded = set(ctx["excluded_features"])
    if "auth" in excluded or "auth" not in eff:
        if "auth" in excluded:
            reason = "spec explicitly excludes auth (negation honored); no auth stack is selected"
        else:
            reason = "spec does not request auth; per anti-hallucination scoping it is not added"
        return _result(
            "stack.select.auth",
            "analysis",
            {"included": False, "reason": reason, "method": "spec-driven; nothing added beyond the spec"},
        )
    pref = str(ctx["preferences"].get("auth", "")).lower()
    enterprise = ctx["project_type"] in {"erp_crm", "saas"} or ctx["scale"] == "large"
    if pref in {"keycloak", "auth0", "clerk", "supabase"}:
        choice = {"keycloak": "Keycloak (self-hosted SSO)", "auth0": "Auth0 (managed)", "clerk": "Clerk (managed)", "supabase": "Supabase Auth"}[pref]
        reasons = [f"explicit preference auth={pref} (scoped deterministic preference)"]
    elif enterprise:
        choice = "Keycloak (self-hosted SSO) or a managed IdP if the team prefers"
        reasons = ["enterprise/internal scope favors centralized identity (SSO, roles, audit)"]
    elif ctx["project_type"] == "api" or "api_only" in eff:
        choice = "JWT access + refresh tokens issued by the backend framework"
        reasons = ["API-only surface: stateless token pair keeps clients simple; refresh rotation limits exposure"]
    else:
        choice = "Auth.js/session cookies (same framework as the frontend)"
        reasons = ["web app scope: httpOnly session cookies avoid token storage pitfalls in browsers"]
    notes = [
        "store credentials only as salted hashes; never log secrets",
        "set explicit session/token lifetimes and rotation policy before build",
        "add rate limiting on login/register endpoints",
    ]
    if "payments" in eff:
        notes.append("checkout flows must re-verify authorization server-side, never from client state")
    return _result(
        "stack.select.auth",
        "analysis",
        {
            "included": True,
            "project_type": ctx["project_type"],
            "choice": choice,
            "reasons": reasons,
            "security_notes": notes,
            "method": "deterministic rules over project_type/scale/preferences; negated or absent auth is never added",
        },
    )


def stack_select_payment(args):
    ctx = _normalize_ctx(args, require_name=False)
    if ctx["project_type"] is None:
        _err("project_type is required (directly, via context, or detectable from text)")
    eff = set(_effective_features(ctx))
    excluded = set(ctx["excluded_features"])
    if "payments" in excluded:
        return _result(
            "stack.select.payment",
            "analysis",
            {
                "included": False,
                "reason": "spec explicitly excludes payments (negation honored); no gateway is selected and no payment flow may be added",
            },
        )
    if "payments" not in eff:
        return _result(
            "stack.select.payment",
            "analysis",
            {
                "included": False,
                "reason": "spec does not request payments; per anti-hallucination scoping no gateway is added",
            },
        )
    raw_text = (ctx["text"] or "").lower()
    named = [name for name in ("midtrans", "xendit", "doku", "stripe", "paypal") if name in raw_text]
    provider_labels = {"midtrans": "Midtrans", "xendit": "Xendit", "doku": "DOKU", "stripe": "Stripe", "paypal": "PayPal"}
    market = str(ctx["preferences"].get("market", "")).lower()
    budget_raw = [str(b).lower() for b in ctx["constraints"].get("budget_raw", [])]
    if named:
        choice = provider_labels[named[0]]
        reasons = [f"spec text explicitly names '{named[0]}'"]
        if named[0] in {"midtrans", "xendit", "doku"}:
            alternatives = [{"option": "Stripe", "when": "international cards become a requirement"}]
        else:
            alternatives = [{"option": "Midtrans", "when": "Indonesian local payment methods (VA, e-wallet) become a requirement"}]
    elif market == "id" or "indonesia" in raw_text or "rupiah" in raw_text or any("rp" in b for b in budget_raw):
        choice = "Midtrans"
        reasons = [
            "deterministic regional preference: Indonesia market signals (indonesia/rupiah/market=id) prefer local gateways first",
            "local gateways expose VA/e-wallet/convenience-store methods common in Indonesia",
        ]
        alternatives = [
            {"option": "Xendit", "when": "broader multi-channel local coverage is needed"},
            {"option": "DOKU", "when": "existing DOKU merchant account exists"},
        ]
    else:
        choice = "Stripe"
        reasons = ["default for international card coverage when no region signal exists"]
        alternatives = [{"option": "PayPal", "when": "wallet coverage is requested"}]
    return _result(
        "stack.select.payment",
        "analysis",
        {
            "included": True,
            "choice": choice,
            "reasons": reasons,
            "alternatives": alternatives,
            "mandatory_controls": [
                "verify webhook signatures before processing",
                "compute amounts server-side only",
                "idempotency keys on payment creation and webhook handling",
                "store provider references, never credentials, in the database",
            ],
            "method": "deterministic: named provider > regional preference > international default",
        },
        limitations=[NO_COST_NO_VERSION_LIMIT[0]],
    )


def stack_select_storage(args):
    ctx = _normalize_ctx(args, require_name=False)
    if ctx["project_type"] is None:
        _err("project_type is required (directly, via context, or detectable from text)")
    dyn = _dynamic_features(ctx)
    excluded = set(ctx["excluded_features"])
    if "upload" in excluded:
        return _result(
            "stack.select.storage",
            "analysis",
            {"included": False, "reason": "spec explicitly excludes uploads (negation honored)"},
        )
    if not dyn["needs_upload"]:
        return _result(
            "stack.select.storage",
            "analysis",
            {"included": False, "reason": "no upload/catalog/blog media needs detected; per anti-hallucination scoping no storage service is added"},
        )
    self_host = bool(ctx["preferences"].get("self_host")) or "vps" in str(ctx["constraints"].get("data_region", [""])).lower()
    if self_host:
        choice = "MinIO (self-hosted S3-compatible)"
        reasons = ["self-hosting preference or data-residency constraint detected"]
        alternatives = [{"option": "S3", "when": "managed durability is preferred"}]
    else:
        choice = "S3-compatible object storage; Cloudinary acceptable for image-focused catalogs"
        reasons = ["managed storage keeps media off the app server; image CDN saves transformation work"]
        alternatives = [{"option": "MinIO", "when": "self-hosting on VPS is preferred"}]
    return _result(
        "stack.select.storage",
        "analysis",
        {
            "included": True,
            "choice": choice,
            "reasons": reasons,
            "alternatives": alternatives,
            "mandatory_controls": [
                "validate MIME type and size server-side",
                "serve via signed URLs or CDN, never straight from the app container disk",
                "store only keys/paths in the database",
            ],
            "method": "deterministic: needs_upload signal + self-host preference",
        },
        limitations=[NO_COST_NO_VERSION_LIMIT[0]],
    )


def stack_select_cache(args):
    ctx = _normalize_ctx(args, require_name=False)
    if ctx["project_type"] is None:
        _err("project_type is required (directly, via context, or detectable from text)")
    dyn = _dynamic_features(ctx)
    if dyn["needs_realtime"] or dyn["needs_queue"] or ctx["scale"] in {"medium", "large"}:
        choice = "Redis"
        reasons = []
        if dyn["needs_realtime"]:
            reasons.append("realtime/chat signals: Redis pub/sub and presence are the common backbone")
        if dyn["needs_queue"]:
            reasons.append("notifications/payments reconciliation want a queue backend; Redis covers lightweight queues")
        if ctx["scale"] in {"medium", "large"}:
            reasons.append(f"scale={ctx['scale']}: session and read caching offload the primary database")
        reasons.append("deterministic rule: realtime/queue/scale signals gate the cache layer")
    else:
        choice = "none for now"
        reasons = [
            "no realtime/queue/scale signals in the spec; introduce cache only when measurements justify it",
            "anti-hallucination scoping: no component added without a spec driver",
        ]
    return _result(
        "stack.select.cache",
        "analysis",
        {
            "choice": choice,
            "reasons": reasons,
            "trigger_metrics": ["p95 read latency", "session store size", "queue depth"],
            "method": "deterministic signal gating",
        },
    )


def stack_select_search(args):
    ctx = _normalize_ctx(args, require_name=False)
    if ctx["project_type"] is None:
        _err("project_type is required (directly, via context, or detectable from text)")
    dyn = _dynamic_features(ctx)
    excluded = set(ctx["excluded_features"])
    if "search" in excluded or not dyn["needs_search"]:
        return _result(
            "stack.select.search",
            "analysis",
            {
                "included": False,
                "reason": "spec explicitly excludes search (negation honored)" if "search" in excluded else "no search/filter feature requested; not added",
            },
        )
    if ctx["scale"] == "large" or ctx["project_type"] in {"marketplace"}:
        choice = "dedicated search engine (Meilisearch for typo-tolerant product search, or Elasticsearch for heavy faceting/logs)"
        reasons = ["large scale/marketplace signals justify a dedicated engine operated alongside the primary DB"]
    else:
        choice = "primary database full-text search first (e.g., Postgres FTS)"
        reasons = [
            "small/medium catalog: the existing relational DB covers search without new infrastructure",
            "deterministic migration trigger: move to a dedicated engine past ~100k documents or complex facet needs",
        ]
    return _result(
        "stack.select.search",
        "analysis",
        {"choice": choice, "reasons": reasons, "method": "deterministic scale/project-type gating"},
        limitations=[NO_COST_NO_VERSION_LIMIT[0]],
    )


def stack_select_monitoring(args):
    ctx = _normalize_ctx(args, require_name=False)
    if ctx["project_type"] is None:
        _err("project_type is required (directly, via context, or detectable from text)")
    self_host = bool(ctx["preferences"].get("self_host"))
    if self_host:
        choice = "self-hosted baseline: uptime checker + error tracker (e.g., GlitchTip) + logs on disk; Prometheus/Grafana when metrics matter"
        reasons = ["self-host preference detected; avoid hosted SaaS dependencies"]
    else:
        choice = "baseline: uptime checks + error tracking + web vitals (e.g., UptimeRobot, Sentry, platform analytics)"
        reasons = ["managed defaults minimize setup time for small/medium teams"]
    return _result(
        "stack.select.monitoring",
        "analysis",
        {
            "choice": choice,
            "reasons": reasons,
            "minimum_set": ["uptime checks on / and a critical API route", "error tracking with release tagging", "web vitals collection"],
            "ops_policy": "updates stay manual, verified, and rollback-ready; no auto-update pipelines are planned here",
            "method": "deterministic self-host preference switch",
        },
        limitations=["Tool names are planning references only; nothing is installed or integrated by this module."],
    )


def stack_recommend_combination(args):
    ctx = _normalize_ctx(args)
    if not ctx["name"]:
        _err("stack.recommend.combination requires 'name' (or detectable from text)")
    if ctx["project_type"] is None:
        _err("project_type is required (directly, via context, or detectable from text)")
    parts = {
        "frontend": stack_select_frontend({"project_type": ctx["project_type"], "features": ctx["features"], "excluded_features": ctx["excluded_features"], "preferences": ctx["preferences"], "scale": ctx["scale"]})["data"]["choice"],
        "backend": stack_select_backend({"project_type": ctx["project_type"], "features": ctx["features"], "excluded_features": ctx["excluded_features"], "preferences": ctx["preferences"], "scale": ctx["scale"]})["data"]["choice"],
        "database": db_engine_match({"project_type": ctx["project_type"], "features": ctx["features"], "scale": ctx["scale"], "preferences": ctx["preferences"], "text": ctx["text"]})["data"]["recommendation"]["label"],
        "hosting": stack_select_hosting({"project_type": ctx["project_type"], "features": ctx["features"], "excluded_features": ctx["excluded_features"], "preferences": ctx["preferences"], "constraints": ctx["constraints"], "scale": ctx["scale"]})["data"]["choice"],
        "auth": stack_select_auth({"project_type": ctx["project_type"], "features": ctx["features"], "excluded_features": ctx["excluded_features"], "preferences": ctx["preferences"], "scale": ctx["scale"]})["data"],
        "payments": stack_select_payment({"project_type": ctx["project_type"], "features": ctx["features"], "excluded_features": ctx["excluded_features"], "preferences": ctx["preferences"], "text": ctx["text"]})["data"],
        "storage": stack_select_storage({"project_type": ctx["project_type"], "features": ctx["features"], "excluded_features": ctx["excluded_features"], "preferences": ctx["preferences"]})["data"],
        "cache": stack_select_cache({"project_type": ctx["project_type"], "features": ctx["features"], "excluded_features": ctx["excluded_features"], "scale": ctx["scale"]})["data"]["choice"],
        "search": stack_select_search({"project_type": ctx["project_type"], "features": ctx["features"], "excluded_features": ctx["excluded_features"], "scale": ctx["scale"]})["data"].get("choice", "none"),
        "monitoring": stack_select_monitoring({"project_type": ctx["project_type"], "preferences": ctx["preferences"]})["data"]["choice"],
    }
    lines = [f"# Stack recommendation: {ctx['name']}", "", f"Project type: {ctx['project_type']} ({ctx['project_type_source']})", ""]
    for layer, value in parts.items():
        summary = value if isinstance(value, str) else (value.get("choice") or ("included" if value.get("included") else "not included"))
        lines.append(f"- {layer}: {summary}")
    lines += [
        "",
        "## Exclusions honored",
        "",
        (", ".join(sorted(set(ctx["excluded_features"]))) if ctx["excluded_features"] else "none recorded"),
        "",
        "## Phased build plan (planning only)",
        "",
        "1. Architecture: arch.design.system, arch.design.database_schema, arch.design.api",
        "2. Design: design.color.palette, design.typography, design.theme, design.prototype",
        "3. Content/SEO: content.gen.placeholder, seo.gen.meta, seo.gen.sitemap, seo.gen.robots",
        "4. Handoff: root layers own scaffolding, code generation, testing, deployment, and monitoring execution.",
        "",
    ]
    doc = "\n".join(lines)
    return _result(
        "stack.recommend.combination",
        "artifact_generation",
        {
            "project_type": ctx["project_type"],
            "combination": parts,
            "exclusions": sorted(set(ctx["excluded_features"])),
            "method": "composition of the deterministic selectors above; single source of rules",
        },
        writes=[_write("docs/stack/stack-recommendation.md", doc)],
        limitations=[NO_COST_NO_VERSION_LIMIT[0], "Recommendation is not a build; scaffolding/execution belongs to root layers."],
    )



DERIVED_TABLES = {
    "users": [
        ("id", "BIGSERIAL", "PRIMARY KEY"),
        ("email", "TEXT", "NOT NULL UNIQUE"),
        ("password_hash", "TEXT", ""),
        ("display_name", "TEXT", ""),
        ("role", "TEXT", "NOT NULL DEFAULT 'member'"),
        ("created_at", "TIMESTAMPTZ", "NOT NULL DEFAULT now()"),
        ("updated_at", "TIMESTAMPTZ", "NOT NULL DEFAULT now()"),
    ],
    "categories": [
        ("id", "BIGSERIAL", "PRIMARY KEY"),
        ("name", "TEXT", "NOT NULL"),
        ("slug", "TEXT", "NOT NULL UNIQUE"),
    ],
    "products": [
        ("id", "BIGSERIAL", "PRIMARY KEY"),
        ("name", "TEXT", "NOT NULL"),
        ("slug", "TEXT", "NOT NULL UNIQUE"),
        ("description", "TEXT", ""),
        ("price_cents", "BIGINT", "NOT NULL CHECK (price_cents >= 0)"),
        ("category_id", "BIGINT", "REFERENCES categories(id)"),
        ("image_url", "TEXT", ""),
        ("is_active", "BOOLEAN", "NOT NULL DEFAULT TRUE"),
        ("created_at", "TIMESTAMPTZ", "NOT NULL DEFAULT now()"),
        ("updated_at", "TIMESTAMPTZ", "NOT NULL DEFAULT now()"),
    ],
    "carts": [
        ("id", "BIGSERIAL", "PRIMARY KEY"),
        ("user_id", "BIGINT", "REFERENCES users(id)"),
        ("status", "TEXT", "NOT NULL DEFAULT 'open'"),
        ("created_at", "TIMESTAMPTZ", "NOT NULL DEFAULT now()"),
        ("updated_at", "TIMESTAMPTZ", "NOT NULL DEFAULT now()"),
    ],
    "cart_items": [
        ("id", "BIGSERIAL", "PRIMARY KEY"),
        ("cart_id", "BIGINT", "NOT NULL REFERENCES carts(id) ON DELETE CASCADE"),
        ("product_id", "BIGINT", "NOT NULL REFERENCES products(id)"),
        ("quantity", "INTEGER", "NOT NULL CHECK (quantity > 0)"),
        ("unit_price_cents", "BIGINT", "NOT NULL"),
    ],
    "orders": [
        ("id", "BIGSERIAL", "PRIMARY KEY"),
        ("user_id", "BIGINT", "REFERENCES users(id)"),
        ("status", "TEXT", "NOT NULL DEFAULT 'pending'"),
        ("total_cents", "BIGINT", "NOT NULL CHECK (total_cents >= 0)"),
        ("placed_at", "TIMESTAMPTZ", "NOT NULL DEFAULT now()"),
        ("updated_at", "TIMESTAMPTZ", "NOT NULL DEFAULT now()"),
    ],
    "order_items": [
        ("id", "BIGSERIAL", "PRIMARY KEY"),
        ("order_id", "BIGINT", "NOT NULL REFERENCES orders(id) ON DELETE CASCADE"),
        ("product_id", "BIGINT", "NOT NULL REFERENCES products(id)"),
        ("quantity", "INTEGER", "NOT NULL CHECK (quantity > 0)"),
        ("unit_price_cents", "BIGINT", "NOT NULL"),
    ],
    "payments": [
        ("id", "BIGSERIAL", "PRIMARY KEY"),
        ("order_id", "BIGINT", "NOT NULL REFERENCES orders(id)"),
        ("provider", "TEXT", "NOT NULL"),
        ("provider_ref", "TEXT", ""),
        ("amount_cents", "BIGINT", "NOT NULL"),
        ("status", "TEXT", "NOT NULL DEFAULT 'initialized'"),
        ("paid_at", "TIMESTAMPTZ", ""),
    ],
    "posts": [
        ("id", "BIGSERIAL", "PRIMARY KEY"),
        ("title", "TEXT", "NOT NULL"),
        ("slug", "TEXT", "NOT NULL UNIQUE"),
        ("body", "TEXT", "NOT NULL"),
        ("status", "TEXT", "NOT NULL DEFAULT 'draft'"),
        ("published_at", "TIMESTAMPTZ", ""),
        ("author_id", "BIGINT", "REFERENCES users(id)"),
        ("created_at", "TIMESTAMPTZ", "NOT NULL DEFAULT now()"),
    ],
    "bookings": [
        ("id", "BIGSERIAL", "PRIMARY KEY"),
        ("user_id", "BIGINT", "REFERENCES users(id)"),
        ("resource", "TEXT", "NOT NULL"),
        ("starts_at", "TIMESTAMPTZ", "NOT NULL"),
        ("ends_at", "TIMESTAMPTZ", "NOT NULL"),
        ("status", "TEXT", "NOT NULL DEFAULT 'requested'"),
        ("notes", "TEXT", ""),
    ],
    "reviews": [
        ("id", "BIGSERIAL", "PRIMARY KEY"),
        ("user_id", "BIGINT", "NOT NULL REFERENCES users(id)"),
        ("product_id", "BIGINT", "REFERENCES products(id)"),
        ("rating", "INTEGER", "NOT NULL CHECK (rating BETWEEN 1 AND 5)"),
        ("body", "TEXT", ""),
        ("created_at", "TIMESTAMPTZ", "NOT NULL DEFAULT now()"),
    ],
    "notifications": [
        ("id", "BIGSERIAL", "PRIMARY KEY"),
        ("user_id", "BIGINT", "NOT NULL REFERENCES users(id)"),
        ("channel", "TEXT", "NOT NULL"),
        ("payload", "JSONB", "NOT NULL DEFAULT '{}'::jsonb"),
        ("read_at", "TIMESTAMPTZ", ""),
        ("created_at", "TIMESTAMPTZ", "NOT NULL DEFAULT now()"),
    ],
    "media": [
        ("id", "BIGSERIAL", "PRIMARY KEY"),
        ("user_id", "BIGINT", "REFERENCES users(id)"),
        ("storage_path", "TEXT", "NOT NULL"),
        ("mime_type", "TEXT", "NOT NULL"),
        ("size_bytes", "BIGINT", "NOT NULL"),
        ("created_at", "TIMESTAMPTZ", "NOT NULL DEFAULT now()"),
    ],
    "wishlists": [
        ("id", "BIGSERIAL", "PRIMARY KEY"),
        ("user_id", "BIGINT", "NOT NULL REFERENCES users(id) ON DELETE CASCADE"),
        ("product_id", "BIGINT", "NOT NULL REFERENCES products(id) ON DELETE CASCADE"),
        ("UNIQUE", "UNIQUE (user_id, product_id)", ""),
    ],
    "threads": [
        ("id", "BIGSERIAL", "PRIMARY KEY"),
        ("user_id", "BIGINT", "NOT NULL REFERENCES users(id)"),
        ("title", "TEXT", "NOT NULL"),
        ("body", "TEXT", "NOT NULL"),
        ("created_at", "TIMESTAMPTZ", "NOT NULL DEFAULT now()"),
    ],
    "replies": [
        ("id", "BIGSERIAL", "PRIMARY KEY"),
        ("thread_id", "BIGINT", "NOT NULL REFERENCES threads(id) ON DELETE CASCADE"),
        ("user_id", "BIGINT", "NOT NULL REFERENCES users(id)"),
        ("body", "TEXT", "NOT NULL"),
        ("created_at", "TIMESTAMPTZ", "NOT NULL DEFAULT now()"),
    ],
    "events": [
        ("id", "BIGSERIAL", "PRIMARY KEY"),
        ("name", "TEXT", "NOT NULL"),
        ("properties", "JSONB", "NOT NULL DEFAULT '{}'::jsonb"),
        ("occurred_at", "TIMESTAMPTZ", "NOT NULL DEFAULT now()"),
    ],
}

FEATURE_TABLES = {
    "catalog": ["categories", "products"],
    "cart": ["carts", "cart_items"],
    "checkout": ["orders", "order_items"],
    "orders": ["orders", "order_items"],
    "payments": ["payments"],
    "auth": ["users"],
    "admin": ["users"],
    "blog": ["posts"],
    "booking": ["bookings"],
    "reviews": ["reviews"],
    "notifications": ["notifications"],
    "upload": ["media"],
    "wishlist": ["wishlists"],
    "forum": ["threads", "replies"],
    "chat": ["threads", "replies"],
    "analytics": ["events"],
}

FIELD_TYPE_HINTS = {
    "int": "INTEGER",
    "integer": "INTEGER",
    "bigint": "BIGINT",
    "money": "BIGINT",
    "cents": "BIGINT",
    "bool": "BOOLEAN",
    "boolean": "BOOLEAN",
    "time": "TIMESTAMPTZ",
    "timestamp": "TIMESTAMPTZ",
    "date": "TIMESTAMPTZ",
    "json": "JSONB",
    "text": "TEXT",
    "str": "TEXT",
}

FK_TARGETS = {
    "user_id": "users",
    "author_id": "users",
    "category_id": "categories",
    "product_id": "products",
    "cart_id": "carts",
    "order_id": "orders",
    "post_id": "posts",
    "thread_id": "threads",
    "parent_id": None,
}


def _resolve_entities(ctx):
    raw_entities = ctx.get("entities_arg")
    tables = {}
    order = []
    if raw_entities:
        for ent in raw_entities:
            if not isinstance(ent, dict) or not isinstance(ent.get("name"), str) or not ent["name"].strip():
                _err("entities entries must be objects with a non-empty 'name'")
            tname = _slug(ent["name"], "table").replace("-", "_")
            cols = []
            for field in ent.get("fields", []) or []:
                if not isinstance(field, str) or not field.strip():
                    _err("entity fields must be strings like 'price_cents:int'")
                if ":" in field:
                    fname, hint = field.split(":", 1)
                    ftype = FIELD_TYPE_HINTS.get(hint.strip().lower(), "TEXT")
                else:
                    fname, ftype = field, "TEXT"
                cols.append((fname.strip(), ftype, ""))
            cols = [("id", "BIGSERIAL", "PRIMARY KEY")] + cols + [
                ("created_at", "TIMESTAMPTZ", "NOT NULL DEFAULT now()"),
                ("updated_at", "TIMESTAMPTZ", "NOT NULL DEFAULT now()"),
            ]
            tables[tname] = cols
            order.append(tname)
        if _has_feature(ctx, "auth") and "users" not in tables:
            tables["users"] = DERIVED_TABLES["users"]
            order.insert(0, "users")
        return tables, order, "explicit entities argument"
    for feature in _effective_features(ctx):
        for tname in FEATURE_TABLES.get(feature, []):
            if tname not in tables:
                tables[tname] = DERIVED_TABLES[tname]
                order.append(tname)
    if "users" in tables and order[0] != "users":
        order.remove("users")
        order.insert(0, "users")
    return tables, order, "derived from spec features via deterministic feature-to-table map"


def _fk_relations(tables, order):
    relations = []
    for tname in order:
        for col, _ftype, constraint in tables[tname]:
            target = FK_TARGETS.get(col)
            if target and target != tname and target in tables:
                verb = "places" if col == "user_id" else "contains"
                relations.append((target, tname, col, verb))
    return relations


def _render_ddl(tables, order, relations):
    lines = ["-- Generic Postgres-compatible DDL generated by planning; review before applying.", ""]
    for tname in order:
        lines.append(f"CREATE TABLE {tname} (")
        rows = []
        for col, ftype, constraint in tables[tname]:
            if col == "UNIQUE":
                rows.append(f"  {ftype}")
            else:
                spec = f"  {col} {ftype}"
                if constraint:
                    spec += f" {constraint}"
                rows.append(spec)
        lines.append(",\n".join(rows))
        lines.append(");")
        lines.append("")
    for src, tgt, col, _verb in relations:
        lines.append(f"CREATE INDEX idx_{tgt}_{col} ON {tgt} ({col});")
    return "\n".join(lines).rstrip() + "\n"


def _render_er_mermaid(tables, order, relations):
    lines = ["erDiagram"]
    for tname in order:
        lines.append(f"  {tname.upper()} {{")
        for col, ftype, constraint in tables[tname]:
            if col == "UNIQUE":
                continue
            marker = " PK" if "PRIMARY KEY" in constraint else (" UK" if "UNIQUE" in constraint else "")
            lines.append(f"    {ftype.split('(')[0].lower()} {col}{marker}")
        lines.append("  }")
    for src, tgt, _col, verb in relations:
        lines.append(f"  {src.upper()} ||--o{{ {tgt.upper()} : {verb}")
    return "\n".join(lines)


def arch_design_system(args):
    ctx = _normalize_ctx(args)
    if not ctx["name"]:
        _err("arch.design.system requires 'name' (or detectable from text)")
    dyn = _dynamic_features(ctx)
    hosting, _hreasons, _halts, _htrigs = _hosting_choice(ctx)
    layers = [
        {"layer": "Presentation", "components": ["web frontend (server-rendered pages)", "static assets/CDN"]},
        {"layer": "Application/API", "components": ["HTTP API / server actions", "auth session handling" if dyn["needs_auth"] else "request validation"]},
        {"layer": "Domain", "components": [f"{f} logic" for f in _effective_features(ctx)] or ["core business rules"]},
        {"layer": "Data", "components": ["primary database"] + (["cache/session store"] if dyn["needs_realtime"] or dyn["needs_queue"] else [])},
        {"layer": "External", "components": (["payment gateway"] if dyn["needs_payments"] else []) + (["email/notification provider"] if "notifications" in _effective_features(ctx) else []) + (["object storage"] if dyn["needs_upload"] else []) or ["none specified by spec"]},
    ]
    mermaid = _mermaid_system(ctx, dyn)
    boundaries = [
        "the browser never talks to the database directly",
        "payment credentials never enter the app database (provider references only)" if dyn["needs_payments"] else "no external credential stores are in scope",
        "admin surfaces are isolated behind auth+role checks" if dyn["needs_auth"] else "public read-only surfaces",
    ]
    doc = _md_document(
        f"{ctx['name']}: system architecture (planning)",
        [
            ("Project type", [f"{ctx['project_type'] or 'unspecified by spec'} ({ctx['project_type_source']})"]),
            ("Features in scope", [", ".join(_effective_features(ctx)) or "none parsed"]),
            ("Exclusions honored", [", ".join(sorted(set(ctx["excluded_features"]))) or "none"]),
            ("Layers", [f"- **{l['layer']}**: " + ", ".join(l["components"]) for l in layers]),
            ("Boundaries and invariants", [f"- {b}" for b in boundaries]),
            ("Target hosting shape", [hosting]),
            ("System diagram", ["```mermaid", mermaid, "```"]),
        ],
    )
    return _result(
        "arch.design.system",
        "artifact_generation",
        {
            "layers": layers,
            "boundaries": boundaries,
            "hosting_shape": hosting,
            "signals": dyn,
            "exclusions": sorted(set(ctx["excluded_features"])),
        },
        writes=[_write("docs/architecture/system.md", doc)],
        limitations=["Design document only; no components were created or deployed."],
    )


def _mermaid_system(ctx, dyn):
    name = _slug(ctx["name"] or "app", "app").upper().replace("-", "_")
    lines = ["flowchart LR", f'  U["User"] --> FE["Web frontend"]']
    lines.append('  FE --> API["API layer"]')
    lines.append('  API --> DB[("Primary database")]')
    if dyn["needs_realtime"] or dyn["needs_queue"]:
        lines.append('  API --> CACHE[("Cache / queue")]')
    if dyn["needs_payments"]:
        lines.append('  API --> PAY["Payment gateway"]')
    if "notifications" in _effective_features(ctx):
        lines.append('  API --> MAIL["Email provider"]')
    if dyn["needs_upload"]:
        lines.append('  API --> STORE["Object storage"]')
    return "\n".join(lines)


def arch_design_database_schema(args):
    ctx = _normalize_ctx(args)
    raw_entities = _opt_list(args, "entities", item_type=dict)
    ctx["entities_arg"] = raw_entities
    if not ctx["name"]:
        _err("arch.design.database_schema requires 'name' (or detectable from text)")
    if raw_entities is None and not _effective_features(ctx) and ctx["text"] is None:
        _err("arch.design.database_schema needs one of: entities, features, or text to derive tables")
    tables, order, source = _resolve_entities(ctx)
    relations = _fk_relations(tables, order)
    ddl = _render_ddl(tables, order, relations)
    er = _render_er_mermaid(tables, order, relations)
    doc = _md_document(
        f"{ctx['name']}: database schema (planning)",
        [
            ("Derivation", [f"tables sourced from: {source}", "exclusions honored: " + (", ".join(sorted(set(ctx['excluded_features']))) or "none")]),
            ("Tables", [f"- **{t}**: " + ", ".join(c[0] for c in tables[t] if c[0] != "UNIQUE") for t in order]),
            ("Relations", [f"- {src} 1..* {tgt} via {col}" for src, tgt, col, _v in relations] or ["- none"]),
            ("DDL", ["```sql", ddl, "```"]),
            ("ER diagram", ["```mermaid", er, "```"]),
        ],
    )
    return _result(
        "arch.design.database_schema",
        "artifact_generation",
        {
            "tables": {t: [{"name": c, "type": ft} for c, ft, _c in tables[t] if c != "UNIQUE"] for t in order},
            "relations": [{"from": src, "to": tgt, "via": col} for src, tgt, col, _v in relations],
            "derivation_source": source,
            "exclusions": sorted(set(ctx["excluded_features"])),
        },
        writes=[_write("db/schema.sql", ddl), _write("docs/db/schema.md", doc)],
        limitations=["DDL is a reviewed-before-apply artifact; nothing was executed against any database."],
    )


def arch_design_api(args):
    ctx = _normalize_ctx(args)
    raw_entities = _opt_list(args, "entities", item_type=dict)
    ctx["entities_arg"] = raw_entities
    if not ctx["name"]:
        _err("arch.design.api requires 'name' (or detectable from text)")
    if raw_entities is None and not _effective_features(ctx) and ctx["text"] is None:
        _err("arch.design.api needs one of: entities, features, or text to derive resources")
    _tables, order, source = _resolve_entities(ctx)
    resources = [t for t in order if t not in {"users", "cart_items", "order_items", "replies"}]
    dyn = _dynamic_features(ctx)
    paths = {}
    for res in resources:
        paths[f"/{res}"] = {
            "get": {"summary": f"List {res} (paginated: page, per_page, sort; q filter when search is in scope)", "responses": {"200": {"description": "OK"}, "400": {"description": "Bad request"}}},
            "post": {"summary": f"Create {res}", "responses": {"201": {"description": "Created"}, "400": {"description": "Validation error"}, "401": {"description": "Unauthorized"}}},
        }
        paths[f"/{res}/{{id}}"] = {
            "get": {"summary": f"Fetch one {res}", "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}}},
            "patch": {"summary": f"Update {res}", "responses": {"200": {"description": "OK"}, "404": {"description": "Not found"}, "403": {"description": "Forbidden"}}},
            "delete": {"summary": f"Delete {res}", "responses": {"204": {"description": "Deleted"}, "404": {"description": "Not found"}}},
        }
    if dyn["needs_auth"]:
        paths["/auth/register"] = {"post": {"summary": "Create account", "responses": {"201": {"description": "Created"}, "409": {"description": "Email taken"}}}}
        paths["/auth/login"] = {"post": {"summary": "Start session / issue tokens", "responses": {"200": {"description": "OK"}, "401": {"description": "Invalid credentials"}, "429": {"description": "Rate limited"}}}}
        paths["/auth/logout"] = {"post": {"summary": "End session / revoke refresh", "responses": {"204": {"description": "Logged out"}}}}
        paths["/me"] = {"get": {"summary": "Current account", "responses": {"200": {"description": "OK"}, "401": {"description": "Unauthorized"}}}}
    if dyn["needs_payments"]:
        paths["/payments/intent"] = {"post": {"summary": "Create payment intent (server-side amount, idempotency key required)", "responses": {"201": {"description": "Created"}, "402": {"description": "Payment required"}}}}
        paths["/webhooks/payments"] = {"post": {"summary": "Provider webhook (signature verified, idempotent)", "responses": {"200": {"description": "Processed"}}}}
    openapi = {
        "openapi": "3.1.0",
        "info": {"title": f"{ctx['name']} API (planning draft)", "version": "0.1.0-draft"},
        "paths": paths,
        "components": {"securitySchemes": ({"sessionCookie": {"type": "apiKey", "in": "cookie", "name": "session"}} if dyn["needs_auth"] else {})},
    }
    md_lines = [
        f"# {ctx['name']}: API design (planning)",
        "",
        f"Resources derived from: {source}. Exclusions honored: {', '.join(sorted(set(ctx['excluded_features']))) or 'none'}.",
        "",
        "| Method | Path | Purpose |",
        "|---|---|---|",
    ]
    for path, ops in paths.items():
        for method, spec in ops.items():
            md_lines.append(f"| {method.upper()} | {path.replace('{id}', ':id')} | {spec['summary']} |")
    md_lines += [
        "",
        "## Conventions",
        "",
        "- JSON responses use `{\"data\": ..., \"meta\": ...}` envelopes.",
        "- Errors use `{\"error\": {\"code\", \"message\"}}` with correct HTTP status codes.",
        "- List endpoints are paginated (`page`, `per_page`) and sorted explicitly.",
        "- Mutating POSTs that trigger provider calls require idempotency keys.",
    ]
    doc = "\n".join(md_lines) + "\n"
    return _result(
        "arch.design.api",
        "artifact_generation",
        {
            "style": "REST",
            "resources": resources,
            "path_count": len(paths),
            "auth_endpoints": dyn["needs_auth"],
            "payment_endpoints": dyn["needs_payments"],
            "exclusions": sorted(set(ctx["excluded_features"])),
        },
        writes=[_write("docs/api/api-design.md", doc), _write("docs/api/openapi.json", json.dumps(openapi, indent=2, ensure_ascii=False) + "\n")],
        limitations=["Design artifact only; no routes were implemented or served."],
    )


PAGE_COMPONENTS = {
    "catalog": ["CatalogPage", "ProductCard", "ProductDetailPage", "CategoryNav"],
    "cart": ["CartPage", "CartItemRow"],
    "checkout": ["CheckoutPage", "AddressForm", "PaymentMethodPicker"],
    "orders": ["OrdersPage", "OrderDetailPage"],
    "auth": ["LoginPage", "RegisterPage", "ForgotPasswordPage"],
    "admin": ["AdminLayout", "AdminDashboard", "ResourceTable"],
    "dashboard": ["DashboardLayout", "StatCard", "ChartPanel"],
    "blog": ["BlogListPage", "BlogPostPage"],
    "booking": ["BookingPage", "SlotPicker"],
    "reviews": ["ReviewList", "ReviewForm"],
    "wishlist": ["WishlistPage"],
    "forum": ["ThreadListPage", "ThreadPage", "ReplyForm"],
    "chat": ["ChatWindow", "MessageBubble"],
    "notifications": ["NotificationBell"],
    "upload": ["FileDropzone"],
    "search": ["SearchBar", "FilterPanel"],
    "analytics": ["AnalyticsPanel"],
    "dark_mode": ["ThemeToggle"],
}

FRAMEWORK_EXT = {"react": "tsx", "vue": "vue", "svelte": "svelte"}


def arch_design_component_tree(args):
    ctx = _normalize_ctx(args)
    if not ctx["name"]:
        _err("arch.design.component_tree requires 'name' (or detectable from text)")
    framework = _opt_choice(args, "framework", ("react", "vue", "svelte"), default=None) or "react"
    ext = FRAMEWORK_EXT[framework]
    eff = _effective_features(ctx)
    tree = {"AppShell": {"NavBar": {}, "Footer": {}}}
    pages = ["HomePage"]
    for feature in eff:
        for comp in PAGE_COMPONENTS.get(feature, []):
            if comp.endswith("Page"):
                pages.append(comp)
            else:
                tree.setdefault("shared", {}).setdefault(comp, {})
    tree["AppShell"]["pages"] = {p: {} for p in pages}
    lines = ["flowchart TD", f'  APP["AppShell"] --> NAV["NavBar"]', "  APP --> FOOT[\"Footer\"]"]
    for p in pages:
        lines.append(f'  APP --> { _slug(p, "page").upper().replace("-", "_") }["{p}"]')
    md = [f"# {ctx['name']}: frontend component tree (planning)", "", f"Framework convention: {framework} (.{ext} files).", "", "```mermaid", "\n".join(lines), "```", "", "| Component | Suggested path |", "|---|---|", f"| AppShell | src/components/AppShell.{ext} |", f"| NavBar | src/components/NavBar.{ext} |", f"| Footer | src/components/Footer.{ext} |"]
    for p in pages:
        md.append(f"| {p} | src/pages/{p}.{ext} |")
    for comp in sorted(tree.get("shared", {})):
        md.append(f"| {comp} | src/components/{comp}.{ext} |")
    md += ["", f"Features in scope: {', '.join(eff) or 'none parsed'}. Exclusions honored: {', '.join(sorted(set(ctx['excluded_features']))) or 'none'}.", ""]
    doc = "\n".join(md)
    return _result(
        "arch.design.component_tree",
        "artifact_generation",
        {
            "framework": framework,
            "pages": pages,
            "shared_components": sorted(tree.get("shared", {})),
            "features": eff,
            "exclusions": sorted(set(ctx["excluded_features"])),
        },
        writes=[_write("docs/frontend/component-tree.md", doc)],
        limitations=["Planning tree only; no component code was generated here (frontend builder layers own that)."],
    )


FRAMEWORK_TREES = {
    "next": ("Next.js", "app/\n  layout.tsx\n  page.tsx\n  (routes)/\ncomponents/\nlib/\nstyles/\npublic/\ntests/"),
    "astro": ("Astro", "src/\n  pages/\n  layouts/\n  components/\n  content/\npublic/"),
    "nuxt": ("Nuxt", "app/\n  pages/\n  components/\n  composables/\npublic/"),
    "sveltekit": ("SvelteKit", "src/\n  routes/\n  lib/\n    components/\nstatic/"),
    "react_vite": ("Vite + React SPA", "src/\n  pages/\n  components/\n  hooks/\n  api/\npublic/"),
    "express": ("Express", "src/\n  routes/\n  controllers/\n  services/\n  middlewares/\ntests/"),
    "fastapi": ("FastAPI", "app/\n  main.py\n  api/\n  models/\n  schemas/\n  services/\ntests/"),
    "nestjs": ("NestJS", "src/\n  modules/\n    <domain>/\n  common/\n  config/\ntest/"),
    "django": ("Django", "config/\napps/\n  <domain>/\n    models.py\n    views.py\ntemplates/\nstatic/"),
    "laravel": ("Laravel", "app/\n  Http/\n    Controllers/\n  Models/\nroutes/\ndatabase/\n  migrations/\n  seeders/"),
    "monorepo": ("Monorepo (Turborepo/Nx style)", "apps/\n  web/\n  api/\npackages/\n  ui/\n  config/\n  database/"),
}

PROJECT_TYPE_DEFAULT_FRAMEWORK = {
    "docs": "astro",
    "landing": "astro",
    "portfolio": "astro",
    "api": "fastapi",
    "erp_crm": "monorepo",
    "marketplace": "monorepo",
}


def arch_design_folder_structure(args):
    ctx = _normalize_ctx(args)
    if not ctx["name"]:
        _err("arch.design.folder_structure requires 'name' (or detectable from text)")
    framework = _opt_choice(args, "framework", tuple(FRAMEWORK_TREES), default=None)
    source = "argument"
    if framework is None:
        if ctx["project_type"] == "api" or "api_only" in _effective_features(ctx):
            framework = "fastapi"
        elif ctx["project_type"] in PROJECT_TYPE_DEFAULT_FRAMEWORK:
            framework = PROJECT_TYPE_DEFAULT_FRAMEWORK[ctx["project_type"]]
        else:
            framework = "next"
        source = f"default for project_type={ctx['project_type'] or 'unspecified'}"
    label, tree = FRAMEWORK_TREES[framework]
    doc = _md_document(
        f"{ctx['name']}: folder structure (planning)",
        [
            ("Framework template", [f"{label} (selected via {source})"]),
            ("Tree", ["```text", tree, "```"]),
            ("Notes", [
                "- keep business logic out of route handlers",
                "- tests live beside or above the code they cover",
                "- environment-specific values stay in env files, never committed with secrets",
            ]),
        ],
    )
    return _result(
        "arch.design.folder_structure",
        "artifact_generation",
        {"framework": framework, "framework_label": label, "selection_source": source},
        writes=[_write("docs/project-structure.md", doc)],
        limitations=["Planning template only; no directories were created."],
    )


def _mermaid_dataflow(ctx):
    dyn = _dynamic_features(ctx)
    lines = ["flowchart TD", '  U["User"] --> FE["Frontend"]', '  FE --> API["API layer"]', '  API --> VAL["Validation + authz"]', '  VAL --> SVC["Domain services"]', '  SVC --> DB[("Database")]']
    if dyn["needs_realtime"] or dyn["needs_queue"]:
        lines += ['  SVC --> CACHE[("Cache/queue")]', '  CACHE --> WK["Worker"]']
    if dyn["needs_payments"]:
        lines += ['  SVC --> PAY["Payment gateway"]', '  PAY -- webhook (signature verified) --> API']
    if dyn["needs_upload"]:
        lines += ['  SVC --> STORE["Object storage"]']
    return "\n".join(lines)


def arch_design_data_flow(args):
    ctx = _normalize_ctx(args)
    if not ctx["name"]:
        _err("arch.design.data_flow requires 'name' (or detectable from text)")
    dyn = _dynamic_features(ctx)
    steps = [
        "browser submits input to the frontend",
        "frontend calls the API layer over HTTPS",
        "validation and authorization run before domain logic",
        "domain services read/write the primary database",
    ]
    if dyn["needs_payments"]:
        steps += ["payment intents go to the gateway; webhooks re-enter through signature-verified endpoints"]
    if dyn["needs_realtime"] or dyn["needs_queue"]:
        steps += ["background work flows through the cache/queue to workers"]
    if dyn["needs_upload"]:
        steps += ["media goes to object storage; only keys are stored in the database"]
    doc = _md_document(
        f"{ctx['name']}: data flow (planning)",
        [
            ("Flow steps", [f"{i}. {s}" for i, s in enumerate(steps, 1)]),
            ("Diagram", ["```mermaid", _mermaid_dataflow(ctx), "```"]),
        ],
    )
    return _result(
        "arch.design.data_flow",
        "artifact_generation",
        {"steps": steps, "signals": dyn},
        writes=[_write("docs/architecture/data-flow.md", doc)],
        limitations=["Planning diagram only; no flows were executed."],
    )


def _mermaid_auth_sequence(strategy):
    lines = [
        "sequenceDiagram",
        "  participant U as User",
        "  participant W as Web app",
        "  participant A as Auth",
        "  participant D as Database",
        "  U->>W: submit credentials",
        "  W->>A: verify credentials",
        "  A->>D: lookup user (hash check)",
        "  D-->>A: user record",
    ]
    if strategy == "jwt":
        lines += ["  A-->>W: access token + refresh token (rotated)", "  W-->>U: signed-in state (refresh stored httpOnly/secure)"]
    else:
        lines += ["  A-->>W: session id", "  W-->>U: httpOnly session cookie"]
    lines += [
        "  U->>W: request protected resource",
        "  W->>A: validate session/token",
        "  A-->>W: allow",
        "  W-->>U: resource",
        "  U->>W: logout",
        "  W->>A: revoke session/refresh",
    ]
    return "\n".join(lines)


def arch_design_auth_flow(args):
    ctx = _normalize_ctx(args)
    if not ctx["name"]:
        _err("arch.design.auth_flow requires 'name' (or detectable from text)")
    strategy = _opt_choice(args, "strategy", ("session_cookie", "jwt", "oauth"), default=None)
    if strategy is None:
        strategy = "jwt" if (ctx["project_type"] == "api" or "api_only" in _effective_features(ctx)) else "session_cookie"
        source = f"default for project_type={ctx['project_type'] or 'unspecified'}"
    else:
        source = "argument"
    lifetimes = {
        "session_cookie": [("session cookie", "httpOnly, Secure, SameSite=Lax; server-side TTL, sliding renewal"), ("revocation", "delete server session")],
        "jwt": [("access token", "short-lived (minutes); kept in memory"), ("refresh token", "rotated on use; httpOnly cookie; revokeable server-side")],
        "oauth": [("authorization code + PKCE", "standard browser flow"), ("tokens", "same storage rules as the chosen local strategy")],
    }[strategy]
    steps = [
        "register: validate input, hash password, create user",
        "login: constant-time hash verification, rate limited",
        "session/token issuance per strategy",
        "protected requests validated server-side on every call",
        "logout revokes server state; client state cleared",
    ]
    if strategy == "oauth":
        steps.insert(1, "oauth: redirect to provider, verify state + code, upsert local user")
    doc = _md_document(
        f"{ctx['name']}: auth flow (planning)",
        [
            ("Strategy", [f"{strategy} (selected via {source})"]),
            ("Token/cookie policy", [f"- {k}: {v}" for k, v in lifetimes]),
            ("Steps", [f"{i}. {s}" for i, s in enumerate(steps, 1)]),
            ("Sequence diagram", ["```mermaid", _mermaid_auth_sequence(strategy), "```"]),
            ("Invariants", [
                "- credentials and secrets never appear in URLs or logs",
                "- authorization is checked server-side per request, never from client state",
                "- login/register endpoints are rate limited",
            ]),
        ],
    )
    return _result(
        "arch.design.auth_flow",
        "artifact_generation",
        {"strategy": strategy, "selection_source": source, "token_policy": dict(lifetimes)},
        writes=[_write("docs/architecture/auth-flow.md", doc)],
        limitations=["Design only; no auth was implemented or tested."],
    )


def _mermaid_deployment(hosting):
    shape = "VPS" if "VPS" in hosting else ("static" if "static" in hosting else "managed")
    if shape == "static":
        return "\n".join([
            "flowchart LR",
            '  U["User"] --> CDN["CDN + TLS"]',
            '  CDN --> STATIC["Static build output"]',
        ])
    if shape == "managed":
        return "\n".join([
            "flowchart LR",
            '  U["User"] --> EDGE["Platform edge (TLS)"]',
            '  EDGE --> APP["Managed app runtime"]',
            '  APP --> DB[("Managed database")]',
        ])
    return "\n".join([
        "flowchart LR",
        '  U["User"] --> DNS["DNS"]',
        '  DNS --> NGINX["Reverse proxy (TLS)"]',
        '  NGINX --> APP["App container(s)"]',
        '  APP --> DB[("Database container/volume")]',
        '  APP -.-> CACHE[("Cache container")]',
    ])


def arch_design_deployment(args):
    ctx = _normalize_ctx(args)
    if not ctx["name"]:
        _err("arch.design.deployment requires 'name' (or detectable from text)")
    hosting_arg = _opt_str(args, "hosting")
    if hosting_arg:
        hosting = hosting_arg
        source = "argument"
    else:
        hosting, reasons, _alts, _trigs = _hosting_choice(ctx)
        source = "derived from " + "; ".join(reasons)
    doc = _md_document(
        f"{ctx['name']}: deployment architecture (planning)",
        [
            ("Target", [hosting, f"source: {source}"]),
            ("Environments", ["staging (deploy target for every change)", "production (promoted after staging checks pass and the user approves)"]),
            ("Rollout steps", [
                "1. build artifact from a tagged revision",
                "2. deploy to staging; run smoke checks",
                "3. apply database migrations with a prior backup",
                "4. promote to production; keep the previous revision for instant rollback",
                "5. verify health checks; rollback on failure",
            ]),
            ("Diagram", ["```mermaid", _mermaid_deployment(hosting), "```"]),
        ],
    )
    return _result(
        "arch.design.deployment",
        "artifact_generation",
        {"hosting": hosting, "selection_source": source, "environments": ["staging", "production"]},
        writes=[_write("docs/architecture/deployment.md", doc)],
        limitations=["Planning document only; nothing was deployed and no server was touched."],
    )


SCALING_STAGES = [
    {"stage": 0, "trigger": "launch baseline", "action": "single app instance + primary database; measure before optimizing"},
    {"stage": 1, "trigger": "p95 read latency or DB load rises", "action": "add cache layer for hot reads and sessions"},
    {"stage": 2, "trigger": "read/write ratio keeps growing", "action": "add read replica(s); route reads explicitly"},
    {"stage": 3, "trigger": "background work delays responses", "action": "move notifications/emails/reconciliation into queue workers"},
    {"stage": 4, "trigger": "sustained CPU/traffic growth", "action": "run multiple app instances behind a load balancer; make the app stateless"},
    {"stage": 5, "trigger": "media bandwidth dominates", "action": "serve media via CDN/object storage signed URLs"},
    {"stage": 6, "trigger": "search latency or facet complexity grows", "action": "introduce a dedicated search engine synced from the source of truth"},
]


def arch_design_scaling(args):
    ctx = _normalize_ctx(args)
    if not ctx["name"]:
        _err("arch.design.scaling requires 'name' (or detectable from text)")
    scale = ctx["scale"]
    relevant = [s for s in SCALING_STAGES if s["stage"] == 0] + [s for s in SCALING_STAGES[1:] if scale != "small" or s["stage"] <= 2]
    doc = _md_document(
        f"{ctx['name']}: scaling strategy (planning)",
        [
            ("Principle", ["measure first; each stage has an explicit trigger metric and a reversible action", f"current declared scale: {scale}"]),
            ("Stages", [f"- Stage {s['stage']} \u2014 trigger: {s['trigger']} \u2192 action: {s['action']}" for s in relevant]),
            ("Guardrails", [
                "- no stage is applied automatically; each needs measurement and user approval",
                "- keep every action rollback-ready",
            ]),
        ],
    )
    return _result(
        "arch.design.scaling",
        "artifact_generation",
        {"stages": relevant, "declared_scale": scale},
        writes=[_write("docs/architecture/scaling.md", doc)],
        limitations=["Strategy only; no infrastructure was changed and no metrics were collected."],
    )


def arch_design_security(args):
    ctx = _normalize_ctx(args)
    if not ctx["name"]:
        _err("arch.design.security requires 'name' (or detectable from text)")
    dyn = _dynamic_features(ctx)
    eff = _effective_features(ctx)
    items = [
        ("Transport", "HTTPS everywhere; HSTS enabled at the edge"),
        ("Input", "validate and bound-check every input server-side; parameterized queries only"),
        ("Secrets", "env/vault only; never in repo, logs, or client bundles"),
        ("Headers", "Content-Security-Policy, X-Content-Type-Options, Referrer-Policy, frame ancestors deny"),
        ("Dependencies", "manual, verified updates with rollback; no auto-update chain"),
        ("Backups", "scheduled, restore-tested; kept out of web-accessible storage"),
    ]
    if dyn["needs_auth"]:
        items += [("AuthN", "salted password hashes; rate-limited login; server-side session/token validation"), ("AuthZ", "role checks enforced server-side; admin surfaces isolated"), ("Audit", "log auth events and admin mutations without secrets")]
    if dyn["needs_payments"]:
        items += [("Payments", "webhook signature verification; server-side amounts; idempotency keys; PCI scope reduced by using hosted provider flows")]
    if dyn["needs_upload"]:
        items += [("Uploads", "MIME/size allowlists; randomized storage keys; serve via signed URLs; never executable locations")]
    if dyn["needs_realtime"]:
        items += [("Realtime", "authenticate the socket handshake; authorize per channel; bound message size/rate")]
    doc = _md_document(
        f"{ctx['name']}: security architecture (planning)",
        [
            ("Checklist", [f"- **{area}**: {item}" for area, item in items]),
            ("Applies because", [f"features considered: {', '.join(eff) or 'none parsed'}"]),
        ],
    )
    return _result(
        "arch.design.security",
        "artifact_generation",
        {"checklist": [{"area": a, "item": i} for a, i in items], "features": eff},
        writes=[_write("docs/architecture/security.md", doc)],
        limitations=["Planning checklist only; no security tooling was installed or audited."],
    )


def arch_diagram_generate(args):
    ctx = _normalize_ctx(args, require_name=True)
    kind = _opt_choice(args, "kind", ("system", "data_flow", "er", "auth_sequence", "deployment", "component"), None)
    if kind is None:
        _err("arch.diagram.generate requires 'kind' one of: system, data_flow, er, auth_sequence, deployment, component")
    fmt = _opt_choice(args, "format", ("mermaid", "plantuml"), default="mermaid")
    dyn = _dynamic_features(ctx) if ctx["text"] or ctx["features"] or ctx["project_type"] else _dynamic_features({"features": ["auth"], "excluded_features": [], "project_type": "landing", "constraints": {}, "scale": "small", "preferences": {}})
    if kind == "system":
        text = _mermaid_system(ctx, dyn)
        title = "system"
    elif kind == "data_flow":
        text = _mermaid_dataflow(ctx)
        title = "data-flow"
    elif kind == "er":
        ctx["entities_arg"] = _opt_list(args, "entities", item_type=dict)
        tables, order, _src = _resolve_entities(ctx)
        relations = _fk_relations(tables, order)
        text = _render_er_mermaid(tables, order, relations)
        title = "er"
    elif kind == "auth_sequence":
        text = _mermaid_auth_sequence(_opt_choice(args, "strategy", ("session_cookie", "jwt", "oauth"), default="jwt"))
        title = "auth-sequence"
    elif kind == "deployment":
        text = _mermaid_deployment(_opt_str(args, "hosting") or "VPS (Docker)")
        title = "deployment"
    else:
        text = "\n".join(["flowchart TD", '  APP["AppShell"] --> NAV["NavBar"]', '  APP --> HOME["HomePage"]'])
        title = "components"
    ext = "mmd" if fmt == "mermaid" else "puml"
    if fmt == "plantuml":
        text = _to_plantuml(kind, text, title)
    slug = _slug(ctx["name"], "diagram")
    path = f"diagrams/{slug}-{title}.{ext}"
    wrapped = text if fmt == "mermaid" else text
    return _result(
        "arch.diagram.generate",
        "artifact_generation",
        {"kind": kind, "format": fmt, "rendered": wrapped, "artifact_path": path},
        writes=[_write(path, wrapped + "\n")],
        limitations=["Diagram source only; rendering to image happens in the caller's tooling."],
    )


def _to_plantuml(kind, mermaid_text, title):
    out = ["@startuml", f"title {title}"]
    if kind == "er":
        out += ["hide circle", "skinparam linetype ortho"]
        entity = None
        for line in mermaid_text.splitlines():
            line = line.strip()
            if line.startswith("erDiagram"):
                continue
            if line and not line.startswith(" ") and line.endswith("{"):
                entity = line[:-1].strip()
                out.append(f'entity "{entity}" as {entity} {{')
            elif line == "}" and entity:
                out.append("}")
                entity = None
            elif entity and line.strip():
                parts = line.split()
                if len(parts) >= 2:
                    out.append(f"  {parts[1]} : {parts[0]}")
        for line in mermaid_text.splitlines():
            m = re.match(r"\s*(\w+)\s*\|\|--o\{\s*(\w+)\s*:", line)
            if m:
                out.append(f'{m.group(1)} ||--o{{ {m.group(2)}')
    elif "sequenceDiagram" in mermaid_text:
        for line in mermaid_text.splitlines():
            line = line.strip()
            if line.startswith("participant "):
                parts = line.split()
                out.append(f'participant "{parts[2]}" as {parts[1]}')
            elif "->>" in line or "-->>" in line:
                out.append(line)
    else:
        for line in mermaid_text.splitlines():
            m = re.match(r'\s*(\w+)(?:\["([^"]+)"\]|\[\("([^"]+)"\)\])?\s*-->(.*)$', line)
            if not m:
                continue
            src, slabel, sdb, rest = m.group(1), m.group(2), m.group(3), m.group(4)
            srclabel = slabel or sdb or src
            shape = "database" if sdb else "rectangle"
            out.append(f'{shape} "{srclabel}" as {src}')
            rm = re.match(r'\s*(\w+)(?:\["([^"]+)"\]|\[\("([^"]+)"\)\])?\s*(.*)$', rest)
            if rm:
                tgt, tlabel, tdb = rm.group(1), rm.group(2), rm.group(3)
                tgtshape = "database" if tdb else "rectangle"
                out.append(f'{tgtshape} "{tlabel or tgt}" as {tgt}')
                out.append(f"{src} --> {tgt}")
    out.append("@enduml")
    return "\n".join(out)


def arch_document_generate(args):
    title = _need_str(args, "title")
    context = _need_str(args, "context")
    decision = _need_str(args, "decision")
    options = _opt_list(args, "options") or []
    consequences = _opt_str(args, "consequences")
    number = _opt_int(args, "number", 1, minimum=1, maximum=9999)
    status = _opt_choice(args, "status", ("proposed", "accepted", "superseded"), default="proposed")
    slug = _slug(title, "decision")
    lines = [
        f"# ADR-{number:04d}: {title}",
        "",
        f"Status: {status}",
        "",
        "## Context",
        "",
        context,
        "",
        "## Options considered",
        "",
    ]
    if options:
        lines += [f"- {o}" for o in options]
    else:
        lines.append("- (record the alternatives that were evaluated)")
    lines += [
        "",
        "## Decision",
        "",
        decision,
        "",
        "## Consequences",
        "",
        consequences or "- (record the resulting trade-offs and follow-ups)",
        "",
        "## Notes",
        "",
        "- This ADR records a planning decision; it does not assert that the decision was implemented.",
        "",
    ]
    doc = "\n".join(lines)
    path = f"docs/adr/ADR-{number:04d}-{slug}.md"
    return _result(
        "arch.document.generate",
        "artifact_generation",
        {"adr_number": number, "title": title, "status": status, "artifact_path": path},
        writes=[_write(path, doc)],
        limitations=["ADR text only; decisions recorded here are not implemented by this module."],
    )




def design_color_palette(args):
    color = _need_str(args, "color")
    brand = _opt_str(args, "name")
    palette = _build_palette(_rgb_to_hex(_parse_color_input(color)))
    payload = {
        "brand": brand,
        "palette": palette,
        "usage": {
            "primary_600": "buttons and key accents",
            "primary_50": "subtle backgrounds",
            "neutral_scale": "text, borders, surfaces",
            "semantic": "status colors; keep them consistent app-wide",
        },
        "method": palette["method"],
    }
    doc = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    return _result(
        "design.color.palette",
        "artifact_generation",
        payload,
        writes=[_write("design/tokens.colors.json", doc)],
        limitations=["Contrast values are computed (WCAG relative luminance); full design audit still needs human review."],
    )


TYPOGRAPHY_PAIRS = {
    "modern": {"headings": "Inter", "body": "Inter", "note": "single modern sans; add Source Serif 4 for long-form if needed"},
    "editorial": {"headings": "Playfair Display", "body": "Source Sans 3", "note": "serif display + humanist sans body"},
    "playful": {"headings": "Nunito", "body": "Open Sans", "note": "rounded display + neutral body"},
    "enterprise": {"headings": "IBM Plex Sans", "body": "IBM Plex Sans", "note": "Plex Mono for data tables/code"},
    "minimal": {"headings": "system-ui", "body": "system-ui", "note": "zero-download stack"},
}


def design_typography(args):
    style = _opt_choice(args, "style", tuple(TYPOGRAPHY_PAIRS), None)
    if style is None:
        _err("design.typography requires 'style' one of: " + ", ".join(sorted(TYPOGRAPHY_PAIRS)))
    pair = TYPOGRAPHY_PAIRS[style]
    base = 16
    ratio = 1.25
    scale = {}
    for name, step in (("sm", -1), ("base", 0), ("lg", 1), ("xl", 2), ("2xl", 3), ("3xl", 4), ("4xl", 5)):
        scale[name] = f"{round(base * (ratio ** step), 2)}px"
    payload = {
        "style": style,
        "families": {
            "headings": f"'{pair['headings']}', system-ui, sans-serif",
            "body": f"'{pair['body']}', system-ui, sans-serif",
            "mono": "ui-monospace, SFMono-Regular, Menlo, monospace",
        },
        "note": pair["note"],
        "modular_scale": {"base": f"{base}px", "ratio": str(ratio), "sizes": scale},
        "font_loading": "self-host fonts or use system stacks; always pair webfonts with font-display: swap and a fallback",
    }
    doc = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    return _result(
        "design.typography",
        "artifact_generation",
        payload,
        writes=[_write("design/tokens.typography.json", doc)],
        limitations=["Font names are planning suggestions; verify licensing/self-hosting before shipping."],
    )


GRID_PRESETS = {
    "marketing": {"max_width": 1280, "columns": 12, "gutter": 24, "margin_mobile": 20, "margin_desktop": 64, "section_spacing": 96},
    "app": {"sidebar_width": 260, "columns": 12, "gutter": 16, "max_width": None, "section_spacing": 24},
    "dashboard": {"sidebar_width": 260, "columns": 12, "gutter": 24, "max_width": 1440, "section_spacing": 32, "card_padding": 24},
}


def design_layout_grid(args):
    preset = _opt_choice(args, "preset", tuple(GRID_PRESETS), None)
    if preset is None:
        _err("design.layout.grid requires 'preset' one of: " + ", ".join(sorted(GRID_PRESETS)))
    tokens = dict(GRID_PRESETS[preset])
    tokens["breakpoints"] = dict(BREAKPOINTS)
    css_lines = [":root {"]
    for key, val in tokens.items():
        if isinstance(val, dict):
            continue
        if val is not None:
            css_lines.append(f"  --{key.replace('_', '-')}: {val}px;")
    for key, val in BREAKPOINTS.items():
        css_lines.append(f"  --bp-{key}: {val}px;")
    css_lines.append("}")
    tokens["css"] = "\n".join(css_lines)
    tokens["method"] = "deterministic preset + standard breakpoints; override per design review"
    doc = json.dumps(tokens, indent=2, ensure_ascii=False) + "\n"
    return _result(
        "design.layout.grid",
        "artifact_generation",
        tokens,
        writes=[_write("design/tokens.grid.json", doc)],
        limitations=["Tokens only; visual verification happens in the browser layers."],
    )


ICON_SETS = {
    "lucide": {"license": "ISC", "note": "clean 24px stroke icons; first-class React/Vue packages"},
    "heroicons": {"license": "MIT", "note": "Tailwind-flavored solid/outline/mini variants"},
    "tabler": {"license": "MIT", "note": "very broad coverage, consistent stroke style"},
    "phosphor": {"license": "MIT", "note": "multiple weights incl. duotone; fits playful tones"},
    "feather": {"license": "MIT", "note": "small minimal set; fits minimal tones"},
}


def design_icon_set(args):
    framework = _need_str(args, "framework")
    tone = _opt_str(args, "tone")
    low = framework.lower()
    if tone in {"playful"}:
        choice = "phosphor"
        reasons = ["playful tone benefits from Phosphor's weight range (incl. duotone)"]
    elif tone in {"minimal"}:
        choice = "feather"
        reasons = ["minimal tone fits Feather's small, restrained set"]
    elif tone in {"enterprise", "corporate"}:
        choice = "heroicons"
        reasons = ["enterprise tone: Heroicons matches Tailwind-based enterprise UIs closely"]
    elif "vue" in low or "nuxt" in low:
        choice = "lucide"
        reasons = ["Lucide ships maintained Vue packages and a consistent 24px grid"]
    else:
        choice = "lucide"
        reasons = ["default: broad coverage, consistent style, tree-shakeable packages for React-based stacks"]
    payload = {
        "choice": choice,
        "license": ICON_SETS[choice]["license"],
        "reasons": reasons,
        "alternatives": [
            {"option": k, "license": v["license"], "note": v["note"]}
            for k, v in sorted(ICON_SETS.items())
            if k != choice
        ],
        "usage_rules": [
            "single icon set across the product; no mixing",
            "import per-icon to keep bundles small",
            "verify the license file in the installed package before shipping",
        ],
        "method": "deterministic tone/framework mapping",
    }
    return _result("design.icon.set", "analysis", payload, limitations=["Selection only; nothing was downloaded or installed."])


def design_illustration(args):
    name = _need_str(args, "name")
    style = _opt_choice(args, "style", ("blobs", "grid", "waves"), default="blobs")
    width = _opt_int(args, "width", 960, minimum=320, maximum=3840)
    height = _opt_int(args, "height", 540, minimum=240, maximum=2160)
    palette_colors = _opt_list(args, "palette") or [c for c in _build_palette("#2563EB")["primary"].values()]
    seed = _seed_int("illustration", name, style)
    rng = random.Random(seed)
    shapes = []
    count = 6
    for i in range(count):
        cx = rng.randint(width // 8, width - width // 8)
        cy = rng.randint(height // 8, height - height // 8)
        r = rng.randint(min(width, height) // 10, min(width, height) // 4)
        fill = palette_colors[(i * 3) % len(palette_colors)]
        opacity = round(rng.uniform(0.5, 0.9), 2)
        if style == "grid":
            shapes.append(f'<rect x="{cx - r}" y="{cy - r}" width="{2 * r}" height="{2 * r}" rx="24" fill="{fill}" opacity="{opacity}"/>')
        elif style == "waves":
            shapes.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{2 * r}" ry="{r // 2}" fill="{fill}" opacity="{opacity}"/>')
        else:
            shapes.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}" opacity="{opacity}"/>')
    svg = "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-label="{_esc_attr(name)} decorative illustration">',
            f'<rect width="{width}" height="{height}" fill="{palette_colors[0]}"/>',
            *shapes,
            "</svg>",
            "",
        ]
    )
    slug = _slug(name, "illustration")
    path = f"design/illustrations/{slug}.svg"
    return _result(
        "design.illustration",
        "artifact_generation",
        {"style": style, "seed": seed, "shape_count": count, "artifact_path": path, "method": "seeded deterministic layout (sha256 of name/style); regenerate deterministically with the same inputs"},
        writes=[_write(path, svg)],
        limitations=["Abstract generated art; review for taste/brand fit before shipping."],
    )


def _esc_attr(text):
    return _esc_html(text)


def _initials(name):
    words = [w for w in re.split(r"[^0-9A-Za-z\u00C0-\u024F]+", name) if w]
    if not words:
        return "H"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


def design_logo(args):
    name = _need_str(args, "name")
    style = _opt_choice(args, "style", ("monogram", "wordmark", "badge"), default="monogram")
    color = _opt_str(args, "color") or "#2563EB"
    primary = _build_palette(color)["primary"]["600"]
    on = _on_color(_hex_to_rgb(primary))
    initials = _initials(name)
    mark = "\n".join(
        [
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240" role="img" aria-label="' + _esc_attr(name) + ' logo mark">',
            f'<rect width="240" height="240" rx="48" fill="{primary}"/>',
            f'<text x="120" y="152" font-family="system-ui, sans-serif" font-size="104" font-weight="700" text-anchor="middle" fill="{on}">{_esc_text(initials)}</text>',
            "</svg>",
            "",
        ]
    )
    slug = _slug(name, "logo")
    writes = [_write(f"design/logo-mark.svg", mark)]
    if style == "wordmark":
        svg = "\n".join(
            [
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 160" role="img" aria-label="' + _esc_attr(name) + ' wordmark">',
                f'<rect width="160" height="160" rx="32" fill="{primary}"/>',
                f'<text x="80" y="102" font-family="system-ui, sans-serif" font-size="64" font-weight="700" text-anchor="middle" fill="{on}">{_esc_text(initials)}</text>',
                f'<text x="192" y="98" font-family="system-ui, sans-serif" font-size="56" font-weight="600" fill="#111827">{_esc_text(_truncate(name, 18))}</text>',
                "</svg>",
                "",
            ]
        )
        writes = [_write(f"design/logo-{slug}.svg", svg), _write("design/logo-mark.svg", mark)]
    elif style == "badge":
        svg = "\n".join(
            [
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 160" role="img" aria-label="' + _esc_attr(name) + ' badge">',
                f'<rect width="320" height="160" rx="80" fill="{primary}"/>',
                f'<text x="160" y="102" font-family="system-ui, sans-serif" font-size="64" font-weight="700" text-anchor="middle" fill="{on}">{_esc_text(initials)}</text>',
                "</svg>",
                "",
            ]
        )
        writes = [_write(f"design/logo-{slug}-badge.svg", svg), _write("design/logo-mark.svg", mark)]
    else:
        writes = [_write(f"design/logo-{slug}.svg", mark)]
    return _result(
        "design.logo",
        "artifact_generation",
        {"style": style, "initials": initials, "primary": primary, "on_primary": on, "artifacts": [w["path"] for w in writes], "method": "deterministic monogram/wordmark template; no external logo service called"},
        writes=writes,
        limitations=["Simple geometric mark; a professional brand pass may still be wanted."],
    )


def design_favicon(args):
    name = _need_str(args, "name")
    color = _opt_str(args, "color") or "#2563EB"
    palette = _build_palette(color)
    primary = palette["primary"]["600"]
    on = palette["contrast"]["on_primary"]
    initials = _initials(name)
    size = _opt_int(args, "size", 64, minimum=16, maximum=512)
    svg = "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">',
            f'<rect width="{size}" height="{size}" rx="{size // 5}" fill="{primary}"/>',
            f'<text x="{size // 2}" y="{round(size * 0.68)}" font-family="system-ui, sans-serif" font-size="{round(size * 0.52)}" font-weight="700" text-anchor="middle" fill="{on}">{_esc_text(initials)}</text>',
            "</svg>",
            "",
        ]
    )
    link_snippet = f'<link rel="icon" href="/favicon.svg" type="image/svg+xml">'
    return _result(
        "design.favicon",
        "artifact_generation",
        {"initials": initials, "primary": primary, "artifact_path": "design/favicon.svg", "link_tag": link_snippet},
        writes=[_write("design/favicon.svg", svg)],
        limitations=[
            "SVG favicon only: raster .ico/.png export needs an external rasterizer (stdlib cannot rasterize).",
            "Provide a PNG fallback for legacy browsers.",
        ],
    )


def _wrap_text(text, max_chars, max_lines):
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    if not lines:
        lines = [text[:max_chars]]
    return lines[:max_lines]


def design_og_image(args):
    title = _need_str(args, "title")
    subtitle = _opt_str(args, "subtitle")
    brand = _opt_str(args, "brand")
    color = _opt_str(args, "color") or "#2563EB"
    palette = _build_palette(color)
    c1, c2 = palette["primary"]["600"], palette["primary"]["800"]
    w, h = 1200, 630
    title_lines = _wrap_text(title, 26, 3)
    elements = [
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/></linearGradient></defs>',
        f'<rect width="{w}" height="{h}" fill="url(#g)"/>',
    ]
    y = 180
    for line in title_lines:
        elements.append(f'<text x="80" y="{y}" font-family="system-ui, sans-serif" font-size="64" font-weight="800" fill="#FFFFFF">{_esc_text(line)}</text>')
        y += 78
    if subtitle:
        elements.append(f'<text x="80" y="{y + 40}" font-family="system-ui, sans-serif" font-size="30" fill="#FFFFFF" opacity="0.85">{_esc_text(_truncate(subtitle, 90))}</text>')
    if brand:
        elements.append(f'<text x="80" y="{h - 70}" font-family="system-ui, sans-serif" font-size="28" font-weight="600" fill="#FFFFFF" opacity="0.9">{_esc_text(_truncate(brand, 40))}</text>')
    svg = "\n".join([f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">', *elements, "</svg>", ""])
    return _result(
        "design.og_image",
        "artifact_generation",
        {"artifact_path": "design/og-image.svg", "title_lines": title_lines, "dimensions": "1200x630"},
        writes=[_write("design/og-image.svg", svg)],
        limitations=[
            "SVG source only: most crawlers require PNG/JPG under ~300KB; rasterize externally before shipping.",
        ],
    )


def _theme_tokens(color, name, dark, radius):
    palette = _build_palette(color)
    p = palette["primary"]
    n = palette["neutral"]
    light = {
        "bg": n["50"],
        "surface": "#FFFFFF",
        "text": n["800"],
        "muted": n["500"],
        "border": n["200"],
        "primary": p["600"],
        "on_primary": palette["contrast"]["on_primary"],
    }
    tokens = {
        "brand": name,
        "radius": f"{radius}px",
        "light": light,
        "primary_ramp": p,
        "semantic": palette["semantic"],
        "method": palette["method"],
    }
    if dark:
        tokens["dark"] = {
            "bg": n["900"],
            "surface": n["800"],
            "text": n["100"],
            "muted": n["400"],
            "border": n["700"],
            "primary": p["400"],
            "on_primary": "#111111",
        }
    return tokens


def design_theme(args):
    color = _need_str(args, "color")
    name = _opt_str(args, "name") or "App"
    dark = _opt_bool(args, "dark_mode", True)
    radius = _opt_int(args, "radius", 12, minimum=0, maximum=32)
    tokens = _theme_tokens(color, name, dark, radius)
    css = [":root {"]
    for key, val in tokens["light"].items():
        css.append(f"  --color-{key.replace('_', '-')}: {val};")
    css += [f"  --radius: {tokens['radius']};", "}"]
    if dark:
        css += ["", '[data-theme="dark"] {']
        for key, val in tokens["dark"].items():
            css.append(f"  --color-{key.replace('_', '-')}: {val};")
        css.append("}")
    css += [
        "",
        "body { background: var(--color-bg); color: var(--color-text); font-family: system-ui, sans-serif; }",
        "",
    ]
    doc = "\n".join(css)
    payload = {"tokens": {k: v for k, v in tokens.items() if k != "primary_ramp"}, "css_variables": css}
    return _result(
        "design.theme",
        "artifact_generation",
        payload,
        writes=[_write("styles/theme.css", doc), _write("design/tokens.theme.json", json.dumps(tokens, indent=2, ensure_ascii=False) + "\n")],
        limitations=["Token-level theme; component styling and visual QA happen in the builder/browser layers."],
    )


SECTION_LABELS = {
    "catalog": ("Products", "Produk"),
    "search": ("Find it fast", "Temukan cepat"),
    "auth": ("Your account", "Akun Anda"),
    "admin": ("Back office", "Back office"),
    "blog": ("Latest updates", "Update terbaru"),
    "booking": ("Book a slot", "Pesan jadwal"),
    "orders": ("Track orders", "Lacak pesanan"),
    "reviews": ("What customers say", "Kata pelanggan"),
    "analytics": ("Insights", "Analitik"),
    "forum": ("Community", "Komunitas"),
    "chat": ("Chat", "Chat"),
    "notifications": ("Stay informed", "Tetap terinformasi"),
}


def design_prototype(args):
    name = _need_str(args, "name")
    ctx2 = {"features": _opt_list(args, "features") or [], "excluded_features": [], "project_type": _opt_choice(args, "project_type", PROJECT_TYPE_KEYWORDS.keys()), "text": _opt_str(args, "text")}
    features = ctx2["features"]
    language = _opt_choice(args, "language", ("en", "id"), default="en")
    color = _opt_str(args, "color") or "#2563EB"
    tokens = _theme_tokens(color, name, True, 12)
    L = tokens["light"]
    D = tokens.get("dark", L)
    sections = []
    nav = [f'<a href="#hero">{"Home" if language == "en" else "Beranda"}</a>']
    for feature in features:
        label = SECTION_LABELS.get(feature, (feature.replace("_", " ").title(),) * 2)[0 if language == "en" else 1]
        sid = _slug(feature, feature)
        nav.append(f'<a href="#{sid}">{_esc_html(label)}</a>')
        cards = "".join(
            f'<div class="card"><div class="card-media"></div><div class="card-body"><div class="card-line"></div><div class="card-line short"></div></div></div>'
            for _ in range(3)
        )
        sections.append(
            f'<section id="{sid}"><h2>{_esc_html(label)}</h2><p class="placeholder-note">[Placeholder \u2014 replace with real {language} copy]</p><div class="cards">{cards}</div></section>'
        )
    hero_tag = "Planning prototype \u2014 static wireframe" if language == "en" else "Prototipe perencanaan \u2014 wireframe statis"
    html_doc = "\n".join(
        [
            "<!DOCTYPE html>",
            '<html lang="' + language + '">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{_esc_html(name)} \u2014 prototype</title>",
            "<style>",
            ":root { --bg: %s; --surface: %s; --text: %s; --muted: %s; --border: %s; --primary: %s; --on-primary: %s; --radius: %s; }"
            % (L["bg"], L["surface"], L["text"], L["muted"], L["border"], L["primary"], L["on_primary"], tokens["radius"]),
            '[data-theme="dark"] { --bg: %s; --surface: %s; --text: %s; --muted: %s; --border: %s; --primary: %s; --on-primary: %s; }'
            % (D["bg"], D["surface"], D["text"], D["muted"], D["border"], D["primary"], D["on_primary"]),
            "* { box-sizing: border-box; margin: 0; }",
            "body { background: var(--bg); color: var(--text); font-family: system-ui, sans-serif; line-height: 1.5; }",
            "header { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; padding: 16px 24px; background: var(--surface); border-bottom: 1px solid var(--border); position: sticky; top: 0; }",
            "header .brand { font-weight: 700; margin-right: auto; }",
            "header a { color: var(--text); text-decoration: none; padding: 6px 10px; border-radius: var(--radius); }",
            "header a:hover { background: var(--bg); }",
            "#hero { padding: 96px 24px; background: linear-gradient(135deg, var(--primary), var(--surface)); color: var(--on-primary); }",
            "#hero h1 { font-size: 40px; max-width: 640px; }",
            "#hero p { margin-top: 12px; opacity: 0.9; }",
            "section { padding: 48px 24px; max-width: 1100px; margin: 0 auto; }",
            "section h2 { margin-bottom: 8px; }",
            ".placeholder-note { color: var(--muted); font-style: italic; margin-bottom: 20px; }",
            ".cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; }",
            ".card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }",
            ".card-media { height: 120px; background: var(--border); }",
            ".card-body { padding: 12px; }",
            ".card-line { height: 10px; background: var(--border); border-radius: 6px; }",
            ".card-line.short { width: 60%; margin-top: 8px; }",
            "footer { padding: 32px 24px; color: var(--muted); border-top: 1px solid var(--border); }",
            "</style>",
            "</head>",
            "<body>",
            "<header>",
            f'<span class="brand">{_esc_html(name)}</span>',
            *nav,
            "</header>",
            '<section id="hero">',
            f"<h1>{_esc_html(name)}</h1>",
            f"<p>[Placeholder headline] {hero_tag}.</p>",
            "</section>",
            *sections,
            f"<footer>[Placeholder] \u00a9 {_esc_html(name)}</footer>",
            "</body>",
            "</html>",
            "",
        ]
    )
    slug = _slug(name, "prototype")
    path = f"prototypes/{slug}-prototype.html"
    return _result(
        "design.prototype",
        "artifact_generation",
        {"artifact_path": path, "sections": [f for f in features], "language": language, "clickable": "in-page anchor navigation between sections"},
        writes=[_write(path, html_doc)],
        limitations=["Static wireframe with placeholder content; not a working application."],
    )




CONTENT_PACKS = {
    "landing": {
        "en": [("hero", "Headline", "Subheadline", "Primary CTA"), ("features", "Feature list", "Three to five key benefits", None), ("about", "About", "Who you are and why you exist", None), ("contact", "Contact", "How to reach you", None), ("faq", "FAQ", "Common questions", None)],
        "id": [("hero", "Judul utama", "Subjudul", "CTA utama"), ("features", "Daftar fitur", "Tiga sampai lima manfaat utama", None), ("about", "Tentang", "Siapa Anda dan mengapa ada", None), ("contact", "Kontak", "Cara menghubungi", None), ("faq", "FAQ", "Pertanyaan umum", None)],
    },
    "ecommerce": {
        "en": [("hero", "Store headline", "What you sell and for whom", "Shop now"), ("featured_categories", "Featured categories", "Top category cards", None), ("product_card", "Product card fields", "image, name, price, rating, CTA", None), ("trust", "Trust badges", "shipping, returns, security", None), ("shipping_info", "Shipping info", "areas, costs, etas (fill real values)", None), ("faq", "FAQ", "orders, payments, returns", None)],
        "id": [("hero", "Judul toko", "Apa yang dijual dan untuk siapa", "Belanja sekarang"), ("featured_categories", "Kategori unggulan", "Kartu kategori teratas", None), ("product_card", "Isi kartu produk", "gambar, nama, harga, rating, CTA", None), ("trust", "Lencana kepercayaan", "pengiriman, retur, keamanan", None), ("shipping_info", "Info pengiriman", "area, biaya, estimasi (isi nilai nyata)", None), ("faq", "FAQ", "pesanan, pembayaran, retur", None)],
    },
    "blog": {
        "en": [("masthead", "Masthead", "Blog name and promise", None), ("featured_post", "Featured post", "Editor's pick", None), ("categories", "Categories", "Topic list", None), ("about", "About", "Who writes this", None)],
        "id": [("masthead", "Masthead", "Nama dan janji blog", None), ("featured_post", "Artikel unggulan", "Pilihan editor", None), ("categories", "Kategori", "Daftar topik", None), ("about", "Tentang", "Siapa penulisnya", None)],
    },
    "dashboard": {
        "en": [("welcome", "Welcome block", "Signed-in user context", None), ("kpi_labels", "KPI labels", "Define 4-6 real metrics", None), ("empty_state", "Empty states", "Guidance when no data yet", None)],
        "id": [("welcome", "Blok sambutan", "Konteks pengguna terlogin", None), ("kpi_labels", "Label KPI", "Tentukan 4-6 metrik nyata", None), ("empty_state", "Keadaan kosong", "Panduan saat data belum ada", None)],
    },
    "saas": {
        "en": [("hero", "Headline", "Problem you solve", "Start free"), ("features", "Features", "Capability blocks", None), ("pricing", "Pricing tiers", "Tier names and limits \u2014 [PRICE PLACEHOLDER]", None), ("testimonial", "Testimonials", "Real quotes only \u2014 placeholder until collected", None), ("faq", "FAQ", "billing, security, onboarding", None)],
        "id": [("hero", "Judul utama", "Masalah yang Anda selesaikan", "Coba gratis"), ("features", "Fitur", "Blok kemampuan", None), ("pricing", "Tier harga", "Nama tier dan batas \u2014 [HANYA PLACEHOLDER HARGA]", None), ("testimonial", "Testimoni", "Hanya kutipan nyata \u2014 placeholder sampai dikumpulkan", None), ("faq", "FAQ", "penagihan, keamanan, onboarding", None)],
    },
}


def content_gen_placeholder(args):
    name = _need_str(args, "name")
    project_type = _opt_choice(args, "project_type", PROJECT_TYPE_KEYWORDS.keys())
    language = _opt_choice(args, "language", ("en", "id"), default="en")
    if project_type is None:
        project_type = "landing"
    pack = CONTENT_PACKS.get(project_type, CONTENT_PACKS["landing"])[language]
    pages = {}
    for sid, heading, body, cta in pack:
        pages[sid] = {
            "heading": heading,
            "body": f"[Placeholder \u2014 {body}]",
            "cta": cta,
        }
    doc_lines = [f"# Placeholder content: {name} ({project_type}, {language})", "", "All strings below are explicitly placeholders; replace before publishing.", ""]
    for sid, heading, body, cta in pack:
        doc_lines += [f"## {heading}", "", f"- section id: {sid}", f"- body: [Placeholder \u2014 {body}]"]
        if cta:
            doc_lines.append(f"- CTA: {cta}")
        doc_lines.append("")
    doc = "\n".join(doc_lines)
    return _result(
        "content.gen.placeholder",
        "artifact_generation",
        {"project_type": project_type, "language": language, "pages": pages, "policy": "structure from project-type templates; every value is an explicit placeholder (no invented facts, prices, or quotes)"},
        writes=[_write("content/placeholder-content.md", doc)],
        limitations=["Placeholder copy only; real copy must come from the user."],
    )


COPY_TEMPLATES = {
    "en": {
        "headlines": ["{name}: {kw} done right", "Everything you need for {kw}, in one place", "{name} helps you get {kw} done faster"],
        "taglines": ["{kw}, simplified.", "Built around your {kw} workflow.", "{name} \u2014 {kw} that stays out of your way."],
    },
    "id": {
        "headlines": ["{name}: {kw} dengan cara yang benar", "Semua yang Anda butuhkan untuk {kw}, dalam satu tempat", "{name} membantu {kw} selesai lebih cepat"],
        "taglines": ["{kw}, dibuat sederhana.", "Dirancang untuk alur kerja {kw} Anda.", "{name} \u2014 {kw} tanpa ribet."],
    },
}

KEYWORD_DEFAULTS = {
    "ecommerce": ("online shopping", "belanja online"),
    "saas": ("your workflow", "alur kerja Anda"),
    "blog": ("reading", "membaca"),
    "dashboard": ("daily operations", "operasional harian"),
    "booking": ("scheduling", "penjadwalan"),
    "landing": ("your business", "bisnis Anda"),
}


def content_gen_copy(args):
    name = _need_str(args, "name")
    language = _opt_choice(args, "language", ("en", "id"), default="en")
    tone = _opt_str(args, "tone")
    keywords = _opt_list(args, "keywords")
    project_type = _opt_choice(args, "project_type", PROJECT_TYPE_KEYWORDS.keys())
    if keywords:
        kw = keywords[0]
    else:
        kw = KEYWORD_DEFAULTS.get(project_type, ("your goals", "tujuan Anda"))[0 if language == "en" else 1]
    tpl = COPY_TEMPLATES[language]
    headlines = [f"{t.format(name=name, kw=kw)}" for t in tpl["headlines"]]
    taglines = [t.format(name=name, kw=kw) for t in tpl["taglines"]]
    payload = {
        "language": language,
        "tone_note": tone or "not specified; templates are tone-neutral",
        "headlines": [{"variant": f"H{i}", "text": h} for i, h in enumerate(headlines, 1)],
        "taglines": [{"variant": f"T{i}", "text": t} for i, t in enumerate(taglines, 1)],
        "policy": "template-derived copy; requires human review and editing before publishing (no facts invented)",
    }
    lines = [f"# Copy draft: {name} ({language})", "", payload["policy"], "", "## Headlines", ""]
    lines += [f"- {v['variant']}: {v['text']}" for v in payload["headlines"]]
    lines += ["", "## Taglines", ""]
    lines += [f"- {v['variant']}: {v['text']}" for v in payload["taglines"]]
    lines.append("")
    return _result(
        "content.gen.copy",
        "artifact_generation",
        payload,
        writes=[_write("content/copy.md", "\n".join(lines))],
        limitations=["Template output, not creative direction; human review required."],
    )


def _validate_base_url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        _err(f"base_url/url must be an absolute http(s) URL, got: {url}")
    return url.rstrip("/")


def seo_gen_meta(args):
    name = _need_str(args, "name")
    url = _validate_base_url(_need_str(args, "url"))
    description = _opt_str(args, "description")
    language = _opt_choice(args, "language", ("en", "id"), default="en")
    color = _opt_str(args, "color")
    noindex = _opt_bool(args, "noindex", False)
    title = _truncate(f"{name}", 60)
    desc = _truncate(description or f"{name} \u2014 [placeholder description; replace with real copy]", 155)
    lines = [
        "<!-- generated meta tags (planning) -->",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_esc_html(title)}</title>",
        f'<meta name="description" content="{_esc_html(desc)}">',
    ]
    if noindex:
        lines.append('<meta name="robots" content="noindex, nofollow">')
    else:
        lines.append('<meta name="robots" content="index, follow">')
    lines.append(f'<link rel="canonical" href="{_esc_attr(url + "/")}">')
    if language:
        lines.append(f'<meta http-equiv="content-language" content="{_esc_attr(language)}">')
    if color:
        palette = _build_palette(color)
        lines.append(f'<meta name="theme-color" content="{_esc_attr(palette["primary"]["600"])}">')
    doc = "\n".join(lines) + "\n"
    return _result(
        "seo.gen.meta",
        "artifact_generation",
        {"title": title, "title_length": len(title), "description": desc, "description_length": len(desc), "canonical": url + "/", "language": language, "noindex": noindex},
        writes=[_write("partials/meta.html", doc)],
        limitations=["If description was not provided, a clearly marked placeholder is used; replace with real copy."],
    )


def _default_routes(ctx):
    routes = ["/", "/about", "/contact"]
    eff = set(_effective_features(ctx))
    if "catalog" in eff:
        routes += ["/products"]
    if "blog" in eff:
        routes += ["/blog"]
    if "booking" in eff:
        routes += ["/booking"]
    return routes


def seo_gen_sitemap(args):
    base_url = _validate_base_url(_need_str(args, "base_url"))
    ctx = {"features": _opt_list(args, "features") or [], "excluded_features": [], "text": _opt_str(args, "text")}
    routes_arg = _opt_list(args, "routes")
    routes = routes_arg if routes_arg is not None else _default_routes(ctx)
    seen = set()
    url_entries = []
    for route in routes:
        if not isinstance(route, str):
            _err("routes entries must be strings")
        path = route if route.startswith("/") else "/" + route
        if path in seen:
            continue
        seen.add(path)
        quoted = urllib.parse.quote(path, safe="/?=&")
        url_entries.append(f"  <url><loc>{_esc_text(base_url + quoted)}</loc></url>")
    xml = "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
            *url_entries,
            "</urlset>",
            "",
        ]
    )
    return _result(
        "seo.gen.sitemap",
        "artifact_generation",
        {"routes": sorted(seen), "route_count": len(seen), "base_url": base_url},
        writes=[_write("sitemap.xml", xml)],
        limitations=["Static routes only; add dynamic URLs (product/blog details) after they exist."],
    )


def seo_gen_robots(args):
    base_url = _validate_base_url(_need_str(args, "base_url"))
    ctx = {"features": _opt_list(args, "features") or [], "excluded_features": [], "text": _opt_str(args, "text")}
    extra = _opt_list(args, "disallow") or []
    disallow = set(extra)
    eff = set(_effective_features(ctx))
    if "admin" in eff:
        disallow.add("/admin/")
    if "dashboard" in eff:
        disallow.add("/dashboard/")
    if eff & {"auth", "cart", "checkout", "orders", "payments"}:
        disallow.add("/api/")
    lines = ["User-agent: *", "Allow: /"]
    for path in sorted(disallow):
        lines.append(f"Disallow: {path}")
    crawl_delay = _opt_int(args, "crawl_delay", 0, minimum=0, maximum=30)
    if crawl_delay:
        lines.append(f"Crawl-delay: {crawl_delay}")
    lines.append(f"Sitemap: {base_url}/sitemap.xml")
    doc = "\n".join(lines) + "\n"
    return _result(
        "seo.gen.robots",
        "artifact_generation",
        {"disallow": sorted(disallow), "sitemap": base_url + "/sitemap.xml"},
        writes=[_write("robots.txt", doc)],
        limitations=["Derived disallow rules only cover admin/dashboard/api patterns from features; review for hidden routes."],
    )


SCHEMA_TYPE_MAP = {
    "ecommerce": "OnlineStore",
    "marketplace": "OnlineStore",
    "blog": "Blog",
    "saas": "WebApplication",
    "dashboard": "WebApplication",
    "api": "WebApplication",
    "booking": "WebSite",
}


def seo_gen_schema(args):
    name = _need_str(args, "name")
    url = _validate_base_url(_need_str(args, "url"))
    project_type = _opt_choice(args, "project_type", PROJECT_TYPE_KEYWORDS.keys())
    description = _opt_str(args, "description")
    logo_url = _opt_str(args, "logo_url")
    same_as = _opt_list(args, "same_as")
    locale = _opt_str(args, "locale")
    schema_type = SCHEMA_TYPE_MAP.get(project_type, "WebSite")
    payload = {"@context": "https://schema.org", "@type": schema_type, "name": name, "url": url}
    missing = []
    if description:
        payload["description"] = description
    else:
        missing.append("description")
    if logo_url:
        payload["logo"] = logo_url
    else:
        missing.append("logo")
    if same_as:
        payload["sameAs"] = same_as
    if locale:
        payload["inLanguage"] = locale
    if project_type == "ecommerce":
        missing.append("address/geo/telephone (only add if you run this business)")
    html_doc = "\n".join(
        [
            "<!-- JSON-LD; only fields derived from provided inputs; fill missing facts yourself -->",
            '<script type="application/ld+json">',
            _jsonld_safe(payload),
            "</script>",
            "",
        ]
    )
    return _result(
        "seo.gen.schema",
        "artifact_generation",
        {"schema_type": schema_type, "payload": payload, "intentionally_omitted": missing, "policy": "no invented ratings, addresses, phone numbers, or company facts; add only verified real values"},
        writes=[_write("partials/schema-jsonld.html", html_doc)],
        limitations=["Structured data covers only provided fields; richer markup needs verified business data from the user."],
    )


def seo_gen_og(args):
    url = _validate_base_url(_need_str(args, "url"))
    title = _need_str(args, "title")
    description = _opt_str(args, "description")
    image_url = _opt_str(args, "image_url")
    site_name = _opt_str(args, "site_name")
    og_type = _opt_choice(args, "type", ("website", "article", "product"), default="website")
    twitter_handle = _opt_str(args, "twitter_handle")
    lines = [
        "<!-- generated Open Graph / Twitter tags (planning) -->",
        f'<meta property="og:title" content="{_esc_html(_truncate(title, 70))}">',
        f'<meta property="og:type" content="{_esc_attr(og_type)}">',
        f'<meta property="og:url" content="{_esc_attr(url)}">',
    ]
    if description:
        lines.append(f'<meta property="og:description" content="{_esc_html(_truncate(description, 200))}">')
    if site_name:
        lines.append(f'<meta property="og:site_name" content="{_esc_html(site_name)}">')
    if image_url:
        lines.append(f'<meta property="og:image" content="{_esc_attr(image_url)}">')
        lines.append('<meta name="twitter:card" content="summary_large_image">')
    else:
        lines.append('<meta name="twitter:card" content="summary">')
    if twitter_handle:
        lines.append(f'<meta name="twitter:site" content="{_esc_attr(twitter_handle)}">')
    doc = "\n".join(lines) + "\n"
    return _result(
        "seo.gen.og",
        "artifact_generation",
        {"og_type": og_type, "has_image": bool(image_url), "twitter_card": "summary_large_image" if image_url else "summary"},
        writes=[_write("partials/og.html", doc)],
        limitations=["Provide a real og:image (PNG/JPG) for rich previews; see design.og_image for a rasterization caveat."],
    )


def seo_optimize_performance(args):
    project_type = _opt_choice(args, "project_type", PROJECT_TYPE_KEYWORDS.keys(), default=None)
    if project_type is None:
        _err("seo.optimize.performance requires 'project_type'")
    ctx = {"features": _opt_list(args, "features") or [], "excluded_features": [], "constraints": _opt_dict(args, "constraints") or {}, "text": _opt_str(args, "text")}
    eff = set(_effective_features(ctx))
    items = [
        {"metric": "LCP", "item": "preload the hero image/asset and above-the-fold webfonts; use responsive srcset sizes", "applies": "every page has a largest element"},
        {"metric": "CLS", "item": "set explicit width/height (or aspect-ratio) on all media; reserve space for embeds and ads", "applies": "every page"},
        {"metric": "INP", "item": "split bundles, defer non-critical JS, keep event handlers cheap", "applies": "every page"},
    ]
    if "catalog" in eff:
        items += [
            {"metric": "LCP", "item": "paginate listings; never render an unbounded product grid", "applies": "catalog feature"},
            {"metric": "LCP", "item": "serve catalog images via resizing CDN/object-storage transforms", "applies": "catalog feature"},
        ]
    if "blog" in eff:
        items.append({"metric": "LCP", "item": "statically generate posts; font-display swap with sized fallbacks", "applies": "blog feature"})
    if "dashboard" in eff or project_type in {"dashboard", "erp_crm"}:
        items += [
            {"metric": "INP", "item": "virtualize long tables; lazy-load charts; debounce filters", "applies": "dashboard surfaces"},
        ]
    if ctx["constraints"].get("mobile_first") or "mobile_first" in str(ctx["constraints"]):
        items.append({"metric": "all", "item": "budget JS on mid-tier Android; test on throttled networks", "applies": "mobile-first constraint"})
    if "accessibility" in ctx["constraints"] or "seo" in ctx["constraints"]:
        items.append({"metric": "all", "item": "keep semantic headings, focus states, and contrast >= 4.5:1 for text", "applies": "accessibility/seo constraint"})
    doc = _md_document(
        "SEO performance plan (Core Web Vitals)",
        [
            ("Scope", [f"project_type={project_type}; features={', '.join(sorted(eff)) or 'none'}"]),
            ("Checklist", [f"- [{i['metric']}] {i['item']} ({i['applies']})" for i in items]),
            ("Honesty note", [
                "This is a static plan. Real LCP/CLS/INP numbers require lab and field measurement in a browser, which this planning module does not perform.",
            ]),
        ],
    )
    return _result(
        "seo.optimize.performance",
        "analysis",
        {"items": items, "project_type": project_type, "features": sorted(eff), "policy": "recommendations derived deterministically from project type, features, and constraints; no score claims"},
        writes=[_write("docs/seo/performance-plan.md", doc)],
        limitations=["No measurements were taken; run browser QA layers for actual CWV numbers."],
    )



_TEXT = {"type": "string", "minLength": 1}
_STR_LIST = {"type": "array", "items": {"type": "string"}, "minItems": 1}
_URL = {"type": "string", "minLength": 8}


def _sch(title, description, required, props):
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": title,
        "description": description,
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": props,
    }


COMMON_PROPS = {
    "name": {**_TEXT, "description": "Project or brand name"},
    "project_type": {"type": "string", "enum": sorted(PROJECT_TYPE_KEYWORDS.keys())},
    "features": {**_STR_LIST, "description": "Canonical or free-text feature list"},
    "excluded_features": {"type": "array", "items": {"type": "string"}, "description": "Features explicitly excluded by the spec (negations)"},
    "constraints": {"type": "object", "description": "Constraints dict, e.g. {mobile_first: true} or parsed constraints"},
    "scale": {"type": "string", "enum": list(SCALES)},
    "preferences": {"type": "object", "description": "Scoped deterministic preferences, e.g. {frontend: vue, language: python, self_host: true}"},
    "context": {"type": "object", "description": "Structured context from prior planning steps"},
    "text": {**_TEXT, "description": "Resolved spec text (root readers supply this)"},
    "language": {"type": "string", "enum": ["en", "id"]},
}


def _reader_schema(skill_id, format_label):
    return _sch(
        skill_id,
        f"Root-handled reader ({format_label}): the root integration resolves the source into plain text; this planning module never opens files, APIs, or audio. Contract: pass the resolved text to spec.parse.* skills.",
        ["text"],
        {"text": {**_TEXT, "description": f"Resolved {format_label} content as plain text"}, "source_label": {"type": "string", "description": "Optional human label of the origin (no path is read here)"}},
    )


def _selector_schema(skill_id, description, required):
    props = {k: COMMON_PROPS[k] for k in ("project_type", "features", "excluded_features", "constraints", "scale", "preferences", "context", "text")}
    return _sch(skill_id, description, required, props)


SCHEMAS = {}
for _fid in ROOT_READER_IDS:
    _label = {
        "spec.read.markdown": "markdown file",
        "spec.read.txt": "plain-text file",
        "spec.read.json": "JSON file",
        "spec.read.notation": "Notion page (API)",
        "spec.read.obsidian": "Obsidian vault note",
        "spec.read.voice": "voice note (transcription)",
    }[_fid]
    SCHEMAS[_fid] = _reader_schema(_fid, _label)

SCHEMAS["spec.parse.requirements"] = _sch("spec.parse.requirements", "Parse requirements (functional, non-functional, exclusions) from resolved spec text.", ["text"], {"text": COMMON_PROPS["text"], "name": COMMON_PROPS["name"], "project_type": COMMON_PROPS["project_type"]})
SCHEMAS["spec.parse.user_stories"] = _sch("spec.parse.user_stories", "Derive user stories with acceptance criteria from resolved spec text.", ["text"], {"text": COMMON_PROPS["text"], "language": COMMON_PROPS["language"]})
SCHEMAS["spec.parse.features"] = _sch("spec.parse.features", "Extract requested features and explicit exclusions (negations) from spec text.", ["text"], {"text": COMMON_PROPS["text"]})
SCHEMAS["spec.parse.constraints"] = _sch("spec.parse.constraints", "Extract constraints (performance, compliance, budget/deadline raw text) from spec.", ["text"], {"text": COMMON_PROPS["text"]})
SCHEMAS["spec.parse.design_pref"] = _sch("spec.parse.design_pref", "Extract design preferences (colors, tones, dark mode, mobile-first) from spec.", ["text"], {"text": COMMON_PROPS["text"]})
SCHEMAS["spec.clarify"] = _sch("spec.clarify", "Generate explicit clarification questions for ambiguous or under-specified topics.", ["text"], {"text": COMMON_PROPS["text"], "context": COMMON_PROPS["context"]})

SCHEMAS["stack.analyze.requirements"] = _selector_schema("stack.analyze.requirements", "Analyze requirements into deterministic feature signals and complexity for stack selection.", ["project_type"])
SCHEMAS["stack.select.frontend"] = _selector_schema("stack.select.frontend", "Select a frontend framework with reasons, alternatives, and re-evaluation triggers.", ["project_type"])
SCHEMAS["stack.select.backend"] = _selector_schema("stack.select.backend", "Select a backend approach with reasons, alternatives, and triggers.", ["project_type"])
SCHEMAS["stack.select.database"] = _selector_schema("stack.select.database", "Select a database engine based on feature signals.", ["project_type", "features"])
SCHEMAS["stack.select.hosting"] = _selector_schema("stack.select.hosting", "Select a hosting shape (static/managed/VPS) from feature signals and constraints.", ["project_type", "features"])
SCHEMAS["stack.select.auth"] = _selector_schema("stack.select.auth", "Select an auth strategy; returns included=false when the spec excludes or omits auth.", ["project_type", "features"])
SCHEMAS["stack.select.payment"] = _selector_schema("stack.select.payment", "Select a payment gateway only when payments are requested; exclusions are honored.", ["project_type", "features"])
SCHEMAS["stack.select.storage"] = _selector_schema("stack.select.storage", "Select file storage only when media needs exist.", ["project_type", "features"])
SCHEMAS["stack.select.cache"] = _selector_schema("stack.select.cache", "Select cache/queue layer via deterministic signal gating.", ["project_type", "features"])
SCHEMAS["stack.select.search"] = _selector_schema("stack.select.search", "Select search approach via scale and feature gating.", ["project_type", "features"])
SCHEMAS["stack.select.monitoring"] = _selector_schema("stack.select.monitoring", "Select monitoring baseline (self-host vs managed).", ["project_type", "features"])
SCHEMAS["stack.recommend.combination"] = _sch(
    "stack.recommend.combination",
    "Compose all selectors into one recommended combination document.",
    ["name", "project_type"],
    {k: COMMON_PROPS[k] for k in ("name", "project_type", "features", "excluded_features", "constraints", "scale", "preferences", "text")},
)

_db_engine_props = {
    "name": COMMON_PROPS["name"],
    "features": COMMON_PROPS["features"],
    "scale": COMMON_PROPS["scale"],
    "notes": {"type": "string"},
}
SCHEMAS["db.engine.match"] = _sch(
    "db.engine.match",
    "Score database engines against requirement signals; explicit preference overrides scoring.",
    ["features"],
    {
        "features": COMMON_PROPS["features"],
        "project_type": COMMON_PROPS["project_type"],
        "scale": COMMON_PROPS["scale"],
        "signals": {"type": "object", "description": "Optional explicit signal overrides, values 0..3", "additionalProperties": {"type": "integer", "minimum": 0, "maximum": 3}},
        "preference": {"type": "string", "description": "Explicit engine preference (wins over scoring)"},
        "text": COMMON_PROPS["text"],
    },
)
for _engine in DB_ENGINE_PROFILES:
    SCHEMAS[f"db.engine.{_engine}"] = _sch(
        f"db.engine.{_engine}",
        f"Planning notes for {DB_ENGINE_PROFILES[_engine]['label']}: fit, modeling, ops, backup, metrics, pitfalls.",
        ["name"],
        dict(_db_engine_props),
    )

_arch_props = {
    "name": COMMON_PROPS["name"],
    "project_type": COMMON_PROPS["project_type"],
    "features": COMMON_PROPS["features"],
    "excluded_features": COMMON_PROPS["excluded_features"],
    "constraints": COMMON_PROPS["constraints"],
    "text": COMMON_PROPS["text"],
}
_entity_list = {"type": "array", "minItems": 1, "items": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}, "fields": {"type": "array", "items": {"type": "string"}}}, "additionalProperties": False}}
SCHEMAS["arch.design.system"] = _sch("arch.design.system", "Design a high-level system architecture with layers, boundaries, and a mermaid diagram.", ["name"], dict(_arch_props))
SCHEMAS["arch.design.database_schema"] = _sch(
    "arch.design.database_schema",
    "Design a database schema: DDL, ER diagram, relations; tables derive from entities or features.",
    ["name"],
    {**dict(_arch_props), "entities": _entity_list},
)
SCHEMAS["arch.design.database_schema"]["anyOf"] = [
    {"required": ["entities"]},
    {"required": ["features"]},
    {"required": ["text"]},
]
SCHEMAS["arch.design.api"] = _sch(
    "arch.design.api",
    "Design a REST API: endpoint table plus an OpenAPI 3.1 draft built from entities/features.",
    ["name"],
    {**dict(_arch_props), "entities": _entity_list},
)
SCHEMAS["arch.design.api"]["anyOf"] = [
    {"required": ["entities"]},
    {"required": ["features"]},
    {"required": ["text"]},
]
SCHEMAS["arch.design.component_tree"] = _sch(
    "arch.design.component_tree",
    "Design the frontend component tree with suggested file paths.",
    ["name"],
    {**dict(_arch_props), "framework": {"type": "string", "enum": ["react", "vue", "svelte"]}},
)
SCHEMAS["arch.design.folder_structure"] = _sch(
    "arch.design.folder_structure",
    "Design the project folder structure from a framework template.",
    ["name"],
    {**dict(_arch_props), "framework": {"type": "string", "enum": sorted(FRAMEWORK_TREES)}},
)
SCHEMAS["arch.design.data_flow"] = _sch("arch.design.data_flow", "Design the data flow with steps and a mermaid diagram.", ["name"], {**dict(_arch_props), "hosting": {"type": "string"}})
SCHEMAS["arch.design.auth_flow"] = _sch(
    "arch.design.auth_flow",
    "Design the auth flow: token policy, steps, sequence diagram.",
    ["name"],
    {**dict(_arch_props), "strategy": {"type": "string", "enum": ["session_cookie", "jwt", "oauth"]}},
)
SCHEMAS["arch.design.deployment"] = _sch("arch.design.deployment", "Design the deployment architecture with environments and rollout steps.", ["name"], {**dict(_arch_props), "hosting": {"type": "string"}})
SCHEMAS["arch.design.scaling"] = _sch("arch.design.scaling", "Design a staged scaling strategy with explicit trigger metrics.", ["name"], dict(_arch_props))
SCHEMAS["arch.design.security"] = _sch("arch.design.security", "Design a security checklist mapped to the features in scope.", ["name"], dict(_arch_props))
SCHEMAS["arch.diagram.generate"] = _sch(
    "arch.diagram.generate",
    "Generate a diagram source file (mermaid or PlantUML) for a given kind.",
    ["name", "kind"],
    {
        "name": COMMON_PROPS["name"],
        "kind": {"type": "string", "enum": ["system", "data_flow", "er", "auth_sequence", "deployment", "component"]},
        "format": {"type": "string", "enum": ["mermaid", "plantuml"]},
        "strategy": {"type": "string", "enum": ["session_cookie", "jwt", "oauth"]},
        "hosting": {"type": "string"},
        "title": {"type": "string"},
        "project_type": COMMON_PROPS["project_type"],
        "features": COMMON_PROPS["features"],
        "excluded_features": COMMON_PROPS["excluded_features"],
        "entities": _entity_list,
        "text": COMMON_PROPS["text"],
    },
)
SCHEMAS["arch.document.generate"] = _sch(
    "arch.document.generate",
    "Generate an Architecture Decision Record (ADR) markdown file.",
    ["title", "context", "decision"],
    {
        "title": _TEXT,
        "context": _TEXT,
        "decision": _TEXT,
        "options": {"type": "array", "items": {"type": "string"}},
        "consequences": {"type": "string"},
        "number": {"type": "integer", "minimum": 1, "maximum": 9999},
        "status": {"type": "string", "enum": ["proposed", "accepted", "superseded"]},
    },
)

SCHEMAS["design.color.palette"] = _sch(
    "design.color.palette",
    "Generate a deterministic color ramp, neutrals, and semantic tokens with WCAG contrast math.",
    ["color"],
    {"color": {**_TEXT, "description": "Hex color or known color name (e.g. #2563EB or biru/blue)"}, "name": COMMON_PROPS["name"]},
)
SCHEMAS["design.typography"] = _sch(
    "design.typography",
    "Select a typography pair and modular scale for a given style.",
    ["style"],
    {"style": {"type": "string", "enum": sorted(TYPOGRAPHY_PAIRS)}, "language": COMMON_PROPS["language"]},
)
SCHEMAS["design.layout.grid"] = _sch(
    "design.layout.grid",
    "Emit layout grid tokens (columns, gutters, margins, breakpoints) for a preset.",
    ["preset"],
    {"preset": {"type": "string", "enum": sorted(GRID_PRESETS)}},
)
SCHEMAS["design.icon.set"] = _sch(
    "design.icon.set",
    "Select an icon set with license and usage rules.",
    ["framework"],
    {"framework": _TEXT, "tone": {"type": "string"}},
)
SCHEMAS["design.illustration"] = _sch(
    "design.illustration",
    "Generate a deterministic abstract SVG illustration.",
    ["name"],
    {
        "name": COMMON_PROPS["name"],
        "style": {"type": "string", "enum": ["blobs", "grid", "waves"]},
        "palette": {"type": "array", "items": {"type": "string"}},
        "width": {"type": "integer", "minimum": 320, "maximum": 3840},
        "height": {"type": "integer", "minimum": 240, "maximum": 2160},
    },
)
SCHEMAS["design.logo"] = _sch(
    "design.logo",
    "Generate a deterministic monogram/wordmark/badge SVG logo.",
    ["name"],
    {"name": COMMON_PROPS["name"], "style": {"type": "string", "enum": ["monogram", "wordmark", "badge"]}, "color": _TEXT},
)
SCHEMAS["design.favicon"] = _sch(
    "design.favicon",
    "Generate an SVG favicon with the brand monogram.",
    ["name"],
    {"name": COMMON_PROPS["name"], "color": _TEXT, "size": {"type": "integer", "minimum": 16, "maximum": 512}},
)
SCHEMAS["design.og_image"] = _sch(
    "design.og_image",
    "Generate a 1200x630 Open Graph image source (SVG).",
    ["title"],
    {"title": _TEXT, "subtitle": {"type": "string"}, "brand": {"type": "string"}, "color": _TEXT},
)
SCHEMAS["design.theme"] = _sch(
    "design.theme",
    "Generate light/dark theme tokens and a CSS variables file.",
    ["color"],
    {"color": _TEXT, "name": COMMON_PROPS["name"], "dark_mode": {"type": "boolean"}, "radius": {"type": "integer", "minimum": 0, "maximum": 32}},
)
SCHEMAS["design.prototype"] = _sch(
    "design.prototype",
    "Generate a clickable static HTML wireframe prototype with anchor navigation.",
    ["name"],
    {"name": COMMON_PROPS["name"], "features": COMMON_PROPS["features"], "project_type": COMMON_PROPS["project_type"], "language": COMMON_PROPS["language"], "color": _TEXT},
)

SCHEMAS["content.gen.placeholder"] = _sch(
    "content.gen.placeholder",
    "Generate explicitly-placeholder content structure for the project type (no invented facts).",
    ["name"],
    {"name": COMMON_PROPS["name"], "project_type": COMMON_PROPS["project_type"], "language": COMMON_PROPS["language"]},
)
SCHEMAS["content.gen.copy"] = _sch(
    "content.gen.copy",
    "Generate template-derived headline/tagline variants (human review required).",
    ["name"],
    {"name": COMMON_PROPS["name"], "tone": {"type": "string"}, "keywords": _STR_LIST, "language": COMMON_PROPS["language"], "project_type": COMMON_PROPS["project_type"]},
)
SCHEMAS["seo.gen.meta"] = _sch(
    "seo.gen.meta",
    "Generate escaped meta tags (title, description, canonical, robots, theme-color).",
    ["name", "url"],
    {"name": COMMON_PROPS["name"], "url": {**_URL, "description": "Absolute http(s) site URL"}, "description": {"type": "string"}, "language": COMMON_PROPS["language"], "color": _TEXT, "noindex": {"type": "boolean"}},
)
SCHEMAS["seo.gen.sitemap"] = _sch(
    "seo.gen.sitemap",
    "Generate an escaped sitemap.xml from routes (defaults derived from features).",
    ["base_url"],
    {"base_url": {**_URL, "description": "Absolute http(s) site URL"}, "routes": {"type": "array", "items": {"type": "string"}}, "features": COMMON_PROPS["features"], "text": COMMON_PROPS["text"]},
)
SCHEMAS["seo.gen.robots"] = _sch(
    "seo.gen.robots",
    "Generate robots.txt with derived disallow rules and sitemap reference.",
    ["base_url"],
    {"base_url": {**_URL, "description": "Absolute http(s) site URL"}, "disallow": {"type": "array", "items": {"type": "string"}}, "features": COMMON_PROPS["features"], "text": COMMON_PROPS["text"], "crawl_delay": {"type": "integer", "minimum": 0, "maximum": 30}},
)
SCHEMAS["seo.gen.schema"] = _sch(
    "seo.gen.schema",
    "Generate JSON-LD schema.org markup with only user-provided facts (no invented company data).",
    ["name", "url"],
    {"name": COMMON_PROPS["name"], "url": {**_URL, "description": "Absolute http(s) site URL"}, "project_type": COMMON_PROPS["project_type"], "description": {"type": "string"}, "logo_url": {"type": "string"}, "same_as": {"type": "array", "items": {"type": "string"}}, "locale": {"type": "string"}},
)
SCHEMAS["seo.gen.og"] = _sch(
    "seo.gen.og",
    "Generate escaped Open Graph / Twitter meta tags.",
    ["url", "title"],
    {"url": {**_URL, "description": "Absolute http(s) page URL"}, "title": _TEXT, "description": {"type": "string"}, "image_url": {"type": "string"}, "site_name": {"type": "string"}, "type": {"type": "string", "enum": ["website", "article", "product"]}, "twitter_handle": {"type": "string"}},
)
SCHEMAS["seo.optimize.performance"] = _sch(
    "seo.optimize.performance",
    "Produce a Core Web Vitals recommendation plan from project type, features, and constraints.",
    ["project_type"],
    {"project_type": COMMON_PROPS["project_type"], "features": COMMON_PROPS["features"], "constraints": COMMON_PROPS["constraints"], "text": COMMON_PROPS["text"]},
)


def schema(skill_id):
    if not isinstance(skill_id, str):
        raise SkillNotOwned("skill_id must be a string")
    if skill_id not in SCHEMAS:
        raise SkillNotOwned(skill_id)
    return json.loads(json.dumps(SCHEMAS[skill_id]))



HANDLERS = {
    "spec.parse.requirements": spec_parse_requirements,
    "spec.parse.user_stories": spec_parse_user_stories,
    "spec.parse.features": spec_parse_features,
    "spec.parse.constraints": spec_parse_constraints,
    "spec.parse.design_pref": spec_parse_design_pref,
    "spec.clarify": spec_clarify,
    "stack.analyze.requirements": stack_analyze_requirements,
    "stack.select.frontend": stack_select_frontend,
    "stack.select.backend": stack_select_backend,
    "stack.select.database": stack_select_database,
    "stack.select.hosting": stack_select_hosting,
    "stack.select.auth": stack_select_auth,
    "stack.select.payment": stack_select_payment,
    "stack.select.storage": stack_select_storage,
    "stack.select.cache": stack_select_cache,
    "stack.select.search": stack_select_search,
    "stack.select.monitoring": stack_select_monitoring,
    "stack.recommend.combination": stack_recommend_combination,
    "db.engine.match": db_engine_match,
}
for _engine in DB_ENGINE_PROFILES:
    HANDLERS[f"db.engine.{_engine}"] = db_engine_plan(_engine)
HANDLERS.update(
    {
        "arch.design.system": arch_design_system,
        "arch.design.database_schema": arch_design_database_schema,
        "arch.design.api": arch_design_api,
        "arch.design.component_tree": arch_design_component_tree,
        "arch.design.folder_structure": arch_design_folder_structure,
        "arch.design.data_flow": arch_design_data_flow,
        "arch.design.auth_flow": arch_design_auth_flow,
        "arch.design.deployment": arch_design_deployment,
        "arch.design.scaling": arch_design_scaling,
        "arch.design.security": arch_design_security,
        "arch.diagram.generate": arch_diagram_generate,
        "arch.document.generate": arch_document_generate,
        "design.color.palette": design_color_palette,
        "design.typography": design_typography,
        "design.layout.grid": design_layout_grid,
        "design.icon.set": design_icon_set,
        "design.illustration": design_illustration,
        "design.logo": design_logo,
        "design.favicon": design_favicon,
        "design.og_image": design_og_image,
        "design.theme": design_theme,
        "design.prototype": design_prototype,
        "content.gen.placeholder": content_gen_placeholder,
        "content.gen.copy": content_gen_copy,
        "seo.gen.meta": seo_gen_meta,
        "seo.gen.sitemap": seo_gen_sitemap,
        "seo.gen.robots": seo_gen_robots,
        "seo.gen.schema": seo_gen_schema,
        "seo.gen.og": seo_gen_og,
        "seo.optimize.performance": seo_optimize_performance,
    }
)

CATALOG_NUMBERS = {
    1: "spec.read.markdown", 2: "spec.read.txt", 3: "spec.read.json", 4: "spec.read.notation",
    5: "spec.read.obsidian", 6: "spec.read.voice", 7: "spec.parse.requirements", 8: "spec.parse.user_stories",
    9: "spec.parse.features", 10: "spec.parse.constraints", 11: "spec.parse.design_pref", 12: "spec.clarify",
    13: "stack.analyze.requirements", 14: "stack.select.frontend", 15: "stack.select.backend",
    16: "stack.select.database", 17: "stack.select.hosting", 18: "stack.select.auth", 19: "stack.select.payment",
    20: "stack.select.storage", 21: "stack.select.cache", 22: "stack.select.search", 23: "stack.select.monitoring",
    24: "stack.recommend.combination", 25: "db.engine.match", 26: "db.engine.postgresql", 27: "db.engine.mysql",
    28: "db.engine.mongodb", 29: "db.engine.redis", 30: "db.engine.elasticsearch", 31: "db.engine.sqlite",
    32: "db.engine.duckdb", 33: "db.engine.influxdb", 34: "db.engine.surrealdb", 35: "arch.design.system",
    36: "arch.design.database_schema", 37: "arch.design.api", 38: "arch.design.component_tree",
    39: "arch.design.folder_structure", 40: "arch.design.data_flow", 41: "arch.design.auth_flow",
    42: "arch.design.deployment", 43: "arch.design.scaling", 44: "arch.design.security",
    45: "arch.diagram.generate", 46: "arch.document.generate", 134: "design.color.palette",
    135: "design.typography", 136: "design.layout.grid", 137: "design.icon.set", 138: "design.illustration",
    139: "design.logo", 140: "design.favicon", 141: "design.og_image", 142: "design.theme",
    143: "design.prototype", 144: "content.gen.placeholder", 145: "content.gen.copy", 146: "seo.gen.meta",
    147: "seo.gen.sitemap", 148: "seo.gen.robots", 149: "seo.gen.schema", 150: "seo.gen.og",
    151: "seo.optimize.performance",
}

OWNED_IDS = frozenset(CATALOG_NUMBERS.values())

ARTIFACT_IDS = {
    "stack.recommend.combination", "arch.design.system", "arch.design.database_schema", "arch.design.api",
    "arch.design.component_tree", "arch.design.folder_structure", "arch.design.data_flow",
    "arch.design.auth_flow", "arch.design.deployment", "arch.design.scaling", "arch.design.security",
    "arch.diagram.generate", "arch.document.generate", "design.color.palette", "design.typography",
    "design.layout.grid", "design.illustration", "design.logo", "design.favicon", "design.og_image",
    "design.theme", "design.prototype", "content.gen.placeholder", "content.gen.copy", "seo.gen.meta",
    "seo.gen.sitemap", "seo.gen.robots", "seo.gen.schema", "seo.gen.og",
} | {f"db.engine.{e}" for e in DB_ENGINE_PROFILES}


def dispatch(skill_id, args=None):
    if not isinstance(skill_id, str):
        return _not_owned_result(str(skill_id))
    if skill_id in ROOT_READER_IDS:
        return _root_reader_result(skill_id)
    if skill_id not in OWNED_IDS:
        return _not_owned_result(skill_id)
    if not isinstance(args, dict):
        raise SkillInputError(f"args must be a dict for {skill_id}")
    sch = SCHEMAS[skill_id]
    for req in sch.get("required", []):
        if args.get(req) in (None, "", [], {}):
            if req == "context" and isinstance(args.get("context"), str) and args.get("context"):
                continue
            _err(f"{skill_id}: missing required argument '{req}'")
    handler = HANDLERS.get(skill_id)
    if handler is None:
        return _not_owned_result(skill_id)
    result = handler(args)
    kind = "artifact_generation" if skill_id in ARTIFACT_IDS else "analysis"
    result["execution_kind"] = kind
    return result


def owned_skills():
    out = []
    for number in sorted(CATALOG_NUMBERS):
        skill_id = CATALOG_NUMBERS[number]
        out.append(
            {
                "number": number,
                "id": skill_id,
                "execution_kind": None,
                "handled_by": "root" if skill_id in ROOT_READER_IDS else "planning",
            }
        )
    return out
