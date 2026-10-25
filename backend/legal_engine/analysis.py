"""Source-grounded educational legal analysis lenses (scope: expert.*, ite.*, kuhp.*).

Read-only handlers. Every substantive reading is anchored to an exact quote
taken from trusted source records fetched via ctx.sources BEFORE any guidance
is composed. ``ite.pasal.*`` and ``kuhp.pasal.*`` IDs are compatibility
routing identifiers: their original labels are never asserted as the meaning
of current law; actual article references are only reported as they literally
appear inside trusted quotes. No guilt or legal-applicability verdicts, no
certified currentness, no automatic legal-regime transition resolution, no
network/CLI/filesystem access, no writes.
"""
import datetime as _dt
import importlib.util as _ilu
import json as _json
import os as _os
import re as _re

try:
    from .common import DomainError, require, identifier, parse_time, now_iso, validate
except ImportError:
    _ROOT_DIR = _os.environ.get('HARMONIKA_ROOT') or _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    _COMMON_PATH = _os.path.join(_ROOT_DIR, 'backend', 'legal_engine', 'common.py')
    if not _os.path.isfile(_COMMON_PATH):
        raise ImportError('real root common not found at %s; set HARMONIKA_ROOT' % _COMMON_PATH)
    _spec = _ilu.spec_from_file_location('_harmonika_root_legal_common', _COMMON_PATH)
    _common = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_common)
    DomainError = _common.DomainError
    require = _common.require
    identifier = _common.identifier
    parse_time = _common.parse_time
    now_iso = _common.now_iso
    validate = _common.validate

DISCLAIMER = 'Edukasi hukum, bukan nasihat hukum formal atau pengganti advokat.'
LENS_NOTE = 'Mode ahli adalah lensa analisis edukatif, bukan advokat berlisensi dan bukan pengganti pendapat advokat.'
NO_VERDICT_REASONS = [
    'currentness sumber tidak tersertifikasi (not_certified) walau berasal dari fetch resmi',
    'transisi/renumbering antar rezim hukum tidak diselesaikan otomatis',
    'keaslian bukti tidak diverifikasi secara forensik (authenticity not_verified)',
    'lensa edukatif bukan pendapat advokat berlisensi',
]
UNIVERSAL_BOUNDS = [
    'Lensa edukatif; bukan advokat berlisensi dan bukan pendapat hukum formal.',
    'Tidak memberi vonis bersalah/tidak bersalah dan tidak memberi kesimpulan penerapan pasal yang mengikat.',
    'Currentness sumber tidak tersertifikasi (not_certified); fetch resmi bukan sertifikasi status hukum.',
    'Transisi/renumbering antar rezim hukum tidak diselesaikan otomatis.',
    'Tanpa sumber terpercaya yang relevan, tidak ada panduan substantif.',
    'Read-only; tidak menulis data, tidak filing, dan tidak mengirim notifikasi resmi.',
]

RIGHTS_BASE = [
    'Apakah saya berhak didampingi advokat pada setiap tahap penanganan perkara?',
    'Bagaimana cara mengajukan permohonan bantuan hukum bila biaya menjadi kendala?',
]
RIGHTS_PERSPECTIVE = {
    'pelapor': [
        'Bagaimana cara melapor yang benar dan ke mana laporan diajukan?',
        'Apa hak saya untuk mendapatkan informasi perkara setelah laporan diterima?',
    ],
    'terlapor': [
        'Apa arti asas praduga tak bersalah bagi saya saat ini?',
        'Pada tahap mana saya berhak dan sebaiknya didampingi advokat?',
    ],
    'netral': [
        'Apa saja hak pelapor dan hak terlapor yang perlu dipahami berdampingan?',
        'Bagaimana memastikan kronologi saya netral dan tidak menuduh sebelum ada putusan?',
    ],
}
RIGHTS_DOMAIN = {
    'ite': [
        'Bagaimana asas praduga tak bersalah berlaku bila saya berstatus terlapor perkara ITE?',
        'Bagaimana cara meminta pemeriksaan bukti elektronik oleh pihak yang independen?',
    ],
    'pidana': [
        'Apa hak terlapor untuk tidak membuat keterangan yang dapat menjatuhkan dirinya?',
        'Apa hak pelapor untuk mendapatkan berita status perkara?',
    ],
    'perdata': [
        'Pilihan penyelesaian sengketa apa saja yang dapat dipertimbangkan sebelum litigasi?',
        'Dokumen perikatan apa yang perlu disiapkan sebelum berkonsultasi?',
    ],
    'keluarga': [
        'Bagaimana proses mediasi biasanya berjalan sebelum persidangan?',
        'Apa yang perlu dipersiapkan terkait kepentingan anak dalam proses hukum?',
    ],
    'tenagakerja': [
        'Ke mana pekerja dapat meminta pertimbangan atas sengketa hubungan industrial?',
        'Hak apa yang perlu dikonfirmasi dari sumber resmi terkait pengakhiran hubungan kerja?',
    ],
    'konsumen': [
        'Bagaimana cara mengajukan keberatan tertulis kepada pelaku usaha?',
        'Lembaga mana yang berwenang menangani sengketa konsumen menurut aturan saat ini?',
    ],
    'pdp': [
        'Bagaimana cara meminta akses, perbaikan, atau penghapusan data pribadi kepada pengendali data?',
        'Ke mana dapat menyampaikan dugaan kebocoran data pribadi?',
    ],
    'opinion': [
        'Pertanyaan hak apa yang paling mendesak untuk ditanyakan kepada advokat?',
    ],
}
GAPS_DOMAIN = {
    'ite': [
        'Awetkan konten asli (bukan hanya tangkapan layar), URL, dan waktu akses.',
        'Catat identitas akun/waktu penyebaran yang tampak, tanpa mengambil jalan pintas sendiri.',
    ],
    'pidana': [
        'Catat kronologi waktu-tempat-pihak sedetail mungkin.',
        'Identifikasi saksi yang melihat atau mendengar langsung kejadian.',
    ],
    'perdata': [
        'Kumpulkan dokumen perikatan: perjanjian, invoice, bukti transfer, korespondensi.',
    ],
    'keluarga': [
        'Siapkan dokumen perkawinan, bukti penghasilan, dan catatan kebutuhan sehari-hari.',
    ],
    'tenagakerja': [
        'Simpan salinan perjanjian kerja, slip upah, dan riwayat komunikasi terkait pengakhiran.',
    ],
    'konsumen': [
        'Simpan bukti transaksi, kemasan/iklan, dan hasil komplain sebelumnya.',
    ],
    'pdp': [
        'Simpan tangkapan layar pemberitahuan, catat waktu kejadian, dan identitas pengendali data yang terlihat.',
    ],
    'opinion': [
        'Lengkapi bagian perkara yang masih kosong sebelum membawa draf ini ke advokat.',
    ],
}

