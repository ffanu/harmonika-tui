# Harmonika Unified Engine — backend aktif

Backend `chat.harmonika.id/v1/unified` menjalankan70 ID inti, orchestrator dan
integrasi yang dirinci dalam blueprint1.302 baris. Kelima komponen yang sebelumnya
belum tersedia sudah terhubung dan diuji melalui HTTPS/WSS publik.

| Status | Jumlah | Makna |
|---|---:|---|
| available | 57 | Callable sesuai schema, mode dan izin token. |
| internal_only | 12 | Auth, enkripsi, rate limit, audit, priority queue dan kebijakan pemulihan ditegakkan server. |
| native_endpoint | 1 | `gateway.websocket`, melalui koneksi WSS terautentikasi. |
| unavailable | 0 | Untuk70 ID konkret tersebut. |

Ini mencakup core/integrasi, bukan klaim seluruh1.153 skill spesialis konseptual
sudah menjadi implementasi. Blueprint tidak merinci ID semua spesialis. Domain
coding, legal, web/mobile dan media tetap mengikuti capability masing-masing.
Perencanaan/source generation tidak disamakan dengan build, signing atau deploy.

## Endpoint

```text
GET  /v1/unified/capabilities
GET  /v1/unified/skills
GET  /v1/unified/skills/{skill_id}
POST /v1/unified/invoke
POST /v1/unified/sessions
GET  /v1/unified/sessions/{session_id}
POST /v1/unified/jobs
GET  /v1/unified/jobs/{job_id}
POST /v1/unified/jobs/{job_id}/stop
GET  /v1/unified/jobs/{job_id}/events?after_sequence=1
POST /v1/unified/bus/events
POST /v1/unified/bus/replay
POST /v1/unified/knowledge/query
POST /v1/unified/secrets
POST /v1/unified/graphql
POST /v1/unified/ticket
WSS  /v1/unified/ws
```

REST memakai Bearer API key. Gateway menurunkan actor dan scopes dari token;
header `X-Harmonika-*` bukan cara memberikan hak akses dari client.
Discovery/invoke juga terhubung ke `/v1/skills`. Kontrak lama seperti
`data.transform` tetap memakai engine asal. TUI tidak diubah pada pekerjaan ini;
client memakai namespace baru ketika siap, sementara chat lama tetap kompatibel.

## Job, session, Stop dan pemulihan

Buat session dengan POST `/sessions` body `{}`. Kirim job dengan
`Idempotency-Key`8–128ASCII:

```json
{"session_id":"UUID-dari-server","messages":[{"role":"user","content":"Buat website katalog dan aplikasi Android."}]}
```

Dengan session_id, kirim satu pesan user baru; server memuat history dan memangkas
pasangan pesan terlama ketika konteks penuh. Tanpa session_id, messages dapat
memuat40 pesan/48.000 karakter; satu pesan maksimal16.000 karakter. Ukuran JSON
request tetap maksimal128KiB. Response202 berisi id job. Token lain mendapatkan404
saat meminta job/session yang bukan miliknya.

Status queued/running/completed/failed/stopped/recovery_required tersimpan. Key
idempotency yang sama dan payload sama mengembalikan job asli, bukan eksekusi baru.
Payload berbeda menghasilkan409. Restart tidak otomatis menjalankan ulang job
belum terminal. Pengunci instance mencegah dua service memulihkan DB yang sama.

Priority queue bersifat non-preemptive, FIFO untuk prioritas sama, dengan aging30
 detik agar antrean lama tidak tertunda terus. Prioritas ditetapkan dari aturan
urgensi server, bukan field priority milik caller. Maksimal16 job dalam antrean,
2 worker,1 job aktif/token,50 job/token/24jam dan deadline150detik.

Stop tetap terminal; hasil terlambat tidak dimasukkan ke session. Stop dan
penerbitan ticket tidak memakai kuota generate. Batas rate dan autentikasi tetap
berlaku. Tidak ada retry model otomatis yang bisa menggandakan efek samping.

