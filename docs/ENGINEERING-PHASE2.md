# Phase 2 — Power Coding Agent

Implementasi Phase 2A–2F menambahkan **20 ID** ke 18 operasi workspace sebelumnya.
Status aktivasi dan bukti akhir dicatat di `ENGINEERING-PHASE2-STATUS.json`.
Pendaftaran ID bukan bukti kesiapan: client harus membaca `/v1/coding/capabilities`.

| Fase | Implementasi | Gate pengujian |
|---|---|---|
| 2A | scaffold.project, scaffold.component, scaffold.api, code.diff, code.patch | Starter nyata; patch satu file terikat SHA256; publikasi COW atomik |
| 2B | code.format, format.run, lint.run, type.check, build.run, pkg.list, pkg.install | Toolchain Linux terpin, formatter/type checker nyata, artifact build, import paket pada container berikutnya |
| 2C | test.single, test.coverage | Tepat satu tes terpilih, coverage terukur, kegagalan tes menghasilkan job failed |
| 2D | code.rename, refactor.rename, refactor.extract, refactor.dead_code | Binding/import lintas file, perilaku sebelum/sesudah, perubahan parsial gagal dibuang |
| 2E | debug.attach, debug.breakpoint | DAP nyata: entry, breakpoint, inspect, step, Stop, ownership dan TTL |
| 2F | API/TUI, regresi dan aktivasi | Tool discovery maksimal 32 definisi, regresi 18 skill, public Bearer smoke, paket bertanda tangan dan rollback |

Pengerjaan sesi ini dilakukan **Codex dan OpenCode**, tanpa Claude. Jadwal kalender
pada rencana awal digantikan eksekusi aktif seluruh subfase dalam sesi ini.

## Kontrak bersama

- Base URL tetap `https://chat.harmonika.id/v1`; Bearer key Telegram yang sama.
- Workspace privat per token. Proyek lokal terminal tidak otomatis disalin ke server.
- Chat biasa dengan `workspace_id` memakai pemilihan tool internal. Helper discovery
  menjaga batas 32 definisi per panggilan; pengguna tidak perlu memilih ID skill.
- `POST /coding/workspaces/{id}/invoke` menerima `skill_id`, `input`, dan
  `Idempotency-Key`. Mutasi/hash/approval memakai kontrak yang sama dengan Phase 1.
- Eksekusi source, package install, formatter/refactor, build, test, commit dan
  peluncuran debugger memerlukan approval client. Model tidak menyetujui dirinya sendiri.
- Read-only lookup tetap scoped. `refactor.dead_code` dengan `apply=false` hanya inspeksi.

## Dukungan nyata dan batasnya

- Starter: Python HTTP, Node HTTP, komponen React JSX, helper route Python.
- Format: Black Python, Prettier JS/TS/JSON. Lint: Ruff Python, pemeriksaan sintaks
  JavaScript, diagnostik compiler TypeScript; bukan klaim ESLint lengkap.
- Type check: mypy atau TypeScript compiler dengan konfigurasi terkontrol.
- Build: zipapp entry-point Python atau bundle esbuild JS/TS/JSX/TSX. Bukan arbitrary
  `npm run build`/framework deployment.
- Rename/extract: analisis Python melalui rope. Rename bukan replace regex global.
- Dead-code: diagnostik unused import; apply hanya perbaikan import yang aman.
- Patch: satu file UTF-8 existing, exact unified diff dan SHA256. Binary, multi-file,
  create/delete melalui patch, serta kasus EOF tanpa newline ditolak eksplisit.
- Paket offline: **humanize 4.12.2** dan **decimal.js 10.5.0**. Installer memakai
  cache terverifikasi dan tidak menjalankan lifecycle script atau akses registry.
  Manifest dependency dipulihkan ke `/tmp` setiap container baru.
- Dependensi toolchain dibangun dengan Python wheel hash dan npm lock/integrity.
  Snapshot source tidak menyimpan seluruh virtualenv atau node_modules.

## Debugger terpisah dari job

`debug.attach` memulai Python source milik workspace dalam container privat, lalu
mengembalikan `result.debug_session.id`. Tidak menerima PID host atau attach terminal.
`debug.breakpoint` memakai `session_id`, `path`, dan `lines`.

| Method | Endpoint | Fungsi |
|---|---|---|
| GET | `/v1/coding/debug/sessions/{id}` | Status/recovery milik token |
| GET | `/v1/coding/debug/sessions/{id}/events` | JSON atau SSE replay, `Last-Event-ID`/`after_sequence` |
| POST | `/v1/coding/debug/sessions/{id}/actions` | `{action: status\|continue\|next\|inspect}`, Idempotency-Key |
| POST | `/v1/coding/debug/sessions/{id}/stop` | Stop idempotent, export snapshot tervalidasi |

`inspect` hanya stack/scopes/variables, tanpa expression evaluate. Satu sesi global,
maksimal 300 detik dan idle 60 detik. Sesi menahan satu slot eksekusi. Perubahan
source dan eksekusi lain pada workspace dikunci selama sesi debug aktif. Restart
menandai sesi interrupted; tidak menjalankan ulang kode diam-diam. Perubahan file
hanya dipublikasikan setelah Stop dengan snapshot valid dan versi workspace sama.

## Isolasi dan pemulihan

Container network=none, root filesystem read-only, user10000, cap-drop ALL, tanpa
mount source host yang writable, CPU/memori/PID/output terbatas. Source tetap
16MiB/256 file/512KiB per file. Job maksimum120 detik; debug punya TTL sendiri.
Snapshot memakai tmpfs volume keeper dan publikasi generation COW/CAS. Refactor,
formatter, package/build yang gagal tidak menerbitkan perubahan parsial.

`deploy/activate-phase2.py` mensyaratkan bukti smoke20, toolchain, debugger nyata,
regresi dan public contract; memeriksa source produksi belum berubah, membuat
backup, lalu mengganti hanya runtime coding dan gateway chat. Engine inference,
layanan CS/ERP dan sesi terminal pengguna tidak menjadi target restart.

## Perintah verifikasi

```sh
harmonika coding capabilities
python3 -m unittest discover -s tests -p 'test_phase2*.py' -v
python3 backend/coding_runtime/acceptance_phase2.py --help
harmonika coding debug-status SESSION_ID
harmonika coding debug-events SESSION_ID --after-sequence 0
harmonika coding debug-action SESSION_ID inspect --idempotency-key inspect-001
harmonika coding debug-stop SESSION_ID
```

Smoke Linux membaca private service secret dari server; secret tidak dicetak,
tidak masuk paket, dan tidak diberikan ke TUI. Pengujian lokal yang membutuhkan
Docker/toolchain boleh skip secara eksplisit; gate aktivasi tetap meminta uji
Linux nyata. Tidak ada commit/push atau auto-update binary native pada fase ini.
