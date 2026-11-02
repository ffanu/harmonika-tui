# Hasil perbaikan SSH terminal

- Aktif lokal: rilis `2026.10.29.1`, signature Ed25519 dan checksum diverifikasi;
  doctor/preflight lulus. Binary TUI, ASCII, config, izin, sesi pengguna tetap.
- Akar keterbatasan: argv hanya menerima executable POSIX dalam daftar operator;
  RouterOS tidak punya serializer/platform audit dan password file sebelumnya.
- Perbaikan: platform RouterOS, daftar menu baca-saja, serializer tanpa shell,
  password file privat opsional, discovery metadata, deteksi error CLI exit nol.
- Uji: 68 Python + 5 Node lulus. Termasuk SSH socket key/password ke fixture
  RouterOS sintetis, REST parsing, injection/mutasi ditolak, output/timeout,
  SFTP POSIX regresi, host-key mismatch, dan log tanpa isi sensitif.
- CLI terpasang: `harmonika ssh invoke` connect dan `uname -s` ke alias `vps`
  sukses, hasil Linux. Tidak mengubah konfigurasi host tujuan.
- MCP terpasang: 7 tool terdaftar, deskripsi RouterOS ada, discovery mengenali
  kedua target POSIX. Target `chr` timeout dari Mac dan dari VPS pada port 22059.
- Belum ada target RouterOS terdaftar di Mac maupun registry VPS. Audit pada
  perangkat MikroTik nyata belum dilakukan; menunggu endpoint/port/user dan
  akses operator serta pin host yang terverifikasi.
- Source adapter REST di repo sudah diperbarui dan diuji; layanan REST VPS
  belum di-deploy/restart. Terminal menggunakan worker lokal yang sudah aktif.
- Proses terminal/MCP lama bisa memakai rilis lama sampai pengguna membuka
  ulang Harmonika; tidak ada proses pengguna yang dihentikan.

Tidak ada klaim login MikroTik berdasarkan nama alias `chr` ataupun hasil fixture.
