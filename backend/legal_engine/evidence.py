"""Evidence layer for the legal core: vault-bound uploads, bounded parsing and honest-by-default analysis.

Actual file bytes only ever come from ctx.evidence.read (server vault). Upload
handlers register the link between a case and already-uploaded bytes after
verifying magic bytes, size and kind. OCR/VLM/ASR are not configured by the
server, so those analyzers report unavailable with analysis_performed=False and
never describe unseen content. No filesystem, network or shell access; no raw
bytes are ever returned; audit events carry keys only.
"""
import hashlib
import io
import re
import struct
import time
import zipfile
import zlib
import xml.etree.ElementTree as ET

try:
    from . import common as C
except ImportError:
    from backend.legal_engine import common as C

DISCLAIMER = 'Edukasi hukum, bukan nasihat hukum formal atau pengganti advokat.'
ENCRYPTION_AT_REST = 'AES-256-GCM'

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 10 * 1024 * 1024
MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_NAME_LENGTH = 120
MAX_TEXT_CHARS = 200000
MAX_PDF_STREAMS = 400
MAX_PDF_STREAM_BYTES = 4 * 1024 * 1024
MAX_JPEG_SCAN = 1 << 20
ZIP_MAX_ENTRIES = 1000
ZIP_MAX_ENTRY_BYTES = 5 * 1024 * 1024
ZIP_MAX_TOTAL_BYTES = 20 * 1024 * 1024
ZIP_MAX_RATIO = 200
EBML_CODEC_SCAN = 262144
DEFAULT_PARSE_PAGES = 100
MAX_PARSE_PAGES = 100
DEFAULT_PARSE_TIMEOUT = 5.0
MAX_PARSE_TIMEOUT = 10.0
MAX_CUSTODY_ENTRIES = 1000
MAX_CUSTODY_NOTE = 500
MAX_REDACT_PREVIEW = 2000
MAX_RELEVANCE_SOURCE_CHARS = 4000
MAX_RELEVANCE_TEXT_CHARS = 100000
GENESIS_HASH = '0' * 64

MIME_PDF = 'application/pdf'
MIME_DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
MIME_TXT = 'text/plain'
MIME_MSWORD = 'application/msword'
TEXT_MIMES = (MIME_PDF, MIME_DOCX, MIME_TXT)

_KIND_FORMATS = {
    'image': {'png', 'jpeg', 'gif', 'webp', 'bmp', 'tiff'},
    'video': {'mp4', 'mov', 'webm_video', 'avi'},
    'voice': {'wav', 'mp3', 'ogg', 'flac', 'm4a', 'webm_audio'},
    'document': {'pdf', 'docx', 'msword_cfb', 'txt'},
}
_KIND_LIMITS = {
    'image': MAX_IMAGE_BYTES,
    'video': MAX_VIDEO_BYTES,
    'voice': MAX_AUDIO_BYTES,
    'document': MAX_DOCUMENT_BYTES,
}
_MEDIA_CLASS = {
    'png': 'image', 'jpeg': 'image', 'gif': 'image', 'webp': 'image', 'bmp': 'image', 'tiff': 'image',
    'mp4': 'video', 'mov': 'video', 'webm_video': 'video', 'avi': 'video',
    'wav': 'audio', 'mp3': 'audio', 'ogg': 'audio', 'flac': 'audio', 'm4a': 'audio', 'webm_audio': 'audio',
    'pdf': 'document', 'docx': 'document', 'msword_cfb': 'document', 'txt': 'document',
}
_MIME_BY_FORMAT = {
    'png': 'image/png', 'jpeg': 'image/jpeg', 'gif': 'image/gif', 'webp': 'image/webp',
    'bmp': 'image/bmp', 'tiff': 'image/tiff', 'mp4': 'video/mp4', 'mov': 'video/quicktime',
    'webm_video': 'video/webm', 'avi': 'video/x-msvideo', 'wav': 'audio/x-wav',
    'mp3': 'audio/mpeg', 'ogg': 'audio/ogg', 'flac': 'audio/flac', 'm4a': 'audio/mp4',
    'webm_audio': 'audio/webm', 'pdf': MIME_PDF, 'docx': MIME_DOCX,
    'msword_cfb': MIME_MSWORD, 'txt': MIME_TXT,
}


def _u16le(b, o):
    return struct.unpack('<H', b[o:o + 2])[0]


def _u16be(b, o):
    return struct.unpack('>H', b[o:o + 2])[0]


def _u32le(b, o):
    return struct.unpack('<I', b[o:o + 4])[0]


def _u32be(b, o):
    return struct.unpack('>I', b[o:o + 4])[0]


def _i32le(b, o):
    return struct.unpack('<i', b[o:o + 4])[0]


def _sniff_ebml(data):
    head = data[:4096]
    if b'webm' not in head:
        return None, None
    scan = data[:EBML_CODEC_SCAN]
    audio = any(t in scan for t in (b'A_OPUS', b'A_VORBIS', b'A_AAC', b'A_MPEG', b'A_PCM'))
    video = any(t in scan for t in (b'V_VP8', b'V_VP9', b'V_AV1', b'V_MPEG'))
    if audio and not video:
        return 'audio/webm', 'webm_audio'
    if video and not audio:
        return 'video/webm', 'webm_video'
    return None, None


def _sniff_zip(data):
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception:
        return None, None
    try:
        names = zf.namelist()
        if len(names) > ZIP_MAX_ENTRIES:
            return None, None
        if 'word/document.xml' in names:
            return MIME_DOCX, 'docx'
    finally:
        zf.close()
    return None, None


