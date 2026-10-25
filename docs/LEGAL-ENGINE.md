# Harmonika Legal Assistant

Domain legal terpisah untuk edukasi hukum Indonesia, pengelolaan perkara privat,
penanganan bukti, dan kutipan sumber resmi. Bukan layanan advokat, vonis, sertifikasi
bukti, atau mesin yang menjamin seluruh hukum terbaru telah terhimpun.

## Dokumen acuan dan batas cakupan

Dokumen `master legal skill.txt` memiliki 99 ID unik dalam tujuh kelompok, meskipun
judulnya mengklaim 221. Implementasi mempertahankan 99 ID tersebut. Nama pasal dan
uraian pada dokumen permintaan **bukan otoritas hukum**; nomor pasal lama maupun label
ITE yang keliru tidak disalin menjadi pengetahuan normatif.

`GET /v1/legal/capabilities` adalah sumber status per token. ID yang belum memiliki
korpus/adaptor nyata tetap `readiness.status=unavailable`. `available` berarti fungsi
teknis berjalan sesuai batasnya, bukan seluruh substansi hukum bidang tersebut tersedia.
Sumber pidana/ITE/PDP tidak dipakai sebagai pengganti korpus perdata, keluarga,
ketenagakerjaan, atau pajak yang belum tersambung.

## Alur dan keamanan

1. Gateway memvalidasi Bearer token dan scope `chat` atau `legal`, menurunkan principal
   dari ID token di server, serta menerapkan kuota token yang sudah ada.
2. Buat perkara (conversation ID = case ID), isi yurisdiksi, fakta, perspektif, dan
   tanggal kejadian. Tidak ada konteks perkara lain yang dicampur.
3. Unggah bukti ke vault privat. MIME/dimensi diperiksa dari bytes, SHA-256 dihitung,
   lalu original dienkripsi AES-256-GCM dengan AAD milik tenant/file.
4. Handler mengambil data perkara dan sumber resmi sebelum memberi bacaan edukatif.
   Kutipan harus benar-benar ada pada snapshot; hasil pengamatan gambar belum menjadi
   fakta hukum. Chat saat ini memakai perakitan jawaban deterministik berbasis sumber,
   bukan opini hukum bebas yang dihasilkan LLM.
5. Request, hasil, riwayat pesan, dan payload audit dienkripsi. Log tidak mencetak
   fakta perkara, full prompt, media, token, atau hasil OCR. Audit hanya nama field.
6. `Idempotency-Key` diperlukan untuk create/invoke/chat. Request yang sudah selesai
   dapat dipulihkan dan event terminal direplay; ini bukan antrean async atau API Stop.

Parser PDF memakai subprocess tetap dengan batas waktu/memori/output. Operasi baca
berat tidak memegang writer SQLite. Parser tidak mengeksekusi kode, makro, atau aksi PDF.
Pencarian sumber menggunakan korpus resmi terbatas dan pencocokan leksikal, bukan
klaim pencarian vektor seluruh hukum Indonesia.

## API publik

Base URL: `https://chat.harmonika.id/v1/legal`.
Seluruh endpoint memerlukan `Authorization: Bearer <token>`.

| Method | Path | Fungsi |
| --- | --- | --- |
| GET | `/capabilities` | Status aktual, batas, 99 descriptor |
| POST | `/cases` | Buat perkara privat |
| GET | `/cases/{id}` | Perkara, riwayat 40 pesan terakhir, metadata bukti |
| POST | `/invoke` | `{ "skill_id": "...", "input": {...} }` |
| POST | `/chat` | `{ "case_id": "UUID", "message": "...", "stream": true }` |
| POST | `/files` | Multipart `case_id` dan `file`, maksimal 10 MiB |
| GET | `/files/{file_id}/download` | Original privat, attachment + nosniff |
| GET | `/requests/{id}` | Recovery request milik token |
| GET | `/requests/{id}/events` | JSON atau SSE replay; Last-Event-ID |
| GET | `/sources` | Manifest dokumen resmi yang diizinkan |
| POST | `/sources/refresh` | Refresh satu `source_id` yang diizinkan |

