---
name: harmonika-documents
description: Read and create local PDF, DOCX, XLSX and PPTX documents; select, merge, rotate or watermark PDF pages with explicit capability checks.
---

Use `harmonika skill documents capabilities --detailed` first. Operations run locally without network or implicit dependency installation. `manifest.yaml` is a JSON-compatible YAML 1.2 registry consumed by the dispatcher, not native TUI discovery metadata. `SKILL.md` remains the discovery entrypoint.

## Commands

- Read: `harmonika skill documents inspect --root /absolute/project --input /absolute/project/file.pdf`
- Create: `harmonika skill documents create --root /absolute/project --output /absolute/project/new.pdf --data /absolute/project/spec.json`
- PDF page edit: `harmonika skill documents edit --root /absolute/project --input /absolute/project/original.pdf --output /absolute/project/edited.pdf --data /absolute/project/edit.json`

Keep inputs and outputs inside the explicitly selected existing project root. Dot-directories, arbitrary symlinks and overwrites are refused; canonical macOS `/tmp` and `/var` aliases are accepted. Choose a new output filename for edits. Never copy private files into a project to evade scope restrictions. The normal public CLI selects the owner-installed document runtime; new dependencies require explicit installation by the owner.

## Content specifications

- PDF: `{"title":"Report","paragraphs":["Introduction","Body"],"page_size":"A4","cjk":false}`. Title is optional; page size is A4 or LETTER. Text is escaped, not interpreted as markup. Default uses built-in Helvetica/WinAnsi; unsupported glyphs fail explicitly. Set `cjk:true` for basic Chinese/ASCII via ReportLab CID font; viewer rendering may vary. No OS font is silently embedded.
- DOCX: `{"paragraphs":["Title","Body"]}`.
- XLSX: `{"sheets":[{"name":"Data","rows":[["Name","Count"],["A",1]]}]}`. Input strings remain literal text, including strings beginning with `=`; no formula execution.
- PPTX: `{"slides":[{"title":"Title","body":["First point","Second point"]}]}`.
- PDF edit: `{"pages":[1,2],"rotation":90,"append_files":["/absolute/project/extra.pdf"],"watermark":"DRAFT"}`. All fields optional, at least one required. Edit removes source annotations, forms, page/catalog actions and attachment catalogs; it does not promise general malware detection. Pages are 1-based. Rotation accepts 0/90/180/270. This is page-level manipulation, not arbitrary text replacement or layout-preserving editing.

## Limits and fallbacks

PDF inputs/outputs: 10 MiB. Extraction: first 20 pages, 30,000 characters. PDF source/edit output: at most 1,000 pages; combined edit inputs at most 30 MiB. Scanned PDF without text returns `status:degraded,error:pdf_needs_ocr`; never invent OCR or extracted content. Encrypted PDFs are refused. Local parser is pypdf; ReportLab generates PDF and optional watermark overlays. Existing MCP `read_pdf` remains available as an explicit compatibility fallback if local parsing is unavailable; it does not provide OCR or creation/editing either. Do not send a local document remotely without authorization for that transfer.

Office inputs/outputs: 20 MiB; bounded ZIP expansion. Read output reports extraction limits. Office creation supports basic content, not charts, tracked changes, macros, advanced layout or animation. Edit/convert operations on Office and MD/HTML/EPUB/RTF handlers are not implemented. Reserved formats are disabled in the registry and must not be advertised as ready.

Inspect the generated artifact and report the actual operations, coverage and limitations. Link only files actually created. CLI errors are structured JSON on stderr with nonzero exit; do not treat them as document contents.

## Shared executable operations

For automation through the shared job contract, use `harmonika skills list` to
check actual readiness. `harmonika skills execute doc.create_pdf --root
/absolute/project --idempotency-key UNIQUE_ID` reads JSON stdin:
`{"output_path":"/absolute/project/report.pdf","content":{"paragraphs":["Body"]}}`.
Read operation `doc.read_pdf` takes `{"path":"/absolute/project/report.pdf"}`.
Other registered formats are docx/xlsx/pptx; PDF edit uses path/output_path/changes.
Write keys must be unique per intended operation and reused only for retries of
that same input. Never automatically retry status `unknown` or `recovery_required`;
check job status and existing output first. Legacy commands above remain supported.
Shared commands do not enable HTTP, remote filesystem or generic shell access.
