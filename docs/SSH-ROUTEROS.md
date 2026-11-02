# Audit MikroTik melalui Harmonika Terminal

Tool SSH khusus memakai Paramiko tanpa shell POSIX dan tanpa pseudo-TTY.
RouterOS memakai bahasa console sendiri; `/system/resource print` tidak boleh
diubah menjadi nama executable Linux. Rujukan:
[SSH resmi MikroTik](https://manual.mikrotik.com/docs/management-tools/ssh/).

## Alur

`remote_targets` → `ssh_connect` → `ssh_exec` → periksa hasil dan exit status.
Contoh input tool: `{"target":"router-audit","argv":["/system/resource","print"]}`.
Platform serta daftar menu yang diizinkan disertakan pada discovery dan connect.
Satu koneksi per operasi; tidak menganggap sesi login persisten.

Registry privat `~/.config/harmonika/ssh-targets.json` menerima target berikut.
Ini template, bukan router aktif. Ganti alamat/pin/credential secara lokal oleh
operator; jangan kirim credential sebagai argumen tool atau prompt.

```json
{
  "host": "router.example.invalid",
  "port": 22,
  "username": "audit",
  "platform": "routeros",
  "auth_method": "public_key",
  "identity_file": "/absolute/private/router-key",
  "known_hosts": "/absolute/private/known_hosts",
  "allowed_routeros_paths": [
    "/system/resource", "/system/identity", "/interface",
    "/ip/address", "/ip/route", "/ip/firewall/filter",
    "/ip/firewall/nat", "/queue/simple"
  ]
}
```

Untuk router yang belum memakai key: `auth_method: "password"` dan
`password_file: "/absolute/private/router-password"`, hilangkan identity_file.
Berkas harus dimiliki UID runtime, mode 0600, bukan symlink. Host key tetap
wajib diverifikasi dan dipin; tidak menerima host otomatis. Tidak mengubah
konfigurasi autentikasi router maupun membuka firewall.

Hanya menu audit yang ada pada daftar kode dan registry operator diterima.
Aksi hanya `print`, opsi terbatas detail/terse/stats/count-only/without-paging.
Mutasi, script, export, secret, ekspresi dan shell chaining ditolak sebelum jaringan.
Perintah yang tidak didukung model RouterOS dilaporkan gagal, bukan disembunyikan.
SFTP/tunnel untuk platform routeros belum didukung. Target POSIX tidak berubah.

Timeout, output maksimum 64 KiB, host-key checking dan audit mekanik tetap aktif.
Output RouterOS yang berisi error tidak dianggap sukses walau SSH mengembalikan
exit code nol. Exit status yang tidak diketahui tetap uncertain/error.
Audit SQLite tidak menyimpan command/output/password/host/chat pelanggan.

## Verifikasi dan batas

Tes `tests/ssh/test_routeros.py` memakai SSH socket nyata ke server sintetis
yang meniru respons RouterOS; ini bukan bukti telah login ke perangkat MikroTik.
Tes REST `test_backend_api.py` juga menguji parsing dan penolakan mutasi RouterOS.
Source REST di repo siap, tetapi perlu aktivasi layanan tersendiri sebelum
fitur REST baru dipakai di VPS. Pembaruan terminal tidak merestart VPS.

Aktivasi lokal manual: `python3 scripts/activate-routeros-helper.py`.
Signature, checksum, MCP handshake dan doctor diperiksa sebelum pergantian rilis.
Binary, branding, konfigurasi, izin native dan sesi pengguna tidak diganti.
Proses TUI/MCP yang sudah terbuka dapat masih memakai rilis lama; buka ulang
terminal Harmonika pada waktu yang dipilih pengguna. Jangan hentikan sesi lain.
