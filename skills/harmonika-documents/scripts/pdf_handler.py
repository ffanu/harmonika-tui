"""Local PDF text extraction, generation and page-level edits; no network/OCR."""
import logging
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
from document_common import strings, scoped

_logger = logging.getLogger('pypdf')
_logger.addHandler(logging.NullHandler())
_logger.propagate = False

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PAGES = 20
MAX_TEXT = 30000

def reader(file):
    from pypdf import PdfReader
    if file.stat().st_size > MAX_PDF_BYTES:
        raise ValueError('file_too_large: PDF exceeds 10 MiB')
    with file.open('rb') as handle:
        if not handle.read(8).startswith(b'%PDF-'):
            raise ValueError('invalid_pdf: PDF signature missing')
    result = PdfReader(file, strict=True)
    if result.is_encrypted:
        raise ValueError('pdf_encrypted: decrypt through a separately authorized workflow')
    if len(result.pages) > 1000:
        raise ValueError('pdf_page_limit: maximum 1000 source pages')
    return result

def inspect(file):
    doc = reader(file)
    pages, remaining = [], MAX_TEXT
    for number, page in enumerate(doc.pages[:MAX_PAGES], 1):
        text = page.extract_text() or ''
        kept = text[:remaining]
        pages.append({'page': number, 'text': kept})
        remaining -= len(kept)
        if remaining <= 0:
            break
    text = '\n'.join(page['text'] for page in pages)[:MAX_TEXT]
    return {'status': 'ok' if text.strip() else 'degraded',
            'error': None if text.strip() else 'pdf_needs_ocr',
            'text': text, 'pages': pages, 'total_pages': len(doc.pages),
            'pages_processed': len(pages),
            'truncated': len(doc.pages) > len(pages) or remaining <= 0,
            'content_is_untrusted': True,
            'limits': {'max_pages': MAX_PAGES, 'max_text_characters': MAX_TEXT,
                       'max_bytes': MAX_PDF_BYTES}, 'ocr': False}

def validate_glyphs(text, cjk=False):
    if cjk:
        if any(not (ord(char)<128 or 0x3000<=ord(char)<=0x303F or 0x4E00<=ord(char)<=0x9FFF or 0xFF00<=ord(char)<=0xFFEF) for char in text):
            raise ValueError('unsupported_glyphs: CJK route supports basic Chinese/ASCII; requested script needs a separately licensed font')
    else:
        try:text.encode('cp1252')
        except UnicodeEncodeError:raise ValueError('unsupported_glyphs: use cjk for supported Chinese text or provide an approved font workflow') from None

def _font(cjk=False):
    # Built-in PDF fonts only: never silently embed an OS/proprietary font.
    if cjk:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        if 'STSong-Light' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        return 'STSong-Light'
    return 'Helvetica'

class PDFDocument:
    def __init__(self, data):
        if set(data) - {'title', 'paragraphs', 'page_size', 'cjk'}:
            raise ValueError('invalid_spec: unsupported PDF creation field')
        self.paragraphs = strings(data.get('paragraphs'), 'paragraphs')
        self.title = data.get('title', '')
        if not isinstance(self.title, str) or len(self.title) > 1000:
            raise ValueError('invalid_spec: title must be a string up to 1000 characters')
        if sum(map(len, self.paragraphs)) > 1000000:
            raise ValueError('invalid_spec: PDF text exceeds 1 million characters')
        if data.get('page_size', 'A4') not in ('A4', 'LETTER') or type(data.get('cjk', False)) is not bool:
            raise ValueError('invalid_spec: use page_size A4/LETTER and boolean cjk')
        self.data = data
        validate_glyphs(self.title+'\n'.join(self.paragraphs), data.get('cjk', False))
        self.font = 'STSong-Light' if data.get('cjk') else 'Helvetica'
        self.warnings = ['CJK CID font rendering depends on PDF viewer support'] if data.get('cjk') else []
    def save(self, handle):
        from reportlab.lib import pagesizes
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        font = _font(self.data.get('cjk', False))
        style = ParagraphStyle('Body', fontName=font, fontSize=11, leading=16,
                               spaceAfter=8, wordWrap='CJK' if self.data.get('cjk') else None)
        heading = ParagraphStyle('Title', parent=style, fontSize=18, leading=24, spaceAfter=16)
        story = []
        if self.title:
            story.append(Paragraph(escape(self.title), heading))
        for text in self.paragraphs:
            story.append(Paragraph(escape(text).replace('\n', '<br/>'), style) if text else Spacer(1, 10))
        if not story:
            story.append(Spacer(1, 1))
        SimpleDocTemplate(handle, pagesize=getattr(pagesizes, self.data.get('page_size', 'A4')),
                          title=self.title, author='Harmonika').build(story, onFirstPage=self._page_limit, onLaterPages=self._page_limit)
    @staticmethod
    def _page_limit(canvas, document):
        if document.page > 1000:
            raise ValueError('pdf_page_limit: maximum 1000 output pages')

