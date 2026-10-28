# Instalasi tiga engine video — 28 Oktober 2026

Selesai dipasang dan diuji pada Windows RTX 5070 12 GB, RAM 64 GB.
Linux tetap menjadi gateway CPU. Implementasi/pengujian: Codex. Review source
serta pola pemuatan runtime: OpenCode. Claude tidak digunakan.

| Engine | Hasil nyata | Batas pemakaian saat ini |
|---|---|---|
| WanGP / Wan 2.1 1.3B | MP4 832×480, 16 frame, 1 detik, 50 langkah; startup pertama416,11 detik. Job melalui bridge Linux juga selesai114,05 detik. | Operator privat; tidak diekspos sebagai API publik/berbayar/white-label. |
| FramePack F1 | Referensi PNG → MP4 832×480, 30 frame, 1 detik, 25 langkah; startup dan render pertama1450,36 detik. | Operator privat. Pemuatan awal sangat berat; durasi lebih panjang belum dikualifikasi. |
| MMAudio | Audio44.100 Hz, 8,01 detik dan berhasil digabung dengan video uji;50,02 detik. | CLI riset nonkomersial. API soundtrack sengaja ditolak karena lisensi bobot CC-BY-NC-4.0. |

55 file model, sekitar 62,31 GiB, selesai diverifikasi SHA-256. Source dan model
memakai revisi tetap; tiga lingkungan Python terpisah. Versi 176/80/117 paket
masing-masing dicatat dalam package-lock.json untuk audit, bukan installer.

19 pengujian lokal lulus. Uji nyata juga membuktikan bridge terautentikasi,
401 tanpa token,404 untuk artifact yang tidak dimiliki, respons offline terstruktur,
idempotency dua submit menghasilkan satu job, hasil unduhan cocok SHA-256,
input sementara dibersihkan dan teks prompt tidak tersimpan di SQLite. Menjalankan
broker kedua ditolak sebelum database disentuh, termasuk uji langsung di Windows.

Video Wan dan FramePack telah didekode seluruhnya dan sampel awal/akhir diperiksa.
Wan menunjukkan router dengan warna netral; FramePack mempertahankan referensi
router, telepon dan ruangan. Ini adalah sampel validasi satu detik, bukan iklan
promosi 10 detik yang sudah selesai.

- [Video Wan](artifacts/video-toolkit/wan-smoke.mp4)
- [Video FramePack](artifacts/video-toolkit/framepack-smoke.mp4)
- [Bukti teknis JSON](VIDEO-TOOLKIT-VERIFICATION.json)
- [Prosedur operator](../backend/video_toolkit/README.md)

Bridge baru hanya untuk operator: Linux loopback 18240 → Windows loopback 18237.
Service tunnel Linux aktif dan key khusus ditolak untuk shell. Token disimpan
pada file privat di server dan tidak masuk repository. Folder job baru berada
di NTFS C dengan ACL terbatas; model publik berada di D. GPU dijalankan pada
Windows, tanpa mengaktifkan GPU/DRM Linux.

PIDs empat layanan utama Linux tetap sama dan aktif. Tidak ada perubahan pada
routing API publik, iOS/APK, WebUI lama, H3/Flux/vision, firewall atau hms-sstp.
Tidak ada commit/push. Broker Windows saat ini proses detached; autostart Windows
saat login/reboot belum dikonfigurasi. Toolkit mengunci satu job GPU antarketiga
engine ini; tidak mengambil alih scheduler worker lama.
