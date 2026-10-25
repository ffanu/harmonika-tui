# Audit aktivasi katalog legal .24 — 24 Oktober 2026

Codex mengerjakan integrasi, isolasi media CPU, validasi, deployment dan klien terminal.
OpenCode mengerjakan konektor riset, workspace pembaruan, fallback putusan dan audit
akhir independen. Claude tidak dilibatkan.

## Cakupan yang benar-benar berjalan

Dokumen master berisi99 ID unik dalam tujuh kelompok (header221 tidak sesuai isi).
Seluruh99 mempunyai handler; capability production menunjukkan98 `available`,
1 `degraded`,0 `unavailable`. `kb.advokat.verify` tidak berpura-pura berhasil: komponen
registri penerbit PERADI menjawab halaman rusak. Alternatif direktori organisasi dengan
provenance berbeda tidak dipakai untuk mengesahkan profesi.

Tambahan terhadap .23:

- 14 konektor riset publik berbatas waktu/ukuran/host. MA/PN/PT memakai fallback arsip
JDIH Kemenkes ketika portal primer403; cakupannya terbatas, bukan seluruh Indonesia.
- Pencatatan relasi perubahan dengan kutipan tepat, workspace perbandingan versi dan
watch privat subscribe/check/list/unsubscribe. Konsolidasi resmi memerlukan sumber
konsolidasi yang ditentukan server; klaim caller tidak cukup.
- OCR PNG/JPEG/WebP, deskripsi gambar, transkripsi audio dan dua frame video nyata
melalui service CPU terpisah, DynamicUser, Unix socket0600, tanpa jaringan IP/GPU,
tanpa credential inference, dan temporary per-job yang dibersihkan setelah timeout.
- Model revision/hash dipin. Gate kesiapan memerlukan proof aktual ketiga jenis media;
cache analisis terikat tenant, filehash, bahasa dan revision adaptor.

API tetap `https://chat.harmonika.id/v1/legal` dengan Bearer token gateway.
Vault/kunci AES, perkara pelanggan, inference, coding, Telco dan gateway dipertahankan.
Rotasi hanya secret internal transport legal; API key pengguna tidak berubah.
Backup kode/unit/transport-secret berada di direktori server root-only untuk rollback.

## Pengujian

| Pemeriksaan | Hasil |
| --- | --- |
| Legal wajib macOS |269 lulus,0 skip |
| Legal wajib Linux |269 lulus,0 skip |
| Node.js |55 lulus,0 skip |
| Katalog staging melalui API |99 dipanggil;91 completed,8 degraded;0 gagal |
| Isolasi staging |case/file lintas token404; SSE completed/DONE |
| Regresi Python gabungan |1.361 ditemukan:1.179 lulus,182 skip;0 gagal |
| Katalog HTTPS production |99 dipanggil;91 completed,8 degraded;0 gagal;120,52detik |
| Paket/TUI .24 |Signature/install/rollback diuji; hasil pascaaktivasi di laporan JSON |

Delapan hasil `degraded` di smoke: tujuh pemeriksaan kelengkapan/status hukum yang
memang memerlukan penelaahan sumber/manusia, serta satu registri advokat gagal.
Tidak ada `unknown_skill` atau sukses analisis media dari metadata saja.
Fixture seluruhnya sintetis; token uji dibatasi dan dihapus setelah pengujian.

Media CPU aktual, serial: gambar sekitar24detik, suara sekitar5detik, dua frame video
sekitar47detik. OCR berhasil membaca marker `LEGALVISION7319`; transkripsi memuat
kata dokumen/Senin/bukti. Model kecil tetap dapat melewatkan bentuk dan salah eja;
pengujian ini tidak membuktikan akurasi profesional atau analisis video menyeluruh.

## Temuan audit yang ditutup

1. Unduhan HTTP dengan Content-Length terpotong kini ditolak sebelum menjadi snapshot.
Tes socket nyata mencakup EOF terpotong, chunk terpotong dan close-delimited sah.
2. Envelope fallback menyebut listing yang dicocokkan lokal, bukan mengklaim pencarian
server. Host/provenance/cakupan berasal dari sumber aktual.
3. Verifikasi kutipan relasi dihitung ulang terhadap snapshot terkini; snapshot berubah
membatalkan boolean verifikasi saat ini, sementara bukti historis tetap tersimpan.
4. Caller tidak dapat menjadikan dokumen sembarang sebagai konsolidasi resmi hanya
dengan menyebut nomor hukum. Tes memisahkan kutipan tepat dari validitas relasi hukum.
5. Default transkripsi Indonesia dan revision-cache mencegah penggunaan ulang keluaran
adaptor lama. Cache/idempotency auto-media berubah bila revision berubah.
6. Pemeriksaan non-root, akses credential dan integritas model tidak bergantung pada
`assert`; manifest kosong/parsial tidak menjadi ready. Worker siap sebelum core start.
7. Timeout/cancel hanya menghentikan process group job yang baru dibuat service sendiri;
plaintext temporary dihapus parent. Tidak menghentikan sesi terminal/agent pengguna.

OpenCode memberikan audit read-only dan reproduksi independen. Tes worker yang memakai
Python tanpa dependensi legal tidak dihitung sebagai acceptance; venv legal khusus
macOS/Linux menjalankan seluruh269 tes tanpa skip.

## Batas produksi yang tetap eksplisit

Ini edukasi berbasis sumber, bukan layanan advokat, vonis atau sertifikasi bukti.
Currentness, keaslian, identitas, speaker, penerapan dan kelengkapan graf hukum tetap
belum disertifikasi. Snapshot UU1/2026 dapat unavailable karena portal503.

Cache riset berisi metadata publik; query privat tidak disimpan mentah. Hasil pencarian
bukan teks hukum yang boleh langsung dikutip sebagai pasal. Timer sekitar12jam hanya
menyegarkan lima sumber manifest dan memeriksa watch; tidak menemukan semua hukum baru
atau mengirim notifikasi eksternal.

Isolasi per API token. Integrator satu token untuk banyak pengguna harus mengotorisasi
case_id sendiri di server aplikasinya. Jangan menaruh key server dalam browser publik.

Native binary tetap v0.98.1,15 panduan/440 descriptor. Jumlah descriptor tidak berarti
440 integrasi live. Auto-update tetapfalse; tidak ada commit/push GitHub pada pekerjaan ini.
