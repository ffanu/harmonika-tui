# Harmonika Web Full-Stack Builder

Engine proyek privat untuk membaca spesifikasi, memilih stack, membuat source/config,
menjalankan pemeriksaan sandbox dan menggunakan target deployment yang terdaftar.
Dokumen master berisi161 ID dalam14 layer; label155 pada judul tidak sama dengan isi.
Tidak ada klaim bahwa seluruh penyedia cloud/database sudah aktif hanya karena ID ada.

## Kontrak publik

Base URL `https://chat.harmonika.id/v1/builder`, Bearer key Harmonika yang sama.
Identity berasal dari ID token gateway; header principal dari client diabaikan.

| Method | Path | Fungsi |
| --- | --- | --- |
| GET | `/capabilities` |161 descriptor, schema, readiness, mode dan batas aktual |
| GET / POST | `/projects` |Daftar/buat proyek privat; create perlu Idempotency-Key |
| GET | `/projects/{id}` |Revisi dan metadata source files |
| POST | `/projects/{id}/files` |`path`, `content` atau `bytes_base64`, `expected_revision` |
| POST | `/projects/{id}/invoke` |`skill_id`, `input`, optional `expected_revision`, `overwrite` |
| POST | `/projects/{id}/build` |`name`, `spec`, `stack=auto`, `run_checks` |
| POST | `/projects/{id}/deploy` |Target terdaftar, environment, expected_revision; selalu approval |
| GET | `/projects/{id}/artifact` |Source ZIP privat, Bearer, attachment + nosniff |
| GET | `/jobs/{id}` |Recovery status/result tanpa mengeksekusi ulang |
| GET | `/jobs/{id}/events` |JSON atau SSE; after_sequence / Last-Event-ID |
| POST | `/jobs/{id}/approve` |`decision=approve`, `input_hash`; terikat revisi |
| POST | `/jobs/{id}/stop` |Stop lokal, tidak mempublikasikan source terlambat |
| GET | `/jobs/{id}/deployment` |Status provider untuk deployment milik job/token |

Invoke/build/deploy memakai `Idempotency-Key` unik. Replay payload sama mengembalikan
job lama; perubahan payload409. Request mendapat202 dan job async; ini tidak berarti
source sudah ditulis, test lulus atau situs sudah deployed.

State: `queued`, `awaiting_approval`, `running`, `completed`, `failed`, `stopped`,
`interrupted`. Event: `job.queued`, `job.awaiting_approval`, `job.progress`,
`job.completed`, `job.error`, `job.stopped`, lalu SSE `[DONE]` saat terminal.
Event membawa event_id,job_id,sequence,timestamp,type,data. Tidak ada token-stream palsu.

`completed` dibaca bersama `execution_kind`: analysis/artifact_generation/workspace_read/
sandbox_execution/deployment_submission/operator_recipe. Recipe bukan perubahan host;
submission bukan READY. Job dapat completed dengan provider masihQUEUED/BUILDING.
Stop/shutdown tidak menjamin pembatalan operasi remote yang sudah dikirim. Hasil remote
harus dipulihkan/diperiksa; jangan mengirim deployment ulang secara buta saat timeout.

## Keamanan dan isolasi

- Proyek, payload/result job, source dan event memakai AES-256-GCM dengan AAD actor/id.
- Satu token maksimal20 proyek,2 job aktif,128MiB data; source proyek16MiB/256file,
  maksimal512KiB/file, inputJSON1MiB; antrean global32 queued/running.
- Path relatif ketat; tidak menerima `/etc`, traversal, symlink atau path sesi pengguna.
- Publish snapshot atomik dengan revision-CAS; kegagalan/Stop tidak menulis sebagian.
  Overwrite memerlukan revisi yang telah dibaca dan opt-in eksplisit.
- Kode/SQL/test hanya dieksekusi di container per job: non-root, networknone,
  read-only-rootfs, tanpa host bind mount, capdropALL, pid/RAM/CPU/time terbatas.
- Sandbox web memberi256pid/1GiB agar Chromium dapat berjalan; image coding lama tidak
  diubah. Pengujian browser memakai `--disable-gpu`, bukan GPU/DRM host.
- Tidak mencetak source/spec pelanggan, prompt lengkap, OCR, key atau stdout test ke log.
  Output eksekusi hanya ada pada hasil job privat terenkripsi.

Isolasi per API token. Integrator satu key untuk banyak pengguna harus mengotorisasi
project_id pada server aplikasinya. Jangan menaruh key server di browser publik.

## Kesiapan per mode

