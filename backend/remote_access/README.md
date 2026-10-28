# Harmonika SSH backend

Layanan SSH privat untuk pemilik/operator Harmonika. REST async memakai worker
Paramiko yang sama dengan TUI. API hanya bind `127.0.0.1`; key API publik Harmonika
bukan credential layanan ini. API utama, CS, aplikasi iOS/Android tidak diubah.

## Alur

Owner token → sesi server → target terdaftar → rate/quota → validasi argv/path →
approval operator bila mengubah data → audit started → worker terisolasi → hasil
exit/checksum → audit final. Unknown/changed host key ditolak; tidak ada auto-add.
Perintah shell gabungan/interpreter/sudo ditolak, termasuk saat ada approval.
Node memakai [client.cjs](client.cjs), bukan koneksi SSH atau credential di frontend.

## API lokal

Base URL produksi: `http://127.0.0.1:18231`. Header `Authorization: Bearer ...`
memakai `/etc/harmonika-remote/api.token` (owner-only). Jangan menaruh token pada
contoh repo, URL, terminal history, TUI prompt atau log. Jalankan request dari
proses operator yang membaca file secara lokal. Public reverse proxy tidak dipasang.

| Method | Path | Input / hasil |
|---|---|---|
| GET | `/healthz` | Liveness tanpa credential atau metadata sensitif |
| POST | `/api/v1/ssh/sessions` | `{}` → `session_id`, berlaku 24 jam |
| GET | `/api/v1/ssh/targets` | Target terdaftar tanpa key/password |
| POST | `/api/v1/ssh/test` | `{target}` → autentikasi terverifikasi |
| POST | `/api/v1/ssh/execute` | `{target, argv}` atau `{target, command}` |
| POST | `/api/v1/ssh/upload` | `{target, project_root, local_path, remote_path}` |
| POST | `/api/v1/ssh/download` | Input path yang sama; tanpa overwrite |
| POST | `/api/v1/ssh/tunnel` | `{target, tunnel}` → uji direct-tcpip terdaftar |
| POST | `/api/v1/ssh/approvals` | `{operation, input}` + credential operator terpisah |
| GET | `/api/v1/ssh/audit/verify` | Verifikasi rantai HMAC |
| GET | `/metrics` | Prometheus, autentikasi wajib, label teknis saja |

Tambahkan `X-Harmonika-Session` dari sesi yang diterbitkan server untuk operasi
SSH. Kompatibilitas contoh dokumen: `host/user/port` juga diterima, tetapi harus
cocok tepat dengan satu target terdaftar. Tidak dapat memilih key/password/path
credential dari body. Payload maksimum 32 KiB; stdout/stderr 64 KiB; SFTP 10 MiB.
Koneksi/auth 8 detik, exec maksimum 120 detik, maksimum 4 worker aktif.

Contoh body execute (jangan sertakan token dalam source):

```json
{"target":"chr","command":"uname -s","timeout":30}
```

`success` alias dari `ok`; hasil ada di `data.stdout/stderr/exit_code` atau
`data.sha256/size_bytes`. HTTP 401 salah token, 403 policy/approval, 429 limit,
502 kegagalan transport, 504 timeout. Mutasi yang statusnya uncertain tidak diulang.

## Approval dan limit

Upload serta executable non-readonly memerlukan approval. Token approval 60 detik,
sekali pakai, terikat actor + operasi + argv/path lengkap. Penerbitan memerlukan
`X-Harmonika-Approval-Key` dari file terpisah, bukan field `approved:true` dari AI.
Executable tetap harus diizinkan pada registry. Native approval TUI tetap terpisah;
akses backend tidak diam-diam menonaktifkan permission lokal.

Batas 30 panggilan/menit berlaku pada actor, target dan IP (nilai disimpan sebagai
HMAC). Pelanggaran memicu cooldown 300 detik. Maksimum 100 operasi per sesi.
Sesi hanya diterbitkan server, maksimum 5 sesi baru per 5 menit per actor. Layanan
saat ini memakai satu credential owner, bukan klaim multi-tenant publik.

## Instalasi aktif

- Server engine: SSH alias `vps`, layanan `harmonika-remote-access.service`.
- Source: `/opt/harmonika-ai/remote-access/current/remote_access/`.
- Venv terpisah: `/opt/harmonika-ai/remote-access/venv`.
- Config/key/pin: `/etc/harmonika-remote/`, file 0600, user `harmonika-remote`.
- State: `/var/lib/harmonika-remote-access/`, mode 0700, SQLite WAL.
- Target aktif `chr`; key backend baru terpisah dari key pemilik Mac dan staging.
- OpenSSH tersedia; transport aplikasi memakai Paramiko. Key encrypted didukung
  melalui `passphrase_file` privat. Instalasi otomatis ini memakai key tanpa
  passphrase yang dilindungi file owner-only dan sandbox systemd; Vault/HSM belum
  dikonfigurasi. Tidak mengklaim rotasi 90 hari otomatis telah terpasang.