_PASAL_RE = _re.compile(r'[Pp]asal\s+([0-9]{1,4}[A-Za-z]?)(?:\s+ayat\s*\(([0-9]+)\))?')
_LABEL_ARTICLE_RE = _re.compile(r'Pasal\s+([0-9]{1,4}(?:\s*[-\u2013]\s*[0-9]{1,4})?[A-Za-z]?)')
_DATE_WORDS_RE = _re.compile(r'\b([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4})\b')
_DATE_ISO_RE = _re.compile(r'\b([0-9]{4}-[0-9]{2}-[0-9]{2})\b')


def _L(i, arch, domain, topic, queries, prompts, routing=None, transition=False):
    return {'id': i, 'archetype': arch, 'domain': domain, 'topic': topic,
            'queries': list(queries), 'prompts': list(prompts),
            'routing': routing, 'transition': transition}


def _ite_lens(pid, desc, kws, art):
    ident = 'ite.pasal.' + pid
    topic, label = desc if isinstance(desc, tuple) else (desc, desc)
    return _L(ident, 'routing', 'ite', topic,
              list(kws) + ['informasi dan transaksi elektronik'],
              ['Perhatikan kata kerja dan syarat dalam kutipan; tandai bagian kronologi Anda yang menjawabnya.',
               'Nomor pasal hanya diambil dari teks kutipan sumber, bukan dari label ID.'],
              routing={'label_claim': label, 'routing_article_ref': art}, transition=False)


def _kuhp_lens(slug, desc, kws, transition=False):
    arts = [m.group(1).replace(' ', '') for m in _LABEL_ARTICLE_RE.finditer(desc)]
    return _L('kuhp.pasal.' + slug if slug != 'baru.2026' else 'kuhp.baru.2026',
              'routing', 'kuhp', desc, list(kws),
              ['Nomor pasal hanya dilaporkan sebagaimana muncul dalam kutipan; label nomor lama tidak dijadikan default hukum saat ini.',
               'Bandingkan kutipan dengan tanggal kejadian Anda; rezim hukum tidak diputuskan otomatis.'],
              routing={'label_claim': desc, 'label_articles': arts}, transition=transition)


