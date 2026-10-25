<div align="center">

# HARMONIKA TUI

### Satu terminal. Satu ruang kerja. Harmonika Ai.

Coding, dokumen, analisis media, dan pengujian browser dalam alur kerja terminal yang terkontrol.

**19 panduan skill** · **8 tool MCP** · **5 LSP** · **Update manual terverifikasi**

[Website](https://hai.harmonika.id) · [Panel & API](https://chat.harmonika.id) · [Status rilis](docs/STATUS.md) · [Dokumentasi](#mulai)

![Demo terminal Harmonika](docs/assets/harmonika-demo.gif)

[▶ Tonton video MP4](docs/assets/harmonika-demo.mp4) · [Lihat screenshot](docs/assets/harmonika-terminal.png)

</div>

> **Source preview** — aplikasi lokal sudah diuji. Installer publik, distribusi binary, dan entitlement key terminal masih disiapkan. Jangan membagikan credential instalasi pemilik. [Lihat kesiapan publik →](docs/STATUS.md)

## Ruang kerja yang menyatu

| | Kemampuan | Yang tersedia |
|---|---|---|
| ⌨️ | **Coding & terminal** | Baca/edit file, membuat folder proyek, bash dengan persetujuan pengguna, dan diagnostik LSP. |
| 🧩 | **Skill terarah** | Alur UI, debug, browser QA, dokumen, Git, dan rilis. |
| 🌐 | **Browser QA** | Screenshot desktop/mobile, pemeriksaan overflow, serta pencatatan error halaman. |
| 📄 | **Dokumen** | Baca/buat PDF, DOCX, XLSX, PPTX secara lokal; pengolahan halaman dan watermark PDF. |
| 🖼️ | **Media** | Analisis gambar, frame video, transkripsi audio lokal, dan permintaan gambar melalui layanan Harmonika Ai. |
| 🔑 | **Kontrol key** | Validasi key ke server, pemeriksaan entitlement, dan penyimpanan key privat. |
| 🛡️ | **Pembaruan terkendali** | Signature Ed25519, checksum SHA-256, preflight, dan rollback. Tidak ada auto-update. |

### Skill resmi

| Skill | Fokus |
|---|---|
| `harmonika-ui` | Implementasi UI responsif, aksesibilitas, dan kesesuaian referensi. |
| `harmonika-debug` | Reproduksi bug, diagnosis berbasis bukti, serta pengujian regresi. |
| `harmonika-browser-qa` | Bukti browser nyata pada viewport desktop dan mobile. |
| `harmonika-shared-core` | Data terbatas, memory terenkripsi per token, workflow DAG dan adaptor workspace melalui API internal. |
| `harmonika-skill-index` | Discovery capability, analisis defensif, dan pemeriksaan HTML browser terisolasi. |
| `harmonika-mobile-builder` | Analisis spec dan source kit Android/iOS; build/sign/store mengikuti readiness worker terpisah. |
| `harmonika-web-builder` | Spesifikasi → arsitektur → source privat → QA sandbox → deployment target terdaftar. |
| `harmonika-legal` | Perkara/bukti privat, edukasi berbasis sumber resmi, dan gateway legal terpisah; bukan layanan advokat. |
| `harmonika-coding` | 38 operasi backend dalam workspace privat: baca/edit/run/test/Git dengan approval. |
| `harmonika-engineering` | Review kode, diagnosis berbasis bukti, rencana pengujian dan dokumentasi dari input yang dikirim. |
| `harmonika-seo` | Audit halaman/teknis/konten/schema/sitemap, visual, drift, strategi dan adapter data Google. |
| `harmonika-documents` | Baca/buat dokumen melalui runtime lokal yang terdeteksi. |
| `harmonika-git` | Review perubahan dan alur Git tanpa tindakan destruktif otomatis. |
| `harmonika-release` | Pemeriksaan rilis, paket, verifikasi, dan rencana pemulihan. |
| `harmonika-tui-dev` | Layout terminal, resize, fokus keyboard, dan clipping ANSI. |
| `harmonika-async-concurrency` | Antrean terbatas, streaming nonblocking, timeout dan pembatalan. |
| `harmonika-ai-integration` | Parser SSE/UTF-8, penyusunan konteks dan riwayat tool yang utuh. |
| `harmonika-state-persistence` | Integritas sesi native, checkpoint dan cache metadata privat per proyek. |
| `harmonika-terminal-qa` | Uji reducer, keyboard, resize dan PTY terisolasi. |

Helper baru memakai Python stdlib dan fixture sintetis; hasil self-test membuktikan helper, bukan perubahan pada event loop binary aplikasi. Skill memberi panduan dan implementasi referensi yang dapat dipakai saat mengerjakan source TUI.

[Filosofi arsitektur core mandiri](docs/ARCHITECTURE-PHILOSOPHY.md): mentor eksternal, interface internal, dan target worker media Windows RTX.

## Engine modular

Kontrak `AIRequest → AIIntegration → AIAdapter` memisahkan TUI dari model guru, lokal, atau LLM mandiri. Payload, event, dan identitas UI tetap sama. [Interface dan contoh pemakaian](docs/AI-INTEGRATION.md). [Restart aman dan verifikasi](docs/GATEWAY-MAINTENANCE.md).

## Unified engine backend

Backend `chat.harmonika.id/v1/unified` menyediakan sesi privat, job, routing otomatis,
Stop dan replay SSE. Tepat70 ID blueprint:57 callable,12 mekanisme internal,1 transport WSS;
seluruh70 ID konkret terhubung. GraphQL, event bus, knowledge API dan secret connector
tersedia sesuai scope. Perencanaan web/mobile memakai engine yang sudah ada; bukan klaim build
atau deploy. [Kontrak backend](docs/UNIFIED-ENGINE.md) · [Laporan](docs/UNIFIED-ENGINE-COMPLETION-REPORT.json).

## Registry otomatis

Modul operator baru dipindai dan diverifikasi saat boot serta setiap 5 detik,
lalu langsung tersedia di `/v1/skills` tanpa restart gateway. Kontrak rusak,
checksum berubah dan ID bentrok ditolak dengan audit SQLite terpisah.
`harmonika state status` menampilkan status engine; binary TUI tetap sama.
[Kontrak dan batas](docs/ENGINE-DISCOVERY.md) · [Hasil uji](docs/ENGINE-DISCOVERY-REPORT.json).

## Shared Skill Core

Fondasi `/v1/core` menyatukan **99 ID** dengan status yang eksplisit: **12 operasi
data/memory/workflow**, **19 adaptor workspace**, **2 adaptor chat**, **6 dokumen
lokal**, dan **60 belum tersedia**. Adaptor workspace mempertahankan permission,
approval dan job engine coding. Memory terenkripsi, TTL dan idempotency diperiksa
per token; workflow hanya memakai operasi pure yang terdaftar.
Gunakan `harmonika core capabilities`, `harmonika core inspect ID`, atau
`harmonika core invoke ID` dengan JSON stdin. REST ini belum menyediakan stream
atau native MCP tambahan. [Kontrak dan contoh](docs/CORE-FOUNDATION.md).

Registry bersama memuat 9 operasi dokumen, 14 operasi SEO, 4 workflow engineering, dan 38 operasi coding workspace, ditambah 276 deskriptor telekomunikasi dan 99 deskriptor legal dan 161 deskriptor web builder (601 total; status aktif mengikuti capability, bukan jumlah katalog).
Coding workspace memakai runtime terisolasi tersendiri; perintah lokal tidak diam-diam menjalankannya di host. [Kontrak coding MVP](docs/CODING-MVP.md).
Identity/proyek, permission, idempotency, Stop dan event job diperiksa sebelum
worker lokal dijalankan. `harmonika skills list` menampilkan readiness aktual;
`harmonika skills execute` menerima JSON stdin. API publik telah menghubungkan subset SEO read-only serta workflow engineering dari pesan pengguna. Operasi dokumen dan artifact lokal tetap melalui executor lokal; tidak semua operasi tersedia lewat REST/MCP. [Kontrak dan batas fase](docs/SHARED-SKILLS.md).

## Harmonika SEO

Audit berbasis bukti untuk halaman, teknis, konten, JSON-LD, sitemap, local SEO dan
GEO. Tersedia cluster keyword, snapshot visual desktop/mobile, baseline drift,
verifikasi tautan sumber, serta adapter PSI dan Search Console/GA4. Readiness/data
provider tetap diperiksa; OAuth Google diperlukan untuk data akun, dan screenshot
JS-disabled bukan audit SPA penuh. [Kontrak, batas, dan setup](docs/SEO.md).

## Engineering otomatis

Code review, debug, testing strategy dan documentation menggunakan bukti kode/diff/log yang dikirim pengguna. Backend memilih workflow tanpa parameter skill manual. Worker statis tidak menjalankan kode, membaca repository privat, atau mengklaim test/coverage telah lulus. [Kontrak dan batas](docs/ENGINEERING.md). [Hasil verifikasi](docs/ENGINEERING-REPORT.json).

## Phase 2 — Power Coding Agent

[Kontrak dan checkpoint](docs/ENGINEERING-PHASE2.md): 20 skill tambahan untuk scaffold,
format/lint/type check, build, paket offline, test/coverage, refactor Python dan
debugger sandbox. Readiness per skill dibaca dari capabilities; [laporan aktivasi](docs/ENGINEERING-PHASE2-STATUS.json).

## Engine telekomunikasi

Katalog **276 ID / 25 engine**, dengan operasi domain lokal untuk inventory,
provisioning, billing, pelanggan, sales/dealer, teknisi, SLA, analisis dan workflow.
Readiness per operasi dibaca dari `harmonika telco capabilities`; jumlah katalog
bukan jumlah integrasi live. Mutasi memerlukan preview dan approval terikat hash,
SSE/recovery memakai ownership token, dan data tenant terpisah dari database
perusahaan. [Endpoint, penggunaan, pengujian dan batas live](docs/TELCO-ENGINE.md).

## Indeks skill pusat

`harmonika catalog capabilities` memperlihatkan rekonsiliasi master A–Z dengan
registry yang benar-benar ada. Master memuat1.200 ID unik; katalog gabungan1.795
bukan jumlah operasi aktif. Tersedia28 analyzer defensif berbasis input dan satu
workflow Chromium terisolasi. Namespace/konteks engine lama tetap diperiksa;
operasi yang belum dibangun atau belum terhubung tidak diklaim siap.
[Kontrak dan batas](docs/MASTER-SKILLS.md) · [Laporan](docs/MASTER-SKILLS-REPORT.json).

## Mobile builder Android & iOS

Katalog **214 ID** dengan analisis spec, rekomendasi stack/arsitektur dan generator
source. `harmonika mobile capabilities` membedakan **planning/source_generation**
dari eksekusi native. Source kit bukan APK/IPA; build, signing, device farm dan
store publishing tetap false sebelum worker/connector berizin diuji.
[Endpoint dan batas](docs/MOBILE-BUILDER.md).

## Web builder full-stack

Katalog **161 ID** untuk spesifikasi, pemilihan stack/database, arsitektur,
scaffold 10 framework, frontend/backend, SQL, QA sandbox, desain, SEO,
serta konfigurasi deployment/monitoring. `harmonika build from spec.md`
membuat proyek privat dan job; `harmonika builder capabilities` menunjukkan
readiness. Source diunduh memakai token pemilik, bukan URL publik.

Generator menghasilkan source/config yang perlu ditinjau. Selesai menulis
file tidak berarti seluruh fitur bebas telah dibuat, semua framework telah
diuji runtime, atau aplikasi sudah production. SSH, Notion dan cloud memerlukan
target/akun berizin. [Kontrak dan batas](docs/WEB-BUILDER.md) ·
[Hasil audit](docs/WEB-BUILDER-AUDIT.md).

## Pipeline Windows RTX

Adapter HTTP async dan antrean persisten tersedia sebagai backend privat terpisah. Generate RTX nyata sudah diuji; vision worker nyata belum tersedia. [Kontrak, batas dan deployment](backend/rtx_queue/README.md).

## Mouse, clipboard dan koneksi

Mouse/wheel native aktif. Adapter clipboard OS, gateway reasoning dan engine failover tersedia. Offline baru siap setelah runtime/model lokal terdeteksi; saat ini belum tersedia pada instalasi pemilik. [Kontrak dan hasil uji](docs/TUI-INTERACTION.md).

## Sesi dan cache

Riwayat native tetap disimpan oleh aplikasi. Checkpoint baru menyimpan pointer sesi per proyek, tanpa menyalin percakapan atau key.

```sh
harmonika state status --format text
harmonika state checkpoint --data-dir /path/data-sesi
harmonika state resume --data-dir /path/data-sesi
harmonika state clear-cache
```

`resume` memberikan argv untuk dijalankan sendiri, tanpa attach otomatis. Cache metadata dibatasi 128 proyek/24 jam; clear-cache tidak menghapus chat. Permission privat bukan enkripsi. [Kontrak dan batasnya](docs/TUI-STATE.md).

## Demo yang nyata

GIF dan video di atas direkam dari aplikasi Harmonika yang berjalan pada **PTY terisolasi**. Demo menampilkan pemanggilan terminal, versi, pemeriksaan kesehatan, banner HARMONIKA, skill, dan koneksi MCP. Tidak memakai mockup percakapan, token contoh yang menyerupai key nyata, atau data pelanggan.

Script perekam tersedia di [`docs/assets/record-demo.py`](docs/assets/record-demo.py). Perekaman memerlukan aplikasi terpasang, Pillow, pyte, serta font monospace yang sesuai platform. Video dapat dibuat dari GIF dengan encoder lokal. Demo bukan bukti bahwa fresh installer publik sudah tersedia.

## Mulai

### Instalasi cepat (macOS, Linux, Windows)

**macOS & Linux:**

```sh
curl -fsSL https://chat.harmonika.id/assets/harmonika-terminal/install.sh | sh
```

**Windows (PowerShell):**

```powershell
irm https://chat.harmonika.id/assets/harmonika-terminal/install.ps1 | iex
```

Atau instalasi langsung dari repository ini:

```sh
git clone https://github.com/ffanu/harmonika-tui.git
cd harmonika-tui
python3 runtime/install.py
python3 runtime/license.py activate
```

Pada instalasi Harmonika yang sudah disiapkan:

```sh
harmonika
harmonika doctor
harmonika network status
harmonika clipboard status
harmonika skill terminal-qa self-test
harmonika skill async-concurrency self-test
harmonika skill ai-integration self-test
harmonika skill documents capabilities
harmonika skill browser --url http://localhost:3000 --out /path/proyek/qa
```

Dari Home, minta AI membuat folder melalui `create_directory` di Downloads atau Projects. Operasi file berikutnya menggunakan folder proyek tersebut; direktori privat dan credential tetap dilindungi.

### Untuk pengembang

```sh
git clone git@github.com:ffanu/harmonika-tui.git
cd harmonika-tui
python3 runtime/check-public-source.py
python3 runtime/public-preflight.py
```

`public-preflight.py` sengaja mengembalikan `public_ready=false` selama penghalang distribusi belum diselesaikan. Repository ini belum menyediakan perintah instalasi satu baris untuk perangkat baru.

## Key & layanan

**Base URL:** `https://chat.harmonika.id/v1`<br>
**Identitas layanan:** Harmonika Ai

Setiap pengguna harus memiliki key sendiri yang diterbitkan serta dapat dicabut oleh server. Hak coding dan unlimited berasal dari entitlement server; mengubah konfigurasi client tidak memberikan hak tambahan.

```sh
python3 runtime/license.py check
python3 runtime/license.py activate
```

Aktivasi meminta key melalui prompt tersembunyi, bukan argumen shell. Key disimpan dengan permission `0600`. Aktivasi gagal tidak mengganti key sebelumnya. Token standar Telegram saat ini belum otomatis memberi coding/unlimited. Jalur penerbitan key terminal masih menjadi bagian pekerjaan backend publik.

Unlimited mengacu pada kuota request key, sementara concurrency, waktu proses, dan ketersediaan worker tetap berlaku. Source tidak memberikan akses ke akun, SSH, browser pribadi, CS, ERP, atau database pelanggan.

## Update dari Harmonika

```sh
harmonika update --check
harmonika update --apply /path/paket-rilis.zip
harmonika rollback
```

Pembaruan dilakukan atas perintah pengguna. Paket diverifikasi sebelum diaktifkan; versi sebelumnya disimpan untuk pemulihan. Key, cookie, history, dan signing key privat tidak disertakan dalam paket publik.

[Katalog layanan & dokumentasi Harmonika](https://chat.harmonika.id/assets/harmonika-terminal/)

[Matriks kesiapan 16 fitur dan hasil smoke aktual](docs/SKILL-READINESS.md). [Dispatcher dokumen lokal](docs/DOCUMENTS.md).

## Verifikasi & batas kemampuan

Source telah diuji pada **Mac Apple Silicon**. Pengujian meliputi tool MCP, pembatasan path, branding, dokumen, lisensi key, bootstrap, signature, rollback, serta HOME terisolasi dengan dependency yang disediakan.

- Browser QA diuji pada desktop **1440 × 900** dan mobile **390 × 844**.
- Dokumen Office mendukung konten dasar, bukan jaminan pelestarian layout kompleks atau makro.
- PDF lokal: baca/buat, seleksi/merge/rotasi halaman dan watermark; ekstraksi **20 halaman/30.000 karakter**, maksimal **10 MiB**. PDF scan/OCR belum didukung.
- Audio: maksimal **120 detik pertama**.
- Video: **6 frame sampel**; suara tidak ikut dianalisis.
- Media menggunakan path file melalui MCP; bukan upload native UI.
- Kemampuan layanan tetap mengikuti entitlement key dan kesiapan worker.
- Windows/Linux dan instalasi lengkap pada perangkat baru belum diverifikasi.

[Status terperinci dan penghalang publik →](docs/STATUS.md)

## Struktur repository

```text
runtime/      Launcher, validasi key, pemeriksaan rilis, dan tool MCP
skills/       Tiga belas skill resmi beserta helper
tests/       Pengujian terisolasi
docs/        Status, demo GIF/video, dan screenshot
```

Credential, binary pribadi, database, cookie, sesi pengguna, audit privat, dan direktori dependency tidak dimasukkan ke repository.

## Lisensi & pemberitahuan

Komponen pihak ketiga tetap mengikuti pemberitahuan hak cipta dan ketentuan lisensinya. Lihat [NOTICE.md](NOTICE.md) dan [UPSTREAM-LICENSE.md](UPSTREAM-LICENSE.md). Perubahan merek tidak menghapus kewajiban tersebut.

---

<div align="center">

**Harmonika Ai — ruang kerja Anda, tetap dalam kendali Anda.**

[hai.harmonika.id](https://hai.harmonika.id) · [chat.harmonika.id](https://chat.harmonika.id)

</div>

### Legal Assistant — domain privat

Perkara dan bukti terenkripsi, penelaahan edukatif, kutipan sumber resmi, recovery/SSE,
dan CLI `harmonika legal`. Status masing-masing skill mengikuti capability token.
Katalog memuat99 handler: OCR/gambar/audio/video sampel melalui worker CPU terisolasi,
riset portal publik, perbandingan versi, dan watch privat. Registri advokat tetap
degraded saat sumber penerbit gagal. Lihat [kontrak dan batas legal](docs/LEGAL-ENGINE.md).
Bukan pengganti advokat; metadata/pengamatan bukan kepastian hukum.