SSE menggunakan event_id `job_uuid:sequence`, sequence, timestamp dan status.
Terminal membawa result. Gunakan Last-Event-ID atau after_sequence yang konsisten.
Koneksi SSE maksimal25detik lalu reconnect dengan cursor. Ini stream status job,
bukan delta token teks. Retensi job24jam; session7hari dan history20pesan/48KiB.

## Event bus privat

Publish `{topic,payload}` ke `/bus/events` dengan Idempotency-Key. Topic memakai
`[A-Za-z0-9_.-]{1,80}` dan payload object maksimal16KiB. Replay menerima
`{topic,after_sequence?,limit?}` dan mengembalikan events serta next_sequence.
`bus.publish.event` dan `bus.subscribe.event` juga tersedia melalui invoke.

Payload dan nama topic disimpan terenkripsi/terhash. Dua token dengan nama topic
sama tetap terisolasi. Sequence monotonik per actor+topic dipertahankan setelah
payload kedaluwarsa; cursor lama tidak kehilangan event baru karena sequence reset.
Idempotency event terikat actor+topic+key, berlaku selama retensi event24jam.
Batas:100 topic/token,1.000 event/token,10.000 event global; replay maksimal100.

Pub/sub menggunakan backend lokal durable dan WSS, bukan Kafka lintas server.
Tidak ada webhook eksternal atau callback kode caller yang dieksekusi otomatis.

## Knowledge terverifikasi dan retrieval

Corpus awal berisi6 dokumen kontrak API Harmonika yang disetujui operator.
Setiap dokumen memiliki id, title, text, source http(s), SHA256 isi dan verified_at.
Loader menolak hash salah, ID/field JSON duplikat, sumber tanpa http(s), URL dengan
credential dan snapshot yang tidak valid. Tidak ada download URL caller.

`shared.knowledge`, `/knowledge/query` dan `core.retrieve` tanpa sources mencari
corpus tersebut dengan BM25. Query maksimal400 karakter; limit1–20. Hasil memberi
citation.source, sha256 dan verified_at. Ini retrieval leksikal, bukan Vector DB;
source approval/integritas dokumen bukan sertifikat kebenaran seluruh klaim.
Pertanyaan teknis API yang relevan juga memperoleh konteks retrieval saat job
menyusun balasan melalui adapter chat internal.

`core.retrieve` dengan sources tetap menerima bukti caller, tetapi `verified:true`
dari caller tidak dapat mengesahkan kebenaran sumber. Corpus bisnis/harga/coverage,
data pelanggan dan hukum tidak diimpor otomatis. Tambahkan hanya melalui review
operator dan loader yang sama; jangan mengarang data yang belum tersedia.

## Secret connector

POST `/secrets` atau invoke `shared.secrets` menerima:

```json
{"operation":"set","name":"my.connector","value":"nilai-yang-diberikan-pemilik"}
```

Operasi set/delete membutuhkan scope `secrets:write`; metadata membutuhkan
`secrets:read`. Scope diberikan oleh pengelola token, bukan header/body client.
Key chat biasa tetap403. `/capabilities.token_permissions` menjelaskan izin token.

Nama maksimal64 karakter, value maksimal4.096byteUTF-8. Batas50 secret/token,
500 global. Nilai dan nama tersimpan AES-GCM/HMAC per token. Tidak ada endpoint
untuk membaca nilai/ciphertext/sufiks secret. Ini bukan akses env, key provider,
credential root atau session browser server. `secret_resolve` hanya fungsi internal
untuk purpose terdaftar, tidak dipublikasikan melalui REST/GraphQL/WSS.

Riwayat idempotency set/delete disimpan24jam. Retry lama setelah rotasi tidak
mengembalikan nilai lama ke storage; replay delete tidak menghapus secret yang
sudah dibuat ulang. Metadata replay adalah hasil operasi asal, bukan snapshot
terbaru; gunakan operasi metadata untuk melihat updated_at terkini.