LENSES = [
    _L('expert.ite.assess', 'assess', 'ite', 'Analisa tindak pidana ITE',
       ['informasi dan transaksi elektronik'],
       ['Cocokkan kata kerja dalam kutipan (mendistribusikan/mengirimkan/membuat dapat diakses) dengan kronologi Anda.',
        'Perhatikan syarat kesengajaan dan tanpa hak pada kutipan; tandai yang belum jelas.']),
    _L('expert.ite.unsur_breakdown', 'unsur', 'ite', 'Pemecahan fragmen pasal ITE',
       ['informasi dan transaksi elektronik'],
       ['Baca tiap fragmen terpisah; pemetaan unsur doktrinal perlu advokat.']),
    _L('expert.ite.bukti_digital', 'bukti', 'ite', 'Pembacaan metadata bukti digital',
       ['bukti elektronik', 'alat bukti'],
       ['Isi media tidak dibaca; pertanyaan di bawah hanya tentang pengelolaan dan keamanan bukti.']),
    _L('expert.pidana.assess', 'assess', 'pidana', 'Analisa tindak pidana umum',
       ['kitab undang-undang hukum pidana'],
       ['Cocokkan perbuatan dalam kronologi dengan redaksi kutipan; jangan menyimpulkan sendiri.',
        'Perhatikan apakah kutipan mensyaratkan kesengajaan, ancangan, atau persiapan.']),
    _L('expert.pidana.unsur_breakdown', 'unsur', 'pidana', 'Pemecahan fragmen pasal pidana',
       ['kitab undang-undang hukum pidana'],
       ['Baca tiap fragmen terpisah; pemetaan unsur doktrinal perlu advokat.']),
    _L('expert.pidana.sifat_perbuatan', 'sifat', 'pidana', 'Sifat perbuatan (kesengajaan/kelalaian)',
       ['kesengajaan'],
       ['Teks kutipan dipakai untuk merumuskan pertanyaan; keputusan sengaja/lalai tidak dibakukan di sini.',
        'Tandai bagian kronologi yang menunjukkan niat atau kelalaian untuk didiskusikan dengan advokat.']),
    _L('expert.perdata.assess', 'assess', 'perdata', 'Analisa sengketa perdata',
       ['perikatan'],
       ['Cocokkan sumber kewajiban (perjanjian atau undang-undang) dengan kutipan.']),
    _L('expert.perdata.wanprestasi', 'assess', 'perdata', 'Analisa wanprestasi',
       ['wanprestasi'],
       ['Perhatikan pada kutipan bentuk kealpaan yang disebut dan bukti yang menyertainya.']),
    _L('expert.perdata.perbuatan_lawan', 'assess', 'perdata', 'Analisa perbuatan melawan hukum',
       ['perbuatan melawan hukum'],
       ['Perhatikan unsur kesalahan dan kerugian pada kutipan; tandai yang belum terbukti.']),
    _L('expert.keluarga.assess', 'assess', 'keluarga', 'Analisa perkara keluarga',
       ['perceraian'],
       ['Fokus pada proses dan kepentingan anak; keputusan akhir ada di pengadilan.']),
    _L('expert.keluarga.nafkah', 'assess', 'keluarga', 'Analisa nafkah',
       ['nafkah'],
       ['Perhatikan pada kutipan kewajiban yang disebut dan dokumen pendukungnya.']),
    _L('expert.tenagakerja.assess', 'assess', 'tenagakerja', 'Analisa hubungan kerja',
       ['perjanjian kerja', 'upah'],
       ['Cocokkan isi perjanjian kerja Anda dengan kutipan; jangan anggap otomatis berlaku.']),
    _L('expert.konsumen.assess', 'assess', 'konsumen', 'Analisa sengketa konsumen',
       ['perlindungan konsumen'],
       ['Perhatikan hak dan kewajiban pelaku usaha pada kutipan; bandingkan dengan kronologi.']),
    _L('expert.pdp.assess', 'assess', 'pdp', 'Analisa pelindungan data pribadi',
       ['pelindungan data pribadi', 'data pribadi'],
       ['Perhatikan definisi dan kewajiban pengendali data pada kutipan.']),
    _L('expert.opinion.generate', 'opinion', 'opinion', 'Draf opini edukatif',
       ['undang-undang'],
       ['Draf ini menyusun kutipan sumber dan fakta perkara; bukan opini advokat.']),
    _ite_lens('27', ('muatan elektronik', 'Pasal 27: muatan mengganggu, penipuan'), ['muatan elektronik', 'penipuan'], 'Pasal 27'),
    _ite_lens('27.ayat1', ('pencemaran nama baik', 'Ayat 1: pencemaran nama baik'), ['pencemaran nama baik'], 'Pasal 27 ayat (1)'),
    _ite_lens('27.ayat3', ('penghinaan', 'Ayat 3: penghinaan atau penyebaran'), ['penghinaan'], 'Pasal 27 ayat (3)'),
    _ite_lens('27.ayat4', ('ancaman kekerasan', 'Ayat 4: pengancaman kekerasan'), ['ancaman kekerasan', 'menakut-nakuti'], 'Pasal 27 ayat (4)'),
    _ite_lens('28', ('berita bohong', 'Pasal 28: berita kebencian & palsu'), ['berita bohong'], 'Pasal 28'),
    _ite_lens('28.ayat1', ('kebencian SARA', 'Ayat 1: berita kebencian SARA'), ['kebencian', 'sara'], 'Pasal 28 ayat (1)'),
    _ite_lens('28.ayat2', ('ancaman kekerasan', 'Ayat 2: ancaman kekerasan'), ['kekhawatiran', 'menakut-nakuti'], 'Pasal 28 ayat (2)'),
    _ite_lens('30', ('akses ilegal', 'Pasal 30: akses ilegal & gangguan'), ['sistem elektronik', 'akses elektronik'], 'Pasal 30'),
    _ite_lens('30.ayat1', ('akses tidak sah', 'Ayat 1: akses tidak sah'), ['mengakses elektronik', 'akses elektronik'], 'Pasal 30 ayat (1)'),
    _ite_lens('32', ('intersepsi', 'Pasal 32: intersepsi & wiretapping'), ['intersepsi', 'penyadapan'], 'Pasal 32'),
    _ite_lens('35', ('perbuatan tidak menyenangkan', 'Pasal 35: perbuatan tidak menyenangkan'), ['perbuatan tidak menyenangkan'], 'Pasal 35'),
    _ite_lens('45', ('ancaman pidana', 'Pasal 45: ancaman pidana'), ['ancaman pidana', 'sanksi pidana'], 'Pasal 45'),
    _kuhp_lens('pencurian', 'Analisa pencurian (Pasal 362)', ['pencurian']),
    _kuhp_lens('penggelapan', 'Analisa penggelapan (Pasal 372)', ['penggelapan']),
    _kuhp_lens('penipuan', 'Analisa penipuan (Pasal 378)', ['penipuan']),
    _kuhp_lens('pemerasan', 'Analisa pemerasan (Pasal 368)', ['pemerasan']),
    _kuhp_lens('pengancaman', 'Analisa pengancaman (Pasal 369)', ['pengancaman']),
    _kuhp_lens('pencemaran', 'Analisa pencemaran nama baik (Pasal 310-311)', ['pencemaran nama baik']),
    _kuhp_lens('penghinaan', 'Analisa penghinaan (Pasal 312-321)', ['penghinaan']),
    _kuhp_lens('perlindungan_anak', 'Analisa pasal anak (Pasal 76-81)', ['perlindungan anak']),
    _kuhp_lens('kdrt', 'Analisa KDRT (Pasal 44 UU PKDRT)', ['kekerasan dalam rumah tangga']),
    _kuhp_lens('pembunuhan', 'Analisa pembunuhan (Pasal 338-350)', ['pembunuhan']),
    _kuhp_lens('penganiayaan', 'Analisa penganiayaan (Pasal 351-358)', ['penganiayaan']),
    _kuhp_lens('perkosaan', 'Analisa perkosaan (Pasal 285 UU HP)', ['perkosaan']),
    _kuhp_lens('korupsi', 'Analisa korupsi (Pasal 2-3 UU Tipikor)', ['tindak pidana korupsi', 'korupsi']),
    _kuhp_lens('narkotika', 'Analisa narkotika (Pasal 111-148 UU Narkotika)', ['narkotika']),
    _kuhp_lens('baru.2026', 'KUHP Baru (UU 1/2023) - efektif 2026',
               ['kitab undang-undang hukum pidana baru', 'nomor 1 tahun 2023'], transition=True),
]

