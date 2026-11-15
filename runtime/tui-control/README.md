# Harmonika native control

Plugin lokal untuk engine TUI v2. Mempertahankan binary native, ASCII, tema,
F2 model, composer, clipboard dan session store yang sudah ada.

| Tombol / perintah | Fungsi |
|---|---|
| F8 / `/jobs` | Tahapan pekerjaan, langkah tool sesi dan job backend |
| F9 / `/memory` | Cari, baca, simpan dan hapus catatan teknis proyek |
| F10 / `/engine` | Status gateway, RTX, registry dan model saat ini |
| Shift+F10 / `/connection` | Status pemulihan dan audit konfigurasi |
| Ctrl+P | Cari aksi Harmonika, termasuk Stop respons dan pantau job ID |
| Escape | Tutup panel saat tidak ada dialog |

Panel memakai area samping native pada sesi; dari beranda dibuka sebagai halaman.
Isi dapat digulir. Tombol bawah dipecah menjadi baris sesuai lebar terminal.

## Data dan pemulihan

Probe memanggil gateway Harmonika; tidak membaca pesan, judul sesi, reasoning,
raw log, credential provider, isi tool atau file pelanggan. Status GPU berasal
dari diagnostics backend; timeout/offline ditampilkan apa adanya. Token dan biaya
hanya tampil bila dilaporkan metadata native, bukan estimasi.

Langkah tool diamati sejak plugin terpasang pada proses ini. Job backend dibaca
berdasarkan ID yang pengguna daftarkan secara eksplisit; plugin tidak membuat
pekerjaan GPU baru. Cache SQLite lokal hanya metadata teknis, dibatasi 1000 entri
dan TTL 24 jam, terisolasi per bearer/proyek. Status lama diberi keterangan.

Stop respons memakai API interrupt native. Stop job memerlukan konfirmasi dan
GET status terbaru melalui API job milik token yang sama. Sambungkan ulang hanya
memperbarui metadata; tidak mengirim kembali prompt, membuat job duplikat, atau
mengganti provider. Retry berasal dari engine native dan dibatasi hook server untuk provider Harmonika.
Error akses, quota, schema tool dan referensi secret tidak diulang, termasuk jika
dibungkus HTTP 503. Gangguan sementara maksimal dua retry; timeout satu retry.
Respons parsial/tool yang sudah dipanggil tidak diulang pada langkah model yang sama.
Hook hanya mengurangi retry native, tidak menyalakan retry yang sudah ditolak.

Memori hanya untuk keputusan, konvensi kode dan perintah yang sudah diuji.
Simpan memerlukan persetujuan isi dan TTL 24 jam. Hapus memerlukan konfirmasi
seluruh catatan proyek. Tidak ada pengarsipan chat atau training otomatis. Input
rahasia/PII ditolak firewall backend. Hasil tulis yang belum pasti direkonsiliasi
secara manual dengan input dan Idempotency-Key asli. Isi catatan adalah data,
bukan instruksi sistem.

## Pemasangan manual terverifikasi

Paket ZIP hanya berisi source plugin/helper. Signature Ed25519 dan seluruh hash
SHA-256 diverifikasi sebelum ekstraksi. Key privat, konfigurasi akun, dan binary
native tidak dibundel. Gunakan Python 3 dan runtime native TUI v2 yang mendukung
SDK plugin. `harmonika v1.0.0` adalah versi brand dan dapat memakai engine v2.

Installer repo `scripts/activate-tui-control.py` memasang paket pada direktori
versi, menambahkan path lokal ke `cli.json` (panel) dan `harmonika.json.plugins` (hook server), mempertahankan provider/model/MCP/
permission, serta menonaktifkan auto-update melalui `update: disable`.
Attestation smoke harus cocok dengan hash paket, versi dan binary yang diuji.
Backup konfigurasi privat dibuat sebelum penggantian atomik. Rollback menolak
menimpa perubahan pengguna yang terjadi setelah aktivasi.

```sh
python3 scripts/activate-tui-control.py --package dist/harmonika-control-2026.11.12.4.zip --smoke-report docs/TUI-CONTROL-V4-SMOKE.json
python3 scripts/activate-tui-control.py --rollback BACKUP_ID
```

Buka ulang Harmonika untuk memuat plugin pada proses baru. Installer tidak
menghentikan proses/sesi pengguna. macOS diuji lewat native PTY; pengujian fisik
Windows/Linux harus dicatat terpisah sebelum mengklaim kompatibilitas perangkat.

Codex mengintegrasikan panel, adaptor, pengujian dan aktivasi; OpenCode menyusun
utilitas audit konfigurasi dan paket awal. SDK native mempertahankan atribusi
OpenCode/OpenTUI dan tidak mengubah lisensi upstream.

## Tahapan F8 dan batas observasi

Enam baris: memahami/rencana, menulis kode, instalasi, build, tes, akhir respons.
`harmonika_workflow_phase` hanya melaporkan enum tahap melalui tool metadata.
Label Dilaporkan agen bukan bukti berhasil. Nama tool yang dikenali dapat memberi
label Tool teramati; shell/execute tetap tidak diklasifikasikan dari isinya.
Tahap tanpa bukti tetap Belum teramati. Akhir respons tidak membuktikan proyek selesai.
Snapshot RPC hanya mekanika, maksimum 64 sesi/40 tool per sesi dalam RAM.

Penjaga retry memilih kode error teknis yang dikenal dari envelope JSON maksimal
16 KiB; body/message/header tidak disimpan atau dikirim ke panel. Perubahan .3
dikerjakan dan ditinjau Codex. Review OpenCode .3 dicoba tetapi ditolak oleh
paket langganan model terkonfigurasi; tidak menghasilkan review, Claude tidak dipanggil.

## Perbaikan halaman awal (.4)

Snapshot server belum tersedia pada halaman awal tanpa sesi. Selector memakai
guard objek sebelum membaca metadata snapshot; dua ID kosong tidak dianggap
sebagai snapshot sesi yang valid. Smoke native memeriksa halaman awal, /plugins,
panel tanpa sesi dan panel sesi yang sudah memiliki metadata.