def _sniff(data):
    if not data or len(data) < 12:
        return None, None
    h = data[:16]
    if h[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png', 'png'
    if h[:3] == b'\xff\xd8\xff':
        return 'image/jpeg', 'jpeg'
    if h[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif', 'gif'
    if h[:4] == b'RIFF':
        if h[8:12] == b'WEBP':
            return 'image/webp', 'webp'
        if h[8:12] == b'WAVE':
            return 'audio/x-wav', 'wav'
        if h[8:12] == b'AVI ':
            return 'video/x-msvideo', 'avi'
        return None, None
    if h[:4] == b'\x1a\x45\xdf\xa3':
        return _sniff_ebml(data)
    if h[4:8] == b'ftyp':
        brand = data[8:12].rstrip(b'\x00 ')
        if brand == b'qt':
            return 'video/quicktime', 'mov'
        if data[8:12].startswith(b'M4A'):
            return 'audio/mp4', 'm4a'
        return 'video/mp4', 'mp4'
    if h[:3] == b'ID3':
        return 'audio/mpeg', 'mp3'
    if h[0] == 0xFF and (h[1] & 0xE0) == 0xE0:
        return 'audio/mpeg', 'mp3'
    if h[:4] == b'OggS':
        return 'audio/ogg', 'ogg'
    if h[:4] == b'fLaC':
        return 'audio/flac', 'flac'
    if h[:5] == b'%PDF-':
        return MIME_PDF, 'pdf'
    if h[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
        return MIME_MSWORD, 'msword_cfb'
    if h[:4] == b'PK\x03\x04':
        return _sniff_zip(data)
    if h[:2] == b'BM':
        return 'image/bmp', 'bmp'
    if h[:4] in (b'II*\x00', b'MM\x00*'):
        return 'image/tiff', 'tiff'
    return None, None


def _looks_like_text(data):
    if not data or b'\x00' in data:
        return False
    for ch in data:
        if ch < 0x20 and ch not in (0x09, 0x0A, 0x0D):
            return False
    try:
        data.decode('utf-8')
    except UnicodeDecodeError:
        return False
    return True


def _detect_for_kind(kind, data):
    mime, fmt = _sniff(data)
    if fmt is None and kind == 'document' and _looks_like_text(data):
        return MIME_TXT, 'txt'
    if fmt is None:
        raise C.DomainError('unsupported_file_type', 400,
                            {'hint': 'container magic bytes not recognized for any supported format'})
    if fmt not in _KIND_FORMATS[kind]:
        raise C.DomainError('evidence_kind_mismatch', 400,
                            {'requested_kind': kind, 'detected_mime': mime})
    return mime, fmt


def _dims_ok(w, h):
    if 1 <= w <= 1000000 and 1 <= h <= 1000000:
        return {'width': int(w), 'height': int(h)}
    return None


def _jpeg_scan(data):
    dims = None
    exif = False
    n = len(data)
    o = 2
    segs = 0
    limit = min(n, MAX_JPEG_SCAN)
    while o + 4 <= n and o < limit and segs < 256:
        if data[o] != 0xFF:
            break
        while o < n and data[o] == 0xFF:
            o += 1
        if o >= n:
            break
        m = data[o]
        if m == 0x01 or 0xD0 <= m <= 0xD9:
            o += 1
            segs += 1
            continue
        if o + 4 > n:
            break
        seglen = _u16be(data, o + 1)
        if seglen < 2:
            break
        if m == 0xE1 and o + 9 <= n and data[o + 3:o + 9] == b'Exif\x00\x00':
            exif = True
        if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC) and o + 8 <= n:
            hh = _u16be(data, o + 4)
            w = _u16be(data, o + 6)
            return _dims_ok(w, hh), exif
        if m == 0xDA:
            break
        o += 1 + seglen
        segs += 1
    return dims, exif


def _webp_dims(data):
    if len(data) < 30:
        return None
    fourcc = data[12:16]
    if fourcc == b'VP8X':
        w = 1 + (data[24] | (data[25] << 8) | (data[26] << 16))
        h = 1 + (data[27] | (data[28] << 8) | (data[29] << 16))
        return _dims_ok(w, h)
    if fourcc == b'VP8 ' and data[23:26] == b'\x9d\x01\x2a':
        w = _u16le(data, 26) & 0x3FFF
        h = _u16le(data, 28) & 0x3FFF
        return _dims_ok(w, h)
    if fourcc == b'VP8L' and len(data) >= 25 and data[20] == 0x2F:
        b1, b2, b3, b4 = data[21], data[22], data[23], data[24]
        w = 1 + (b1 | ((b2 & 0x3F) << 8))
        h = 1 + ((b2 >> 6) | (b3 << 2) | ((b4 & 0x03) << 10))
        return _dims_ok(w, h)
    return None


def _dims(fmt, data):
    try:
        if fmt == 'png' and len(data) >= 24 and data[12:16] == b'IHDR':
            w, h = struct.unpack('>II', data[16:24])
            return _dims_ok(w, h)
        if fmt == 'gif' and len(data) >= 10:
            return _dims_ok(_u16le(data, 6), _u16le(data, 8))
        if fmt == 'bmp' and len(data) >= 26:
            h = _i32le(data, 22)
            if h < 0:
                h = -h
            return _dims_ok(_i32le(data, 18), h)
        if fmt == 'jpeg':
            return _jpeg_scan(data)[0]
        if fmt == 'webp':
            return _webp_dims(data)
    except Exception:
        return None
    return None


def _wav_duration(data):
    n = len(data)
    o = 12
    byte_rate = None
    data_size = None
    chunks = 0
    while o + 8 <= n and chunks < 1000:
        cid = data[o:o + 4]
        size = _u32le(data, o + 4)
        if cid == b'fmt ' and size >= 16 and o + 24 <= n:
            byte_rate = _u32le(data, o + 16)
        elif cid == b'data':
            data_size = min(size, n - (o + 8))
            break
        if size < 0:
            break
        o += 8 + size + (size & 1)
        chunks += 1
    if byte_rate and data_size and byte_rate > 0:
        secs = data_size / byte_rate
        if 0 < secs <= 7 * 86400:
            return round(secs, 3)
    return None


def _mvhd_duration(data, start, end):
    o = start
    boxes = 0
    while o + 8 <= end and boxes < 64:
        size = _u32be(data, o)
        typ = data[o + 4:o + 8]
        if size < 8:
            return None
        if typ == b'mvhd':
            if o + 40 <= end and data[o + 8] == 1:
                timescale = _u32be(data, o + 28)
                dur = struct.unpack('>Q', data[o + 32:o + 40])[0]
            elif o + 28 <= end and data[o + 8] == 0:
                timescale = _u32be(data, o + 20)
                dur = _u32be(data, o + 24)
            else:
                return None
            if timescale > 0 and 0 < dur <= 10 ** 12:
                secs = dur / timescale
                if 0 < secs <= 7 * 86400:
                    return round(secs, 3)
            return None
        o += size
        boxes += 1
    return None


def _mp4_duration(data):
    n = len(data)
    o = 0
    boxes = 0
    while o + 8 <= n and boxes < 32:
        size = _u32be(data, o)
        typ = data[o + 4:o + 8]
        if size < 8:
            return None
        if typ == b'moov':
            return _mvhd_duration(data, o + 8, min(o + size, n))
        o += size
        boxes += 1
    return None


def _media_facts(fmt, data):
    out = {}
    dims = _dims(fmt, data)
    if dims:
        out.update(dims)
    dur = None
    if fmt == 'wav':
        dur = _wav_duration(data)
    elif fmt in ('mp4', 'mov', 'm4a'):
        dur = _mp4_duration(data)
    if dur is not None:
        out['duration_seconds'] = dur
    return out


def _sanitize_name(raw):
    C.require(type(raw) is str, 'invalid_name')
    s = raw.replace('\\', '/')
    s = s.rsplit('/', 1)[-1]
    s = ''.join(ch for ch in s if ord(ch) >= 0x20 and ord(ch) != 0x7F)
    s = re.sub(r'\s+', ' ', s).strip()
    if not s:
        return 'evidence'
    return s[:MAX_NAME_LENGTH]


def _linked_evidence(ctx, case_id, file_id):
    rows = ctx.list('legal_evidence', where={'case_id': case_id, 'file_id': file_id})
    C.require(len(rows) <= 1, 'evidence_duplicate_link', 409)
    if not rows:
        raise C.DomainError('evidence_not_linked', 404,
                            {'hint': 'call the matching evidence.*.upload handler first'})
    return rows[0]


def _require_case(ctx, case_id):
    return ctx.get('legal_case', case_id)


def _vault_read(ctx, file_id, case_id):
    meta, data = ctx.evidence.read(file_id)
    C.require(type(meta) is dict, 'vault_metadata_invalid')
    C.require(type(data) is bytes, 'vault_bytes_invalid')
    if meta.get('tenant') is not None and meta.get('tenant') != getattr(ctx, 'tenant', None):
        raise C.DomainError('evidence_tenant_mismatch', 403)
    if meta.get('case_id') is not None and meta.get('case_id') != case_id:
        raise C.DomainError('evidence_case_mismatch', 403)
    return meta, data


def _upload_impl(kind):
    def handler(ctx, args):
        case_id = C.identifier(args['case_id'])
        file_id = C.identifier(args['file_id'])
        _require_case(ctx, case_id)
        existing = ctx.list('legal_evidence', where={'case_id': case_id, 'file_id': file_id})
        C.require(len(existing) <= 1, 'evidence_duplicate_link', 409)
        if existing:
            meta,data = _vault_read(ctx,file_id,case_id)
            _detect_for_kind(kind,data)
            rec = existing[0]
            return {
                'evidence_id': rec['id'], 'case_id': case_id, 'file_id': file_id,
                'status': rec.get('status', 'ready'), 'linked': True, 'ready': True,
                'already_registered': True, 'name': rec.get('name'),
                'mime_type': rec.get('mime_type'),
                'size_bytes': rec.get('size_bytes'), 'sha256': rec.get('sha256'),
                'content_analysis': rec.get('content_analysis'),
                'authenticity': rec.get('authenticity'),
                'disclaimer': DISCLAIMER,
            }
        meta, data = _vault_read(ctx, file_id, case_id)
        C.require(len(data) > 0, 'empty_file')
        max_bytes = _KIND_LIMITS[kind]
        C.require(len(data) <= max_bytes, 'file_too_large', 413)
        mime, fmt = _detect_for_kind(kind, data)
        sha = hashlib.sha256(data).hexdigest()
        raw_name = args.get('name') or meta.get('name') or meta.get('filename') or file_id
        record_data = {
            'case_id': case_id,
            'file_id': file_id,
            'name': _sanitize_name(raw_name),
            'mime_type': mime,
            'size_bytes': len(data),
            'sha256': sha,
            'uploaded_at': ctx.now,
            'encryption': ENCRYPTION_AT_REST,
            'status': 'ready',
            'authenticity': 'not_verified',
            'content_analysis': 'not_performed',
        }
        record_data.update(_media_facts(fmt, data))
        record = ctx.create('legal_evidence', record_data)
        ctx.event('evidence.upload', {
            'case_id': case_id, 'file_id': file_id, 'sha256': sha,
            'mime_type': mime, 'size_bytes': len(data),
        })
        out = {
            'evidence_id': record['id'], 'case_id': case_id, 'file_id': file_id,
            'name': record['name'], 'status': 'ready', 'linked': True, 'ready': True,
            'already_registered': False,
            'mime_type': mime, 'mime_source': 'magic_bytes',
            'size_bytes': len(data), 'sha256': sha,
            'encryption': ENCRYPTION_AT_REST,
            'authenticity': 'not_verified', 'content_analysis': 'not_performed',
            'uploaded_at': record['created_at'],
            'disclaimer': DISCLAIMER,
        }
        for opt in ('width', 'height', 'duration_seconds'):
            if opt in record_data:
                out[opt] = record_data[opt]
        declared = meta.get('mime_type') or meta.get('declared_mime')
        if declared and declared != mime:
            out['vault_declared_mime'] = declared
            out['note'] = 'vault declared mime differed; actual mime taken from magic bytes'
        return out
    return handler


def _unavailable_impl(capability, allowed_formats, reason, notice):
    def handler(ctx, args):
        case_id = C.identifier(args['case_id'])
        file_id = C.identifier(args['file_id'])
        _require_case(ctx, case_id)
        record = _linked_evidence(ctx, case_id, file_id)
        mime = record.get('mime_type')
        fmt = None
        for f, m in _MIME_BY_FORMAT.items():
            if m == mime:
                fmt = f
                break
        if fmt is None or fmt not in allowed_formats:
            raise C.DomainError('evidence_kind_not_supported', 400,
                                {'required_evidence_kinds': sorted(allowed_formats),
                                 'actual_mime': mime})
        return {
            'capability': capability,
            'available': False,
            'ready': False,
            'status': 'unavailable',
            'analysis_performed': False,
            'reason': reason,
            'evidence_id': record['id'],
            'mime_type': mime,
            'notice': notice,
            'disclaimer': DISCLAIMER,
        }
    return handler


def _pypdf_module():
    try:
        import pypdf
        return pypdf
    except Exception:
        return None


def _pdf_page_count(data):
    try:
        return len(re.findall(rb'/Type\s*/Page(?![s])', data))
    except Exception:
        return None


_PDF_STRING_RE = re.compile(rb'\((?:\\.|[^\\()])*\)')
_PDF_ESCAPE_RE = re.compile(rb'\\(\d{1,3}|.)')
_PDF_ESCAPES = {b'n': b'\n', b'r': b'\r', b't': b'\t', b'b': b'\b', b'f': b'\f',
                b'(': b'(', b')': b')', b'\\': b'\\'}


def _pdf_unescape(raw):
    def rep(m):
        c = m.group(1)
        if c in _PDF_ESCAPES:
            return _PDF_ESCAPES[c]
        if c[:1].isdigit():
            try:
                return bytes([int(c, 8) & 0xFF])
            except ValueError:
                return b''
        return b''
    return re.sub(_PDF_ESCAPE_RE, rep, raw).decode('latin-1')


def _mostly_printable(s):
    if not s:
        return False
    printable = sum(1 for ch in s if ch.isprintable() or ch in '\n\r\t')
    return printable / len(s) >= 0.85


def _pdf_literal_strings(content):
    try:
        found = [_pdf_unescape(m.group(0)[1:-1]) for m in _PDF_STRING_RE.finditer(content)]
    except Exception:
        return ''
    return ' '.join(t for t in found if t.strip())


def _pdf_stdlib_text(data, deadline):
    parts = []
    deadline_hit = False
    total = 0
    n = len(data)
    pos = 0
    streams = 0
    while streams < MAX_PDF_STREAMS and total < MAX_TEXT_CHARS:
        s = data.find(b'stream', pos)
        if s < 0:
            break
        ds = data.find(b'endstream', s)
        if ds < 0:
            break
        raw_start = s + 6
        if data[raw_start:raw_start + 2] == b'\r\n':
            raw_start += 2
        elif data[raw_start:raw_start + 1] in (b'\n', b'\r'):
            raw_start += 1
        raw = data[raw_start:ds]
        pos = ds + 9
        streams += 1
        if time.monotonic() > deadline:
            deadline_hit = True
            break
        if not raw or len(raw) > MAX_PDF_STREAM_BYTES:
            continue
        content = None
        try:
            content = zlib.decompressobj().decompress(raw, MAX_PDF_STREAM_BYTES + 1)
        except Exception:
            try:
                content = zlib.decompressobj(-15).decompress(raw, MAX_PDF_STREAM_BYTES + 1)
            except Exception:
                content = raw
        if not content or len(content) > MAX_PDF_STREAM_BYTES:
            continue
        text = _pdf_literal_strings(content)
        if text and _mostly_printable(text):
            parts.append(text)
            total += len(text)
    joined = ' '.join(parts)
    if len(joined) > MAX_TEXT_CHARS:
        joined = joined[:MAX_TEXT_CHARS]
        truncated = True
    else:
        truncated = False
    return joined, truncated, deadline_hit


def _pdf_extract_via_pypdf(pypdf, data, pages_cap, deadline):
    try:
        from .document_sandbox import extract_pdf
    except ImportError:
        from backend.legal_engine.document_sandbox import extract_pdf
    return extract_pdf(data,pages_cap,deadline)


def _read_zip_entry_bounded(zf, name, cap):
    with zf.open(name) as fh:
        data = fh.read(cap + 1)
    if len(data) > cap:
        raise C.DomainError('zip_entry_too_large', 413)
    return data


def _docx_extract(data, deadline):
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception:
        raise C.DomainError('corrupt_zip_container', 400)
    truncated = False
    deadline_hit = False
    text = ''
    with zf:
        names = zf.namelist()
        if len(names) > ZIP_MAX_ENTRIES:
            raise C.DomainError('zip_entries_exceeded', 413)
        if 'word/document.xml' not in names:
            raise C.DomainError('docx_document_part_missing', 400)
        raw = _read_zip_entry_bounded(zf, 'word/document.xml', ZIP_MAX_ENTRY_BYTES)
        try:
            infos = {i.filename: i for i in zf.infolist()}
            info = infos.get('word/document.xml')
            if info is not None and info.compress_size > 0:
                if len(raw) / info.compress_size > ZIP_MAX_RATIO:
                    raise C.DomainError('zip_ratio_exceeded', 413)
        except C.DomainError:
            raise
        except Exception:
            pass
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            raise C.DomainError('docx_xml_invalid', 422)
        wns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        paras = []
        total = 0
        count = 0
        for p in root.iter(wns + 'p'):
            texts = [t.text for t in p.iter(wns + 't') if t.text]
            if texts:
                piece = ''.join(texts)
                paras.append(piece)
                total += len(piece) + 1
            count += 1
            if count % 2000 == 0 and time.monotonic() > deadline:
                deadline_hit = True
                break
            if total > MAX_TEXT_CHARS:
                truncated = True
                break
        text = '\n'.join(paras)
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS]
            truncated = True
    return text, truncated, deadline_hit


def _txt_extract(data):
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        raise C.DomainError('text_decoding_failed', 422)
    truncated = False
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
        truncated = True
    return text, truncated


def _extract_text(ctx, record, max_pages, timeout_seconds):
    meta, data = _vault_read(ctx, record['file_id'], record['case_id'])
    actual_sha = hashlib.sha256(data).hexdigest()
    if record.get('sha256') is not None and actual_sha != record['sha256']:
        raise C.DomainError('evidence_bytes_changed', 409)
    mime = record.get('mime_type')
    deadline = time.monotonic() + timeout_seconds
    base = {
        'mime_type': mime,
        'sha256': actual_sha,
        'text': '',
        'text_present': False,
        'text_truncated': False,
        'deadline_exceeded': False,
        'analysis_performed': False,
        'status': 'unsupported',
        'reason': 'container_not_supported_for_text_extraction',
        'method': None,
        'page_count_detected': None,
    }
    if mime == MIME_PDF:
        base['analysis_performed'] = True
        base.pop('reason')
        result = None
        pypdf = _pypdf_module()
        if pypdf is not None:
            result = _pdf_extract_via_pypdf(pypdf, data, max_pages, deadline)
            method = 'pypdf'
        if result is None:
            if pypdf is not None:
                base['note'] = 'pypdf could not read the container; bounded stdlib extraction used'
            method = 'stdlib_pdf_literal_strings'
            text, truncated, deadline_hit = _pdf_stdlib_text(data, deadline)
            result = {
                'text': text,
                'truncated': truncated,
                'deadline_exceeded': deadline_hit,
                'pages_seen': None,
                'page_total': _pdf_page_count(data),
            }
        if result.get('parse_unavailable'):
            base.update({'status':'degraded','reason':'bounded_pdf_parser_unavailable_or_invalid','analysis_performed':False,'deadline_exceeded':result['deadline_exceeded'],'method':'isolated_pdf_parser'})
            return base
        base['method'] = method
        base['text'] = result['text']
        base['text_present'] = bool(result['text'].strip())
        base['text_truncated'] = bool(result['truncated'])
        base['deadline_exceeded'] = bool(result['deadline_exceeded'])
        base['page_count_detected'] = result['page_total']
        if base['text_present'] and method == 'stdlib_pdf_literal_strings' and not _mostly_printable(base['text']):
            base['text'] = ''
            base['text_present'] = False
            base['decoding_uncertain'] = True
        base['status'] = 'partial' if (base['text_truncated'] or base['deadline_exceeded']) else 'parsed'
        return base
    if mime == MIME_DOCX:
        base['analysis_performed'] = True
        base.pop('reason')
        text, truncated, deadline_hit = _docx_extract(data, deadline)
        base['method'] = 'stdlib_docx_xml'
        base['text'] = text
        base['text_present'] = bool(text.strip())
        base['text_truncated'] = truncated
        base['deadline_exceeded'] = deadline_hit
        base['status'] = 'partial' if (truncated or deadline_hit) else 'parsed'
        return base
    if mime == MIME_TXT:
        base['analysis_performed'] = True
        base.pop('reason')
        text, truncated = _txt_extract(data)
        base['method'] = 'plain_text_utf8'
        base['text'] = text
        base['text_present'] = bool(text.strip())
        base['text_truncated'] = truncated
        base['status'] = 'partial' if truncated else 'parsed'
        return base
    if mime == MIME_MSWORD:
        base['reason'] = 'legacy_binary_word_not_supported'
        return base
    return base


def _parse_impl(ctx, args):
    case_id = C.identifier(args['case_id'])
    file_id = C.identifier(args['file_id'])
    _require_case(ctx, case_id)
    record = _linked_evidence(ctx, case_id, file_id)
    max_pages = args.get('max_pages', DEFAULT_PARSE_PAGES)
    timeout = args.get('timeout_seconds', DEFAULT_PARSE_TIMEOUT)
    result = _extract_text(ctx, record, max_pages, timeout)
    result.pop('sha256', None)
    result['evidence_id'] = record['id']
    result['case_id'] = case_id
    result['file_id'] = file_id
    result['caps'] = {
        'max_pages': max_pages,
        'timeout_seconds': timeout,
        'max_text_chars': MAX_TEXT_CHARS,
    }
    result['disclaimer'] = DISCLAIMER
    return result


def _metadata_impl(ctx, args):
    case_id = C.identifier(args['case_id'])
    file_id = C.identifier(args['file_id'])
    _require_case(ctx, case_id)
    record = _linked_evidence(ctx, case_id, file_id)
    meta, data = _vault_read(ctx, record['file_id'], case_id)
    actual_sha = hashlib.sha256(data).hexdigest()
    mime, fmt = _sniff(data)
    out = {
        'evidence_id': record['id'],
        'mime_type': record.get('mime_type'),
        'detected_mime': mime,
        'size_bytes': len(data),
        'sha256': actual_sha,
        'recorded_sha256_matches_bytes': record.get('sha256') == actual_sha,
        'not_extracted': ['exif_contents', 'gps_location', 'device_info', 'capture_time'],
        'notice': 'Metadata terbatas pada magic bytes, SHA-256 dan nilai yang bisa dibaca dari header wadah; tidak ada klaim EXIF perangkat, GPS atau waktu jepret.',
        'disclaimer': DISCLAIMER,
    }
    dims = _dims(fmt, data) if fmt else None
    if dims:
        out['dimensions'] = dims
    dur = None
    if fmt == 'wav':
        dur = _wav_duration(data)
    elif fmt in ('mp4', 'mov', 'm4a'):
        dur = _mp4_duration(data)
    if dur is not None:
        out['duration_seconds'] = dur
    markers = {'container_format': fmt}
    if fmt == 'jpeg':
        markers['jpeg_exif_app1_marker_present'] = bool(_jpeg_scan(data)[1])
    out['markers'] = markers
    return out


def _authenticity_impl(ctx, args):
    case_id = C.identifier(args['case_id'])
    file_id = C.identifier(args['file_id'])
    _require_case(ctx, case_id)
    record = _linked_evidence(ctx, case_id, file_id)
    meta, data = _vault_read(ctx, record['file_id'], case_id)
    actual_sha = hashlib.sha256(data).hexdigest()
    mime, fmt = _sniff(data)
    checks = []
    checks.append({
        'check': 'recorded_sha256_matches_actual_bytes',
        'result': record.get('sha256') == actual_sha,
    })
    checks.append({
        'check': 'container_magic_matches_recorded_mime',
        'result': mime == record.get('mime_type'),
        'detail': {'detected_mime': mime},
    })
    if fmt == 'pdf':
        checks.append({
            'check': 'pdf_eof_marker_present',
            'result': b'%%EOF' in data[-1024:],
        })
    if fmt == 'docx':
        zip_ok = False
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
            with zf:
                zip_ok = len(zf.namelist()) <= ZIP_MAX_ENTRIES and zf.testzip() is None
        except Exception:
            zip_ok = False
        checks.append({'check': 'zip_structure_and_crc_ok', 'result': zip_ok})
    if fmt == 'wav':
        try:
            _wav_duration(data)
            wav_ok = True
        except Exception:
            wav_ok = False
        checks.append({'check': 'riff_chunk_walk_ok', 'result': wav_ok})
    if fmt in ('mp4', 'mov', 'm4a'):
        checks.append({'check': 'moov_mvhd_box_found', 'result': _mp4_duration(data) is not None or b'mvhd' in data[:262144]})
    passed = all(c['result'] for c in checks)
    return {
        'evidence_id': record['id'],
        'verdict': 'basic_container_checks_consistent' if passed else 'basic_container_checks_failed',
        'checks': checks,
        'forensic_analysis_performed': False,
        'legal_admissibility_assessed': False,
        'notice': 'Pemeriksaan wadah dan hash dasar saja; bukan penilaian forensik, bukan penilaian keaslian hukum, dan bukan penilaian kelayakan sebagai alat bukti.',
        'disclaimer': DISCLAIMER,
    }


def _custody_id(case_id, file_id, sequence):
    digest = hashlib.sha256(('%s|%s|%d' % (case_id, file_id, sequence)).encode('utf-8')).hexdigest()
    return 'cst-' + digest[:40]


def _custody_entry_hash(tenant, case_id, file_id, sequence, prev_hash, action, actor, note, entry_id):
    payload = '|'.join([tenant or '', case_id, file_id, str(sequence), prev_hash,
                        action, actor or '', note or '', entry_id])
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _custody_impl(ctx, args):
    case_id = C.identifier(args['case_id'])
    file_id = C.identifier(args['file_id'])
    action = args['action']
    _require_case(ctx, case_id)
    record = _linked_evidence(ctx, case_id, file_id)
    prior = ctx.list('legal_custody_event', where={'case_id': case_id, 'file_id': file_id})
    prior.sort(key=lambda r: (r.get('sequence', 0), r.get('created_at', ''), r.get('id', '')))
    if action == 'view':
        prev = GENESIS_HASH
        broken_at = None
        head = GENESIS_HASH
        expected_seq = 1
        for e in prior:
            expect_id = _custody_id(case_id, file_id, e.get('sequence'))
            recompute = _custody_entry_hash(
                getattr(ctx, 'tenant', None), case_id, file_id, e.get('sequence'),
                prev, e.get('action', ''), e.get('actor'), e.get('note'), expect_id)
            ok = (e.get('sequence') == expected_seq and e.get('id') == expect_id
                  and e.get('prev_hash') == prev and e.get('entry_hash') == recompute)
            if not ok:
                broken_at = e.get('sequence')
                break
            prev = e['entry_hash']
            head = prev
            expected_seq += 1
        recent = [
            {'sequence': e.get('sequence'), 'action': e.get('action'), 'note': e.get('note'),
             'actor': e.get('actor'), 'created_at': e.get('created_at'), 'entry_hash': e.get('entry_hash')}
            for e in prior[-100:]
        ]
        return {
            'evidence_id': record['id'], 'case_id': case_id, 'file_id': file_id,
            'chain_valid': broken_at is None, 'entries_count': len(prior),
            'head_hash': head, 'broken_at': broken_at, 'entries': recent,
            'notice': 'Catatan rantai hash internal Harmonika; bukan chain of custody tersertifikasi; tidak menetapkan keaslian, keterterimaan, atau bobot pembuktian.',
            'disclaimer': DISCLAIMER,
        }
    C.require(len(prior) < MAX_CUSTODY_ENTRIES, 'custody_chain_too_long', 413)
    note_raw = args.get('note')
    note = None
    if note_raw is not None:
        note = _sanitize_name(note_raw)[:MAX_CUSTODY_NOTE]
    sequence = (prior[-1].get('sequence', 0) + 1) if prior else 1
    prev_hash = prior[-1]['entry_hash'] if prior else GENESIS_HASH
    entry_id = _custody_id(case_id, file_id, sequence)
    entry_hash = _custody_entry_hash(getattr(ctx, 'tenant', None), case_id, file_id,
                                     sequence, prev_hash, 'append', ctx.actor, note, entry_id)
    data = {
        'case_id': case_id, 'file_id': file_id, 'sequence': sequence,
        'prev_hash': prev_hash, 'entry_hash': entry_hash, 'action': 'append', 'note': note,
    }
    entry = ctx.create('legal_custody_event', data, id=entry_id)
    ctx.event('evidence.chain.custody', {
        'case_id': case_id, 'file_id': file_id, 'sequence': sequence, 'entry_hash': entry_hash,
    })
    return {
        'entry_id': entry['id'], 'evidence_id': record['id'],
        'case_id': case_id, 'file_id': file_id,
        'sequence': sequence, 'prev_hash': prev_hash, 'entry_hash': entry_hash,
        'chain_length': sequence, 'recorded_at': entry['created_at'],
        'notice': 'Catatan rantai hash internal Harmonika; bukan chain of custody tersertifikasi; tidak menetapkan keaslian, keterterimaan, atau bobot pembuktian.',
        'disclaimer': DISCLAIMER,
    }


def _categorize_impl(ctx, args):
    case_id = C.identifier(args['case_id'])
    file_id = C.identifier(args['file_id'])
    _require_case(ctx, case_id)
    record = _linked_evidence(ctx, case_id, file_id)
    mime = record.get('mime_type')
    fmt = None
    for f, m in _MIME_BY_FORMAT.items():
        if m == mime:
            fmt = f
            break
    media_class = _MEDIA_CLASS.get(fmt)
    if media_class is None:
        raise C.DomainError('unsupported_file_type', 422, {'actual_mime': mime})
    category = 'digital_document' if media_class == 'document' else 'digital_media'
    tags = []
    meta, _data = _vault_read(ctx, record['file_id'], case_id)
    if meta.get('encryption') == ENCRYPTION_AT_REST:
        tags.append('vault_encrypted_attested')
    if mime in TEXT_MIMES:
        tags.append('text_extractable_container')
    if record.get('content_analysis') == 'not_performed':
        tags.append('content_analysis_not_performed')
    if record.get('authenticity') == 'not_verified':
        tags.append('authenticity_not_verified')
    return {
        'evidence_id': record['id'],
        'category': category,
        'media_class': media_class,
        'tags': tags,
        'basis': 'detected_container_and_case_linkage_only',
        'notice': 'Kategori hanya dari jenis wadah berkas dan tautan kasus; bukan kategori alat bukti menurut hukum acara.',
        'disclaimer': DISCLAIMER,
    }


_STOPWORDS = frozenset((
    'dan', 'atau', 'yang', 'untuk', 'dengan', 'dari', 'dalam', 'pada', 'adalah', 'di', 'ke',
    'oleh', 'ini', 'itu', 'akan', 'tidak', 'juga', 'sebagai', 'karena', 'agar', 'jika',
    'kami', 'saya', 'kita', 'mereka', 'dia', 'anda', 'adanya', 'bagi', 'atas', 'dapat',
    'bisa', 'harus', 'sudah', 'telah', 'lebih', 'para', 'secara', 'antara', 'hukum',
    'kasus', 'terkait', 'hal', 'tersebut', 'mana', 'apa', 'kapan', 'bagaimana',
    'the', 'and', 'for', 'with', 'from', 'that', 'this', 'have', 'has', 'will',
    'would', 'could', 'should',
))


def _tokens(text):
    return [t for t in re.findall(r'[a-z0-9]{4,}', text.lower()) if t not in _STOPWORDS]


def _relevance_impl(ctx, args):
    case_id = C.identifier(args['case_id'])
    file_id = C.identifier(args['file_id'])
    case = _require_case(ctx, case_id)
    record = _linked_evidence(ctx, case_id, file_id)
    mime = record.get('mime_type')
    text_capable = mime in TEXT_MIMES or mime == MIME_MSWORD
    if not text_capable:
        return {
            'assessment_performed': False,
            'evidence_id': record['id'],
            'reason': 'evidence_has_no_extractable_text',
            'hint': 'OCR/VLM/ASR analyzers are not configured; only text containers can be assessed lexically',
            'disclaimer': DISCLAIMER,
        }
    extraction = _extract_text(ctx, record, DEFAULT_PARSE_PAGES, DEFAULT_PARSE_TIMEOUT)
    if not extraction['text_present']:
        return {
            'assessment_performed': False,
            'evidence_id': record['id'],
            'reason': 'no_text_extracted_from_container',
            'detail_reason': extraction.get('reason'),
            'disclaimer': DISCLAIMER,
        }
    source_text = ((case.get('topic') or '') + ' ' + (case.get('facts') or ''))[:MAX_RELEVANCE_SOURCE_CHARS]
    source_terms = sorted(set(_tokens(source_text)))
    evidence_text = extraction['text'][:MAX_RELEVANCE_TEXT_CHARS]
    ev_tokens = _tokens(evidence_text)
    counts = {}
    for t in ev_tokens:
        counts[t] = counts.get(t, 0) + 1
    matched = [{'term': t, 'count': counts[t]} for t in source_terms if t in counts]
    matched.sort(key=lambda m: (-m['count'], m['term']))
    matched = matched[:25]
    coverage = round(len(matched) / max(1, len(source_terms)), 4)
    return {
        'assessment_performed': True,
        'method': 'lexical_keyword_overlap',
        'evidence_id': record['id'],
        'matched_terms': matched,
        'matched_distinct': len(matched),
        'source_terms_considered': len(source_terms),
        'evidence_tokens_considered': len(ev_tokens),
        'lexical_coverage': coverage,
        'notice': 'Kecocosan kata leksikal saja; bukan penilaian relevansi pembuktian menurut hukum dan bukan ukuran keyakinan.',
        'disclaimer': DISCLAIMER,
    }


def _store_secure_impl(ctx, args):
    case_id = C.identifier(args['case_id'])
    file_id = C.identifier(args['file_id'])
    _require_case(ctx, case_id)
    record = _linked_evidence(ctx, case_id, file_id)
    meta, data = _vault_read(ctx, file_id, case_id)
    encryption = meta.get('encryption')
    if encryption != ENCRYPTION_AT_REST:
        return {
            'secured': False,
            'evidence_id': record['id'],
            'vault_encryption_metadata': encryption,
            'reason': 'vault_encryption_not_attested',
            'no_changes_made': True,
            'disclaimer': DISCLAIMER,
        }
    actual_sha = hashlib.sha256(data).hexdigest()
    if record.get('sha256') is not None and actual_sha != record['sha256']:
        raise C.DomainError('evidence_bytes_changed', 409)
    verification = {
        'algorithm': encryption,
        'attested_by': 'server_vault_metadata',
        'file_id': file_id,
        'sha256': actual_sha,
        'size_bytes': len(data),
        'verified_at': ctx.now,
    }
    ctx.update('legal_evidence', record['id'], {
        'secure_store_verified_at': ctx.now,
        'storage_verification': verification,
    })
    ctx.event('evidence.store.secure', {
        'case_id': case_id, 'file_id': file_id, 'sha256': actual_sha,
    })
    return {
        'secured': True,
        'evidence_id': record['id'],
        'verification': verification,
        'notice': 'Tersimpan pada vault server terenkripsi; atribut enkripsi berasal dari metadata vault server, bukan klaim pemanggil.',
        'disclaimer': DISCLAIMER,
    }


_EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
_PHONE_RE = re.compile(r'(?<!\d)(?:\+62|62|0)8\d{7,11}(?!\d)')
_IPV4_RE = re.compile(r'(?<!\d)(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?!\d)')
_CARD_RE = re.compile(r'(?<!\d)\d{13,19}(?!\d)')
_NIK_RE = re.compile(r'(?<!\d)\d{16}(?!\d)')

_REDACT_LABELS = {
    'email': '[REDACTED:email]',
    'phone': '[REDACTED:phone]',
    'ip_address': '[REDACTED:ip]',
    'possible_card_number': '[REDACTED:card]',
    'nik': '[REDACTED:nik]',
}


def _valid_ipv4(m):
    return all(0 <= int(g) <= 255 for g in m.groups())


def _luhn(m):
    digits = m.group(0)
    total = 0
    alt = False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _detect_pii(text):
    spans = []
    occupied = []

    def free(s, e):
        return all(e <= a or s >= b for a, b in occupied)

    def add(regex, label, validator=None):
        for m in regex.finditer(text):
            if validator is not None and not validator(m):
                continue
            s, e = m.span()
            if free(s, e):
                spans.append((s, e, label))
                occupied.append((s, e))

    add(_EMAIL_RE, 'email')
    add(_PHONE_RE, 'phone')
    add(_IPV4_RE, 'ip_address', _valid_ipv4)
    add(_CARD_RE, 'possible_card_number', _luhn)
    add(_NIK_RE, 'nik')
    spans.sort()
    return spans


def _apply_spans(text, spans):
    out = []
    pos = 0
    for s, e, label in spans:
        out.append(text[pos:s])
        out.append(_REDACT_LABELS[label])
        pos = e
    out.append(text[pos:])
    return ''.join(out)


def _redact_impl(ctx, args):
    case_id = C.identifier(args['case_id'])
    file_id = C.identifier(args['file_id'])
    _require_case(ctx, case_id)
    record = _linked_evidence(ctx, case_id, file_id)
    mime = record.get('mime_type')
    base = {
        'evidence_id': record['id'],
        'mime_type': mime,
        'supported': False,
        'redaction_performed': False,
        'transformation_applied': None,
        'image_blackbox_applied': False,
        'irreversible_file_redaction_performed': False,
        'original_file_modified': False,
        'derived_file_persisted': False,
        'disclaimer': DISCLAIMER,
    }
    text_capable = mime in TEXT_MIMES or mime == MIME_MSWORD
    if not text_capable:
        base['reason'] = 'redaction_requires_text_extraction'
        base['notice'] = 'Penyaringan hanya tersedia pada teks yang benar-benar terekstrak; tidak ada black-box gambar dan tidak ada redaksi PDF yang diklaim.'
        return base
    extraction = _extract_text(ctx, record, DEFAULT_PARSE_PAGES, DEFAULT_PARSE_TIMEOUT)
    if not extraction['text_present']:
        base['supported'] = True
        base['reason'] = 'no_text_extracted_from_container'
        base['detail_reason'] = extraction.get('reason')
        base['notice'] = 'Tidak ada teks terekstrak sehingga tidak ada redaksi yang dilakukan; tidak ada konten yang dikarang.'
        return base
    text = extraction['text']
    spans = _detect_pii(text)
    masked = _apply_spans(text, spans)
    findings = {}
    for _s, _e, label in spans:
        findings[label] = findings.get(label, 0) + 1
    base.update({
        'supported': True,
        'redaction_performed': len(spans) > 0,
        'transformation_applied': 'text_masking_preview' if spans else 'none_needed',
        'findings': [{'category': k, 'count': v} for k, v in sorted(findings.items())],
        'total_redacted_spans': len(spans),
        'redacted_preview': masked[:MAX_REDACT_PREVIEW],
        'preview_truncated': len(masked) > MAX_REDACT_PREVIEW,
        'source_text_truncated': extraction.get('text_truncated', False),
        'notice': 'Pratinjau penyaringan tingkat teks; berkas asli immutable tidak diubah dan tidak ada berkas turunan yang disimpan oleh modul ini.',
    })
    return base


def _obj(required, props):
    return {'type': 'object', 'additionalProperties': False, 'required': required, 'properties': props}


_ID = {'type': 'string', 'pattern': r'[A-Za-z0-9_-]{1,100}'}
_BASE_SCHEMA = _obj(['case_id', 'file_id'], {'case_id': _ID, 'file_id': _ID})
_UPLOAD_SCHEMA = _obj(['case_id', 'file_id'], {
    'case_id': _ID,
    'file_id': _ID,
    'name': {'type': 'string', 'minLength': 1, 'maxLength': MAX_NAME_LENGTH},
})
_PARSE_SCHEMA = _obj(['case_id', 'file_id'], {
    'case_id': _ID,
    'file_id': _ID,
    'max_pages': {'type': 'integer', 'minimum': 1, 'maximum': MAX_PARSE_PAGES},
    'timeout_seconds': {'type': 'number', 'minimum': 1, 'maximum': MAX_PARSE_TIMEOUT},
})
_CUSTODY_SCHEMA = _obj(['case_id', 'file_id', 'action'], {
    'case_id': _ID,
    'file_id': _ID,
    'action': {'type': 'string', 'enum': ['view', 'append']},
    'note': {'type': 'string', 'minLength': 1, 'maxLength': MAX_CUSTODY_NOTE},
})


def _with_schema(schema, fn):
    def handler(ctx, args):
        C.require(type(args) is dict, 'invalid_args')
        C.validate(args, schema)
        return fn(ctx, args)
    return handler


SCHEMAS = {
    'evidence.image.upload': _UPLOAD_SCHEMA,
    'evidence.video.upload': _UPLOAD_SCHEMA,
    'evidence.voice.upload': _UPLOAD_SCHEMA,
    'evidence.document.upload': _UPLOAD_SCHEMA,
    'evidence.ocr.extract': _BASE_SCHEMA,
    'evidence.vlm.analyze': _BASE_SCHEMA,
    'evidence.asr.transcribe': _BASE_SCHEMA,
    'evidence.document.parse': _PARSE_SCHEMA,
    'evidence.metadata.extract': _BASE_SCHEMA,
    'evidence.authenticity.check': _BASE_SCHEMA,
    'evidence.chain.custody': _CUSTODY_SCHEMA,
    'evidence.categorize': _BASE_SCHEMA,
    'evidence.relevance.assess': _BASE_SCHEMA,
    'evidence.store.secure': _BASE_SCHEMA,
    'evidence.privacy.redact': _BASE_SCHEMA,
}

HANDLERS = {
    'evidence.image.upload': _with_schema(SCHEMAS['evidence.image.upload'], _upload_impl('image')),
    'evidence.video.upload': _with_schema(SCHEMAS['evidence.video.upload'], _upload_impl('video')),
    'evidence.voice.upload': _with_schema(SCHEMAS['evidence.voice.upload'], _upload_impl('voice')),
    'evidence.document.upload': _with_schema(SCHEMAS['evidence.document.upload'], _upload_impl('document')),
    'evidence.ocr.extract': _with_schema(
        SCHEMAS['evidence.ocr.extract'],
        _unavailable_impl('ocr', {'png', 'jpeg', 'gif', 'webp', 'bmp', 'tiff'},
                          'ocr_engine_not_configured_by_server',
                          'Mesin OCR belum dikonfigurasi server; tidak ada analisis isi gambar yang dilakukan dan tidak ada teks yang dikarang.')),
    'evidence.vlm.analyze': _with_schema(
        SCHEMAS['evidence.vlm.analyze'],
        _unavailable_impl('vlm', {'png', 'jpeg', 'gif', 'webp', 'bmp', 'tiff', 'mp4', 'mov', 'webm_video', 'avi'},
                          'vlm_engine_not_configured_by_server',
                          'Mesin VLM belum dikonfigurasi server; tidak ada deskripsi isi gambar/video yang dihasilkan.')),
    'evidence.asr.transcribe': _with_schema(
        SCHEMAS['evidence.asr.transcribe'],
        _unavailable_impl('asr', {'wav', 'mp3', 'ogg', 'flac', 'm4a', 'webm_audio'},
                          'asr_engine_not_configured_by_server',
                          'Mesin ASR belum dikonfigurasi server; tidak ada transkripsi audio yang dihasilkan.')),
    'evidence.document.parse': _with_schema(SCHEMAS['evidence.document.parse'], _parse_impl),
    'evidence.metadata.extract': _with_schema(SCHEMAS['evidence.metadata.extract'], _metadata_impl),
    'evidence.authenticity.check': _with_schema(SCHEMAS['evidence.authenticity.check'], _authenticity_impl),
    'evidence.chain.custody': _with_schema(SCHEMAS['evidence.chain.custody'], _custody_impl),
    'evidence.categorize': _with_schema(SCHEMAS['evidence.categorize'], _categorize_impl),
    'evidence.relevance.assess': _with_schema(SCHEMAS['evidence.relevance.assess'], _relevance_impl),
    'evidence.store.secure': _with_schema(SCHEMAS['evidence.store.secure'], _store_secure_impl),
    'evidence.privacy.redact': _with_schema(SCHEMAS['evidence.privacy.redact'], _redact_impl),
}

MUTATIONS = frozenset({
    'evidence.image.upload',
    'evidence.video.upload',
    'evidence.voice.upload',
    'evidence.document.upload',
    'evidence.chain.custody',
    'evidence.store.secure',
})

LIMITATIONS = {
    'evidence.image.upload': [
        'bytes max %d; bytes diambil penuh dari vault server, bukan dari pemanggil' % MAX_IMAGE_BYTES,
        'mime ditentukan dari magic bytes aktual; format tidak dikenal ditolak',
        'dimensi hanya dari header PNG/JPEG/GIF/WebP/BMP; bukan verifikasi isi gambar',
    ],
    'evidence.video.upload': [
        'bytes max %d; bytes diambil penuh dari vault server' % MAX_VIDEO_BYTES,
        'durasi hanya dari kotak mvhd MP4/MOV/M4A bila ada; tanpa dekode frame',
        'webm: audio/video ditentukan dari codec string pada %d byte pertama; wadah tidak meyakinkan ditolak' % EBML_CODEC_SCAN,
    ],
    'evidence.voice.upload': [
        'bytes max %d; bytes diambil penuh dari vault server' % MAX_AUDIO_BYTES,
        'durasi hanya dihitung untuk WAV (fmt/data) dan MP4-family (mvhd); MP3 tidak dihitung, bukan estimasi',
    ],
    'evidence.document.upload': [
        'bytes max %d; didukung: PDF, DOCX, DOC legacy (CFB), teks UTF-8' % MAX_DOCUMENT_BYTES,
        'subtype CFB tidak diverifikasi per stream internal; diberi label msword container',
    ],
    'evidence.ocr.extract': [
        'OCR tidak dikonfigurasi server: selalu unavailable, analysis_performed False, tidak ada teks dari gambar',
    ],
    'evidence.vlm.analyze': [
        'VLM tidak dikonfigurasi server: selalu unavailable, analysis_performed False, tidak ada deskripsi visual',
    ],
    'evidence.asr.transcribe': [
        'ASR tidak dikonfigurasi server: selalu unavailable, analysis_performed False, tidak ada transkrip',
    ],
    'evidence.document.parse': [
        'PDF via pypdf bila terpasang; jika tidak, ekstraksi stdlib literal-string terbatas (tanpa ToUnicode CMap) sehingga teks subset-font bisa tidak terbaca, tidak pernah dikarang',
        'batas: %d halaman, %d stream, %d byte/stream, %d karakter teks, timeout <= %d detik' % (
            MAX_PARSE_PAGES, MAX_PDF_STREAMS, MAX_PDF_STREAM_BYTES, MAX_TEXT_CHARS, int(MAX_PARSE_TIMEOUT)),
        'DOCX via stdlib zip+XML dengan pelindung zip-bomb (%d entri, %d byte/entri, rasio %dx); DOC legacy binary tidak didukung' % (
            ZIP_MAX_ENTRIES, ZIP_MAX_ENTRY_BYTES, ZIP_MAX_RATIO),
    ],
    'evidence.metadata.extract': [
        'hanya magic bytes, SHA-256, dimensi header, durasi wadah, dan penanda level-byte; EXIF/GPS/perangkat/waktu jepret sengaja tidak diekstrak',
    ],
    'evidence.authenticity.check': [
        'hanya konsistensi hash dan wadah dasar; bukan analisis forensik, bukan penilaian keaslian hukum atau kelayakan alat bukti',
    ],
    'evidence.chain.custody': [
        'log append-only ber-hash-chain per tenant+case+file (maks %d entri); integritas diverifikasi dengan rekalkulasi hash; bukan rantai custody tersertifikasi/legal-grade' % MAX_CUSTODY_ENTRIES,
    ],
    'evidence.categorize': [
        'kategori hanya dari wadah terdeteksi dan tautan kasus; tidak mengklaim kategori pembuktian (dokumen/saksi/digital menurut hukum acara)',
    ],
    'evidence.relevance.assess': [
        'hanya tumpang tindih kata leksikal (maks %d karakter sumber, %d token bukti); bukan relevansi pembuktian menurut hukum' % (
            MAX_RELEVANCE_SOURCE_CHARS, MAX_RELEVANCE_TEXT_CHARS),
        'penilaian hanya untuk wadah teks (PDF/DOCX/txt); OCR/VLM/ASR tidak tersedia sehingga media lain tidak bisa dinilai',
    ],
    'evidence.store.secure': [
        'wajib atribut enkripsi AES-256-GCM dari metadata vault server; flag caller diabaikan/ditolak; tanpa atestasi, tidak ada perubahan',
    ],
    'evidence.privacy.redact': [
        'hanya pratinjau masking teks dari teks terekstrak; tidak ada black-box gambar, tidak ada redaksi PDF ireversibel, tidak ada berkas turunan disimpan modul ini',
        'deteksi konservatif: email, telepon +62/08, IPv4, angka Luhn, NIK 16 digit; pola lain tidak diklaim',
    ],
}