Pembaca markdown/TXT/JSON/Obsidian membaca file yang diunggah ke proyek; bukan seluruh
filesystem/vault komputer. Voice memakai adaptor ASR CPU terisolasi yang ada, bila siap.
Notion memakai halaman yang diizinkan secara eksplisit oleh operator untuk principal.
ID salah eja `spec.read.notation` dipertahankan agar cocok dengan dokumen master.

Selektor stack/arsitektur/desain adalah analisis deterministik dari input, bukan
jaminan memilih arsitektur terbaik atau kutipan harga cloud terkini. Negasi/penghilangan
fitur dipertahankan; konflik dan ambiguitas harus diklarifikasi.

Scaffolds/generators menghasilkan source yang dapat diperiksa. Ia tidak memasang
semua library, menjalankan semua test atau mengaktifkan akun payment. App spec bebas
belum tentu terwakili seluruhnya oleh template; tinjau requirements dan workflow bisnis.
`build --run-checks` melakukan preflight sintaks AST/JSON nyata, bukan mengklaim seluruh
unit/integration/browser test sudah dijalankan. Panggil job test yang relevan sesudahnya.

DB migration/seed yang dieksekusi saat ini adalah SQLite di sandbox proyek sendiri,
bukan database perusahaan yang sedang dipakai. Dialek/engine lain menghasilkan schema
atau deployment recipe; perlu target/config dan verifikasi sebelum apply eksternal.

SSH: target alias, principal whitelist, akun non-root, identity_file dan known_hosts
milik operator, StrictHostKeyChecking=yes. Connect/exec hanya diagnostik tetap;
upload/download file di root target terbatas, nofollow dan hash-check. Setup/tunnel
adalah recipe yang ditinjau, bukan perubahan firewall/daemon/tunnel yang sudah aktif.

Deployment live awal: adapter Vercel dengan target/token operator dan izin biaya;
preview default, production memerlukan izin tambahan dan approval job. Provider lain
menghasilkan konfigurasi/recipe; tidak mengaku sudah tersambung atau membelanjakan saldo.
Tidak ada DNS/domain baru, akun berbayar, atau layanan perusahaan yang diubah untuk demo.

## Terminal

```text
harmonika builder capabilities
harmonika build from spec.md --stack fastapi --run-checks
harmonika builder status JOB_UUID
harmonika builder approve JOB_UUID --input-hash HASH --confirm
harmonika builder events JOB_UUID --after-sequence 0
harmonika builder download PROJECT_UUID project.zip
harmonika builder invoke PROJECT_UUID fe.gen.page < input.json
harmonika builder deploy PROJECT_UUID --target staging --expected-revision 3
harmonika builder deployment JOB_UUID
```

Key dibaca dari penyimpanan privat yang sudah ada, bukan argv atau file artifact.
Native binary dan sesi aktif tidak diganti; update paket terminal manual bertanda tangan.

## Konfigurasi operator

Secret baru: `/etc/harmonika-ai/builder-master.key` (32bytes AES),
`builder-engine.token` (transport gateway privat). Keduanya root0600, tidak di repo.
`builder-connectors.json` opsionalroot0600 berisi target/principal yang diperbolehkan,
bukan credential dari payload publik. Berkas key/token target hanya di
`/etc/harmonika-ai/builder-targets/`, path canonical dan tanpa symlink.

Contoh struktur tanpa credential:

```json
{
  "targets": {"staging": {"principals": ["api-token:123"], "host": "server.example", "port": 22, "user": "builder", "root": "/srv/myapp", "identity_file": "/etc/harmonika-ai/builder-targets/staging.key", "known_hosts": "/etc/harmonika-ai/builder-targets/known_hosts"}},
  "deployments": {"web-preview": {"principals": ["api-token:123"], "provider": "vercel", "project": "myapp", "framework": "nextjs", "token_file": "/etc/harmonika-ai/builder-targets/vercel.token", "cost_authorized": true, "production_authorized": false}},
  "notion": {"specs": {"principals": ["api-token:123"], "page_ids": ["00000000-0000-0000-0000-000000000000"], "token_file": "/etc/harmonika-ai/builder-targets/notion.token"}}
}
```

Tanpa target/key tersebut, capability mengembalikan unavailable/configuredfalse dan
request tidak memalsukan koneksi. Secret inference, member, CS dan key enkripsi legal
bukan pengganti credential target builder.

Generator autentikasi OAuth memerlukan penyelesaian userinfo/local session sebelum
login browser nyata. Generator pembayaran hanya memverifikasi signature dan
mencatat event; tidak menandai pembayaran lunas tanpa rekonsiliasi gateway.
