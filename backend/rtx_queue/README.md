# Harmonika RTX Queue

Backend media **async** terpisah dari TUI/chat. Mengantrekan job generate/analyze,
menghubungi worker Windows RTX melalui HTTP, dan menyimpan hasil sebagai file
privat. Tidak memasang model atau mengubah driver GPU di server Linux.

Implementasi: Python **3.11+**, aiohttp, SQLite dan Pillow. Runtime server yang
diuji: Python 3.12.3. Dependency terpasang di venv service tersendiri.

## Jalur aktif

```text
Caller backend → 127.0.0.1:18186 → SQLite queue → RTXAdapter HTTP
                                           → tunnel privat :18182 → Windows RTX
```

Service: `harmonika-rtx-queue.service`. Data: `/var/lib/harmonika-rtx-queue`.
Source server: `/opt/harmonika-ai/rtx-queue`. Endpoint ini **loopback privat**;
tidak ditambahkan ke Nginx publik atau APK. Engine chat existing tidak direstart.
Jalur generate lama tidak diganti otomatis oleh komponen antrean baru ini.

Worker existing memakai mode `legacy`: generate nyata/CUDA aktif, sedangkan
vision dan reference image belum tersedia pada worker itu. Mode `v1` untuk
vision/reference sudah diimplementasikan dan diuji fixture; belum diklaim aktif
pada Windows. Capability generate bukan bukti image analysis tersedia.

## API caller backend

Seluruh endpoint memakai `Authorization: Bearer <secret khusus queue>`.
Principal diperoleh dari mapping hash key di server, bukan owner_id dari body.
Secret caller terpisah dari credential worker. Secret tidak dikirim ke APK/TUI.

| Endpoint | Fungsi |
|---|---|
| `GET /health` | Kesiapan worker, queue dan kondisi blocked |
| `POST /v1/rtx/jobs` | Enqueue JSON; wajib Idempotency-Key |
| `GET /v1/rtx/jobs/{job_id}` | Status/hasil milik caller |
| `POST /v1/rtx/jobs/{job_id}/cancel` | Batalkan queued / minta buang hasil running |
| `POST /v1/rtx/jobs/{job_id}/recover` | Recovery status remote; tidak POST ulang |
| `POST /v1/rtx/files` | Upload multipart field `file` |
| `GET /v1/rtx/files/{file_id}` | Download privat dengan Bearer |

```json
{
  "kind": "generate",
  "prompt": "Buat mockup ruang kerja minimalis",
  "size": "768x768",
  "reference_file_ids": []
}
```

Untuk membaca gambar gunakan kind=analyze, prompt, dan 1–3 reference_file_ids.
Endpoint menerima format tersebut; worker harus melaporkan analyze=true sebelum
job diproses. Worker legacy saat ini memberi error vision_unavailable.

Respons enqueue HTTP 202:

```json
{"job":{"job_id":"job_example","kind":"generate","status":"queued"}}
```

Request identik dengan idempotency key sama mengembalikan job yang sama (200).
Payload berbeda dengan key sama: 409 idempotency_conflict. Status:
queued → dispatching → running → completed/failed/cancelled/unknown.
Hasil completed berisi result.text dan result.artifacts[] dengan file_id,
mime_type, size_bytes, width, height, sha256, status=ready. Tidak ada path disk,
URL publik, atau Base64 pada kontrak caller. Adapter legacy saja mendekode respons
Base64 lama dari worker internal sebelum validasi dan storage.

Error stabil: `{ "error": { "code": "rtx_offline" } }`. Get/cancel/recover/
file milik principal lain mengembalikan 404. Auth salah/missing: 401.

Contoh integrasi Node pada backend yang berwenang:

```js
const fs = require('node:fs');
const { randomUUID } = require('node:crypto');
const token = fs.readFileSync('/etc/harmonika-ai/rtx-queue-client.token', 'utf8').trim();
const response = await fetch('http://127.0.0.1:18186/v1/rtx/jobs', {
  method: 'POST',
  headers: {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
    'Idempotency-Key': randomUUID(),
  },
  body: JSON.stringify({ kind: 'generate', prompt: 'Mockup ruang kerja', size: '768x768' }),
  signal: AbortSignal.timeout(5000),
});
const result = await response.json();
```

Simpan idempotency key untuk logical request yang sama. Caller publik/member
memerlukan proxy/auth miliknya sendiri dan mapping kepemilikan file sebelum
menggunakan key internal backend; jangan menerima owner_id mentah dari APK.

## Timeout, kapasitas dan recovery

