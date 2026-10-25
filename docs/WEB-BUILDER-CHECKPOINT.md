# Checkpoint Web Full-Stack Builder

Acak nama aplikasi tidak dipakai: engine berada dalam proyek Harmonika Terminal.
Master `engine full stack web.txt` dibaca lengkap:161 ID, bukan155 pada judul.

- Pengguna: pemilik token Harmonika melalui TUI/API; satu token dapat memiliki proyek
  privat sendiri. Integrator multi-user tetap mengotorisasi proyek di server aplikasi.
- Alur: spec → klarifikasi/stack/desain → source artifacts → persetujuan menjalankan
  kode → sandbox QA → target terdaftar → deployment yang hasilnya dapat dibuktikan.
- Data: SQLite metadata dan snapshot AES-GCM; idempotency per actor; revisi proyek;
  job/event terenkripsi; token/SSH key tidak masuk artifacts atau log.
- Eksekusi: container per job, non-root, tanpa jaringan, tanpa host bind mount,
  CPU/RAM/waktu/pid dibatasi. Stop hanya menyasar container job itu sendiri.
- Tidak mengubah ERP/CS, payment, browser bridge atau engine legal/inference/coding.
- Layanan cloud, DNS, payment, Notion dan SSH memerlukan konfigurasi akun/target yang
  berwenang. Artifact konfigurasi bukan bukti layanan sudah dipasang/ditagih.
- Tidak membelanjakan biaya atau mendaftarkan domain untuk proyek contoh.
- Tidak memperbarui binary native TUI, tidak mematikan sesi pengguna, tidak Git push.

Pembagian: OpenCode planning/spec/architecture/design serta generators/templates;
Codex boundary/runtime/gateway/CLI/sandbox, integrasi, pengujian dan aktivasi akhir.
Claude tidak dilibatkan.

Checkpoint akhir: backend dan rilis TUI .25 aktif; 161 kasus HTTPS/staging lulus.
160 tes builder macOS/Linux tanpa skip; 55 tes Node dan gateway Go lulus.
Cookie/state OAuth dan rekonsiliasi webhook diperketat. Token/proyek uji
produksi dibersihkan; service staging dihentikan. SSH/Notion/cloud tetap
menunggu konfigurasi operator. Laporan mesin: WEB-BUILDER-TEST.json.
