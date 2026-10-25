# Engineering workspace MVP

18 operasi executable memakai satu runtime backend. Ini melengkapi empat workflow
review/debug/testing/documentation yang bekerja dari bukti teks; bukan menggantikannya.
188 operasi roadmap belum semuanya diimplementasikan.

| Kelompok | Operasi |
|---|---|
| Filesystem | fs.read, fs.write, fs.list, fs.tree, fs.glob |
| Kode | code.read, code.write, code.edit, code.search, code.grep |
| Eksekusi | exec.shell, exec.python, exec.node, exec.background, test.run |
| Git | git.status, git.diff, git.commit |

## Cara memakai

Bearer key Harmonika yang sudah ada tetap berlaku. Workspace disimpan di server
secara privat per token; proyek lokal TUI tidak otomatis ditransfer.

```sh
harmonika coding capabilities
harmonika coding create Demo --write --execute --git-commit
harmonika coding list
```

Chat biasa dengan `workspace_id` membuat engine memilih skill secara otomatis:

```json
{
  "model": "harmonika-ai",
  "workspace_id": "UUID_DARI_CREATE",
  "stream": true,
  "messages": [{"role":"user","content":"Buat main.py yang mencetak 42, lalu siapkan pengujiannya"}]
}
```

Kirim ke `POST https://chat.harmonika.id/v1/chat/completions` dengan
`Authorization: Bearer KEY`, `Content-Type: application/json`, dan
`Idempotency-Key: UUID_PER_TURN`. Client function tools yang dikirim eksplisit tetap
menggunakan alur lama; remote workspace tidak mengambil alih tool lokalnya.

Perintah terminal setara menerima array messages melalui stdin:
`harmonika coding chat WORKSPACE_ID --idempotency-key UUID`.

## Kontrak REST

Prefix: `https://chat.harmonika.id/v1/coding`.

| Method | Path | Fungsi |
|---|---|---|
| GET | /capabilities | Schema 18 operasi, readiness dan limits aktual |
| GET/POST | /workspaces | Daftar sendiri/buat workspace dengan permissions eksplisit |
| GET | /workspaces/{id} | Metadata dan versi workspace |
| POST | /workspaces/{id}/invoke | `{skill_id,input}`; Idempotency-Key wajib untuk write/exec/Git |
| POST | /workspaces/{id}/agent | `{messages,stream}`; skill dipilih otomatis |
| GET | /jobs/{id} | Recovery status/result/input_hash |
| GET | /jobs/{id}/events | JSON atau SSE dengan Accept:text/event-stream |
| POST | /jobs/{id}/approve | `{decision:"approve",input_hash:"..."}` |
| POST | /jobs/{id}/stop | Hentikan job sendiri |

Permissions: `write`, `execute`, `git_commit`, default false. Git commit memerlukan
write+execute+git_commit. Tidak ada API arbitrary host path atau PID.

`exec.*`, `test.run`, dan `git.commit` mengembalikan `approval_required` lebih dulu.
Client menampilkan preview dan meminta persetujuan pengguna sebelum approve. Model
hanya melihat 18 tool; tidak punya tool untuk menyetujui jobnya sendiri. Approval
terikat hash input, versi workspace, dan TTL 300 detik. Perubahan source membuat
approval lama ditolak. Terminal mendukung `coding approve JOB --input-hash HASH
--confirm` sebagai aksi pengguna, bukan aksi otomatis model.

Retry memakai key sama mengembalikan job yang sama. Key sama dengan input berbeda
menghasilkan `idempotency_conflict`. Restart menandai pekerjaan aktif interrupted,
tidak menjalankannya ulang. Event `job.queued`, `approval.required`,
`approval.accepted`, `job.started`, `process.output`, `job.completed|failed|timeout|stopped`
memiliki event_id/job_id/sequence/timestamp/type/data. Replay mendukung
`after_sequence` dan `Last-Event-ID: JOB:SEQUENCE`. Log event maksimal 256/job,
disimpan sampai 24 jam; source tidak dicetak ke application log.

## Batas MVP

- Maksimum tiga workspace/token, 50 secara global; 256 file, 512 KiB/file,
  16 MiB total/workspace. Directory depth dibatasi. File baru boleh membuat parent.
- Symlink, hardlink, FIFO/special file, path traversal dan berkas credential ditolak.
  Pencocokan credential konservatif dapat menolak nama source mengandung password/secret.
- `code.read` mengembalikan SHA256; overwrite memerlukan expected_sha256; edit harus
  menemukan substring unik. Search symbol Python menggunakan AST, bahasa lain best effort.
- Maksimum 30 permintaan eksekusi/jam/token, 120 operasi/menit/token, satu job aktif/token,
  dua eksekusi dan empat antrean global; delapan langkah agent/turn, 180 detik/turn.
- Command maksimal 120 detik, CPU1/RAM512MiB/PID64 per container, tanpa jaringan,
  root filesystem read-only, UID10000, semua capabilities dibuang.
- Source diimpor ke volume tmpfs 32MiB; source host tidak di-bind-mount. Container
  helper mengekspor snapshot tervalidasi, lalu host menerbitkan generasi baru secara atomik.
- Satu keeper menjaga volume tmpfs tetap terpasang selama import/eksekusi/export.
  Watchdog membatasi umur container jika proses gateway terhenti. Log Docker dimatikan.
- Output 512 KiB/stream pada sandbox, result publik dipotong 64 KiB/stream. Event
  dipotong dan teredaksi. Jika ekspor gagal, perubahan tidak diklaim sudah tersimpan.
- `test.run` mendukung unittest, node --test, npm test dengan dependency yang sudah ada.
  Image MVP tidak menyediakan seluruh compiler, Docker-in-Docker, debugger atau network install.
- Background adalah job HTTP asinkron berumur terbatas, bukan daemon permanen.
  Git commit tersimpan dalam workspace privat, tidak di-push ke GitHub.

Container berbagi kernel host; batas ini bukan isolasi VM. Detail kontrol mengikuti
[dokumentasi Docker run](https://docs.docker.com/engine/containers/run/) dan
[tmpfs](https://docs.docker.com/engine/storage/tmpfs/).

## Operasional

Backend memerlukan Python >=3.11; installer menolak versi lebih lama.
Source: `backend/coding_runtime/`. Service baru hanya loopback 18187, secret privat
server terpisah dari Bearer publik. Gateway Go menurunkan principal dari token DB
terverifikasi dan tidak meneruskan identity/secret dari client.

`deploy/prepare.sh` menyiapkan secret tanpa mencetaknya; unit systemd membatasi
resource proses runtime. Build image dari Dockerfile; jalankan `deploy/smoke.py`
sebelum membuat ready marker/menyalakan capability. Hanya service coding dan gateway
panel terkait boleh di-restart. Core inference dan domain lain tidak perlu di-restart.

Status hasil aktual dan pengujian tercatat di `CODING-MVP-REPORT.json`.
