---
name: harmonika-ssh
description: Discover registered SSH, Telnet and VNC targets, execute verified remote operations, and handle key authentication without exposing credentials.
---

# Harmonika SSH

Gunakan tool MCP `harmonika-ssh` untuk koneksi SSH, bukan `bash ssh/scp`.

- `ssh_connect` memverifikasi key dan host terdaftar; koneksi ditutup setelah pemeriksaan.
- `ssh_exec` menjalankan argv yang executable-nya diizinkan operator pada target.
- `ssh_upload` / `ssh_download` memakai SFTP, path relatif dalam root terdaftar,
  batas 10 MiB, dan menolak overwrite serta symlink.
- Sebut alias target yang diminta pengguna. Jangan memilih server lain saat target gagal.
- Target, port, private key dan known_hosts hanya di konfigurasi lokal privat.
  Jangan membaca, menampilkan, atau mengirim key/password ke model/API.
- Pertahankan persetujuan native TUI untuk tindakan remote; tidak ada auto-grant.
- Hasil tool adalah bukti. Jangan mengklaim tersambung, perintah selesai, atau
  file tersimpan sebelum `ok:true`. Nonzero exit bukan sukses.
- Timeout/mutasi terputus dapat berstatus uncertain; jangan otomatis mengulang
  command/upload karena bisa menggandakan tindakan.
- Audit hanya mekanik (waktu, skill, status/error, latensi), tanpa command/output,
  history chat, host, username, path, atau isi file.
- Coding sandbox tetap terisolasi. Token publik tidak memberi akses key lokal.

CLI: `harmonika ssh targets`; `harmonika ssh invoke` membaca envelope JSON stdin.
Kontrak: `docs/SSH-CONNECTOR-SPEC.txt`. SSH tidak memerlukan AI provider tambahan.

## Temukan koneksi sebelum menyatakan tidak bisa

1. Panggil `remote_targets` dengan hostname atau endpoint yang disebut pengguna.
   Alias SSH dan endpoint lengkap terdaftar sama-sama diterima oleh tool SSH.
2. Untuk SSH, `ssh_connect` lalu `ssh_exec` dengan executable yang diizinkan.
   Target `chr` tersedia untuk root@chr.banyu.live:22059. Jangan kirim password
   ke tool atau membaca file credential.
3. `telnet_connect`/`telnet_exec` memakai login prompt dan credential lokal
   operator, melalui tunnel SSH. Jika belum ada target Telnet, jelaskan bahwa
   host/port/credential tujuan belum ditentukan; jangan menganggap SSH=Telnet.
4. `vnc_connect` membaca framebuffer; `vnc_capture` menyimpan PNG privat ke
   proyek; `vnc_control` click/key/text hanya untuk target yang mengizinkan kontrol.
   Gunakan capture sebelum/sesudah aksi untuk verifikasi, bukan asumsi.
5. Jangan membuka public port, memasang daemon Telnet, atau mengubah layanan
   desktop pengguna untuk memaksa tes menjadi hijau.

Transport dibangun sebagai core backend `backend/remote_access/connector.py`,
worker lokal rilis memakai salinan identik `tools/remote_connector.py`. Setiap
panggilan berjalan dalam proses terpisah dengan deadline; library VNC ditutup
setelah satu operasi sehingga event loop tidak mengganggu sesi terminal.

## Tool budget compatibility
Gateway membatasi maksimal 32 tool per request. MCP mengiklankan 7 tool remote:
ssh_connect, ssh_exec, ssh_upload, ssh_download, remote_targets, telnet, vnc.
Semua 10 operasi tetap ada. Gunakan `telnet` dengan operation connect/exec;
gunakan `vnc` dengan operation connect/capture/control, lalu parameter terkait.
Nama telnet_connect/exec dan vnc_connect/capture/control di atas adalah nama
operasi legacy; tidak diiklankan sebagai tool terpisah pada rilis compact.