SCOPE_IDS = tuple(l['id'] for l in LENSES)
_BY_ID = dict((l['id'], l) for l in LENSES)
require(len(SCOPE_IDS) == 42, 'lens_scope_must_be_42', 500)


def _catalog_scope_check():
    path = _os.path.join(_os.path.dirname(__file__) if '__file__' in globals() else '.', 'catalog_source.json')
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            cat = _json.load(fh)
    except Exception:
        return
    ids = set()
    for s in cat.get('skills', []):
        sid = s.get('id')
        if isinstance(sid, str) and sid.startswith(('expert.', 'ite.', 'kuhp.')):
            ids.add(sid)
    if ids and ids != set(SCOPE_IDS):
        raise ImportError('analysis scope mismatch vs canonical catalog_source.json')


_catalog_scope_check()


def _base_schema(optional_extra=(), required_extra=()):
    props = {
        'case_id': {'type': 'string', 'minLength': 1, 'maxLength': 100, 'pattern': '^[A-Za-z0-9_-]{1,100}$'},
        'question': {'type': 'string', 'minLength': 1, 'maxLength': 2000},
        'source_id': {'type': 'string', 'minLength': 1, 'maxLength': 100, 'pattern': '^[A-Za-z0-9_-]{1,100}$'},
        'evidence_file_id': {'type': 'string', 'minLength': 1, 'maxLength': 100, 'pattern': '^[A-Za-z0-9_-]{1,100}$'},
    }
    allowed = {'case_id', 'question'} | set(optional_extra) | set(required_extra)
    return {'type': 'object', 'additionalProperties': False,
            'required': ['case_id'] + list(required_extra),
            'properties': {k: v for k, v in props.items() if k in allowed}}


SCHEMAS = {}
LIMITATIONS = {}
for _l in LENSES:
    optional, required = (), ()
    if _l['archetype'] == 'unsur':
        optional = ('source_id',)
    if _l['archetype'] == 'bukti':
        required = ('evidence_file_id',)
    SCHEMAS[_l['id']] = _base_schema(optional, required)

    bounds = list(UNIVERSAL_BOUNDS)
    bounds.append('Kueri sumber terbatas pada: %s.' % ', '.join(_l['queries']))
    if _l['archetype'] == 'routing':
        bounds.append('ID %s adalah identifier routing kompatibilitas; label %r tidak diassert sebagai makna pasal; nomor pasal hanya dilaporkan sebagaimana muncul dalam kutipan sumber.' % (_l['id'], _l['routing'].get('label_claim')))
    if _l['domain'] == 'kuhp':
        bounds.append('Label nomor KUHP lama tidak dijadikan default hukum yang berlaku saat ini.')
    if _l['archetype'] == 'unsur':
        bounds.append('Pemecahan fragmen bersifat mekanis atas kutipan; pemetaan unsur doktrinal perlu advokat.')
    if _l['archetype'] == 'bukti':
        bounds.append('OCR/ASR/VLM/analisis forensik tidak dikonfigurasi pada modul ini; isi media tidak dibaca (not_seen); analysis_performed=false.')
    if _l['archetype'] == 'sifat':
        bounds.append('Distingsi kesengajaan/kelalaian tidak diputuskan; hanya pertanyaan pembacaan teks.')
    if _l['archetype'] == 'opinion':
        bounds.append('Draf opini adalah struktur edukatif dari kutipan sumber dan fakta perkara; bukan opini advokat dan tidak dibuat-kan seolah advokat.')
    LIMITATIONS[_l['id']] = bounds
del optional, required, _l


def _trusted(rec):
    return (isinstance(rec, dict) and rec.get('has_full_text') is not False
            and isinstance(rec.get('id'), str) and rec.get('id')
            and isinstance(rec.get('title'), str)
            and isinstance(rec.get('url'), str)
            and isinstance(rec.get('body'), str) and rec['body']
            and isinstance(rec.get('sha256'), str) and rec.get('sha256')
            and rec.get('provenance') == 'official_live_fetch'
            and isinstance(rec.get('verification'), dict)
            and rec['verification'].get('official_host') is True
            and rec['verification'].get('fetched_by_server') is True
            and isinstance(rec.get('fetched_at'), str))


def _citation(rec, quote):
    require(isinstance(quote, str) and quote and quote in rec['body'] and len(quote) <= 500,
            'citation_quote_must_be_exact_substring', 500)
    return {'source_id': rec['id'], 'url': rec['url'], 'title': rec['title'],
            'retrieved_at': rec['fetched_at'], 'sha256': rec['sha256'], 'quote': quote}


def _passage(body, keyword, max_len=500):
    low = body.lower()
    kw = keyword.lower()
    idx = low.find(kw)
    if idx < 0:
        return None
    start = max(0, idx - 160)
    end = min(len(body), idx + 340)
    return body[start:end].strip()


def _refs_in(text):
    out = []
    for m in _PASAL_RE.finditer(text):
        ref = 'Pasal ' + m.group(1) + (' ayat (' + m.group(2) + ')' if m.group(2) else '')
        if ref not in out:
            out.append(ref)
    return out


