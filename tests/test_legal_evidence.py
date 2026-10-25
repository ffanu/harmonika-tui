import hashlib
import importlib.util
import io
import os
import struct
import sys
import unittest
import zipfile
import zlib

WORKTREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_REPO = WORKTREE
if not os.path.isdir(os.path.join(ROOT_REPO, 'backend', 'legal_engine')):
    raise unittest.SkipTest('root backend/legal_engine not found at %s' % ROOT_REPO)
if ROOT_REPO not in sys.path:
    sys.path.insert(0, ROOT_REPO)
from backend.legal_engine import common as C

MODULE_PATH = os.path.join(WORKTREE, 'backend', 'legal_engine', 'evidence.py')
_spec = importlib.util.spec_from_file_location('harmonika_legal_evidence_under_test', MODULE_PATH)
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)

EVIDENCE_IDS = [
    'evidence.image.upload',
    'evidence.video.upload',
    'evidence.voice.upload',
    'evidence.document.upload',
    'evidence.ocr.extract',
    'evidence.vlm.analyze',
    'evidence.asr.transcribe',
    'evidence.document.parse',
    'evidence.metadata.extract',
    'evidence.authenticity.check',
    'evidence.chain.custody',
    'evidence.categorize',
    'evidence.relevance.assess',
    'evidence.store.secure',
    'evidence.privacy.redact',
]


class FakeCtx:
    def __init__(self, case_id='case-1'):
        self.records = {}
        self.audit = []
        self.vault = {}
        self.tenant = 'tenant-hash-1'
        self.actor = 'api-token:42'
        self.now = '2026-10-24T00:00:00.000Z'
        self._seq = 0
        from types import SimpleNamespace
        self.evidence = SimpleNamespace(read=self._read, save_derived=self._save_derived)
        self.records['legal_case'] = {case_id: {
            'id': case_id, 'jurisdiction': 'ID', 'occurred_on': '2026-01-05',
            'perspective': 'pelapor', 'topic': 'penipuan jual beli online',
            'facts': 'terjadi dugaan penipuan pembayaran transfer', 'questions': [],
            'status': 'open',
        }}
        self.case_id = case_id

    def tick(self):
        self._seq += 1
        self.now = '2026-10-24T00:00:%02d.000Z' % (self._seq % 60)

    def _read(self, file_id):
        entry = self.vault.get(file_id)
        if entry is None:
            raise C.DomainError('not_found', 404)
        return dict(entry['meta']), entry['data']

    def _save_derived(self, case_id, name, mime_type, data, provenance):
        raise AssertionError('save_derived must never be called by evidence handlers')

    def get(self, kind, record_id):
        rec = self.records.get(kind, {}).get(record_id)
        if rec is None:
            raise C.DomainError('not_found', 404)
        return dict(rec)

    def list(self, kind, where=None, limit=1000):
        rows = [dict(r) for r in self.records.get(kind, {}).values()]
        if where:
            rows = [r for r in rows if all(r.get(k) == v for k, v in where.items())]
        rows.sort(key=lambda r: (r.get('sequence', 0), r.get('created_at', ''), r.get('id', '')))
        if len(rows) > limit:
            raise C.DomainError('result_limit_exceeded', 413)
        return rows

    def create(self, kind, data, id=None):
        reserved = {'id', 'version', 'created_at', 'updated_at', 'actor', 'tenant'}
        bad = reserved & set(data)
        if bad:
            raise C.DomainError('reserved_fields_supplied', 400)
        rid = id or '%s-%d' % (kind, len(self.records.get(kind, {})) + 1)
        if rid in self.records.get(kind, {}):
            raise C.DomainError('already_exists', 409)
        self.tick()
        rec = {'id': rid, 'version': 1, 'created_at': self.now, 'updated_at': self.now,
               'actor': self.actor, 'tenant': self.tenant}
        rec.update(data)
        self.records.setdefault(kind, {})[rid] = rec
        return dict(rec)

    def update(self, kind, record_id, changes, expected_version=None):
        rec = self.records.get(kind, {}).get(record_id)
        if rec is None:
            raise C.DomainError('not_found', 404)
        if expected_version is not None and rec['version'] != expected_version:
            raise C.DomainError('version_conflict', 409)
        self.tick()
        rec.update(changes)
        rec['version'] += 1
        rec['updated_at'] = self.now
        return dict(rec)

    def event(self, kind, data):
        self.audit.append((kind, dict(data)))

    def add_vault_file(self, file_id, data, case_id=None, name='bukti.bin',
                       encryption='AES-256-GCM', declared_mime=None):
        meta = {'file_id': file_id, 'case_id': case_id or self.case_id,
                'tenant': self.tenant, 'name': name, 'size_bytes': len(data)}
        if encryption is not None:
            meta['encryption'] = encryption
        if declared_mime:
            meta['mime_type'] = declared_mime
        self.vault[file_id] = {'meta': meta, 'data': data}


def png_bytes(w=3, h=2):
    def chunk(typ, payload):
        return (struct.pack('>I', len(payload)) + typ + payload
                + struct.pack('>I', zlib.crc32(typ + payload) & 0xFFFFFFFF))
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    raw = b''.join(b'\x00' + b'\x11\x22\x33' * w for _ in range(h))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr)
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def wav_bytes(seconds=1, rate=8000):
    payload = b'\x00\x00' * (rate * seconds)
    fmt = struct.pack('<HHIIHH', 1, 1, rate, rate * 2, 2, 16)
    body = (b'WAVE' + b'fmt ' + struct.pack('<I', 16) + fmt
            + b'data' + struct.pack('<I', len(payload)) + payload)
    return b'RIFF' + struct.pack('<I', len(body)) + body


def mp3_bytes():
    return b'ID3\x04\x00\x00\x00\x00\x00\x00' + b'\xff\xfb\x90\x64' + b'\x00' * 100


