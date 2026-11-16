"""Harmonika File Parser — PDF/DOCX/XLSX/PPTX/TXT/CSV/JSON -> text.
Port 17900. Endpoints:
  GET  /health
  POST /parse {"filename", "content_b64"} -> {"kind", "pages": [...], "text", "meta"}
"""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import base64, io, json, zipfile, re

def parse_pdf(data):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    meta = {"pages": len(pages)}
    if reader.metadata:
        meta["title"] = str(reader.metadata.title or "")
    return pages, meta

def parse_docx(data):
    import docx
    doc = docx.Document(io.BytesIO(data))
    pages = ["\n".join(p.text for p in doc.paragraphs if p.text.strip())]
    for t in doc.tables:
        rows = []
        for row in t.rows:
            rows.append(" | ".join(c.text.strip() for c in row.cells))
        pages.append("\n".join(rows))
    return pages, {"paragraphs": len(doc.paragraphs)}

def parse_xlsx(data):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    pages = []
    for ws in wb.worksheets:
        lines = []
        for row in ws.iter_rows(max_row=2000, values_only=True):
            if any(v is not None for v in row):
                lines.append(" | ".join("" if v is None else str(v) for v in row[:50]))
        pages.append("Sheet: " + ws.title + "\n" + "\n".join(lines))
    return pages, {"sheets": len(pages)}

def parse_pptx(data):
    from pptx import Presentation
    prs = Presentation(io.BytesIO(data))
    pages = []
    for i, slide in enumerate(prs.slides):
        lines = [f"Slide {i+1}"]
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    t = "".join(r.text for r in para.runs)
                    if t.strip():
                        lines.append(t)
        pages.append("\n".join(lines))
    return pages, {"slides": len(pages)}

def parse_zip_html(data, filename):
    # docx/xlsx/pptx fallback jika lib gagal: ekstrak XML mentah
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if n.endswith(".xml") and ("word/" in n or "ppt/" in n or "xl/" in n)]
            chunks = []
            for n in names[:200]:
                xml = z.read(n).decode("utf-8", errors="replace")
                text = re.sub(r"<[^>]+>", " ", xml)
                text = re.sub(r"\s+", " ", text).strip()
                if len(text) > 50:
                    chunks.append(text)
            return ["\n\n".join(chunks)], {"fallback": True}
    except Exception:
        return [], {"error": "unparseable"}

def parse_any(filename, data):
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        return parse_pdf(data)
    if name.endswith(".docx"):
        try:
            return parse_docx(data)
        except Exception:
            return parse_zip_html(data, name)
    if name.endswith(".xlsx"):
        try:
            return parse_xlsx(data)
        except Exception:
            return parse_zip_html(data, name)
    if name.endswith(".pptx"):
        try:
            return parse_pptx(data)
        except Exception:
            return parse_zip_html(data, name)
    # plain text family
    text = data.decode("utf-8", errors="replace")
    return [text], {"encoding": "utf-8"}

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path == "/health":
            self._json({"ok": True, "service": "harmonika-fileparser",
                        "formats": ["pdf", "docx", "xlsx", "pptx", "txt", "csv", "json", "md"]})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 20 * 1024 * 1024:
            self._json({"error": "body_too_large"}, 413)
            return
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._json({"error": "invalid_json"}, 400)
            return
        if self.path != "/parse":
            self._json({"error": "not found"}, 404)
            return
        filename = data.get("filename") or ""
        b64 = data.get("content_b64") or ""
        if not filename or not b64:
            self._json({"error": "filename and content_b64 required"}, 400)
            return
        try:
            raw = base64.b64decode(b64, validate=False)
        except Exception:
            self._json({"error": "invalid_base64"}, 400)
            return
        if len(raw) > 12 * 1024 * 1024:
            self._json({"error": "file_too_large", "max_bytes": 12582912}, 413)
            return
        try:
            pages, meta = parse_any(filename, raw)
        except Exception as e:
            self._json({"error": f"parse_failed: {e}"}, 422)
            return
        text = "\n\n".join(pages)
        self._json({
            "kind": filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin",
            "pages": [p[:60000] for p in pages],
            "text": text[:200000],
            "length": len(text),
            "meta": meta,
        })

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

print("FileParser on 127.0.0.1:17900", flush=True)
ThreadingHTTPServer(("127.0.0.1", 17900), Handler).serve_forever()
