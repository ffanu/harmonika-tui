# Rencana SQLite WAL Terkontrol — Harmonika

Status: **proposal untuk root**. Tidak ada mode jurnal yang diubah pada database
aktif oleh tugas ini. Helper migrasi/revert tersedia dan teruji; aktivasi
menjadi keputusan root setelah prasyarat di bawah dipenuhi.

Pemindaian read-only dilakukan 2026-10-25 terhadap sumber
`/Users/id/Projects/harmonika-tui` (worktree `/tmp/harmonika-wal-worker`) dan
VPS `chat.harmonika.id`. Tidak ada isi chat, key, atau baris data yang dibaca
atau dicetak; hanya header file, mode, permission, dan status integritas.

## Temuan live (read-only, VPS)

| Database | Path | Mode header | Perm | quick_check ro | Sidecar |
|---|---|---|---|---|---|
| Discovery | `/var/lib/harmonika-master/discovery.sqlite3` (24 KiB) | delete | 0600/0700 | ok | tidak ada |
| Core | `/var/lib/harmonika-core/core.sqlite3` (48 KiB) | delete | 0600/0700 | ok | tidak ada |
| Unified | `/var/lib/harmonika-unified/state.sqlite3` (116 KiB) | delete | 0600/0700 | ok | tidak ada |
| RTX queue | `/var/lib/harmonika-rtx-queue/queue.sqlite3` (28 KiB) | **wal** | 0600/0700 | ok | wal/shm absen (idle, ter-checkpoint) |

Audit tidak memindahkan path data RTX. Direktori layanan aktif tetap
`/var/lib/harmonika-rtx-queue`. Semua database aktif dipertahankan selama evaluasi.

## Matriks keputusan WAL vs DELETE

| Database | Rekomendasi | Alasan |
|---|---|---|
| RTX queue | **WAL (sudah, dipertahankan)** | Antrean async multi-koneksi; writer+reader serentak; sudah aktif dan sehat. |
| Discovery | **tetap DELETE** | Satu proses systemd, koneksi per-panggilan yang selalu ditutup. Saat semua koneksi tutup, sidecar `-wal/-shm` dihapus, sehingga pembaca ro eksternal justru bisa gagal di jendela idle (WAL bukan obat otomatis untuk read-only shm). Kontensi internal sudah ditangani `busy_timeout=5000`. |
| Core (`core_engine`) | **tetap DELETE** | Source **memaksa** `PRAGMA journal_mode=DELETE` di `backend/core_engine/state.py:340` setiap koneksi, plus `synchronous=FULL`+`secure_delete` untuk blob terenkripsi/sealed. Koneksi per-transaksi (`BEGIN IMMEDIATE`, tutup tiap panggilan) membuat WAL nyaris tanpa keuntungan; jika header dipaksa WAL tanpa patch source, PRAGMA per-koneksi akan membalikkan mode balik ke DELETE (atau gagal BUSY), jadi aktivasi tanpa patch source dilarang. |
| Unified | **tetap DELETE** | Memakai `Store` yang sama dengan core (`backend/unified_engine/state.py:4-5`), sehingga turut memaksa DELETE; alasan identik dengan core. |
| Cache sesi lokal (`session-cache.sqlite3`) | **tetap DELETE** | `runtime/tools/state_store.py:45` memaksa DELETE. DB kecil (≤128 baris metadata), koneksi .5s per operasi, satu proses pengguna. WAL hanya menambah churn sidecar. |
| Native session DB (aplikasi binary) | **WAL milik binary; jangan dimigrasi** | Header sudah WAL, dikelola aplikasi native. Alat Harmonika hanya boleh memeriksa read-only; lihat patch proposal diagnostik. |

Jika kelak root tetap menginginkan WAL pada discovery/core/unified (misal untuk
reader audit serentak saat scan/write berlangsung), prasyarat wajib:

1. Patch source yang memaksa DELETE: `backend/core_engine/state.py:340`
   (berlaku juga untuk unified) dan `runtime/tools/state_store.py:45` — ganti
   menjadi membaca mode sekali saat init, bukan men-set tiap koneksi.
2. Koneksi service harus **persisten** (bukan per-panggilan) agar sidecar
   `-shm` stabil untuk pembaca read-only.
3. Jalankan helper ini di jendela quiescence; jangan pernah `PRAGMA journal_mode`
   sambil ada koneksi lain aktif (helper menolak dengan `database_busy`).

## Batasan read-only + WAL (bukti)

`mode=ro` pada DB header-WAL **gagal** (`unable to open database file`) bila
`-shm` tidak ada dan koneksi ro tidak boleh membuatnya; matriks empiris
(SQLite 3.45.1 di VPS, 3.51.0 lokal):

| Skenario | hasil ro |
|---|---|
| Writer terbuka, `-shm` ada | ok |
| Close bersih, sidecar hilang | ok (wal kosong) |
| `-wal` berisi frame, `-shm` absen, direktori writable | ok (heap wal-index, tanpa writer) |
| `-wal` berisi frame, `-shm` absen, direktori read-only | **gagal** |

Inilah kegagalan `native_readonly_diagnostic` pada audit
`docs/SYSTEM-INTEGRATION-AUDIT-2026-10-25.json` (native store header WAL, shm
absen). Solusi aman bukan `immutable=1` (bisa menghilangkan transaksi WAL aktif)
melainkan salinan diagnostik — lihat patch proposal.

## Helper `scripts/sqlite-maintenance.py`

Subperintah: `inspect`, `backup`, `migrate-wal`, `revert-delete`, `checkpoint`.

Jaminan desain:

- **Identitas file**: lstat `(st_dev, st_ino)` diverifikasi sebelum dan sesudah
  setiap langkah; `nlink==1`, owner uid, bukan symlink (file maupun direktori;
  alias sistem `/var → /private/var` diizinkan agar path macOS sah tidak salah ditolak).