def mp4_bytes(timescale=1000, duration=5000):
    def box(typ, payload):
        return struct.pack('>I', 8 + len(payload)) + typ + payload
    mvhd_payload = b'\x00\x00\x00\x00' + b'\x00' * 8 + struct.pack('>II', timescale, duration)
    moov = box(b'moov', box(b'mvhd', mvhd_payload))
    ftyp = box(b'ftyp', b'isom' + struct.pack('>I', 512) + b'isomiso2')
    return ftyp + moov + box(b'mdat', b'')


def build_pdf(texts):
    objs = {}
    kids = ' '.join('%d 0 R' % (4 + i * 2) for i in range(len(texts)))
    objs[1] = b'<< /Type /Catalog /Pages 2 0 R >>'
    objs[2] = ('<< /Type /Pages /Kids [%s] /Count %d >>' % (kids, len(texts))).encode()
    objs[3] = b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'
    for i, text in enumerate(texts):
        page_id = 4 + i * 2
        content_id = page_id + 1
        comp = zlib.compress(('BT /F1 12 Tf 72 720 Td (%s) Tj ET' % text).encode('latin-1'))
        objs[page_id] = ('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] '
                         '/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>' % content_id).encode()
        objs[content_id] = (b'<< /Length %d /Filter /FlateDecode >>\nstream\n' % len(comp)
                            + comp + b'\nendstream')
    out = bytearray(b'%PDF-1.4\n')
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += b'%d 0 obj\n' % num + objs[num] + b'\nendobj\n'
    xref_pos = len(out)
    max_num = max(objs)
    out += b'xref\n0 %d\n' % (max_num + 1)
    out += b'0000000000 65535 f \n'
    for num in range(1, max_num + 1):
        out += b'%010d 00000 n \n' % offsets[num]
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (max_num + 1, xref_pos)
    return bytes(out)


def build_docx(paragraphs):
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
          '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>')
    body = ''.join('<w:p><w:r><w:t>%s</w:t></w:r></w:p>' % p for p in paragraphs)
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:body>%s</w:body></w:document>' % body)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', ct)
        z.writestr('_rels/.rels', rels)
        z.writestr('word/document.xml', doc)
    return buf.getvalue()


def assert_no_bytes(value):
    if isinstance(value, bytes):
        raise AssertionError('raw bytes leaked into response')
    if isinstance(value, dict):
        for v in value.values():
            assert_no_bytes(v)
    if isinstance(value, (list, tuple)):
        for v in value:
            assert_no_bytes(v)


def has_any_key(value, keys):
    if isinstance(value, dict):
        for k, v in value.items():
            if k in keys:
                return True
            if has_any_key(v, keys):
                return True
    if isinstance(value, (list, tuple)):
        for v in value:
            if has_any_key(v, keys):
                return True
    return False