Unit systemd memakai UID khusus, NoNewPrivileges, ProtectHome/ProtectSystem,
capability kosong, memory/task limit, dan izin tulis hanya pada state sendiri.
Tidak membuka firewall atau mengubah sshd server target. Tidak memakai sshpass.
Script deployment menolak instalasi yang sudah ada agar update tidak menimpa diam-diam.

## Monitoring dan Data Firewall

Ledger primer menyimpan timestamp, skill ID, status, error teknis, latency, serta
rantai HMAC; tidak menyimpan command, output, host, username, nama file, credential,
chat pelanggan atau screenshot. Scope rate limiter/approval berupa HMAC. Worker
punya audit mekanik terpisah dengan retensi maksimum 5000 record; ledger backend
primer tidak menghapus record otomatis. Kebijakan operasi: simpan ledger minimal
365 hari dan salin snapshot terverifikasi ke penyimpanan operator di luar host.
Tidak ada klaim storage eksternal immutable: root OS masih dapat mengubah file;
verifikasi HMAC mendeteksi perubahan isi/rantai yang diuji, bukan menjamin root
berbahaya tidak bisa mengganti seluruh state. Audit yang gagal ditulis menghentikan
operasi sebelum koneksi remote. Key integritas terpisah dari token API agar rotasi
API token tidak merusak rantai lama.

`/metrics` dan verifikasi integritas aktif. Contoh Prometheus, alert dan dashboard
Grafana ada di `monitoring/`. Collector Prometheus/Grafana/Loki/SIEM eksternal belum
terhubung karena alamat dan akses collector belum diberikan. Tidak mencetak payload
ke journal/access log. Backup DB gunakan SQLite backup API, termasuk konsistensi WAL;
jangan hanya menyalin berkas utama saat ada writer aktif.

## Docker / Kubernetes

Runtime aktif adalah bare metal/systemd. Dockerfile dan manifest Kubernetes adalah
template opsional, bukan deployment cluster yang sudah diuji. Build dari root repo:
`docker build -f backend/remote_access/Dockerfile .`. Mount credential milik UID
10001 (0600), state privat, root FS read-only. API tetap loopback dan hanya diakses
operator atau gateway privat dalam network namespace yang sama. Kubernetes contoh
menyalin Secret projected ke tmpfs nyata karena connector menolak symlink credential.
Isi Secret hanya lewat operator, jangan commit. Egress harus IP /32 dan port target
sesungguhnya; bukan rule `0.0.0.0/0:22`. Edit contoh sebelum apply.

## Verifikasi

```text
ssh-runtime/bin/python -m unittest discover -s tests/ssh -v
node --test tests/remote-client.test.cjs
runuser -u harmonika-remote -- env HARMONIKA_SSH_CONFIG=/etc/harmonika-remote/targets.json HARMONIKA_SSH_STATE_DIR=/var/lib/harmonika-remote-access/worker-audit /opt/harmonika-ai/remote-access/venv/bin/python /opt/harmonika-ai/remote-access/smoke.py
curl --fail http://127.0.0.1:18231/healthz
```

Staging memakai port 18232, config/state/key berbeda; diuji sebelum produksi.
Smoke menguji SSH nyata, execute, upload/download fixture sendiri, approval replay,
blocked command, token publik ditolak, tunnel VNC, audit dan metrics. Fixture dibersihkan.
Key staging dicabut sesudah test; layanan staging dihentikan oleh proses deployment
pemiliknya. Jangan menghentikan sesi Codex/OpenCode atau sshd lain untuk cleanup.

## Disable / rollback

Untuk pause tanpa restart utama, operator mengubah `enabled:false` di target
registry dengan penulisan atomik yang mempertahankan UID/mode. Perubahan dibaca
setiap request. `SSH_ENABLED=false` juga didukung pada startup layanan.

Rollback layanan SSH baru ini: operator menghentikan hanya
`harmonika-remote-access.service`, lalu mengembalikan symlink `current` ke release
SSH sebelumnya (bila ada), dan memverifikasi health sebelum start. Tidak mengubah
`harmonika-ai.service`. Cabut key berdasarkan blob public key tepat yang kompromi,
bukan `sed` yang menghapus semua key dengan kata Harmonika. Rotasi key: buat baru,
pin key host tetap terverifikasi, enroll public key, tes key baru, ganti config atomik,
lalu cabut key lama. Jangan menghapus seluruh authorized_keys atau private key Mac.

## Penyesuaian terhadap panduan asal

Semua 14 bagian panduan 1.979 baris dibaca. Implementasi memilih Python + adapter
Node internal, dedicated SSH skill (alternatif rekomendasi §6.3), bare metal nyata,
RejectPolicy, argv terkontrol dan log mekanik. AutoAddPolicy, accept-new otomatis,
root API tanpa auth, pembukaan egress global, command mentah di audit dan tunnel
reverse-forward yang contoh kodenya tidak benar tidak dipakai. Vault, cluster serta
collector eksternal dijelaskan sebagai integrasi operator, bukan readiness palsu.
