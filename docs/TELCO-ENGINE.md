# Engine telekomunikasi Harmonika

Core domain baru memakai API internal terpisah dari model mentor dan dari layanan CS/ERP yang sudah berjalan. Web, terminal, atau integrasi lain dapat memakai kontrak yang sama. SQLite menyimpan data per token, request, persetujuan, event berurutan, dan audit teredaksi.

Dokumen sumber sudah dibaca penuh (1.141 baris; SHA-256 `57cf870d1d763774633495099ca351fae043dad059a2ed1de2fa807bfc44381e`). Header menyebut 320 skill, tetapi tabel berisi 277 baris dan **276 ID unik dalam 25 engine**. Duplikasi `fraud.usage.anomaly` dihapus dari registry.

## Batas kesiapan

`GET /v1/telco/capabilities` merupakan sumber readiness aktual per ID. Registrasi tidak berarti semua fungsi atau integrasi live telah tersedia. Operasi lokal memakai data yang dimasukkan pemilik token; tidak otomatis membaca database pelanggan Harmonika.

- `scope.kind = isolated_tenant`, `company_data_connected = false`.
- `telco_network_live = false`, `telco_payment_gateway = false`, `telco_kyc = false`.
- Monitoring/prediksi mengolah bukti bertanggal dengan sumber; tidak mengaku melakukan polling atau memprediksi kerusakan secara pasti.
- Pembayaran tercatat sebagai `recorded_unverified`; bukti dari pemanggil tidak dapat menyatakan pembayaran terverifikasi. Top-up dengan klaim `verified_by_adapter=true` ditolak.
- Komisi hanya dihitung atas invoice **paid**, periodenya sesuai, dengan atribusi penerima yang tidak ambigu. Pesanan disetujui saja tidak menghasilkan komisi.
- KYC, refund, penagihan ulang, notifikasi dan tindakan jaringan yang memerlukan otoritas eksternal tidak dijalankan hanya karena model menyarankannya.
- Perhitungan pajak/kontribusi memerlukan tarif dan policy eksplisit; tidak ada tarif hukum bawaan atau klaim pelaporan/pembayaran pajak.
- Merge akun dinonaktifkan: satu orang dapat mempunyai beberapa langganan sah.

## API publik terautentikasi

Base URL `https://chat.harmonika.id/v1`. Header `Authorization: Bearer <key Harmonika>`; key ber-scope `chat` atau `telco` hanya memberi akses ke tenant key tersebut. Key tidak memberi akses jaringan atau database perusahaan.

| Metode | Path | Tujuan |
|---|---|---|
| GET | `/telco/capabilities` | Readiness, schema, batas dan alasan unavailable |
| POST | `/telco/invoke` | Jalankan operasi lokal / buat preview perubahan |
| POST | `/telco/agent` | Model memilih tool domain secara otomatis |
| GET | `/telco/requests/{id}` | Recovery status dan hasil milik token |
| GET | `/telco/requests/{id}/events` | JSON atau SSE, `after_sequence`/`Last-Event-ID` |
| POST | `/telco/requests/{id}/approve` | Persetujuan eksplisit terikat hash |
| POST | `/telco/requests/{id}/stop` | Batalkan request yang belum disetujui |

`invoke` memerlukan `Idempotency-Key` dan body:

```json
{"skill_id":"product.create","input":{"name":"Paket contoh milik tenant","price_idr":150000,"speed_mbps":30}}
```

Semua mutasi mengembalikan `status=approval_required`, `approval_preview`, `input_hash`, dan `expires_at` (300 detik). Periksa preview, lalu kirim:

```json
{"decision":"approve","input_hash":"<hash dari request>"}
```

Perubahan tenant setelah preview membuat approval basi (`stale_approval`). Hash salah, tenant lain, key idempotensi konflik, dan approval kedaluwarsa ditolak. Status akhir `completed/failed/stopped/expired/interrupted`; kegagalan tidak menyisakan perubahan parsial. Restart menandai pekerjaan yang belum pasti sebagai `interrupted`, tidak mengeksekusinya ulang. Stop saat transaksi sudah committing menghasilkan 409, tanpa mengklaim pembatalan palsu.

