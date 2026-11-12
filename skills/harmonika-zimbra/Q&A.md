# Q&A operasional Zimbra

Panduan ini merangkum mekanik yang ditinjau dari ujian perbaikan. Tidak menyimpan
chat pengguna, credential, identitas pelanggan, alamat server ujian, atau isi email.
Ini bukan training bobot model. Data kanonis ada di backend `zimbra/knowledge.py`;
tool `zimbra_qa` mengembalikannya dan `zimbra_health` menyertakan panduan yang cocok.

## Alur keputusan

1. Verifikasi target terdaftar, versi terpasang dan health awal.
2. Akses log/PID gagal? Periksa metadata/akses efektif, cadangkan ACL, koreksi
   direktori terbukti rusak sesuai versi vendor. Uji akses sebelum menyalakan layanan.
3. Proxy 502? Verifikasi mailbox/backend, bukan port proxy saja. Start hanya yang mati.
4. Controller exit 0 tetapi menulis gagal? Tetap gagal. Periksa kedua stream,
   lingkungan login zimbra dan dependensi nyata sebelum menginstal paket.
5. `/zimbra/` 404? Uji `/` dengan TLS dan marker login. Jangan mengubah portal sehat
   demi jalur yang diasumsikan probe.
6. Wrapper statistik mati tetapi root collector hidup? Cocokkan receipt, UID,
   comm, state dan start ticks; periksa progres sampel. Jangan hapus receipt atau
   buat PID pengganti. Dengan izin process control spesifik yang sudah diberikan,
   backup dan revalidasi identitas; stop hanya fd collector memakai script resmi;
   verifikasi proses lama berakhir, lalu start resmi statistik dalam login zimbra.
   Jangan ulang meminta izin yang sudah sah atau memperluas scope izin.
7. Timeout/unknown? Baca status/receipt untuk rekonsiliasi, jangan retry buta.
8. Tool baru tidak terlihat? Discovery sekali; gunakan tool sempit yang ada atau
   checkpoint dan sesi baru. Jangan berputar pada pencarian sama atau UI jobs.
9. Verifikasi seluruh komponen, dua snapshot, progres statistik, login HTTPS,
   SMTP dan IMAP. Status running bukan bukti email terkirim. Catat batas delivery,
   reboot persistence dan flag systemd historis secara terpisah.

## Topik yang dapat dipanggil

`log_access`, `proxy_backend`, `web_route`, `perl_environment`, `controller_status`,
`stats_orphan`, `pid_identity`, `unknown_execution`, `probe_transport`,
`tool_discovery`, `systemd_history`, `success_criteria`.

Enam topik audit SSL/DNS: `tls_certificate`, `smtp_starttls`, `dns_mail_policy`,
`tls_policy_evidence`, `renewal_readiness`, `external_perspective`.

SSL automation adds `ssl_automation` and `certbot_config_format`. Use the
read-only `zimbra_ssl_plan` before preparing automation. Checks twice daily do
not force issuance. Standalone staging needs scoped maintenance permission;
config and directory hooks must both be isolated, and a staging certificate is
never deployed. Preserve SANs and compare source/deployed/served fingerprints.

Panggil `zimbra_tls_audit` untuk pengamatan nyata, lalu gunakan topik yang sesuai.
Bandingkan chain/hostname/expiry setiap port, bukan HTTPS saja; pastikan EHLO ulang
setelah STARTTLS. Jangan simpulkan server menolak TLS1.3 ketika client tidak
mendukungnya. SPF a/mx/include/redirect memerlukan evaluasi, bukan perbandingan
ip4 literal saja. DNS gagal dibaca bukan berarti record tidak ada. Timer aktif
bukan bukti renewal/deploy, dan catatan DNS bukan bukti signing/alignment/delivery.

Contoh: `zimbra_qa({"topic":"stats_orphan"})`. Tool hanya membaca aturan yang
ditinjau; keputusan dan eksekusi tetap melalui otorisasi serta bukti runtime.

Mailjet: `mailjet_account_audit`, `mailjet_relay`, `browser_account_capture`,
dan `mailjet_provisioning`. Pisahkan status provisioning, onboarding, izin browser,
credential tersimpan, AUTH SMTP dan delivery. Akun baru memakai reference privat
dan grant sendiri; jangan mencampurnya dengan key akun sebelumnya. Cocokkan tab
Zimbra, DNS dan alias SSH sebelum mengubah relay. Pemilik dapat mengizinkan
pemrosesan satu pesan verifikasi melalui adapter privat; itu tidak mengizinkan
pembacaan seluruh inbox atau pengeluaran OTP/activation token ke model/log.

Mac: `mailjet_native` menyediakan observasi aksesibilitas terbatas untuk halaman
onboarding/activation ketika Apple Events JavaScript tetap ditolak. Tool ini
mengembalikan flag tetap dan nama isian, bukan raw UI/field values, serta hanya
dapat membuka status/form dalam izin aktivasi yang ada. Ia tidak mengirim formulir
atau mengambil kunci. Email verified dan business approval adalah dua state berbeda;
volume/frekuensi email serta jumlah kontak harus berasal dari pemilik.