## GraphQL

POST `/graphql` menggunakan parser dan schema GraphQL sungguhan, read-only:

```graphql
query {
  capabilities
  skills { id status mode }
  knowledge(query: "idempotency status stopped")
}
```

Field tersedia: capabilities, skills, job(id), session(id), events(topic,after,
limit), knowledge(query,limit), secretMetadata(name). Field data yang fleksibel
memakai scalar JSON. Variables dan fragment didukung. Mutations, subscriptions
GraphQL dan introspection tidak diaktifkan. Scope secret dan ownership tetap
berlaku di resolver. Query8KiB, depth8, field cost40; errors tidak mencetak query,
variable value atau secret. [Schema](../backend/unified_engine/graphql.schema.graphql).

## WebSocket

Native client dapat menghubungkan Bearer pada handshake. Browser memperoleh ticket
satu kali melalui POST `/ticket` body `{}` dengan Bearer, lalu membuka:

```text
wss://chat.harmonika.id/v1/unified/ws?ticket=TICKET_SEKALI_PAKAI
```

Ticket berlaku60detik, terikat actor dan Origin, dikonsumsi sekali, tidak disimpan
sebagai plaintext di DB. Status token diperiksa kembali saat ticket ditebus;
ticket milik token yang dicabut ditolak. WSS tidak memakai cookie login panel.

Frame hello/ping/pong/resume mendapat connected/hello_ack/pong/resume.accepted,
event dan resume.state. Resume menerima salah satu job_id atau topic:

```json
{"type":"resume","topic":"project.events","after_sequence":2}
```

Event yang masuk setelah subscribe juga dikirim live. Koneksi read-only: create,
publish dan Stop tetap melalui REST sehingga permission/kuota tidak dilewati.
Maksimal16 koneksi global,2/token, frame16KiB dan lifetime120detik. Watchdog memeriksa
auth kembali setiap30detik dan menutup koneksi saat service restart. Gangguan auth
server dibedakan dari token yang tidak valid. Reconnect menggunakan ticket baru
atau Bearer dengan cursor terakhir. Jangan memasukkan API key ke query URL.

## Keamanan, operasi dan batas

State di `/var/lib/harmonika-unified/state.sqlite3`0600, direktori0700. Kunci auth
service dan key storage terpisah dan tidak berubah saat upgrade. DB mempunyai
batas fisik100MiB; admission80MiB menyisakan ruang untuk hasil job dan tombstone.
Batas kapasitas menghasilkan error terstruktur, bukan mengorbankan hasil terminal.

Log hanya HMAC actor, status dan durasi. Tidak mencetak prompt, history, media,
value secret atau Bearer. JSON upstream dibaca sampai EOF dengan batas total;
respons yang terpecah beberapa paket tidak diparse sebagai JSON yang terpotong.

Modul tetap membedakan rule-based NLU, hasil model, perencanaan dan tindakan nyata.
Confidence tidak dikarang; tidak ada training otomatis. Analisis media memakai
adapter internal dan dapat mengembalikan degraded bila observer belum siap.
Aksi browser/build/sign/deploy memerlukan engine dan konteks berizin masing-masing.
Tidak ada perubahan pada TUI, engine CS, ERP, database pelanggan, GPU atau DRM.

Dependensi GraphQL dipin berhash dan dipasang pada direktori khusus service;
environment engine lain tidak dimodifikasi. Acuan:
[GraphQL-core](https://graphql-core-3.readthedocs.io/en/stable/usage/queries.html),
[aiohttp WebSocket](https://docs.aiohttp.org/en/stable/web_reference.html#aiohttp.web.WebSocketResponse),
[Go ReverseProxy](https://pkg.go.dev/net/http/httputil#ReverseProxy).

[Laporan penutupan](UNIFIED-ENGINE-COMPLETION-REPORT.json).
