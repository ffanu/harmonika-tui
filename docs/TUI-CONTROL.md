# Kontrol native Harmonika TUI

Pembaruan `2026.11.12.4` menyediakan status engine, monitor pekerjaan, memori
proyek, pemulihan koneksi dan audit konfigurasi dalam plugin native v2.
ASCII, tema, F2, composer, clipboard, sesi serta binary bermerek tetap dipakai.

| Buka | Isi dan tindakan |
|---|---|
| **F8** / `/jobs` | Tahapan pekerjaan, status respons/tool, pantau job, Stop |
| **F9** / `/memory` | Cari/baca key, simpan dengan persetujuan, konfirmasi hapus proyek |
| **F10** / `/engine` | Gateway/RTX/registry/model dan metrik yang benar-benar tersedia |
| **Shift+F10** / `/connection` | Status terputus/retry/akses/limit, reconnect metadata, audit config |
| **Ctrl+P** | Cari aksi dengan awalan `Harmonika`, tersedia juga saat panel terbuka |
| **Escape** | Tutup dialog/panel; tidak membatalkan pekerjaan secara tersembunyi |

Pada Mac yang memakai tombol fungsi untuk media, gunakan Fn bersama F8–F10.
Panel sesi berada di samping pada terminal lebar dan memakai area penuh ketika
ruang sempit. Isi dapat digulir, sedangkan baris aksi tetap terlihat.

## Alur dan batas

- Status berasal dari API HTTPS Harmonika. Waktu probe bukan waktu respons model.
  RTX offline/timeout tidak dipoles menjadi online. Angka registry bukan jumlah
  seluruh skill siap pakai. Token/biaya kosong ditampilkan belum dilaporkan.
- Monitor tool hanya membaca event mekanis sejak plugin dimuat. `execute` adalah
  nama tool native luar; tidak membaca isi CodeMode untuk menebak subperintah.
  Selesai berarti native telah menerbitkan hasil tool, bukan bukti semua efek
  eksternal berhasil. Exit code hanya ditampilkan bila metadata menyediakannya.
- Job backend dipantau berdasarkan ID yang didaftarkan pengguna. Plugin tidak
  membuat job GPU dan tidak mencampur pekerjaan antar token/proyek. Stop membaca
  status milik token terbaru sebelum mengirim kontrol ke endpoint yang sesuai.
- Reconnect memperbarui status/metadata sesi. Tidak membuat prompt/job baru,
  mengulang mutation, atau mengganti provider. Retry milik native dibedakan dari
  tindakan kontrol ini. Respons parsial yang terputus tetap perlu diperiksa.
- Memori hanya catatan teknis curated dengan consent eksplisit dan TTL 24 jam.
  Riwayat chat tidak diarsipkan. Tulis yang hasilnya belum pasti memakai ulang
  input dan Idempotency-Key yang sama ketika pengguna memilih Periksa perubahan.
  Hapus mencakup semua catatan pada proyek/token itu, tanpa menghapus sesi chat.

Adapter panel tidak membaca messages.parts, judul sesi, reasoning, isi tool,
raw backend log, file pelanggan atau credential provider. SQLite lokal hanya
metadata job, TTL 24 jam dan maksimum 1000 entri. Berkas key/config/cache privat
tidak masuk paket atau laporan.

## Konfigurasi dan rilis

Paket source/helper lokal dipasang melalui `cli.json` SDK v2 untuk panel dan
`harmonika.json.plugins` untuk hook server. Hook hanya membatasi retry provider
Harmonika; tidak mengubah izin eksekusi atau mengganti model. Konfigurasi
provider/model/MCP/permission lama dipertahankan: normalizer native mendukung
alias lama. Audit tidak menyimpulkan suatu setting diabaikan hanya karena nama
lamanya. Auto-update dinonaktifkan lewat setting native `update: disable`.

`scripts/activate-tui-control.py` memeriksa signature Ed25519, manifest, hash
seluruh file, hash paket dan kecocokan smoke dengan binary. Instalasi atomik
memakai direktori versi serta backup privat. Paket tidak berisi native binary,
key privat atau config pemilik. Rollback menolak menimpa perubahan config yang
terjadi setelah aktivasi.

```sh
python3 scripts/activate-tui-control.py \
  --package dist/harmonika-control-2026.11.12.4.zip \
  --smoke-report docs/TUI-CONTROL-V4-SMOKE.json

python3 scripts/activate-tui-control.py --rollback BACKUP_ID
```

Buka proses Harmonika baru untuk memuat pembaruan. Aktivasi tidak menutup sesi
yang sedang digunakan. Laporan aktivasi menyimpan backup ID untuk rollback.

## Bukti pengujian

[Laporan .3](TUI-CONTROL-V3-REPORT.json), [smoke native .3](TUI-CONTROL-V3-SMOKE.json)
serta [laporan .2 sebelumnya](TUI-CONTROL-REPORT.json)
dan fixture teks pada `docs/mockups/tui-control/` menggunakan data sintetis.
Uji native dilakukan di macOS melalui PTY privat. Windows dan Linux belum diuji
secara fisik untuk plugin ini; hasil Mac tidak diklaim sebagai aktivasi semua PC.

Codex: panel, adaptor, firewall, review, integrasi, pengujian dan aktivasi.
OpenCode: implementasi awal audit konfigurasi dan utilitas paket beserta tes.
Claude tidak dijalankan. [Instruksi delegasi](../AGENTS.md) tetap berlaku bersama
batas scope pengguna; source fork native yang sedang dikerjakan pengguna tidak
diubah atau dibangun ulang oleh pembaruan plugin ini.

## Retry dan tahap pekerjaan (.3)

Error akses, quota, argumen/schema tool dan referensi secret langsung berhenti,
termasuk kode yang dibungkus HTTP 503. Error sementara paling banyak dua retry;
timeout satu retry. Hook tidak pernah mengaktifkan retry yang ditolak native.
Setelah teks/tool muncul, langkah model yang sama tidak diputar ulang. Pesan
saran perbaikan tetap tersedia di F8/Koneksi; reconnect tidak mengirim prompt lagi.

F8 memisahkan memahami/rencana, menulis, instalasi, build, tes, dan akhir respons.
Tool `harmonika_workflow_phase` dapat menandai tahap, dengan label Dilaporkan agen.
Hanya nama tool yang tepat dipakai untuk inferensi; input/output/command tidak
dibaca. Tahap yang tidak teramati tidak dianggap selesai. Build/tes memerlukan
hasil perintah; akhir respons bukan jaminan pekerjaan tuntas.

Runtime memilih kode error dikenal dari envelope JSON terbatas 16 KiB, tanpa
menyimpan body/message/header. Snapshot RPC dalam RAM berisi metadata saja,
maksimum 64 sesi dan 40 tool/sesi, tidak menambah arsip chat atau database.

Implementasi/review .3: Codex. Review tambahan OpenCode tidak tersedia karena
model terkonfigurasi ditolak paket langganan. Tidak ada perpindahan provider.

## Perbaikan .4: plugin failed pada beranda

Guard snapshot di selector status mencegah akses objek undefined saat belum ada
sesi. Uji /plugins secara eksplisit memastikan kontrol TUI dan companion server
aktif; panel status sebelumnya tidak cukup untuk mendeteksi gagal setup di beranda.
[Laporan .4](TUI-CONTROL-V4-REPORT.json) · [Smoke .4](TUI-CONTROL-V4-SMOKE.json).