def _dates_in(text):
    out = []
    for rx in (_DATE_ISO_RE, _DATE_WORDS_RE):
        for m in rx.finditer(text):
            if m.group(1) not in out:
                out.append(m.group(1))
    return out


def _load_case(ctx, args):
    case_id = identifier(args['case_id'])
    case = ctx.get('legal_case', case_id)
    require(isinstance(case, dict), 'case_record_invalid', 500)
    return case_id, case


def _gate_state(case):
    jur = case.get('jurisdiction')
    jur_ok = isinstance(jur, str) and bool(jur.strip()) and _re.fullmatch(r'[A-Za-z]{2}', jur.strip()) is not None
    jur_norm = jur.strip().upper() if jur_ok else None
    raw = case.get('occurred_on')
    date_reason = 'missing'
    date_val = None
    if raw is not None:
        if not isinstance(raw, str) or not raw.strip():
            date_reason = 'invalid'
        else:
            v = raw.strip()
            try:
                if 'T' in v or 't' in v:
                    d = parse_time(v).date()
                else:
                    d = _dt.date.fromisoformat(v[:10])
                if d > _dt.date.today():
                    date_val = d.isoformat()
                    date_reason = 'future_date'
                else:
                    date_val = d.isoformat()
                    date_reason = 'ok'
            except Exception:
                date_val = None
                date_reason = 'invalid'
    return {'jurisdiction': {'value': jur_norm, 'ok': jur_ok},
            'incident_date': {'value': date_val, 'ok': date_reason == 'ok', 'reason': date_reason}}


def _lens_queries(lens, case):
    qs = list(lens['queries'])
    if lens['archetype'] == 'opinion':
        topic = case.get('topic')
        if isinstance(topic, str) and 3 <= len(topic.strip()) <= 60:
            qs.insert(0, topic.strip())
    return list(dict.fromkeys(qs))[:5]


def _do_retrieve(ctx, queries, jurisdiction, jurisdiction_assumed):
    found, order, failed, excluded = {}, [], [], 0
    sources = getattr(ctx, 'sources', None)
    if sources is None or not hasattr(sources, 'search'):
        return {'performed': False, 'queries': queries, 'jurisdiction_used': jurisdiction,
                'jurisdiction_assumed': jurisdiction_assumed, 'found': [], 'failed': [{'error': 'sources_api_unavailable'}],
                'excluded_untrusted': excluded, 'sources_unavailable': True}
    for q in queries:
        try:
            recs = sources.search(query=q, jurisdiction=jurisdiction, limit=10)
            require(isinstance(recs, list), 'bad_source_collection', 502)
        except Exception as exc:
            failed.append({'query': q, 'error': getattr(exc, 'code', type(exc).__name__)})
            continue
        for r in recs:
            if not _trusted(r):
                excluded += 1
                continue
            sid = r['id']
            if sid not in found:
                found[sid] = r
                order.append(sid)
    unavailable = bool(failed) and not order
    return {'performed': True, 'queries': queries, 'jurisdiction_used': jurisdiction,
            'jurisdiction_assumed': jurisdiction_assumed, 'found': [found[i] for i in order],
            'failed': failed, 'excluded_untrusted': excluded, 'sources_unavailable': unavailable}


def _anchors(lens, found, k=3):
    scored = []
    for r in found:
        title=r.get('title','').lower();domain=lens['domain']
        scope={'perdata':('perdata',),'keluarga':('perkawinan','keluarga','kompilasi hukum islam'),'tenagakerja':('ketenagakerjaan','tenaga kerja','cipta kerja'),'konsumen':('perlindungan konsumen','pelindungan konsumen'),'pdp':('data pribadi',),'ite':('transaksi elektronik','informasi elektronik','ite','penyesuaian pidana'),'pidana':('pidana','narkotika','korupsi','rumah tangga','perlindungan anak'),'kuhp':('pidana','narkotika','korupsi','rumah tangga','perlindungan anak')}
        if domain in scope and not any(word in title for word in scope[domain]):continue
        text = (r.get('title', '') + ' ' + r['body']).lower()
        hits = []
        for kw in lens['queries']:
            c = text.count(kw.lower())
            if c > 0:
                hits.append((c, kw))
        if not hits:
            continue
        hits.sort(key=lambda t: (-t[0], lens['queries'].index(t[1])))
        kw = hits[0][1]
        q = _passage(r['body'], kw)
        if q:
            scored.append((sum(h[0] for h in hits), r, q, kw))
    scored.sort(key=lambda t: -t[0])
    return scored[:k]


def _evidence_info(ctx, case_id):
    lst = getattr(ctx, 'list', None)
    if lst is None:
        return {'available': False, 'linked_count': 0, 'items': [], 'note': 'daftar bukti tidak tersedia'}
    try:
        recs = lst('legal_evidence', where={'case_id': case_id}, limit=100)
    except Exception as exc:
        return {'available': False, 'linked_count': 0, 'items': [],
                'note': 'daftar bukti tidak tersedia: %s' % getattr(exc, 'code', type(exc).__name__)}
    items = []
    for r in recs if isinstance(recs, list) else []:
        if isinstance(r, dict):
            items.append({k: r.get(k) for k in ('file_id', 'name', 'mime_type', 'size_bytes', 'uploaded_at', 'authenticity', 'content_analysis')})
    return {'available': True, 'linked_count': len(items), 'items': items, 'note': 'metadata saja; isi media tidak dibaca'}