- Maksimum satu dispatch remote aktif, 64 pending global, 8 pending/principal.
  Claim SQLite transaksional + flock mencegah dua dispatcher service.
- Batas antre menunggu 15 menit, connect/readiness 3 detik, render 120 detik.
  HTTP enqueue langsung 202 dan tidak menunggu render.
- PC offline sebelum pengiriman: failed/rtx_offline. Timeout/putus setelah POST
  mungkin diterima worker: **unknown**, bukan retry buta.
- unknown/cancel_requested tetap menahan slot. Health menampilkan
  blocked_by_uncertain_job=true dan queue_ready=false. Service/API tetap responsif.
- Startup mengubah in-flight menjadi unknown; queued tetap tersimpan. Recovery
  v1 hanya GET job ID yang sama. not_found tidak mengizinkan POST ulang.
- Worker legacy tidak memiliki job registry/recovery. Timeout legacy memerlukan
  verifikasi operator/upgrade worker v1; queue tidak menebak GPU sudah berhenti.
- Cancel running belum menginterrupt GPU; hasil dibuang setelah terminal diketahui.
  Tidak ada fallback ke mentor lain setelah status remote tidak pasti.
- Serialisasi ini berlaku pada job yang masuk antrean ini. Semua caller lain ke
  GPU juga harus melewati antrean/lock worker untuk jaminan global lintas jalur.

## File dan penyimpanan

Input PNG/JPEG/WebP statis, magic bytes + verify/decode, maksimal 10 MiB/file,
4096×4096 / 16.777.216 piksel, maksimal 3 referensi/job. Animasi ditolak. Output
maksimal 16 MiB/gambar; mode v1 memeriksa hash manifest sebelum decode/storage.
Input JSON job maksimal 64 KiB, prompt maksimal 12.000 byte. Folder 0700/file
0600, bukan enkripsi at-rest. Tidak ada prompt/gambar/key di access log.

Storage dibatasi 1 GiB/1000 file dan 10.000 job history; belum ada penghapusan
otomatis. Files hasil parsial/cancel race dapat menunggu housekeeping privat.
Tidak ada penghapusan otomatis data pelanggan atau history chat.

## Kontrak worker Windows v1

- `GET /v1/rtx/health`: `{ok:true,capabilities:{generate:true,analyze:true,references:true}}`.
- `POST /v1/rtx/jobs`: multipart `job` JSON + `reference_0` s.d. `reference_2`.
  JSON: schema_version=1, job_id, kind, prompt, size, request_digest,
  reference_files(field,file_id,mime_type,size_bytes,sha256).
  Header Idempotency-Key=job_id dan Bearer worker khusus.
- Worker wajib dedup durable berdasarkan job_id+digest; ID sama/digest berbeda
  harus ditolak. Response job_id harus sama, status queued/running/terminal.
- `GET /v1/rtx/jobs/{job_id}`: status serta result.text dan result.artifacts[]
  (artifact_id,size_bytes,sha256). Generate membutuhkan artifact; analyze teks nyata.
- `GET /v1/rtx/jobs/{job_id}/artifacts/{artifact_id}`: bytes image privat.

Endpoint worker ditentukan operator, hanya IP privat/loopback literal. Redirect,
proxy environment dan cookies dinonaktifkan. Mode v1 membutuhkan worker secret;
legacy hanya diizinkan pada loopback tunnel privat. Tidak ada URL worker dari body.

## Operasi dan tes

```sh
python3 -m venv /opt/harmonika-ai/rtx-queue-venv
/opt/harmonika-ai/rtx-queue-venv/bin/pip install -r backend/rtx_queue/requirements.lock
/opt/harmonika-ai/rtx-queue-venv/bin/python -m unittest discover -s tests/rtx -v
```

`deploy/setup.py` adalah first-install root yang menolak menimpa instalasi existing,
membuat service user/secret terpisah dan hanya menyalakan service queue ini.
Config memakai systemd LoadCredential. Jangan menjalankan setup ulang untuk upgrade.
`deploy/smoke.py` membaca secret server-side, menjalankan job generate nyata dan
menyimpan laporan metadata. Report terakhir: [RTX queue](../../docs/RTX-QUEUE-REPORT.json).

20 tes mencakup offline, timeout ambigu, singleton, transaksi, cancel, recovery
GET-only, auth/owner, idempotency, gambar corrupt, field invalid dan file privat.
Generate nyata pada Windows RTX menghasilkan PNG 512×512 dalam sekitar 0,82 detik;
request ulang tidak merender lagi. Vision nyata masih belum tersedia.

Referensi: [aiohttp async client](https://docs.aiohttp.org/en/stable/client_quickstart.html).