class ModuleContractTests(unittest.TestCase):
    def test_fifteen_canonical_ids_exact(self):
        self.assertEqual(sorted(ev.HANDLERS), sorted(EVIDENCE_IDS))
        self.assertEqual(sorted(ev.SCHEMAS), sorted(EVIDENCE_IDS))
        self.assertEqual(sorted(ev.LIMITATIONS), sorted(EVIDENCE_IDS))
        self.assertEqual(len(EVIDENCE_IDS), 15)
        self.assertEqual(len(set(EVIDENCE_IDS)), 15)

    def test_schemas_strict_bounded(self):
        for sid in EVIDENCE_IDS:
            schema = ev.SCHEMAS[sid]
            self.assertEqual(schema['type'], 'object', sid)
            self.assertFalse(schema['additionalProperties'], sid)
            self.assertTrue(schema['required'], sid)
            self.assertTrue(schema['properties'], sid)

    def test_mutations_cover_all_potential_writes_only(self):
        self.assertEqual(ev.MUTATIONS, frozenset({
            'evidence.image.upload', 'evidence.video.upload', 'evidence.voice.upload',
            'evidence.document.upload', 'evidence.chain.custody', 'evidence.store.secure',
        }))
        for sid in EVIDENCE_IDS:
            if sid in ev.MUTATIONS:
                continue
            self.assertIn(sid, EVIDENCE_IDS)
            self.assertNotIn(sid, ev.MUTATIONS)

    def test_limitations_explicit_strings(self):
        for sid in EVIDENCE_IDS:
            items = ev.LIMITATIONS[sid]
            self.assertIsInstance(items, list, sid)
            self.assertTrue(items, sid)
            for item in items:
                self.assertIsInstance(item, str)
                self.assertTrue(item.strip(), sid)

    def test_handler_rejects_non_dict_args(self):
        with self.assertRaises(C.DomainError):
            ev.HANDLERS['evidence.metadata.extract'](FakeCtx(), ['case-1', 'file-1'])

    def test_schemas_reject_tenant_actor_secret_fields(self):
        ctx = FakeCtx()
        for extra in ('tenant', 'actor', 'path', 'url', 'secret', 'status_verified',
                      'encrypted', 'content'):
            args = {'case_id': ctx.case_id, 'file_id': 'f1', extra: 'x'}
            with self.assertRaises(C.DomainError, msg=extra):
                C.validate(args, ev.SCHEMAS['evidence.image.upload'])


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.ctx = FakeCtx()
        self.ctx.add_vault_file('file-png-1', png_bytes(7, 5), name='bukti/snap shot.png')

    def upload(self, file_id='file-png-1', kind='image', **kw):
        args = {'case_id': self.ctx.case_id, 'file_id': file_id}
        args.update(kw)
        return ev.HANDLERS['evidence.%s.upload' % kind](self.ctx, args)

    def test_image_upload_links_ready_with_real_dims_and_hash(self):
        data = png_bytes(7, 5)
        out = self.upload()
        self.assertTrue(out['linked'])
        self.assertTrue(out['ready'])
        self.assertEqual(out['status'], 'ready')
        self.assertEqual(out['mime_type'], 'image/png')
        self.assertEqual(out['mime_source'], 'magic_bytes')
        self.assertEqual(out['width'], 7)
        self.assertEqual(out['height'], 5)
        self.assertEqual(out['sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(out['encryption'], 'AES-256-GCM')
        self.assertEqual(out['content_analysis'], 'not_performed')
        self.assertEqual(out['authenticity'], 'not_verified')
        rec = self.ctx.get('legal_evidence', out['evidence_id'])
        self.assertEqual(rec['name'], 'snap shot.png')
        self.assertEqual(rec['case_id'], 'case-1')
        self.assertEqual(rec['file_id'], 'file-png-1')
        self.assertEqual(rec['status'], 'ready')
        self.assertEqual(rec['size_bytes'], len(data))
        self.assertIn('evidence.upload', [k for k, _d in self.ctx.audit])
        assert_no_bytes(out)

    def test_upload_rejects_kind_mismatch(self):
        with self.assertRaises(C.DomainError) as cm:
            self.upload(kind='video')
        self.assertEqual(cm.exception.code, 'evidence_kind_mismatch')

    def test_upload_rejects_unknown_bytes(self):
        self.ctx.add_vault_file('file-junk', b'\x00\x01\x02garbage-not-known')
        with self.assertRaises(C.DomainError) as cm:
            self.upload(file_id='file-junk')
        self.assertEqual(cm.exception.code, 'unsupported_file_type')

    def test_upload_rejects_unknown_case(self):
        with self.assertRaises(C.DomainError) as cm:
            ev.HANDLERS['evidence.image.upload'](self.ctx, {'case_id': 'nope', 'file_id': 'file-png-1'})
        self.assertEqual(cm.exception.status, 404)

    def test_upload_rejects_file_not_in_vault(self):
        with self.assertRaises(C.DomainError) as cm:
            self.upload(file_id='file-missing')
        self.assertEqual(cm.exception.status, 404)

    def test_upload_rejects_case_mismatch(self):
        self.ctx.add_vault_file('file-other-case', png_bytes(), case_id='case-OTHER')
        with self.assertRaises(C.DomainError) as cm:
            self.upload(file_id='file-other-case')
        self.assertEqual(cm.exception.code, 'evidence_case_mismatch')
        self.assertEqual(cm.exception.status, 403)

    def test_upload_rejects_oversize(self):
        huge = b'\x89PNG\r\n\x1a\n' + b'\x00' * (ev.MAX_IMAGE_BYTES + 1)
        self.ctx.add_vault_file('file-huge', huge)
        with self.assertRaises(C.DomainError) as cm:
            self.upload(file_id='file-huge')
        self.assertEqual(cm.exception.code, 'file_too_large')
        self.assertEqual(cm.exception.status, 413)

    def test_upload_rejects_empty_file(self):
        self.ctx.add_vault_file('file-empty', b'')
        with self.assertRaises(C.DomainError) as cm:
            self.upload(file_id='file-empty')
        self.assertEqual(cm.exception.code, 'empty_file')

    def test_duplicate_upload_is_idempotent(self):
        first = self.upload()
        second = self.upload(name='renamed.png')
        self.assertTrue(second['already_registered'])
        self.assertEqual(first['evidence_id'], second['evidence_id'])
        self.assertEqual(second['name'], 'snap shot.png')
        self.assertEqual(len(self.ctx.list('legal_evidence')), 1)

    def test_name_traversal_sanitized(self):
        self.ctx.add_vault_file('file-png-2', png_bytes(), name='../../..\\etc\\passwd\x00.png')
        out = self.upload(file_id='file-png-2')
        self.assertNotIn('..', out['name'])
        self.assertNotIn('/', out['name'])
        self.assertNotIn('\\', out['name'])
        self.assertEqual(out['name'], 'passwd.png')

    def test_txt_document_upload(self):
        self.ctx.add_vault_file('file-txt', 'surat pernyataan harmonika\n'.encode())
        out = self.upload(file_id='file-txt', kind='document')
        self.assertEqual(out['mime_type'], 'text/plain')

    def test_mp3_voice_upload(self):
        self.ctx.add_vault_file('file-mp3', mp3_bytes())
        out = self.upload(file_id='file-mp3', kind='voice')
        self.assertEqual(out['mime_type'], 'audio/mpeg')
        self.assertNotIn('duration_seconds', out)

    def test_wav_voice_upload_with_duration(self):
        self.ctx.add_vault_file('file-wav', wav_bytes(2))
        out = self.upload(file_id='file-wav', kind='voice')
        self.assertEqual(out['mime_type'], 'audio/x-wav')
        self.assertEqual(out['duration_seconds'], 2.0)

    def test_mp4_video_upload_with_duration(self):
        self.ctx.add_vault_file('file-mp4', mp4_bytes())
        out = self.upload(file_id='file-mp4', kind='video')
        self.assertEqual(out['mime_type'], 'video/mp4')
        self.assertEqual(out['duration_seconds'], 5.0)

    def test_vault_declared_mime_mismatch_reported(self):
        self.ctx.add_vault_file('file-lie', png_bytes(), declared_mime='video/mp4')
        out = self.upload(file_id='file-lie')
        self.assertEqual(out['mime_type'], 'image/png')
        self.assertEqual(out['vault_declared_mime'], 'video/mp4')
        self.assertIn('magic bytes', out['note'])

    def test_webm_audio_vs_video_resolved_by_codecs(self):
        head = b'\x1a\x45\xdf\xa3' + b'\x42\x82\x88webm' + b'\x00' * 32
        self.ctx.add_vault_file('file-opus', head + b'A_OPUS' + b'\x00' * 64)
        out = self.upload(file_id='file-opus', kind='voice')
        self.assertEqual(out['mime_type'], 'audio/webm')
        self.ctx.add_vault_file('file-vp9', head + b'V_VP9' + b'\x00' * 64)
        with self.assertRaises(C.DomainError) as cm:
            self.upload(file_id='file-vp9', kind='voice')
        self.assertEqual(cm.exception.code, 'evidence_kind_mismatch')


class UnavailableAnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.ctx = FakeCtx()
        self.ctx.add_vault_file('file-png', png_bytes())
        self.ctx.add_vault_file('file-wav', wav_bytes())
        self.ctx.add_vault_file('file-pdf', build_pdf(['teks']))
        ev.HANDLERS['evidence.image.upload'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-png'})
        ev.HANDLERS['evidence.voice.upload'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-wav'})
        ev.HANDLERS['evidence.document.upload'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-pdf'})

    def test_ocr_unavailable_on_image(self):
        out = ev.HANDLERS['evidence.ocr.extract'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-png'})
        self.assertFalse(out['available'])
        self.assertFalse(out['ready'])
        self.assertFalse(out['analysis_performed'])
        self.assertEqual(out['status'], 'unavailable')
        self.assertEqual(out['reason'], 'ocr_engine_not_configured_by_server')
        self.assertNotIn('text', out)
        assert_no_bytes(out)

    def test_ocr_rejects_non_image(self):
        with self.assertRaises(C.DomainError) as cm:
            ev.HANDLERS['evidence.ocr.extract'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-pdf'})
        self.assertEqual(cm.exception.code, 'evidence_kind_not_supported')

    def test_vlm_unavailable_on_image(self):
        out = ev.HANDLERS['evidence.vlm.analyze'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-png'})
        self.assertFalse(out['analysis_performed'])
        self.assertEqual(out['reason'], 'vlm_engine_not_configured_by_server')

    def test_vlm_rejects_document(self):
        with self.assertRaises(C.DomainError):
            ev.HANDLERS['evidence.vlm.analyze'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-pdf'})

    def test_asr_unavailable_on_voice(self):
        out = ev.HANDLERS['evidence.asr.transcribe'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-wav'})
        self.assertFalse(out['available'])
        self.assertFalse(out['analysis_performed'])
        self.assertEqual(out['reason'], 'asr_engine_not_configured_by_server')
        self.assertNotIn('transcript', out)

    def test_analyzers_require_linked_evidence(self):
        self.ctx.add_vault_file('file-orphan', png_bytes())
        for sid in ('evidence.ocr.extract', 'evidence.vlm.analyze', 'evidence.asr.transcribe'):
            with self.assertRaises(C.DomainError) as cm:
                ev.HANDLERS[sid](self.ctx, {'case_id': 'case-1', 'file_id': 'file-orphan'})
            self.assertEqual(cm.exception.code, 'evidence_not_linked', sid)


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.ctx = FakeCtx()
        self.ctx.add_vault_file('file-pdf', build_pdf([
            'Kontrak kerja sama harmonika halaman pertama',
            'Isi halaman kedua bukti dokumen',
        ]))
        ev.HANDLERS['evidence.document.upload'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-pdf'})

    def test_pdf_parse_extracts_real_text(self):
        out = ev.HANDLERS['evidence.document.parse'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-pdf'})
        self.assertEqual(out['status'], 'parsed')
        self.assertIn(out['method'], ('pypdf', 'stdlib_pdf_literal_strings'))
        self.assertTrue(out['text_present'])
        self.assertIn('Kontrak kerja sama harmonika', out['text'])
        self.assertEqual(out['page_count_detected'], 2)
        self.assertFalse(out['text_truncated'])
        self.assertFalse(out['deadline_exceeded'])
        self.assertTrue(out['analysis_performed'])
        assert_no_bytes(out)

    def test_pdf_page_cap_truncates_honestly(self):
        out = ev.HANDLERS['evidence.document.parse'](self.ctx, {
            'case_id': 'case-1', 'file_id': 'file-pdf', 'max_pages': 1})
        if out['method'] == 'pypdf':
            self.assertEqual(out['status'], 'partial')
            self.assertTrue(out['text_truncated'])
            self.assertNotIn('halaman kedua', out['text'])

    def test_parse_caps_enforced_by_schema(self):
        with self.assertRaises(C.DomainError):
            ev.HANDLERS['evidence.document.parse'](self.ctx, {
                'case_id': 'case-1', 'file_id': 'file-pdf', 'max_pages': 0})
        with self.assertRaises(C.DomainError):
            ev.HANDLERS['evidence.document.parse'](self.ctx, {
                'case_id': 'case-1', 'file_id': 'file-pdf', 'timeout_seconds': 99})

    def test_docx_parse_extracts_paragraphs(self):
        self.ctx.add_vault_file('file-docx', build_docx(['Surat harmonika halaman satu', 'Baris kedua bukti']))
        ev.HANDLERS['evidence.document.upload'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-docx'})
        out = ev.HANDLERS['evidence.document.parse'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-docx'})
        self.assertEqual(out['method'], 'stdlib_docx_xml')
        self.assertEqual(out['status'], 'parsed')
        self.assertIn('Surat harmonika halaman satu', out['text'])
        self.assertIn('Baris kedua bukti', out['text'])

    def test_txt_parse(self):
        self.ctx.add_vault_file('file-txt', 'pernyataan tertulis harmonika'.encode())
        ev.HANDLERS['evidence.document.upload'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-txt'})
        out = ev.HANDLERS['evidence.document.parse'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-txt'})
        self.assertEqual(out['method'], 'plain_text_utf8')
        self.assertIn('pernyataan tertulis', out['text'])

    def test_zip_bomb_docx_rejected(self):
        bomb = io.BytesIO()
        with zipfile.ZipFile(bomb, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('[Content_Types].xml', '<Types/>')
            z.writestr('word/document.xml', b'0' * (40 * 1024 * 1024))
        self.ctx.add_vault_file('file-bomb', bomb.getvalue())
        ev.HANDLERS['evidence.document.upload'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-bomb'})
        with self.assertRaises(C.DomainError) as cm:
            ev.HANDLERS['evidence.document.parse'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-bomb'})
        self.assertIn(cm.exception.code, ('zip_entry_too_large', 'zip_total_exceeded', 'zip_ratio_exceeded'))
        self.assertEqual(cm.exception.status, 413)

    def test_msword_cfb_unsupported_honestly(self):
        self.ctx.add_vault_file('file-doc', b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\x00' * 64)
        ev.HANDLERS['evidence.document.upload'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-doc'})
        out = ev.HANDLERS['evidence.document.parse'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-doc'})
        self.assertEqual(out['status'], 'unsupported')
        self.assertFalse(out['analysis_performed'])
        self.assertEqual(out['text'], '')
        self.assertEqual(out['reason'], 'legacy_binary_word_not_supported')

    def test_parse_detects_bytes_changed_after_registration(self):
        self.ctx.vault['file-pdf']['data'] = build_pdf(['dirubah setelah daftar'])
        with self.assertRaises(C.DomainError) as cm:
            ev.HANDLERS['evidence.document.parse'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-pdf'})
        self.assertEqual(cm.exception.code, 'evidence_bytes_changed')

    def test_parse_requires_linked_evidence(self):
        self.ctx.add_vault_file('file-orphan', build_pdf(['x']))
        with self.assertRaises(C.DomainError) as cm:
            ev.HANDLERS['evidence.document.parse'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-orphan'})
        self.assertEqual(cm.exception.code, 'evidence_not_linked')


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.ctx = FakeCtx()

    def upload_png(self):
        self.ctx.add_vault_file('file-png', png_bytes(11, 4))
        return ev.HANDLERS['evidence.image.upload'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-png'})

    def test_png_dims_and_sha_from_actual_bytes(self):
        up = self.upload_png()
        out = ev.HANDLERS['evidence.metadata.extract'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-png'})
        self.assertEqual(out['dimensions'], {'width': 11, 'height': 4})
        self.assertEqual(out['sha256'], up['sha256'])
        self.assertTrue(out['recorded_sha256_matches_bytes'])
        self.assertEqual(out['detected_mime'], 'image/png')

    def test_jpeg_dims_and_exif_marker_flag(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow absent')
        plain = io.BytesIO()
        Image.new('RGB', (6, 9), 'red').save(plain, format='JPEG')
        with_exif = io.BytesIO()
        img = Image.new('RGB', (6, 9), 'blue')
        exif = img.getexif()
        exif[0x010F] = 'TestCam'
        img.save(with_exif, format='JPEG', exif=exif.tobytes())
        self.ctx.add_vault_file('file-jpg', plain.getvalue())
        self.ctx.add_vault_file('file-jpg-exif', with_exif.getvalue())
        ev.HANDLERS['evidence.image.upload'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-jpg'})
        ev.HANDLERS['evidence.image.upload'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-jpg-exif'})
        plain_out = ev.HANDLERS['evidence.metadata.extract'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-jpg'})
        exif_out = ev.HANDLERS['evidence.metadata.extract'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-jpg-exif'})
        self.assertEqual(plain_out['dimensions'], {'width': 6, 'height': 9})
        self.assertFalse(plain_out['markers']['jpeg_exif_app1_marker_present'])
        self.assertTrue(exif_out['markers']['jpeg_exif_app1_marker_present'])
        self.assertNotIn('device_info', exif_out)
        self.assertNotIn('gps', exif_out)

    def test_no_device_gps_time_claims_ever(self):
        self.upload_png()
        out = ev.HANDLERS['evidence.metadata.extract'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-png'})
        banned = {'exif', 'gps', 'device', 'device_info', 'capture_time', 'location',
                  'camera', 'make', 'model', 'latitude', 'longitude'}
        self.assertFalse(has_any_key(out, banned))
        self.assertIn('gps_location', out['not_extracted'])
        self.assertIn('device_info', out['not_extracted'])

    def test_hash_mismatch_flag_on_tampered_vault_bytes(self):
        self.upload_png()
        self.ctx.vault['file-png']['data'] = png_bytes(12, 4)
        out = ev.HANDLERS['evidence.metadata.extract'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-png'})
        self.assertFalse(out['recorded_sha256_matches_bytes'])

    def test_wav_duration_reported(self):
        self.ctx.add_vault_file('file-wav', wav_bytes(3))
        ev.HANDLERS['evidence.voice.upload'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-wav'})
        out = ev.HANDLERS['evidence.metadata.extract'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-wav'})
        self.assertEqual(out['duration_seconds'], 3.0)


class AuthenticityTests(unittest.TestCase):
    def setUp(self):
        self.ctx = FakeCtx()
        self.ctx.add_vault_file('file-pdf', build_pdf(['isi dokumen harmonika']))
        ev.HANDLERS['evidence.document.upload'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-pdf'})

    def test_pdf_consistent_basic_verdict(self):
        out = ev.HANDLERS['evidence.authenticity.check'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-pdf'})
        self.assertEqual(out['verdict'], 'basic_container_checks_consistent')
        checks = {c['check']: c['result'] for c in out['checks']}
        self.assertTrue(checks['recorded_sha256_matches_actual_bytes'])
        self.assertTrue(checks['container_magic_matches_recorded_mime'])
        self.assertTrue(checks['pdf_eof_marker_present'])

    def test_docx_zip_crc_check(self):
        self.ctx.add_vault_file('file-docx', build_docx(['paragraf uji']))
        ev.HANDLERS['evidence.document.upload'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-docx'})
        out = ev.HANDLERS['evidence.authenticity.check'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-docx'})
        checks = {c['check']: c['result'] for c in out['checks']}
        self.assertTrue(checks['zip_structure_and_crc_ok'])

    def test_tampered_bytes_fail(self):
        self.ctx.vault['file-pdf']['data'] = build_pdf(['isi sudah diubah'])
        out = ev.HANDLERS['evidence.authenticity.check'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-pdf'})
        self.assertEqual(out['verdict'], 'basic_container_checks_failed')
        checks = {c['check']: c['result'] for c in out['checks']}
        self.assertFalse(checks['recorded_sha256_matches_actual_bytes'])

    def test_no_forensic_or_admissibility_claims(self):
        out = ev.HANDLERS['evidence.authenticity.check'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-pdf'})
        self.assertFalse(out['forensic_analysis_performed'])
        self.assertFalse(out['legal_admissibility_assessed'])
        self.assertIn('bukan penilaian forensik', out['notice'])
        self.assertIn('bukan', out['notice'])
        assert_no_bytes(out)


class CustodyTests(unittest.TestCase):
    def setUp(self):
        self.ctx = FakeCtx()
        self.ctx.add_vault_file('file-png', png_bytes())
        ev.HANDLERS['evidence.image.upload'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-png'})

    def append(self, note=None):
        args = {'case_id': 'case-1', 'file_id': 'file-png', 'action': 'append'}
        if note:
            args['note'] = note
        return ev.HANDLERS['evidence.chain.custody'](self.ctx, args)

    def view(self):
        return ev.HANDLERS['evidence.chain.custody'](self.ctx, {'case_id': 'case-1', 'file_id': 'file-png', 'action': 'view'})

    def test_append_builds_hash_chain(self):
        first = self.append('bukti diunggah')
        self.assertEqual(first['sequence'], 1)
        self.assertEqual(first['prev_hash'], '0' * 64)
        second = self.append('diverifikasi petugas')
        self.assertEqual(second['prev_hash'], first['entry_hash'])
        self.assertEqual(second['chain_length'], 2)
        entries = self.ctx.list('legal_custody_event')
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]['actor'], 'api-token:42')
        self.assertIn('evidence.chain.custody', [k for k, _d in self.ctx.audit])

    def test_view_verifies_valid_chain(self):
        last = None
        for i in range(3):
            last = self.append('kejadian %d' % i)
        out = self.view()
        self.assertTrue(out['chain_valid'])
        self.assertIsNone(out['broken_at'])
        self.assertEqual(out['entries_count'], 3)
        self.assertEqual(out['head_hash'], last['entry_hash'])

    def test_view_detects_tampering(self):
        self.append('a')
        second = self.append('b')
        rec = self.ctx.records['legal_custody_event'][second['entry_id']]
        rec['note'] = 'dicatut diubah'
        out = self.view()
        self.assertFalse(out['chain_valid'])
        self.assertEqual(out['broken_at'], 2)

    def test_view_detects_sequence_gap(self):
        self.append('satu')
        recs = self.ctx.list('legal_custody_event')
        self.ctx.records['legal_custody_event'][recs[0]['id']]['sequence'] = 5
        out = self.view()
        self.assertFalse(out['chain_valid'])
        self.assertEqual(out['broken_at'], 5)

    def test_append_requires_linked_evidence(self):
        self.ctx.add_vault_file('file-orphan', png_bytes())
        with self.assertRaises(C.DomainError) as cm:
            ev.HANDLERS['evidence.chain.custody'](self.ctx, {
                'case_id': 'case-1', 'file_id': 'file-orphan', 'action': 'append'})
        self.assertEqual(cm.exception.code, 'evidence_not_linked')

    def test_view_empty_chain_is_valid_genesis(self):
        out = self.view()
        self.assertTrue(out['chain_valid'])
        self.assertEqual(out['entries_count'], 0)
        self.assertEqual(out['head_hash'], '0' * 64)

    def test_notice_disclaims_certification(self):
        out = self.append('x')
        self.assertIn('bukan chain of custody tersertifikasi', out['notice'])
        v = self.view()
        self.assertIn('bukan chain of custody tersertifikasi', v['notice'])
        self.assertIn(ev.DISCLAIMER, out['disclaimer'])

    def test_action_enum_enforced(self):
        with self.assertRaises(C.DomainError):
            ev.HANDLERS['evidence.chain.custody'](self.ctx, {
                'case_id': 'case-1', 'file_id': 'file-png', 'action': 'wipe'})


class CategorizeTests(unittest.TestCase):
    def categorize(self, ctx, file_id):
        return ev.HANDLERS['evidence.categorize'](ctx, {'case_id': 'case-1', 'file_id': file_id})

    def test_image_category_digital_media(self):
        ctx = FakeCtx()
        ctx.add_vault_file('file-png', png_bytes())
        ev.HANDLERS['evidence.image.upload'](ctx, {'case_id': 'case-1', 'file_id': 'file-png'})
        out = self.categorize(ctx, 'file-png')
        self.assertEqual(out['category'], 'digital_media')
        self.assertEqual(out['media_class'], 'image')
        self.assertEqual(out['basis'], 'detected_container_and_case_linkage_only')
        self.assertIn('vault_encrypted_attested', out['tags'])

    def test_pdf_category_digital_document(self):
        ctx = FakeCtx()
        ctx.add_vault_file('file-pdf', build_pdf(['teks']))
        ev.HANDLERS['evidence.document.upload'](ctx, {'case_id': 'case-1', 'file_id': 'file-pdf'})
        out = self.categorize(ctx, 'file-pdf')
        self.assertEqual(out['category'], 'digital_document')
        self.assertIn('text_extractable_container', out['tags'])

    def test_encrypted_tag_requires_vault_attestation(self):
        ctx = FakeCtx()
        ctx.add_vault_file('file-png', png_bytes(), encryption=None)
        ev.HANDLERS['evidence.image.upload'](ctx, {'case_id': 'case-1', 'file_id': 'file-png'})
        out = self.categorize(ctx, 'file-png')
        self.assertNotIn('vault_encrypted_attested', out['tags'])

    def test_no_testimonial_or_admissibility_category(self):
        ctx = FakeCtx()
        ctx.add_vault_file('file-png', png_bytes())
        ev.HANDLERS['evidence.image.upload'](ctx, {'case_id': 'case-1', 'file_id': 'file-png'})
        out = self.categorize(ctx, 'file-png')
        for word in ('saksi', 'testimonial', 'admissible', 'bukti sah'):
            self.assertNotIn(word, str(out).lower())


class RelevanceTests(unittest.TestCase):
    def test_lexical_overlap_supported(self):
        ctx = FakeCtx()
        ctx.records['legal_case']['case-1']['topic'] = 'penganiayaan rumah tangga'
        ctx.records['legal_case']['case-1']['facts'] = 'terjadi penganiayaan berulang di rumah'
        ctx.add_vault_file('file-pdf', build_pdf(['penganiayaan rumah tangga terjadi berulang']))
        ev.HANDLERS['evidence.document.upload'](ctx, {'case_id': 'case-1', 'file_id': 'file-pdf'})
        out = ev.HANDLERS['evidence.relevance.assess'](ctx, {'case_id': 'case-1', 'file_id': 'file-pdf'})
        self.assertTrue(out['assessment_performed'])
        self.assertEqual(out['method'], 'lexical_keyword_overlap')
        terms = {m['term'] for m in out['matched_terms']}
        self.assertIn('penganiayaan', terms)
        self.assertIn('rumah', terms)
        self.assertGreater(out['lexical_coverage'], 0)
        self.assertIn('bukan penilaian relevansi pembuktian', out['notice'])

    def test_non_text_media_not_assessed(self):
        ctx = FakeCtx()
        ctx.add_vault_file('file-png', png_bytes())
        ev.HANDLERS['evidence.image.upload'](ctx, {'case_id': 'case-1', 'file_id': 'file-png'})
        out = ev.HANDLERS['evidence.relevance.assess'](ctx, {'case_id': 'case-1', 'file_id': 'file-png'})
        self.assertFalse(out['assessment_performed'])
        self.assertEqual(out['reason'], 'evidence_has_no_extractable_text')

    def test_no_text_extracted_is_honest(self):
        ctx = FakeCtx()
        ctx.add_vault_file('file-doc', b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\x00' * 64)
        ev.HANDLERS['evidence.document.upload'](ctx, {'case_id': 'case-1', 'file_id': 'file-doc'})
        out = ev.HANDLERS['evidence.relevance.assess'](ctx, {'case_id': 'case-1', 'file_id': 'file-doc'})
        self.assertFalse(out['assessment_performed'])
        self.assertEqual(out['reason'], 'no_text_extracted_from_container')


class StoreSecureTests(unittest.TestCase):
    def upload(self, ctx, encryption='AES-256-GCM'):
        ctx.add_vault_file('file-png', png_bytes(), encryption=encryption)
        return ev.HANDLERS['evidence.image.upload'](ctx, {'case_id': 'case-1', 'file_id': 'file-png'})

    def test_secured_with_real_vault_attestation(self):
        ctx = FakeCtx()
        up = self.upload(ctx)
        out = ev.HANDLERS['evidence.store.secure'](ctx, {'case_id': 'case-1', 'file_id': 'file-png'})
        self.assertTrue(out['secured'])
        self.assertEqual(out['verification']['algorithm'], 'AES-256-GCM')
        self.assertEqual(out['verification']['attested_by'], 'server_vault_metadata')
        rec = ctx.get('legal_evidence', up['evidence_id'])
        self.assertEqual(rec['storage_verification']['sha256'], up['sha256'])
        self.assertIn('evidence.store.secure', [k for k, _d in ctx.audit])

    def test_unattested_vault_returns_secured_false_without_changes(self):
        ctx = FakeCtx()
        up = self.upload(ctx, encryption=None)
        before = ctx.get('legal_evidence', up['evidence_id'])
        out = ev.HANDLERS['evidence.store.secure'](ctx, {'case_id': 'case-1', 'file_id': 'file-png'})
        self.assertFalse(out['secured'])
        self.assertEqual(out['reason'], 'vault_encryption_not_attested')
        self.assertTrue(out['no_changes_made'])
        after = ctx.get('legal_evidence', up['evidence_id'])
        self.assertEqual(before, after)

    def test_caller_encrypted_flag_rejected(self):
        ctx = FakeCtx()
        self.upload(ctx)
        with self.assertRaises(C.DomainError) as cm:
            ev.HANDLERS['evidence.store.secure'](ctx, {
                'case_id': 'case-1', 'file_id': 'file-png', 'encrypted': True})
        self.assertEqual(cm.exception.code, 'unknown_fields')


class RedactTests(unittest.TestCase):
    def test_text_redaction_masks_pii(self):
        ctx = FakeCtx()
        text = ('Pernyataan: NIK 3201234567890001, email saksi@example.com, '
                'telepon 081234567890, kartu 4111111111111111.')
        ctx.add_vault_file('file-txt', text.encode())
        ev.HANDLERS['evidence.document.upload'](ctx, {'case_id': 'case-1', 'file_id': 'file-txt'})
        out = ev.HANDLERS['evidence.privacy.redact'](ctx, {'case_id': 'case-1', 'file_id': 'file-txt'})
        self.assertTrue(out['supported'])
        self.assertTrue(out['redaction_performed'])
        self.assertEqual(out['transformation_applied'], 'text_masking_preview')
        findings = {f['category']: f['count'] for f in out['findings']}
        self.assertEqual(findings.get('nik'), 1)
        self.assertEqual(findings.get('email'), 1)
        self.assertEqual(findings.get('phone'), 1)
        self.assertEqual(findings.get('possible_card_number'), 1)
        preview = out['redacted_preview']
        self.assertNotIn('3201234567890001', preview)
        self.assertNotIn('saksi@example.com', preview)
        self.assertNotIn('081234567890', preview)
        self.assertNotIn('4111111111111111', preview)
        self.assertIn('[REDACTED:nik]', preview)
        self.assertFalse(out['irreversible_file_redaction_performed'])
        self.assertFalse(out['image_blackbox_applied'])
        self.assertFalse(out['original_file_modified'])
        self.assertFalse(out['derived_file_persisted'])
        assert_no_bytes(out)

    def test_pdf_redaction_uses_real_extraction(self):
        ctx = FakeCtx()
        ctx.add_vault_file('file-pdf', build_pdf(['Terlapor email terdakwa@contoh.id tercatat.']))
        ev.HANDLERS['evidence.document.upload'](ctx, {'case_id': 'case-1', 'file_id': 'file-pdf'})
        out = ev.HANDLERS['evidence.privacy.redact'](ctx, {'case_id': 'case-1', 'file_id': 'file-pdf'})
        self.assertTrue(out['redaction_performed'])
        self.assertIn('[REDACTED:email]', out['redacted_preview'])
        self.assertNotIn('terdakwa@contoh.id', out['redacted_preview'])

    def test_image_redaction_never_claims_blackbox(self):
        ctx = FakeCtx()
        ctx.add_vault_file('file-png', png_bytes())
        ev.HANDLERS['evidence.image.upload'](ctx, {'case_id': 'case-1', 'file_id': 'file-png'})
        out = ev.HANDLERS['evidence.privacy.redact'](ctx, {'case_id': 'case-1', 'file_id': 'file-png'})
        self.assertFalse(out['supported'])
        self.assertFalse(out['redaction_performed'])
        self.assertFalse(out['image_blackbox_applied'])
        self.assertFalse(out['irreversible_file_redaction_performed'])
        self.assertEqual(out['reason'], 'redaction_requires_text_extraction')
        self.assertIn('tidak ada black-box gambar', out['notice'])

    def test_no_text_no_fabrication(self):
        ctx = FakeCtx()
        ctx.add_vault_file('file-doc', b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + b'\x00' * 64)
        ev.HANDLERS['evidence.document.upload'](ctx, {'case_id': 'case-1', 'file_id': 'file-doc'})
        out = ev.HANDLERS['evidence.privacy.redact'](ctx, {'case_id': 'case-1', 'file_id': 'file-doc'})
        self.assertFalse(out['redaction_performed'])
        self.assertEqual(out['reason'], 'no_text_extracted_from_container')


class SecurityTests(unittest.TestCase):
    def test_audit_events_carry_keys_only(self):
        ctx = FakeCtx()
        ctx.add_vault_file('file-png', png_bytes())
        ev.HANDLERS['evidence.image.upload'](ctx, {'case_id': 'case-1', 'file_id': 'file-png'})
        ev.HANDLERS['evidence.store.secure'](ctx, {'case_id': 'case-1', 'file_id': 'file-png'})
        ev.HANDLERS['evidence.chain.custody'](ctx, {
            'case_id': 'case-1', 'file_id': 'file-png', 'action': 'append'})
        self.assertTrue(ctx.audit)
        for _kind, data in ctx.audit:
            for v in data.values():
                self.assertIsInstance(v, (str, int, float, bool), v)
                if isinstance(v, str):
                    self.assertLess(len(v), 200)

    def test_readonly_handlers_write_nothing(self):
        ctx = FakeCtx()
        ctx.add_vault_file('file-png', png_bytes())
        ctx.add_vault_file('file-wav', wav_bytes())
        ctx.add_vault_file('file-pdf', build_pdf(['isi bukti dokumen harmonika']))
        ev.HANDLERS['evidence.image.upload'](ctx, {'case_id': 'case-1', 'file_id': 'file-png'})
        ev.HANDLERS['evidence.voice.upload'](ctx, {'case_id': 'case-1', 'file_id': 'file-wav'})
        ev.HANDLERS['evidence.document.upload'](ctx, {'case_id': 'case-1', 'file_id': 'file-pdf'})
        snapshot = {k: {i: dict(r) for i, r in v.items()} for k, v in ctx.records.items()}
        audit_snapshot = list(ctx.audit)
        readonly = [
            ('evidence.ocr.extract', {'case_id': 'case-1', 'file_id': 'file-png'}),
            ('evidence.vlm.analyze', {'case_id': 'case-1', 'file_id': 'file-png'}),
            ('evidence.asr.transcribe', {'case_id': 'case-1', 'file_id': 'file-wav'}),
            ('evidence.document.parse', {'case_id': 'case-1', 'file_id': 'file-pdf'}),
            ('evidence.metadata.extract', {'case_id': 'case-1', 'file_id': 'file-png'}),
            ('evidence.authenticity.check', {'case_id': 'case-1', 'file_id': 'file-pdf'}),
            ('evidence.chain.custody', {'case_id': 'case-1', 'file_id': 'file-png', 'action': 'view'}),
            ('evidence.categorize', {'case_id': 'case-1', 'file_id': 'file-png'}),
            ('evidence.relevance.assess', {'case_id': 'case-1', 'file_id': 'file-pdf'}),
            ('evidence.privacy.redact', {'case_id': 'case-1', 'file_id': 'file-pdf'}),
        ]
        for sid, args in readonly:
            ev.HANDLERS[sid](ctx, args)
        self.assertEqual({k: len(v) for k, v in ctx.records.items()},
                         {k: len(v) for k, v in snapshot.items()})
        self.assertEqual(ctx.audit, audit_snapshot)

    def test_no_module_level_network_shell_or_path_tools(self):
        with open(os.path.join(WORKTREE, 'backend', 'legal_engine', 'evidence.py'), 'rb') as f:source = f.read()
        for banned in (b'import os', b'import sys', b'subprocess', b'socket', b'urllib',
                       b'requests', b'http.client', b'os.system', b'popen', b'eval(',
                       b'exec(', b'__import__'):
            self.assertNotIn(banned, source, banned)

    def test_no_sys_modules_replacement_in_tests_or_module(self):
        with open(os.path.join(WORKTREE, 'backend', 'legal_engine', 'evidence.py'), 'rb') as f:source = f.read()
        self.assertNotIn(b'sys.modules', source)

    def test_common_imported_from_root_backend_not_stubbed(self):
        self.assertEqual(ev.C.__file__, os.path.join(ROOT_REPO, 'backend', 'legal_engine', 'common.py'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