def _missing_evidence(case, ev, lens):
    out = []
    facts = case.get('facts')
    if not isinstance(facts, str) or len(facts.strip()) < 40:
        out.append('Kronologi masih sangat singkat/kosong: tambahkan urutan kejadian, waktu, tempat, dan pihak.')
    if case.get('occurred_on') in (None, ''):
        out.append('Tanggal kejadian belum ada; wajib untuk analisis rezim hukum.')
    if ev['linked_count'] == 0:
        out.append('Belum ada bukti terhubung ke perkara; awetkan salinan/konten asli sebelum dilaporkan.')
    out.extend(GAPS_DOMAIN.get(lens['domain'], GAPS_DOMAIN['opinion']))
    return out


def _rights(case, lens):
    out = list(RIGHTS_BASE)
    out.extend(RIGHTS_DOMAIN.get(lens['domain'], []))
    persp = case.get('perspective')
    if persp in RIGHTS_PERSPECTIVE:
        out.extend(RIGHTS_PERSPECTIVE[persp])
    else:
        out.append(RIGHTS_PERSPECTIVE['netral'][0])
    seen, uniq = set(), []
    for q in out:
        if q not in seen:
            seen.add(q)
            uniq.append(q)
    return uniq[:6]


def _now(ctx):
    n = getattr(ctx, 'now', None)
    return n if isinstance(n, str) and n else now_iso()


def _envelope(lens, ctx, case_id, case, gates, retr, ev):
    gates_ok = gates['jurisdiction']['ok'] and gates['incident_date']['ok']
    clar = []
    if not gates['jurisdiction']['ok']:
        clar.append('Yurisdiksi kejadian belum tercatat. Sebutkan kode negara kejadian (mis. "ID" untuk Indonesia); analisis penerapan hukum tidak diberikan sebelum ini jelas.')
    if not gates['incident_date']['ok']:
        why = gates['incident_date']['reason']
        msg = 'Tanggal kejadian (occurred_on) belum tercatat.' if why == 'missing' else (
            'Tanggal kejadian tidak dapat dibaca (format ISO date diharapkan).' if why == 'invalid' else 'Tanggal kejadian berada di masa depan; mohon konfirmasi.')
        clar.append(msg + ' Tanggal dibutuhkan karena tidak ada resolusi otomatis transisi antar rezim hukum dan currentness sumber tidak tersertifikasi.')
    if case.get('perspective') not in ('pelapor', 'terlapor', 'netral'):
        clar.append('Perspektif perkara belum tercatat (pelapor/terlapor/netral); pertanyaan hak disusun netral.')
    facts = case.get('facts') if isinstance(case.get('facts'), str) else ''
    return {
        'skill_id': lens['id'],
        'mode': 'educational_lens',
        'case_id': case_id,
        'lens': {'domain': lens['domain'], 'topic': lens['topic'],
                 'persona': 'lensa edukatif: ' + lens['topic'], 'is_licensed_expert': False},
        'gates': gates,
        'gates_ok': gates_ok,
        'clarification': clar,
        'retrieval': {'performed': retr['performed'], 'queries': retr['queries'],
                      'jurisdiction_used': retr['jurisdiction_used'],
                      'jurisdiction_assumed': retr['jurisdiction_assumed'],
                      'sources_found': len(retr['found']),
                      'sources_trusted': len(retr['found']),
                      'sources_relevant': 0,
                      'non_relevant_source_ids': [r['id'] for r in retr['found']],
                      'failed': retr['failed'],
                      'excluded_untrusted': retr['excluded_untrusted']},
        'sources_unavailable': bool(retr['failed']) and not retr['found'],
        'no_relevant_source': True,
        'citations': [],
        'reading': None,
        'reading_blocked_reason': None,
        'narrative_as_recorded': {'text': facts or None,
                                  'verification': 'unverified_user_statement',
                                  'status': 'allegation_until_verified'},
        'system_recorded_facts': {'case_fields': {'jurisdiction': gates['jurisdiction']['value'],
                                                  'occurred_on': gates['incident_date']['value'],
                                                  'perspective': case.get('perspective'),
                                                  'topic': case.get('topic')},
                                  'evidence_metadata': ev['items'],
                                  'note': 'metadata sistem; isi media tidak dibaca'},
        'allegations_note': 'Uraian pengguna diperlakukan sebagai versi/allegation yang belum terverifikasi, bukan fakta hukum yang ditetapkan. Fakta sistem terbatas pada metadata perkara dan bukti.',
        'evidence': {'available': ev['available'], 'linked_count': ev['linked_count'],
                     'items': ev['items'], 'note': ev.get('note')},
        'missing_evidence': _missing_evidence(case, ev, lens),
        'neutral_rights_questions': _rights(case, lens),
        'lawyer_referral': {'advised': True,
                            'note': 'Untuk penelaahan kasus dan langkah formal, konsultasikan advokat atau lembaga bantuan hukum.'},
        'no_verdict': {'guilt_or_innocence_concluded': False,
                       'legal_applicability_concluded': False,
                       'reasons': list(NO_VERDICT_REASONS)},
        'limitations_ref': lens['id'],
        'disclaimer': DISCLAIMER,
        'note': LENS_NOTE,
        'generated_at': _now(ctx),
    }


def _block_reason(env, anchors):
    if not env['gates_ok']:
        return 'gates_open'
    if env['sources_unavailable']:
        return 'sources_unavailable'
    if not anchors:
        return 'no_relevant_source'
    return None


def _reading_block(lens, anchors):
    citations, reading = [], []
    for score, rec, quote, kw in anchors:
        cit = _citation(rec, quote)
        citations.append(cit)
        reading.append({'source_id': rec['id'], 'topic_keyword': kw, 'quote': quote,
                        'article_refs_in_quote': _refs_in(quote),
                        'reading_prompt': lens['prompts'][len(reading) % len(lens['prompts'])],
                        'is_conclusion': False})
    return citations, reading


