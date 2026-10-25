# Shared Skill Core

Satu kontrak executable untuk client TUI, gateway internal dan adapter UI masa
depan. Skill panduan `SKILL.md` tetap berbeda dari operasi executable seperti
`doc.create_pdf`. Core ini tidak mendaftarkan 99 kemampuan sebagai fitur siap.

```text
Local CLI / trusted internal caller
              ↓
ExecutionContext dari boundary tepercaya
              ↓
Registry + schema + permission + project scope
              ↓
SQLite metadata/idempotency + event sequence
              ↓
Isolated document worker → existing local handlers
```

## Yang aktif

Registry saat ini mencakup **45 operasi**: 9 dokumen, 14 SEO, 4 workflow engineering, dan 18 operasi coding workspace. Coding memakai runtime remote terisolasi; executor dokumen lokal menolak eksekusinya dengan workspace_required. [Kontrak coding](CODING-MVP.md). Gateway publik terpisah menghubungkan 12 operasi SEO read-only serta 4 workflow engineering dengan key Telegram yang sama; bukan seluruh operasi lokal. Lihat [engineering](ENGINEERING.md) dan [SEO](SEO.md).

Flag `public_api=false` pada probe executor lokal berarti library ini tidak membuka HTTP listener sendiri. Gateway publik memiliki boundary autentikasi dan worker terpisah.

Operasi dokumen: `doc.read_pdf`, `doc.read_docx`, `doc.read_xlsx`, `doc.read_pptx`,
`doc.create_pdf`, `doc.create_docx`, `doc.create_xlsx`, `doc.create_pptx`,
`doc.edit_pdf`. Semuanya memakai handler dokumen yang sudah ada. Tidak ada shell,
network, credential retrieval atau process-kill generik yang ditambahkan.

Descriptor berisi ID/version, input/output schema, permissions, execution target,
timeout, batas byte/kompleksitas, reference fields, cancellation dan idempotency.
Registry bersifat inert; keberadaan descriptor bukan readiness. Probe worker
mengisi `readiness.status=available/unavailable` beserta alasan. Hasil pembacaan PDF
scan bisa `degraded` dengan `pdf_needs_ocr`, tanpa klaim OCR.

## CLI dan kontrak internal

```sh
harmonika skills list
```

`execute` menerima satu objek input JSON lewat stdin, bukan credential/principal:

```sh
harmonika skills execute doc.create_pdf --root /absolute/project --idempotency-key unique-request-id <<'JSON'
{"output_path":"/absolute/project/report.pdf","content":{"title":"Laporan","paragraphs":["Isi laporan"]}}
JSON

harmonika skills execute doc.read_pdf --root /absolute/project <<'JSON'
{"path":"/absolute/project/report.pdf"}
JSON

harmonika skills status JOB_ID --root /absolute/project --after-sequence 0
harmonika skills stop JOB_ID --root /absolute/project
```

CLI menentukan principal dari UID OS. Library menyediakan
`SkillExecutor.execute(skill_id, input, ExecutionContext, idempotency_key, on_event)`.
Context wajib dibuat boundary autentikasi tepercaya: principal, project root,
allowed skills, can_write dan interface. Jangan menerima context dari body JSON
pengguna. Root proyek/perangkat server tidak otomatis menjadi workspace akun publik.

Envelope hasil:

```json
{"schema_version":1,"request_id":"uuid","job_id":"uuid","skill_id":"doc.create_pdf","status":"completed","result":{"created":"/absolute/project/report.pdf","bytes":1433,"format":".pdf"},"replayed":false}
```

Status: `completed`, `degraded`, `clarification_required`, `unavailable`, `failed`, `stopped`, `unknown`. Penolakan sebelum
job dibuat memakai `SkillError.code`, misalnya `permission_denied`, `scope_denied`,
`idempotency_required`, `idempotency_conflict`, `in_progress`, `recovery_required`.
CLI error di stderr JSON dan exit nonzero; descriptor/schema bukan janji semua
konten atau fitur layout dokumen didukung.

