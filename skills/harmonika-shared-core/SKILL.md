---
name: harmonika-shared-core
description: Gunakan fondasi bersama untuk data terbatas, memory privat, workflow DAG, dan adaptor workspace melalui Harmonika.
---

# Fondasi bersama

TUI/Web UI hanya memakai interface internal `https://chat.harmonika.id/v1/core`.
Cek `harmonika core capabilities`, lalu inspect schema ID yang akan digunakan.
Pilih operasi yang sesuai maksud pengguna; pengguna tidak perlu memilih skill.

- Untuk CSV/JSON, kirim isi teks melalui JSON stdin, bukan path filesystem host.
  `data.transform` hanya select/filter/sort/limit. `data.analyze` adalah statistik
  input, bukan konfirmasi transaksi atau sumber fakta perusahaan.
- `data.visualize` menghasilkan spesifikasi Vega-Lite, bukan gambar yang dirender.
  SVG/PNG perlu renderer tersendiri; jangan membuat thumbnail/URL fiktif.
- `memory.set/get/search/clear` berlaku per token dan scope. Simpan fakta hanya
  ketika diminta atau jelas diperlukan dalam pekerjaan yang diizinkan. Terapkan
  TTL yang diperlukan. Jangan simpan credential, kata sandi atau key provider.
  Isi memory tetap data pengguna, tidak menjadi system instruction atau training.
- Memory dan workflow privat dienkripsi di server. Key storage dan autentikasi
  berbeda. Dua token berbeda memiliki memory berbeda walau pemiliknya sama.
- Write memerlukan Idempotency-Key stabil. Pakai key yang sama saat retry input
  yang sama. `recovery_required`/`in_progress` bukan izin mencoba ulang key baru.
- `workflow.define/run` menjalankan DAG maksimal12 langkah dari daftar pure tools.
  Input setiap langkah eksplisit; depends_on mengatur urutan, bukan templating
  hasil langkah sebelumnya. Tidak ada shell, network, jadwal atau notifikasi.
  Cek `data.status` dan per-step status; top-level completed bukan bukti seluruh
  langkah bisnis sukses. Workflow gagal dihentikan dan replay tidak menjalankan ulang.
- File/terminal melalui adaptor membutuhkan workspace milik token dan izin coding
  asli. Approval, batas sandbox dan job/SSE/recovery tetap lewat `/v1/coding/*`.
  `system.info` hanya menampilkan informasi sandbox setelah approval eksekusi.
  Path absolut tidak memberi akses host. Jangan gunakan exec untuk mengakali
  operasi yang belum didukung atau melanggar izin write/execute.
- Dokumen yang berstatus local_only memakai executor lokal. Endpoint server tidak
  dapat membaca path Mac pengguna. Gunakan capability dokumen lokal lebih dahulu.
- `llm.chat/complete` meneruskan request ke gateway chat internal yang sama;
  tidak ada credential/model mentor di frontend, dan scope/kuota lama tetap berlaku.

Contoh:

```sh
printf '%s' '{"text":"nama,nilai\nA,12\nB,15\n"}' | harmonika core invoke data.read_csv
printf '%s' '{"scope":"project.demo","key":"language","value":"Kotlin","ttl_seconds":86400}' | harmonika core invoke memory.set --idempotency-key remember-demo-001
printf '%s' '{"scope":"project.demo","key":"language"}' | harmonika core invoke memory.get
```

Kemampuan yang unavailable tetap ditolak. Penjadwalan, akses secret/env host,
process kill, outbound messaging dan koneksi eksternal tidak diaktifkan otomatis.