def _apply_reading(env, lens, anchors, found):
    reason = _block_reason(env, anchors)
    used = {a[1]['id'] for a in anchors}
    env['retrieval']['non_relevant_source_ids'] = [r['id'] for r in found if r['id'] not in used]
    env['retrieval']['sources_relevant'] = len(used)
    if reason is None:
        citations, reading = _reading_block(lens, anchors)
        env['citations'] = citations
        env['reading'] = reading
        env['no_relevant_source'] = False
    else:
        env['reading_blocked_reason'] = reason
        env['no_relevant_source'] = reason == 'no_relevant_source'
    return env


def _non_relevant(found, anchors):
    used = {a[1]['id'] for a in anchors}
    return [r['id'] for r in found if r['id'] not in used]


def _h_assess(lens, ctx, args):
    case_id, case = _load_case(ctx, args)
    gates = _gate_state(case)
    jur = gates['jurisdiction']['value'] or 'ID'
    retr = _do_retrieve(ctx, _lens_queries(lens, case), jur, gates['jurisdiction']['value'] is None)
    ev = _evidence_info(ctx, case_id)
    anchors = _anchors(lens, retr['found'])
    env = _envelope(lens, ctx, case_id, case, gates, retr, ev)
    _apply_reading(env, lens, anchors, retr['found'])
    if args.get('question'):
        env['user_question'] = args['question']
    return env


def _h_sifat(lens, ctx, args):
    env = _h_assess(lens, ctx, args)
    env['sifat_perbuatan'] = {'decision_given': False,
                              'note': 'Kesengajaan/kelalaian tidak diputuskan di sini; gunakan reading_prompt untuk menyiapkan pertanyaan bagi advokat.'}
    return env


def _h_unsur(lens, ctx, args):
    case_id, case = _load_case(ctx, args)
    gates = _gate_state(case)
    jur = gates['jurisdiction']['value'] or 'ID'
    retr = _do_retrieve(ctx, _lens_queries(lens, case), jur, gates['jurisdiction']['value'] is None)
    ev = _evidence_info(ctx, case_id)
    src = None
    if args.get('source_id'):
        sid = identifier(args['source_id'])
        cand = ctx.sources.get(sid)
        require(_trusted(cand), 'source_not_trusted', 400)
        src = cand
    else:
        anchors = _anchors(lens, retr['found'])
        if anchors:
            src = anchors[0][1]
    env = _envelope(lens, ctx, case_id, case, gates, retr, ev)
    if not env['gates_ok']:
        env['reading_blocked_reason'] = 'gates_open'
        env['no_relevant_source'] = not retr['found']
        return env
    if src is None:
        env['reading_blocked_reason'] = 'sources_unavailable' if env['sources_unavailable'] else 'no_relevant_source'
        env['no_relevant_source'] = not env['sources_unavailable']
        return env
    quote = None
    for kw in lens['queries']:
        quote = _passage(src['body'], kw)
        if quote:
            break
    if not quote:
        quote = src['body'][:500].strip()
    fragments = []
    for i, frag in enumerate(x for x in _re.split(r'(?<=[.!?])\s+', quote) if len(x.strip()) >= 20):
        if i >= 6:
            break
        require(frag in src['body'], 'fragment_must_be_exact_substring', 500)
        fragments.append({'index': i, 'quote': frag, 'reading_question': lens['prompts'][i % len(lens['prompts'])]})
    env['citations'] = [_citation(src, quote)]
    env['reading'] = [{'source_id': src['id'], 'topic_keyword': None, 'quote': quote,
                       'article_refs_in_quote': _refs_in(quote),
                       'reading_prompt': lens['prompts'][0], 'is_conclusion': False}]
    env['fragments'] = fragments
    env['breakdown_source'] = {'source_id': src['id'], 'title': src['title'],
                               'note': 'fragmen mekanis dari kutipan; pemetaan unsur oleh advokat'}
    env['no_relevant_source'] = False
    if args.get('question'):
        env['user_question'] = args['question']
    return env


def _h_bukti(lens, ctx, args):
    case_id, case = _load_case(ctx, args)
    gates = _gate_state(case)
    jur = gates['jurisdiction']['value'] or 'ID'
    retr = _do_retrieve(ctx, _lens_queries(lens, case), jur, gates['jurisdiction']['value'] is None)
    ev = _evidence_info(ctx, case_id)
    envap = getattr(ctx, 'evidence', None)
    if envap is None or not hasattr(envap, 'read'):
        raise DomainError('evidence_api_unavailable', 501)
    fid = identifier(args['evidence_file_id'])
    meta, _data = envap.read(fid)
    require(isinstance(meta, dict), 'evidence_record_invalid', 500)
    if meta.get('case_id') != case_id:
        raise DomainError('evidence_not_linked_to_case', 403)
    anchors = _anchors(lens, retr['found'])
    env = _envelope(lens, ctx, case_id, case, gates, retr, ev)
    _apply_reading(env, lens, anchors, retr['found'])
    env['media'] = {'file_id': meta.get('file_id', fid),
                    'name': (meta.get('name') or '')[:200],
                    'mime_type': meta.get('mime_type'),
                    'size_bytes': meta.get('size_bytes'),
                    'sha256': meta.get('sha256'),
                    'uploaded_at': meta.get('uploaded_at'),
                    'encryption': meta.get('encryption', 'AES-256-GCM'),
                    'authenticity': meta.get('authenticity', 'not_verified'),
                    'contents_seen': False,
                    'content_analysis': 'not_performed',
                    'analysis_performed': False,
                    'degraded_reasons': ['analyzer OCR/ASR/VLM tidak dikonfigurasi pada modul ini',
                                         'modul domain tidak boleh memanggil alat eksternal'],
                    'note': 'media dibaca metadata-only; isi media tidak dilihat dan tidak disimpulkan'}
    env['missing_evidence'] = env['missing_evidence'] + [
        'Simpan berkas asli utuh (bukan konversi) beserta hash-nya.',
        'Bila ada, minta log server/operator melalui jalur resmi.']
    return env


