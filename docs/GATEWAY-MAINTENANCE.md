# Restart gateway dan verifikasi wrapper

Target lokal: LaunchAgent `id.harmonika.tui.gateway`, loopback `127.0.0.1:18184`.
Skrip ini untuk instalasi macOS pemilik yang sudah tersedia, bukan installer publik
atau prosedur restart server chat.harmonika.id.

## Perintah

Dari root repository, pemeriksaan tanpa restart:

```sh
python3 scripts/restart-tui-gateway.py
```

Restart eksplisit dan verifikasi otomatis:

```sh
python3 scripts/restart-tui-gateway.py --apply \
  --target-release 2026.10.24.14 \
  --report docs/GATEWAY-ACTIVATION-REPORT.json
```

Target default mengikuti pointer current yang terpasang, sehingga apply tanpa
versi tidak diam-diam menurunkan rilis. Versi rollback dideteksi dari argv proses gateway yang berjalan. Operator dapat
menentukan versi sehat yang sudah diketahui dengan `--rollback-release`. Versi
harus tersedia lokal, bertanda tangan valid dan memiliki binary yang sama.
Perintah apply dapat menyebabkan jeda singkat; jalankan pada jendela maintenance.

## Prosedur aman

1. Verifikasi signature/checksum target dan rollback, syntax dan MCP preflight.
2. Cocokkan label/plist/launcher serta PID pemilik listener. Tolak proses lain
   pada port yang sama. Ambil maintenance lock dan update lock.
3. Tunggu tiga detik stabil tanpa koneksi TCP pada proses gateway, termasuk
   upstream dan CLOSE_WAIT. Timeout 40 detik; tidak ada force restart jika sibuk.
4. Pilih target runtime secara atomik, lalu restart hanya LaunchAgent tersebut.
   Tidak ada pencocokan/kill massal, restart native writer, SSH, atau service VPS.
5. Tunggu health kontrak v1, cek auth 401, models, SSE dan executable native nyata
   dengan socket/data/config/global-data terisolasi. Tool eksternal dimatikan pada
   fixture native. Teks respons, key, config dan history tidak masuk laporan.
6. Jalankan doctor. Jika verifikasi gagal, tunggu idle lagi, pilih versi rollback
   dan restart service yang sama. Rollback juga menolak memutus request aktif.

Drain menggunakan observasi socket, bukan admission-control atomik. Request baru
masih dapat tiba di sela observasi dan restart, sehingga maintenance window tetap
berlaku. Error upstream di luar gateway tidak dapat diselesaikan oleh rollback
versi. Status rollback gagal/busy dilaporkan dan tidak diikuti kill paksa.

Script tidak mengubah konfigurasi API key, data sesi, database, binary, atau
endpoint aplikasi lain. `--apply` hanya digunakan saat restart gateway memang
diperintahkan operator; dry-run merupakan default.

## Hasil aktivasi

[Laporan aktual](GATEWAY-ACTIVATION-REPORT.json): wrapper `.13` aktif, auth 401,
SSE lulus dengan satu terminal dan satu DONE (~5,14 detik), native TUI lulus
(~4,49 detik), doctor ok. Tidak perlu rollback. Model offline tetap belum tersedia;
hal itu tidak memblokir engine guru melalui kontrak internal.

Cek ulang tanpa restart:

```sh
harmonika network status
harmonika doctor
```

Harus terlihat gateway_running=true, integration_contract_available=1,
integration_contract_active=true dan reasoning_parser_active=true.
