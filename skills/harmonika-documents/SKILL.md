---
name: harmonika-documents
description: Extract PDF text through read_pdf and inspect or create DOCX, XLSX and PPTX with detected optional local Python libraries.
---

Use for reading or creating documents. Check MCP `capabilities` and actual tool schemas first. PDF text extraction uses existing MCP `read_pdf`, maximum 20 pages per request; disclose page coverage and scanned-image/text limitations. Do not claim OCR, layout-preserving PDF editing or PDF creation. Use `analyze_image` only when separately inspecting a supported image, not as an invented PDF parser.

For local DOCX/XLSX/PPTX use `scripts/documents.py`; it never installs packages, runs shell commands or uses network. Run `harmonika skill documents capabilities` first. The CLI selects the owner-installed Python runtime; use this public CLI in normal sessions because private runtime paths may be blocked by the shell guard. The helper never creates or modifies the runtime. Optional imports are `docx` (python-docx), `openpyxl`, and `pptx` (python-pptx). Unsupported dependencies produce an explicit error, not simulated output.

Commands:
- `harmonika skill documents inspect --root /absolute/project --input /absolute/project/file.docx`
- `harmonika skill documents create --root /absolute/project --output /absolute/project/new.xlsx --data /absolute/project/spec.json`

Paths must be absolute and inside the explicitly selected project root; protected dot-directories, symlink paths and overwriting files are refused. Use user-authorized inputs only. With MCP home-scoped files create the absolute Downloads/Projects parent using `create_directory` first. Do not copy private files into the project to bypass scope.

JSON creation schemas (minimal content only): DOCX `{"paragraphs":["Title","Body"]}`; XLSX `{"sheets":[{"name":"Data","rows":[["Name","Count"],["A",1]]}]}`; PPTX `{"slides":[{"title":"Title","body":["First point","Second point"]}]}`. Inspect output is JSON for paragraphs/tables, sheets/rows or slides/text respectively, with bounded extraction. The helper does not preserve complex layouts, tracked changes, macros, charts or animations; never promise those features. Inspect the resulting artifact, report exact capabilities and limitations, and link only files actually created.
