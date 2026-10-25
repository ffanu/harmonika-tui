---
name: harmonika-legal
description: Edukasi hukum berbasis sumber resmi, pengelolaan perkara dan bukti privat melalui domain backend legal Harmonika.
---

Gunakan untuk pertanyaan hukum Indonesia dan penelaahan bukti, bukan tindakan hukum
resmi. Selalu tampilkan: “Edukasi hukum, bukan nasihat hukum formal atau pengganti advokat.”

Baca capability aktual lewat `harmonika legal capabilities`. Buat satu perkara per
masalah dengan yurisdiksi, fakta, perspektif, dan tanggal kejadian. Jangan memasukkan
perkara berbeda ke sesi yang sama. Jangan menganggap tanggal pada bukti adalah tanggal
kejadian; minta klarifikasi dan gunakan keterangan eksplisit pelanggan.

Panggil hanya skill available dari 99 ID resmi. Gunakan descriptor dan input_schema
backend. Metadata/label pasal dari dokumen permintaan tidak membuktikan arti atau
berlakunya pasal. Cari sumber resmi dahulu, berikan kutipan tepat beserta URL dan hash
snapshot. Status hukum terbaru dan penerapan pada kasus tetap belum tersertifikasi.

Bukti menggunakan file_id milik token/perkara dan vault terenkripsi, bukan path host
atau URL publik. OCR/pengamatan gambar perlu konfirmasi manusia; tidak menentukan
identitas, keaslian, atau kesalahan. Bila adaptor tidak siap, nyatakan belum dianalisis.
ASR/video hanya jika capability aktif; video hanya dua frame sampel, bukan seluruh
isi video. Angka/nama/tanggal hasil media harus dikonfirmasi. Tidak ada pengajuan resmi.

Jangan menyatakan telah menghubungi advokat/polisi, membuat laporan resmi, memastikan
vonis, mengarang sumber, atau menyimpan full prompt/bukti di log. Engine terpisah dari
CS, finansial, dan Telco; tidak mengubah konfigurasi layanan lain.

Kontrak dan batas rinci: `docs/LEGAL-ENGINE.md` di repo; dalam paket terminal ada di `tools/docs/LEGAL-ENGINE.md`. CLI `harmonika legal` menyediakan
create-case, invoke, chat, recovery dan replay. Upload/download memakai REST Bearer.