- **Backup online API**: `sqlite3.Connection.backup` — snapshot menangkap commit
  yang masih ada di WAL (dibuktikan tes), diverifikasi `quick_check` sebelum
  migrasi/revert dilanjutkan.
- **Quiescence/lock**: probe `BEGIN EXCLUSIVE` dengan retry terbatas; `-journal`
  panas (writer DELETE aktif) ditolak `hot_journal_present`; kunci sibuk →
  keluar `database_busy` (exit 3) tanpa mengubah mode.
- **Timeout transaksi**: `busy_timeout` terbatas + progress-handler watchdog
  (default 10 dtk) di setiap koneksi sehingga tidak mungkin hang.
- **Checkpoint aman**: `wal_checkpoint(TRUNCATE)`; gagal-busy dilaporkan, tidak dipaksa.
- **Permission**: direktori `0700`, file/sidecar `0600` ditegakkan; pelanggaran
  group/other-write ditolak permanen (bahkan dengan `--fix-permissions`).
- **Larangan live user DB**: path `/.config/harmonika/`, `/.harmonika/`, atau
  nama `session-cache.sqlite3`/`sessions.db` ditolak kecuali
  `--allow-live-user-db` eksplisit.
- **Bukan bagian data**: hanya metadata header/PRAGMA yang dicetak; tidak ada
  baris tabel, chat, atau key.

Kode keluar: 0 ok · 2 ditolak/unsafe · 3 busy · 4 integritas/error DB.

### Runbook aktivasi (hanya oleh root, setelah prasyarat)

```sh
python3 scripts/sqlite-maintenance.py inspect  DB          # verifikasi awal read-only
python3 scripts/sqlite-maintenance.py backup   DB --backup-dir /root/wal-backups
python3 scripts/sqlite-maintenance.py migrate-wal DB        # quiescence → WAL → checkpoint → 0600
python3 scripts/sqlite-maintenance.py inspect  DB          # verifikasi pasca
```

Rollback:

```sh
python3 scripts/sqlite-maintenance.py revert-delete DB      # backup → checkpoint → DELETE → sidecar bersih
```

Jika revert tidak berjalan (busy), `checkpoint` + tunggu jendela idle, lalu ulangi.

## Bukti pengujian (lokal, SQLite 3.51.0 / Python 3.9.6)

`tests/test_sqlite_maintenance.py` — **20/20 lulus** (~11 dtk), mencakup:

- writer terbuka + pembaca ro dengan shm (`inspect` ok, mode wal).
- sidecar hilang: header tetap terbaca wal; inspeksi **tidak pernah membuat
  sidecar** di sekitar file asli.
- snapshot backup memuat commit WAL yang belum ter-checkpoint.
- roundtrip migrate→revert: data utuh, header kembali `delete`, sidecar bersih,
  permission 0600/0700, file backup 0600.
- busy: reader-lock → `database_busy` (exit 3) tanpa perubahan mode; writer
  DELETE aktif → `hot_journal_present`; reader WAL menghalangi revert sampai
  ditutup, lalu revert sukses.
- symlink file & direktori ditolak; identitas (dev,ino) swap terdeteksi.
- permission 0644/0755 ditolak lalu dipulihkan `--fix-permissions`; 0666 ditolak permanen.
- guard live-user-db aktif (dan bisa dilewati eksplisit pada fixture).
- checkpoint TRUNCATE memangkas WAL ke 0 saat writer idle.
- inspeksi tetap selesai <10 dtk saat DB dikunci eksklusif (watchdog).

Patch proposal `proposals/0001-state-store-readonly-wal-diagnostic.patch`:
applies cleanly; suite `tests/test_state_store.py` existing **18/18 lulus**
terhadap file yang dipatch; fallback diagnostik tervalidasi pada skenario
direktori read-only + WAL tanpa shm (status ok, `snapshot=diagnostic_copy`,
original tidak tersentuh, tanpa pembuatan shm di lokasi asli).

## Yang tidak dilakukan tugas ini

- Tidak mengubah mode jurnal DB mana pun di produksi/lokal.
- Tidak restart/kill service, tidak menyentuh DB sesi pengguna asli.
- Tidak mengedit kode engine aktif; patch state_store hanya proposal file tersendiri.
- Tidak ada push.

## Review dan keputusan Codex

Tidak dilakukan migrasi massal pada database aktif. RTX queue sudah WAL;
worker vision baru memakai WAL pada journal privatnya. Core/unified/discovery
masih DELETE, kecil dan sehat. WAL bukan perbaikan otomatis untuk read-only SHM;
terutama source core dan unified mengatur DELETE setiap koneksi sehingga perubahan
mode melalui SQL saja akan dibalikkan oleh aplikasi.

Helper root telah diperketat sesudah review OpenCode: backup mempunyai deadline
riil pada callback SQLite backup (progress handler SQL saja tidak cukup), sidecar
symlink/owner/permission tidak sah ditolak, dan tidak ada penghapusan WAL/SHM manual.
`inspect` hanya membaca header bila WAL-index hilang; SQLite ro Linux dapat membuat
sidecar bila dipaksa membuka cold WAL. Diagnosis salinan harus diminta eksplisit,
dibatasi64MiB per file, tidak menyalin SHM, dan tidak membuktikan integritas snapshot
transaksi asli. Salinan diagnostik tidak boleh dipakai sebagai backup atau resume.

Verifikasi root:23tes macOS dan23tes Linux sebagai user tanpa privilege lulus.
Source helper terpasang di `/opt/harmonika-ai/operator/sqlite-maintenance.py`.
Database sesi native pengguna tidak dimigrasikan, direpair atau dihapus.