Create/invoke/chat memakai `Content-Type: application/json` dan `Idempotency-Key` unik.
Replay kunci yang sama+payload sama mengembalikan request lama; payload berbeda =409.
Upload memiliki batas ukuran, kuota penyimpanan, dan rate limit tersendiri; **tidak
menjanjikan deduplikasi berbasis idempotency**.

Contoh pembuatan perkara (semua data berikut sintetis):

```json
{"topic":"Pertanyaan edukasi bukti elektronik","facts":"Contoh untuk latihan, bukan perkara nyata","jurisdiction":"ID","occurred_on":"2026-01-02","perspective":"netral"}
```

Response envelope:

```json
{"id":"request-uuid","status":"completed","result":{"case":{"id":"case-uuid"}},"disclaimer":"Edukasi hukum, bukan nasihat hukum formal atau pengganti advokat.","jurisdiction_applicability_certified":false,"licensed_expert_present":false}
```

SSE `/chat`: `response.created`, heartbeat komentar setiap sekitar tiga detik saat
bekerja, `response.completed` atau `response.error`, lalu `[DONE]`. Teks tidak
berpura-pura dipecah menjadi token. SSE replay request membawa `event_id`,
`request_id`, `sequence`, `timestamp`, `type`, `data`.

Kompatibilitas `/v1/chat/completions` opsional:
`legal=true`, `legal_case_id=UUID`, model `harmonika-ai`. Tidak dapat digabungkan
dengan workspace coding, Telco, tools, atau history tool. Riwayat perkara di server
menjadi sumber konteks; `messages` memberi pertanyaan terbaru.

Error stabil berupa `{"error":{"code":"..."}}`, atau di envelope request gagal:
`status=failed,error.code`. Di antaranya `invalid_request`, `idempotency_required`,
`idempotency_conflict`, `not_found`, `request_not_found`, `evidence_case_mismatch`,
`unsupported_media`, `corrupt_image`, `evidence_integrity_failed`, `file_too_large`,
`evidence_storage_quota`, `record_quota`, `rate_limited`, `legal_capacity`,
`skill_unavailable`, `source_unavailable`, `source_identity_mismatch`.

## Katalog .24 dan media nyata

Seluruh **99 ID mempunyai handler**. Saat worker sehat, 98 descriptor tersedia;
`kb.advokat.verify` tetap `degraded` karena komponen pencarian PERADI bermasalah.
Handler tetap dapat dipanggil dan mengembalikan alasan terstruktur, bukan sertifikasi
keanggotaan. Tidak memakai direktori organisasi lain sebagai pengganti penerbit.

- PDF/DOCX: ekstraksi lokal dengan batas parser. PNG/JPEG/WebP: OCR Tesseract
  `eng+ind` dan deskripsi visual model lokal. ASR: transkripsi model lokal CPU,
  default bahasa Indonesia; `confidence=null`, bukan persentase yang direkayasa.
- Video MP4: maksimal120detik dan **dua frame sampel**, bukan peninjauan seluruh
  video/audio. Audio maksimal120detik. Setiap file tetap maksimal10MiB.
- Service media terpisah memakai DynamicUser, Unix socket privat, tanpa jaringan IP,
  perangkat GPU, atau akses credential inference. Satu job aktif; batas85detik;
  temporary plaintext per job dibersihkan saat selesai/timeout/cancel.
- Capability publik media hanya aktif setelah hash model/worker cocok dengan bukti
  smoke gambar, suara, dan video nyata. Jika service gagal, hasil tidak dikarang.
  `legal_ocr`, `legal_vision`, `legal_asr`, `legal_video_visual` mengikuti capability.
- Deskripsi model kecil dapat melewatkan objek/detail; transkripsi dapat salah eja.
  Angka, nama, tanggal dan isi penting **harus dikonfirmasi manusia**. Pengamatan
  bukan fakta hukum, bukti keaslian, identifikasi orang atau laporan forensik.

Model server dipin ke revision dan SHA-256: SmolVLM-256M-Instruct serta
faster-whisper-small; paket TUI tidak membawa bobot model atau credential.
`backend/legal_engine/media/requirements.lock` mencatat dependensi CPU.

## Korpus publik dan pembaruan

