"""OpenCode lexical signals, reviewed and integrated by Codex; bounded rules only."""

import re

_NORMALIZE_MAP = {
    "yg": "yang", "dgn": "dengan", "tdk": "tidak", "gk": "tidak", "gak": "tidak",
    "nggak": "tidak", "ngga": "tidak", "ga": "tidak", "jgn": "jangan",
    "bkn": "bukan", "bkin": "bikin", "tp": "tetapi", "kalo": "kalau",
}

_PRIVATE_RE = re.compile(r"\bistri\b|\bsuami\b|\bkeluarga\b|\banak\b|\bpacar\b|\bselingkuh\b|\bgaji\b|"
                         r"\butang\b|\bpinjol\b|\bsakit\b|\bpenyakit\b|\bmental\b|\bdepresi\b|\bcemas\b|"
                         r"\bkhawatir\b|\btakut\b|\bsedih\b|\bcerai\b|\bnikah\b|\bwarisan\b|\bkdrt\b", re.I)

_CRISIS_RE = re.compile(r"\bbunuh diri\b|\bmengakhiri hidup\b|\bakhiri hidup\b|\bakhiri nyawa\b|"
                        r"\bingin mati\b|\bmau mati\b|\bpengen mati\b|\bkepengen mati\b|\bmencederai diri\b|"
                        r"\bmenyakiti diri\b|\bgantung diri\b|\boverdosis\b|\bminum obat semua\b|"
                        r"\btelan semua pil\b|\bself harm\b|\bmembunuh orang\b|\bmau membunuh\b")

_DESTRUCTIVE_RE = re.compile(r"\bhapus semua\b|\bdelete semua\b|\bdelete all\b|\bformat ulang\b|"
                             r"\bdrop table\b|\brm\s+-rf\b|\bmatikan server\b|\bshutdown server\b|"
                             r"\buninstall semua\b|\bwipe semua\b|\breset semua\b|\bhapus database\b")

_HIGH_RE = re.compile(r"\bsegera\b|\bdarurat\b|\burgent\b|\bsecepatnya\b|\bsekarang juga\b|"
                      r"\bpenting banget\b|\bdeadline hari ini\b")

_MEDIUM_RE = re.compile(r"\bhari ini\b|\bsebentar lagi\b|\bminggu ini\b")

_PII = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("nik", re.compile(r"(?<!\d)\d{16}(?!\d)")),
    ("phone", re.compile(r"(?<!\d)(?:\+62|62|0)8\d{7,11}(?!\d)")),
    ("card", re.compile(r"(?<!\d)(?:\d{4}[ -]){3}\d{4}(?!\d)")),
    ("credential", re.compile(r"(?i)\b(?:password|kata sandi|sandi|passcode|api[_ -]?key|apikey|secret|token)\b\s*[:=]\s*\S{4,}")),
]

_TRIGGER_PATTERNS = {
    "web_builder": [r"\bwebsite\b", r"\bwebs?\b", r"\blanding page\b", r"\btoko online\b",
                    r"\bfrontend\b", r"\bbackend\b", r"\bweb app\b", r"\bwordpress\b", r"\bnext\.?js\b"],
    "mobile_builder": [r"\baplikasi\b", r"\bandroid\b", r"\bios\b", r"\bplay store\b", r"\bapp store\b",
                       r"\bflutter\b", r"\breact native\b", r"\bkotlin\b", r"\bswift\b"],
    "browser_auto": [r"\bbrowser\b", r"\bscraping\b", r"\bscrape\b", r"\botomasi browser\b",
                     r"\bautomasi browser\b", r"\bisi form\b", r"\bklik tombol\b", r"\bscreenshot\b",
                     r"\bimacros\b", r"\bbuka situs\b", r"\bcari tahu harga\b", r"\blogin otomatis\b"],
    "legal": [r"\bhukum\b", r"\bhukumnya\b", r"\bpasal\b", r"\buu\b", r"\bundang-undang\b", r"\bkuhp\b",
              r"\bpidana\b", r"\bperdata\b", r"\bgugat(?:an)?\b", r"\bsomasi\b", r"\btertuduh\b",
              r"\bterdakwa\b", r"\bpelapor\b", r"\bpengadilan\b", r"\badvokat\b", r"\bite\b", r"\bpdp\b",
              r"\bperjanjian\b", r"\bkontrak\b", r"\bwarisan\b", r"\bcerai\b", r"\bnikah\b",
              r"\bpenipuan\b", r"\bpencurian\b", r"\bkdrt\b", r"\bpolisi\b", r"\bjaksa\b"],
    "chat": [r"\bhalo\b", r"\bhai\b", r"\bhello\b", r"\bapa kabar\b", r"\bterima kasih\b", r"\bmakasih\b",
             r"\bthanks\b", r"\btemani\b", r"\bngobrol\b", r"\bcerita\b", r"\bsedih\b", r"\bkesal\b",
             r"\bmarah\b", r"\bbingung\b", r"\bgabut\b", r"\bpenat\b", r"\bkesepian\b"],
    "text_typo": [r"\btypo\b", r"\bsalah ketik\b", r"\bsalah tik\b", r"\bperbaiki teks\b",
                  r"\bbetulkan teks\b", r"\bnormalisasi teks\b", r"\bbahasa gaul\b", r"\bbahasa alay\b",
                  r"\balay\b", r"\bejaan\b", r"\bsingkatan\b"],
}