Event memiliki `event_id`, `request_id`, `sequence`, `timestamp`, `type`, `data`. Jenis: `response.created`, `tool.started`, `response.completed`, `response.error`, `response.stopped`. Idempotency dijamin selama jendela replay 24 jam; gunakan key baru hanya untuk tindakan baru. Metadata request/event terminal dipertahankan 24 jam; data bisnis tetap persisten. GET recovery/event dan Stop tidak memotong kuota request gateway.

Agent menerima `{"messages":[{"role":"user","content":"Buat laporan ARPU periode 2026-09 dari data tenant ini"}],"stream":false}`. Maksimal delapan panggilan tool/90 detik, tidak memiliki tool approval. SSE mengirim metadata progres dan heartbeat; ini belum streaming token jawaban model. Tidak ada pelabelan palsu hasil tool sebagai jawaban realtime token demi token.

Untuk klien Chat Completions gunakan `model=harmonika-ai`, `telco=true` dan `Idempotency-Key`. Mode ini tidak boleh digabung dengan `workspace_id` atau tools milik klien. Request chat biasa tetap memakai alur sebelumnya.

Voucher baru hanya ditampilkan pada hasil approval pertama. Recovery/idempotency/agent cache tidak mengembalikan ulang kode; voucher yang diterima divalidasi lalu di-hash sebelum disimpan. Simpan hasil pertama secara aman. Data pelanggan/bukti tidak dicetak ke log; audit menyimpan nama field dan jenis kejadian.

## Terminal

```sh
harmonika telco capabilities
printf '%s' '{"period":"2026-09"}' | harmonika telco invoke report.arpu
printf '%s' '[{"role":"user","content":"Buat laporan ARPU periode 2026-09 dari tenant ini"}]' | harmonika telco chat
harmonika telco status <request-uuid>
harmonika telco events <request-uuid> --after-sequence 1
harmonika telco approve <request-uuid> --input-hash <hash> --confirm
harmonika telco stop <request-uuid>
```

Key dibaca dari file privat konfigurasi Harmonika yang sudah ada, tidak dikirim melalui argumen terminal. Deskriptor registry tidak menjalankan handler domain lewat worker dokumen lokal. Gunakan capabilities server untuk readiness, bukan menghitung jumlah deskriptor.

## Adaptor live berikutnya

`connector_contract.py` menyediakan transport read-only async dengan allowlist operasi, URL milik konfigurasi server, credential internal, verifikasi tenant/skill/request/schema/sumber/waktu, timeout, batas byte dan larangan redirect. Konektor ini **belum tersambung ke perangkat atau data perusahaan**. Write connector sengaja ditolak sampai otorisasi, ownership, validasi, idempotency dan uji target nyata tersedia.

Integrasi live memerlukan profil sumber resmi dan akun layanan berizin untuk OLT/ONT/ACS/SNMP, billing/payment webhook, komunikasi berizin, sistem operator/interconnect, serta proses regulator. Jangan mengaktifkan capability dari sekadar MIME/schema atau keberhasilan HTTP kosong.

## Pengujian dan operasi

`python -m unittest discover -s tests -p 'test_telco_*.py'` menjalankan unit dan acceptance dengan data sintetis. Aiohttp diperlukan untuk tes HTTP. Gateway memiliki unit test serta opt-in `TestTelcoLive` yang membuat dua token sementara, menguji model nyata, lalu menghapus tokennya. Jangan menjalankan smoke memakai akun pelanggan.

Service khusus `harmonika-telco-engine.service`, loopback `127.0.0.1:18189`, state `/var/lib/harmonika-telco/state.sqlite`, secret hanya di `/etc/harmonika-ai/telco-engine.token`. Pembatasan systemd menolak jaringan non-loopback. Tidak ada akses langsung dari publik ke port worker.

Lihat [matriks readiness per engine](TELCO-READINESS.md) untuk daftar ID yang tetap belum dapat dijalankan. Skema registry juga menyertakan output envelope, permission tenant, timeout, rate limit dan akses antarmuka; ini tidak memberi host shell/filesystem kepada model.