def build(data):
    return PDFDocument(data)

def edit(file, data, root):
    from pypdf import PdfWriter
    if not isinstance(data, dict) or not data or set(data) - {'pages', 'rotation', 'append_files', 'watermark'}:
        raise ValueError('invalid_spec: use pages, rotation, append_files or watermark')
    source = reader(file)
    pages = data.get('pages', list(range(1, len(source.pages) + 1)))
    if not isinstance(pages, list) or not 1 <= len(pages) <= 1000 or any(type(n) is not int or not 1 <= n <= len(source.pages) for n in pages):
        raise ValueError('invalid_spec: pages must contain valid 1-based page numbers')
    rotation = data.get('rotation', 0)
    if type(rotation) is not int or rotation not in (0, 90, 180, 270):
        raise ValueError('invalid_spec: rotation must be 0, 90, 180 or 270')
    additional = data.get('append_files', [])
    if not isinstance(additional, list) or len(additional) > 10 or any(not isinstance(p, str) for p in additional):
        raise ValueError('invalid_spec: append_files must contain at most 10 paths')
    extras = [scoped(root, name) for name in additional]
    if file.stat().st_size + sum(path.stat().st_size for path in extras) > 30 * 1024 * 1024:
        raise ValueError('file_too_large: aggregate PDF edit inputs exceed 30 MiB')
    writer = PdfWriter()
    for number in pages:
        page = writer.add_page(source.pages[number - 1])
        if rotation:
            page.rotate(rotation)
    for extra in extras:
        if extra.suffix.lower() != '.pdf':
            raise ValueError('invalid_spec: append_files must be PDF files')
        doc = reader(extra)
        if len(writer.pages) + len(doc.pages) > 1000:
            raise ValueError('pdf_page_limit: maximum 1000 output pages')
        writer.append(doc, import_outline=False)
    if 'watermark' in data:
        text = data['watermark']
        if not isinstance(text, str) or not text.strip() or len(text) > 200:
            raise ValueError('invalid_spec: watermark must contain 1..200 characters')
        validate_glyphs(text)
        from reportlab.pdfgen import canvas
        for page in writer.pages:
            buffer = BytesIO()
            width, height = float(page.mediabox.width), float(page.mediabox.height)
            layer = canvas.Canvas(buffer, pagesize=(width, height))
            layer.setFont(_font(), 18)
            layer.setFillGray(0.65)
            layer.drawCentredString(width / 2, height / 2, text)
            layer.save()
            buffer.seek(0)
            from pypdf import PdfReader
            page.merge_page(PdfReader(buffer).pages[0])
    writer.remove_annotations(None)
    for page in writer.pages:
        for key in ('/AA', '/Annots', '/AF', '/PresSteps'):
            page.pop(key, None)
    for key in ('/OpenAction', '/AA', '/Names', '/AcroForm', '/AF'):
        writer.root_object.pop(key, None)
    return writer
