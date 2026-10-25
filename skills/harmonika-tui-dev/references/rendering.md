# Layout terminal yang stabil

Hitung ruang tersedia dari ukuran PTY saat ini. Sisihkan baris status dan composer sebelum membagi riwayat/panel. Pada viewport kecil, runtuhkan panel sekunder; pada satu baris tampilkan aksi/fokus utama secara terpotong aman. Jangan menghasilkan dimensi negatif, membungkus border ke baris berikutnya, atau mengirim koordinat di luar viewport.

Ukur lebar dalam **sel terminal**, bukan jumlah byte/code point. Gunakan primitive Unicode/grapheme yang sudah ada dalam stack. Jangan memotong di tengah karakter lebar, combining mark atau emoji ZWJ. Ambiguous width, emoji variation dan font terminal dapat berbeda: dokumentasikan kebijakan dan periksa terminal target. Jangan menambah dependency Unicode tanpa kebutuhan serta verifikasi.

ANSI bukan teks berlebar. Pisahkan span gaya dari teks; clip teks ke sel tersedia dan akhiri span/line dengan reset agar warna tidak bocor. Jangan merender escape/control mentah dari output tool/model: gunakan renderer aman atau sanitasi. Wrap dahulu sesuai lebar efektif, kemudian hitung scroll. Pastikan offset dan seleksi tetap valid setelah clip/wrap.

Pada resize, ukur ulang, hitung layout, clamp scroll/cursor lalu render ulang dari state yang sama. Pertahankan draft, fokus, pilihan, dan request aktif. Composer multiline memakai ruang yang dibatasi, bukan menggeser seluruh konten tanpa batas. Riwayat hanya auto-scroll jika sebelumnya berada di bawah; saat pengguna membaca pesan lama, pertahankan anchor dan tampilkan indikator pesan baru.

Matriks minimum: 80×24, 120×40, 40×10, 20×5 dan 1×1; resize bolak-balik sambil mengetik/streaming. Uji ASCII, `界`, `é`, `🙂`, `👩‍💻`, pesan panjang tanpa spasi, output warna dan error. Pastikan baris terlihat tidak melebihi kolom, jumlah baris sesuai tinggi, reset ANSI lengkap dan state bertahan. Dimensi dan glyph di atas adalah target uji; hasil bergantung terminal yang benar-benar dijalankan.