def _routing_block(lens, anchors):
    rt = lens['routing'] or {}
    refs = []
    dates = []
    for _, rec, quote, _kw in anchors:
        for ref in _refs_in(quote):
            if ref not in refs:
                refs.append(ref)
        for d in _dates_in(quote):
            if d not in dates:
                dates.append(d)
    block = {'identifier': lens['id'],
             'label_claim': rt.get('label_claim'),
             'claim_status': 'untrusted_routing_label',
             'label_meaning_asserted': False,
             'routing_article_ref': rt.get('routing_article_ref') or rt.get('label_articles'),
             'article_refs_in_quotes': refs,
             'note': 'Nomor pasal di atas hanya yang tampak dalam kutipan sumber terpercaya; label ID tidak diassert benar.'}
    return block, refs, dates


def _h_routing(lens, ctx, args):
    case_id, case = _load_case(ctx, args)
    gates = _gate_state(case)
    jur = gates['jurisdiction']['value'] or 'ID'
    retr = _do_retrieve(ctx, _lens_queries(lens, case), jur, gates['jurisdiction']['value'] is None)
    ev = _evidence_info(ctx, case_id)
    anchors = _anchors(lens, retr['found'])
    env = _envelope(lens, ctx, case_id, case, gates, retr, ev)
    block, refs, dates = _routing_block(lens, anchors)
    env['routing'] = block
    if lens['domain'] == 'kuhp':
        env['transition'] = {'automatic_resolution': False,
                             'label_articles': lens['routing'].get('label_articles'),
                             'label_articles_asserted_as_current_law': False,
                             'dates_appearing_in_quotes': dates,
                             'effective_date_certified': False,
                             'note': 'Tidak ada resolusi otomatis KUHP lama -> KUHP baru; tanggal efektif hanya dilaporkan bila muncul dalam kutipan dan tetap tidak tersertifikasi.'}
    if lens['transition']:
        env['transition']['kuhp_baru_note'] = 'Klaim "efektif 2026" pada label tidak diassert; periksa kutipan resmi dan konfirmasikan ke advokat.'
    _apply_reading(env, lens, anchors, retr['found'])
    if args.get('question'):
        env['user_question'] = args['question']
    return env


def _h_opinion(lens, ctx, args):
    case_id, case = _load_case(ctx, args)
    gates = _gate_state(case)
    jur = gates['jurisdiction']['value'] or 'ID'
    retr = _do_retrieve(ctx, _lens_queries(lens, case), jur, gates['jurisdiction']['value'] is None)
    ev = _evidence_info(ctx, case_id)
    anchors = _anchors(lens, retr['found'])
    env = _envelope(lens, ctx, case_id, case, gates, retr, ev)
    _apply_reading(env, lens, anchors, retr['found'])
    sections = [
        {'heading': 'Sifat draf', 'content': 'Draf opini edukatif yang disusun otomatis dari kutipan sumber terpercaya dan isian perkara. Penyusunnya bukan advokat dan draf ini bukan pendapat hukum formal.'},
        {'heading': 'Ringkasan perkara', 'content': 'Yurisdiksi: %s. Tanggal kejadian: %s. Perspektif: %s. Topik: %s.' % (
            env['gates']['jurisdiction']['value'] or '(belum diisi)',
            env['gates']['incident_date']['value'] or '(belum diisi)',
            case.get('perspective') or '(belum diisi)',
            case.get('topic') or '(belum diisi)')},
        {'heading': 'Fakta sistem vs narasi', 'content': env['allegations_note']},
        {'heading': 'Narasi pengguna (belum terverifikasi)', 'content': (case.get('facts') or '(kosong)')[:4000]},
        {'heading': 'Kutipan sumber dan pembacaan', 'content': 'Lihat citations/reading: %d kutipan.' % len(env['citations'])},
        {'heading': 'Klarifikasi yang dibutuhkan', 'content': ' '.join(env['clarification']) or '(tidak ada)'},
        {'heading': 'Bukti yang belum ada', 'content': ' '.join(env['missing_evidence'])},
        {'heading': 'Pertanyaan hak yang netral', 'content': ' '.join(env['neutral_rights_questions'])},
        {'heading': 'Batasan', 'content': 'Lihat LIMITATIONS[%s] pada modul. %s' % (lens['id'], LENS_NOTE)},
        {'heading': 'Rujukan advokat', 'content': env['lawyer_referral']['note']},
        {'heading': 'Disclaimer', 'content': DISCLAIMER},
    ]
    env['draft_opinion'] = {'author': {'name': 'Harmonika Educational Legal Engine',
                                       'kind': 'automated_educational_draft',
                                       'is_licensed_lawyer': False,
                                       'impersonates_lawyer': False},
                            'structure': 'dari kutipan sumber aktual dan fakta perkara',
                            'sections': sections}
    if args.get('question'):
        env['user_question'] = args['question']
    return env


_ARCH = {'assess': _h_assess, 'sifat': _h_sifat, 'unsur': _h_unsur,
         'bukti': _h_bukti, 'routing': _h_routing, 'opinion': _h_opinion}


def _make_handler(lens):
    fn = _ARCH[lens['archetype']]

    def handler(ctx, args):
        validate(args, SCHEMAS[lens['id']])
        return fn(lens, ctx, args)
    handler.__name__ = 'handler_' + lens['id'].replace('.', '_')
    return handler


HANDLERS = dict((l['id'], _make_handler(l)) for l in LENSES)
MUTATIONS = set()