Callback event nyata `job.queued`, `job.started`, kemudian terminal `job.completed`,
`job.degraded`, `job.failed`, `job.stopped` atau `job.unknown`. Setiap event punya
job_id, event_id, sequence dan timestamp. Event yang disimpan tidak berisi dokumen,
prompt, paragraph, konten atau raw error parser. Status/replay metadata dibatasi
100 event per pembacaan, dengan `after_sequence`. Bukan endpoint SSE/WSS baru.

## Keandalan dan keamanan

- Write memerlukan idempotency key; scope key adalah principal + identitas filesystem proyek canonical (device/inode).
  Key yang sama dengan input/operasi berbeda ditolak. Respons write sukses dapat
  direplay setelah executor dibuka kembali tanpa menjalankan worker lagi.
- Jurnal SQLite privat `0600`, direktori `0700`; penyimpanan izin filesystem,
  bukan enkripsi. Konten hasil pembacaan dan spec dokumen tidak disimpan. Respons
  write menyimpan metadata output privat; key disimpan sebagai hash.
- Satu job aktif per akun/proyek, maksimal empat worker aktif pada jurnal bersama.
  Metadata maksimal 10.000 job; selesai lebih dari 24 jam dibersihkan ketika submit.
  Write lama berstatus tidak pasti tidak dieksekusi ulang secara otomatis.
- Stop mematikan hanya subprocess worker yang dibuat invocation tersebut. Worker
  dokumen tidak membuat child shell atau proses tool lain. Tidak ada sinyal ke
  sesi Codex/OpenCode/SSH milik pengguna.
- Kalau output sudah terpublikasi tepat sebelum Stop/timeout/error, status
  `unknown`: periksa file dan status sebelum retry; tidak ada klaim rollback.
  File yang sudah ada tidak dianggap otomatis milik job yang sama atau sukses.
- Worker `python -I`, CPU 20 detik, deadline dokumen maksimal 30 detik (pengukuran/visual SEO maksimal 75 detik), output
  maksimal 1 MiB dan stderr 8 KiB; Linux juga membatasi address space 512 MiB.
  ReportLab memakai HOME sementara. Batas PDF/Office mengikuti kontrak dokumen.
- Context dan setiap path/referensi divalidasi sebelum spawn. File credential,
  symlink arbitrer, traversal dan output di luar proyek ditolak. Atomic publication
  tanpa overwrite tetap dilaksanakan handler. Parent directory harus dikuasai
  workspace pengguna; ini bukan sandbox OS terhadap proses lokal lain yang sengaja
  mengganti mount/direktori saat job berjalan.
- Programmatic callbacks harus nonblocking; coroutine callback dibatasi satu detik.
  Callback gagal tidak memicu eksekusi ulang dokumen.

## Batas fase ini

TUI memiliki CLI baru, sementara perintah `harmonika skill documents` lama tetap
berfungsi. Belum ada tool MCP executable shared tambahan, REST publik, WSS, UI web,
worker remote, atau koneksi otomatis ke orchestrator LLM. Client web kelak memakai
service facade yang menyusun context dari autentikasi dan workspace milik akun.
Kesiapan sembilan operasi pada komputer pemilik bukan akses filesystem server dari
API publik. Engine inference dan worker RTX tidak diubah pada fase ini.

Pengujian dan rilis: [SHARED-SKILLS-REPORT.json](SHARED-SKILLS-REPORT.json). Registry disusun tim AI, executor/integrasi oleh Codex, dan audit keamanan oleh OpenCode serta reviewer tim; Claude tidak dilibatkan.

## Ekstensi SEO

Empat belas operasi `seo.*` ditambahkan setelah fase dokumen: [kontrak SEO](SEO.md). Descriptor tetap inert, readiness ditentukan worker. Replay write SEO menyimpan metadata artifact saja; laporan penuh dibaca dari file proyek, bukan jurnal. Timeout pengukuran/visual maksimal 75 detik dan lease stale internal 120 detik; client publik belum diaktifkan.