Empat belas handler riset memakai portal sumber yang ditentukan server, GET terbatas,
allowlist host setiap redirect, DNS publik yang dipin, batas waktu/bytes, dan cache
metadata publik. Query privat diindeks dengan HMAC, tidak disimpan mentah.

- PP, Perpres, Permendagri, Perda dan MK: indeks peraturan.go.id dengan filter jenis,
  nomor/tahun. Direktori MA menolak akses server; fallback arsip putusan JDIH Kemenkes
  hanya mencakup dokumen yang dipublikasikan di sana, bukan seluruh putusan nasional.
- Surat edaran: JDIH Komdigi; ID lama `kb.sirclo.search` dipertahankan sebagai alias,
  bukan integrasi perusahaan SIRCLO. Internasional: korpus terbatas ECOLEX.
- Artikel: metadata publik Crossref; KY: publikasi Komisi Yudisial; LBH: direktori
  YLBHI. Metadata/snippet pencarian bukan kutipan pasal atau bukti status profesi.
- `uu.amandemen.track`: mencatat relasi yang dinyatakan pengguna beserta kutipan tepat
  dari snapshot resmi. Kutipan terverifikasi **tidak memverifikasi makna relasinya**.
- `uu.consolidated.get`: workspace perbandingan versi. Tidak membuat naskah konsolidasi
  resmi dari klaim caller; perlu sumber konsolidasi yang dikonfigurasi di server.
- `uu.alert.new`: subscribe/check/list/unsubscribe privat; event perubahan hash masuk
  vault tenant. Timer refresh lima dokumen sekitar12jam sekaligus memeriksa watch.
  Ini bukan penemuan seluruh hukum baru dan tidak mengirim pesan eksternal.

`currentness_certified`, `applicability_determined`, `licensed_expert_present` dan
sertifikasi keaslian/identitas tetap false. UU1/2026 masih dapat `source_unavailable`
jika portal resminya503. Sumber gagal tidak diganti kutipan buatan.

## Terminal

```text
harmonika legal capabilities
harmonika legal sources
harmonika legal create-case < case.json
harmonika legal chat CASE_UUID < message.json
harmonika legal invoke kb.uu.search < query.json
harmonika legal case CASE_UUID
harmonika legal status REQUEST_UUID
harmonika legal events REQUEST_UUID --after-sequence 1
```

Token dibaca dari penyimpanan privat yang sudah digunakan terminal, tidak lewat
argumen CLI. Registry lokal menyimpan descriptor; status backend per token tetap
menentukan apakah suatu skill bisa dipanggil. File legal tidak dieksekusi lewat
worker shell lokal. Upload/download tersedia lewat REST; belum ada subperintah file.

## Menjalankan dan verifikasi

Pakai venv khusus (`requirements.txt` dipin), kunci AES-256 acak dan secret gateway
terpisah di `/etc/harmonika-ai/`, permission0600, state privat `/var/lib/harmonika-legal`.
Secret tidak boleh dimasukkan repo, release ZIP, contoh request, atau log.

```sh
/path/to/legal-venv/bin/python -m unittest discover -s tests -p 'test_legal_*.py' -v
python3 -m unittest discover -s tests -p 'test_skill_executor.py' -v
```

Unit service/timer tersedia di `backend/legal_engine/deploy/`. Gateway Go diuji
terpisah, termasuk `TestLegalLive` yang opt-in, membuat token fixture terbatas,
memakai data sintetis, dan menghapus token fixture setelah pengujian. Jangan
menjalankannya dengan kasus atau bukti pelanggan.

## Interpretasi hasil test

Smoke seluruh99 ID menggunakan fixture sintetis. Tujuh pemeriksaan status/kepatuhan
mengembalikan `degraded` ketika kelengkapan/penelaahan belum cukup; ini hasil aman yang
diharapkan, bukan jawaban hukum final. Satu hasil degraded lain berasal dari registri
advokat. Jangan menyamakan HTTP200 atau jumlah handler dengan kepastian hukum.

Operator: `scripts/smoke-legal-catalog.py` menerima dua token sementara melalui stdin
JSON, bukan argumen/log. Harness Go opt-in membuat/menghapus token fixture sendiri.
Jangan memakai bukti pelanggan untuk smoke. Metadata laporan tidak memuat isi perkara.
