# Status distribusi

Source preview Harmonika TUI, 25 Oktober 2026.

## Bridge RTX untuk TUI — 25 Oktober 2026

- Aktif: API privat `/v1/media/*`, async queue dan tool MCP `generate_image`.
- Smoke dua token HTTPS lulus: render512×512, idempotency dan ownership privat.
- Stdio MCP terpasang →Windows GPU lulus6,926s; hasil0600 tanpa Base64 di konteks.
-46 tes Node dan24 tes antrean Linux lulus; gateway Go lulus.
- Helper signed `.31`, MCP bridge enabled; proses sesi pengguna tidak direstart.
- Worker nyata belum vision/references. Analisis memakai jalur Harmonika lama
  sebelum ada GPU dispatch. Mouse/wheel dua arah, resize dan auto-copy seleksi kode native lulus PTY
  serta clipboard macOS nyata; binary tidak diubah.
- [Kontrak](RTX-TUI-BRIDGE.md) · [Bukti](RTX-TUI-BRIDGE-REPORT.json).

## Registry hot-reload dan skala — 25 Oktober 2026

- Aktif: wait_ready, client GET polling, chunk64 dan source lazy di disk.
- Batas3.072modul; benchmark seluruh3.072lulus, peak RSS72,17MiB.
- 107tes Linux lulus; regresi537diMac (3skip khusus Linux). HTTPS lulus.
- Helper signed `.30`, binary sama; backlog offline diselaraskan ke SSE.
- [Report](DISCOVERY-SCALE-REPORT.json).

## Auto-discovery engine — 25 Oktober 2026

- Aktif di backend: boot scan, hot reload5detik, kontrak ketat dan audit SQLite.
- Satu modul runtime nyata `text.metrics`; katalog lama1950 tetap, total1951.
- 63 tes Linux; regresi493 tes di Mac (1 tes khusus Linux dilewati di Mac).
- Validator signature/metadata100% coverage; gabungan engine93,75%.
- Benchmark256modul: cold16,644detik, warm0,092detik; eventloop gap22ms.
- HTTP staging dan HTTPS publik lulus; token/fixture dibersihkan.
- Gateway binary dan9PID layanan lain tetap. Hanya registry di-restart.
- Status helper manual signed `.29`, binary TUI sama dengan `.28`, auto-updatefalse.
- [Kontrak](ENGINE-DISCOVERY.md) · [Bukti](ENGINE-DISCOVERY-REPORT.json).

## Unified core completion — 25 Oktober 2026

- 70 ID konkret:57 callable,12 internal,1 native WSS; unavailable0 pada namespace.
- Event bus durable, BM25 corpus operator, connector secret AES-GCM, GraphQL
  read-only dan WSS Bearer/ticket sudah aktif dan diuji publik.
- 121 tes unified macOS/Linux;403 regresi Python dan gateway Go lulus.
- HTTPS/WSS nyata menguji live pub/sub, model+retrieval, scope spoofing,
  expiry cursor, rotasi/delete idempotency, ticket reuse dan revoked token.
- Secret hanya metadata di API. Corpus awal6 dokumen kontrak API, bukan data
  bisnis/hukum fiktif. Readiness spesialis tetap terpisah; tidak mengklaim1153aktif.
- Database/kunci dipertahankan;7 PID engine lain tetap; TUI.28 tidak diubah.
- [Kontrak](UNIFIED-ENGINE.md) · [Bukti](UNIFIED-ENGINE-COMPLETION-REPORT.json).

## Riwayat MVP Unified engine — digantikan laporan completion di atas

- Service `harmonika-unified` aktif; namespace `/v1/unified` dan registry pusat.
- 70 ID konkret dari blueprint1.302 baris:53 callable,12 internal,5unavailable.
- 38 tes unified macOS/Linux;31tes worker OpenCode terpisah;225 regresi awal,
  gateway Go dan75pemeriksaan HTTPS lulus. Empat token/fixture dibersihkan.
- Chat nyata, planner web/mobile, Stop/replay, consent dan isolasi memory teruji.
- TUI tidak diubah. Tujuh engine lain mempertahankan PID.
- [Kontrak dan batas](UNIFIED-ENGINE.md) · [Bukti](UNIFIED-ENGINE-REPORT.json).

## Shared Skill Foundation — 2026.10.25.28

- `/v1/core/*` dan discovery/invoke `/v1/skills` memakai kontrak internal yang sama.
- Katalog99:12 operasi data/memory/workflow;19 adaptor coding workspace;
  2 adaptor chat;6 dokumen lokal;60 belum diimplementasikan.
- Memory AES-GCM per token, TTL, kuota, idempotency dan recovery; workflow DAG
  maksimal12 langkah pure tanpa shell/network/scheduler.
- 101 tes fondasi,10 tes rilis,86 regresi master,104 regresi mobile dan gateway Go
  lulus. Hasil HTTPS dan aktivasi aktual: CORE-FOUNDATION-REPORT.json.
- CLI `harmonika core`,19 panduan; binary native tetap dan auto-update false.
- REST core tidak mengaktifkan transport SSE/WS atau native MCP baru.
- [Kontrak](CORE-FOUNDATION.md) · [Checkpoint](CORE-FOUNDATION-CHECKPOINT.md).

## Mobile Builder Android/iOS — 2026.10.25.27

- Domain `/v1/mobile-builder/*`: 214 ID unik; header 165 pada naskah tidak sesuai.
- **91 tersedia** dalam mode eksplisit: 34 planning, 57 source_generation.
- **123 belum siap**: 56 belum diimplementasikan, 22 memerlukan toolchain,
  45 memerlukan connector/runtime berizin. Bukan klaim seluruh master selesai.
- 104 tes mobile wajib macOS/Linux tanpa skip; 86 regresi master dan gateway Go lulus.
- Staging/curl dan HTTPS menguji seluruh91 operasi aktif, auth, namespace, payload,
  serta penolakan build/store/IAP/OTA tanpa runtime. Token uji sementara dibersihkan.
- 35 kasus modul Expo/React Native + starter lolos typecheck; starter SwiftUI lolos
  typecheck SDK iOS, 23 file Dart lolos parse dan5 skenario merge dijalankan.
- CLI `harmonika mobile`,18 panduan; native binary/sesi dan auto-update false tetap.
- Enam PID engine lain tetap; tidak ada data pelanggan/ERP/CS yang diubah.
- [Kontrak](MOBILE-BUILDER.md) · [Bukti dan gap](MOBILE-BUILDER-REPORT.json).

## Consolidated Skill Index — 2026.10.24.26

- Indeks master:1.203occurrences/1.200unique IDs, bukan1.507. Tabel engine1.408.
- Discovery gabungan1.795 descriptor:29 analyzer/workflow available;567 memerlukan
  konteks engine lama;27local-only;1.111unavailable;54restricted;7benturan domain.
- Tidak mengklaim seluruh master selesai. Gap per ID tersimpan dalam laporan.
- 86tes wajib master macOS/Linux tanpa skip; regresi1.607 ditemukan,
  1.517 lulus/90skip;55tes Node dan gatewayGo lulus.
- HTTP staging/HTTPS29fungsi lulus; builder routing nyata dan ownership dua token lulus.
- Binary/IOC/regex/numerik/CSP diperbaiki setelah review; tidak ada data pelanggan
  atau database ERP/CS yang diubah. Enam engine lama mempertahankan PID.
- CLI `harmonika catalog`,17panduan; native/sesi tetap, update otomatis false.
- [Kontrak](MASTER-SKILLS.md) · [Audit/gap](MASTER-SKILLS-REPORT.json).

## Web Full-Stack Builder — 2026.10.24.25

- Backend aktif pada `/v1/builder/*`, token/principal/antrean terisolasi.
- 161 handler: 155 tersedia sesuai mode; 6 koneksi Notion/SSH belum dikonfigurasi.
  Generator deployment/monitoring adalah artifact, bukan klaim layanan sudah aktif.
- Seluruh161 ID lulus smoke staging dan HTTPS dua token; fixture/token dibersihkan.
- 160 tes builder wajib macOS/Linux tanpa skip; regresi Python 1.521 ditemukan,
  1.431 lulus dan90 skip platform/dependensi;55 tes Node dan suite gateway Go lulus.
- FastAPI+SQLite CRUD/auth/CSRF dan browser desktop/mobile lulus; Next.js lulus
  tsc, lint,5 unit test dan build webpack tanpa jaringan.
- OAuth cookie/state diperketat; signature webhook tidak otomatis menandai paid.
- TUI menambah `harmonika build from spec.md` dan `harmonika builder`;601 descriptor /
  16 panduan. Native binary/sesi tetap, update otomatis false.
- [Kontrak](WEB-BUILDER.md) · [Audit](WEB-BUILDER-AUDIT.md) · [Bukti](WEB-BUILDER-TEST.json).

## Legal Assistant — 2026.10.24.24

- 99 ID legal memiliki handler; 98 siap saat worker sehat dan satu registri advokat
  degraded karena sumber penerbit gagal, tanpa verifikasi profesi palsu.
- OCR/gambar, transkripsi audio Indonesia, dua frame video memakai CPU worker nyata
  terisolasi melalui Unix socket. Tanpa GPU/DRM, tanpa jaringan IP di worker.
- Riset portal publik terhubung, fallback MA terbatas arsip Kemenkes; metadata bukan
  kutipan hukum. Workspace perubahan/konsolidasi dan watch privat persisten tersedia.
- Vault tetap AES-256-GCM; tidak ada full prompt, bukti atau credential di log.
- 269 tes legal macOS dan Linux lulus tanpa skip. Smoke99 ID staging dan HTTPS production lulus;
  regresi serta batas hasil tercatat dalam laporan audit.
- 440 descriptor / 15 panduan; native binary dan sesi pengguna tetap. Auto-update false.
- [Kontrak](LEGAL-ENGINE.md) · [Laporan verifikasi](LEGAL-AUDIT.md).

## Engine telekomunikasi — 2026.10.24.22

- Dokumen master dibaca penuh: 276 ID unik / 25 engine; header 320 tidak sesuai tabel.
- **203 operasi lokal tersedia, 73 ID belum aktif**. Seluruh data terisolasi per key;
  database perusahaan, OLT/ACS, payment dan komunikasi live belum tersambung.
- Backend baru memakai preview/approval terikat hash+revisi, idempotency, SSE/replay,
  Stop sebelum commit, audit teredaksi dan recovery tanpa menjalankan ulang mutasi.
- **266 tes telco Linux lulus tanpa skip**. HTTPS produksi menguji dua token sementara,
  ownership, quota recovery, idempotency, SSE, adaptor tidak tersedia, planner read,
  planner mutation-preview dan Stop. Token serta fixture uji dibersihkan.
- Regresi source: 1.092 tes Python ditemukan, 999 lulus / 93 skip platform/dependensi;
  55 tes Node lulus. Suite Go lulus; tes HTTP wajib terpisah di Linux.
- Terminal menambah `harmonika telco`; shared registry berisi 341 deskriptor,
  bukan 341 integrasi live. Native binary tetap, tidak ada auto-update/Git push.
- [Kontrak](TELCO-ENGINE.md) · [Readiness 25 engine](TELCO-READINESS.md) ·
  [Laporan aktivasi](TELCO-ENGINE-STATUS.json).

## Pembaruan Phase 2 — 2026.10.24.21

- Phase2A–2F selesai: **38 skill coding** dan **65 operasi shared registry**.
- HTTPS produksi: seluruh20 skill baru, 63 pemeriksaan, isolasi token/approval,
  breakpoint/inspect/Stop dan explicit action=start lulus. Planner nyata memilih
  scaffold dari instruksi biasa tanpa pengguna memilih skill.
- **376 tes backend Linux tanpa skip**: regresi201, controller47, toolchain76,
  debugger52. Harness independen OpenCode lulus78 pemeriksaan HTTP.
- Source lokal: 826 tes Python ditemukan (723 lulus, 103 skip dependensi/platform),
  55 tes Node lulus. Kasus backend wajib diuji terpisah di Linux.
- Refactor/debugger awal khusus Python; paket offline terpin, bukan arbitrary
  internet install. Build hanya zipapp entry-point atau bundle esbuild.
- Native binary dan sesi pengguna tetap. Key Telegram/API yang sama memakai scope
  chat/coding dan izin workspace. Tidak ada auto-update atau push GitHub.
- [Kontrak lengkap](ENGINEERING-PHASE2.md) · [Laporan](ENGINEERING-PHASE2-STATUS.json).

## Pembaruan coding workspace — 2026.10.24.20

- 18 operasi engineering MVP aktif pada runtime backend terisolasi.
- Doctor OK; 14 panduan skill dan 45 operasi registry. Binary native tetap sama.
- 201 tes backend Linux lulus tanpa skip, termasuk Docker nyata; smoke HTTPS publik
  lulus untuk pemilihan tool otomatis, approval, Python 42, isolasi token dan SSE replay.
- Key Telegram yang sama dapat memakai `/v1/coding`; chat otomatis memakai workspace_id.
- Eksekusi/commit menunggu approval client. Proyek terminal lokal tidak otomatis ditransfer.
- [Kontrak dan batas MVP](CODING-MVP.md) · [Bukti pengujian](CODING-MVP-REPORT.json).

## Pembaruan engineering — 2026.10.24.19

- Instalasi pemilik: doctor OK, 13 panduan skill, 27 operasi shared registry;
  empat workflow engineering tersedia, binary kustom tetap sama.
- Source gabungan: 450 tes Python dan 55 tes Node lulus; suite Go race lulus.
- API publik memakai key Telegram yang sama dan memilih workflow otomatis dari
  kode/diff/log atau kebutuhan yang dikirim. Keempat workflow lulus smoke HTTPS,
  termasuk metadata laporan pada SSE final; SEO dan chat biasa tetap lulus.
- Engineering backend membaca bukti yang dikirim, bukan repository privat atau
  monitoring. Worker tidak menjalankan kode, test, patch atau deployment.
- [Laporan verifikasi terbaru](ENGINEERING-REPORT.json). Catatan rilis .14 di
  bawah adalah hasil fase sebelumnya; blocker distribusi publik tetap berlaku.

## Sudah diuji

- Instalasi pemilik: rilis 2026.10.24.14, identitas Harmonika, sebelas skill,
  delapan tool MCP, lima LSP, dokumen Office dan browser Chromium.
- Source: **198 tes TUI lulus**, terdiri dari 56 tes Node.js dan 142 tes Python;
  **20 tes backend RTX** terpisah lulus pada Python 3.12.3.
- Empat skill terminal baru lolos validator; self-test keyboard/resize PTY,
  lifecycle async dan parser SSE/UTF-8 lulus. Startup native terisolasi
  menampilkan keempat skill baru. Tidak ada prompt provider yang dikirim.
- Rilis skill ditandatangani Ed25519; checksum binary tidak berubah.
  Helper merupakan implementasi referensi, bukan rewrite event loop native.
- Checkpoint/cache metadata per proyek: 18 tes SQLite, termasuk WAL, lock,
  corruption dan concurrency. Riwayat tetap native; key tidak disalin.
- Gateway loopback aktif setelah cloud smoke lulus; parser reasoning dipakai
  jalur native. Fallback lokal teruji fixture tetapi runtime/model nyata belum tersedia.
- Mouse tracking/SGR terkonfirmasi PTY; adapter clipboard OS tersedia.
- Wrapper inference v1 terpasang: teacher/local/custom, contract test tanpa
  HTTP dan native gateway fixture lulus. Gateway sudah direstart terkontrol;
  integration_contract_active=true, SSE dan native TUI nyata lulus.
- Kontrak mentor diperketat pada wrapper/parser. Empat mentor memiliki fixture
  sintetis dan source review agregator; belum empat tes live per bridge.
- RTX queue privat aktif terpisah, generate nyata 512×512 lulus. Vision worker
  belum tersedia; kontrak v1 analyze diuji fixture.
- Browser smoke desktop 1440×900 dan mobile 390×844 dijalankan dengan fixture lokal.
- HOME baru dengan dependency yang diprovisikan eksplisit dapat memulai MCP dan
  membuat proyek. Media ditolak dengan jelas ketika transport publik tidak ada.
- Source checker meninjau kandidat tanpa mencetak secret.

Pengujian HOME baru bukan bukti bahwa installer lengkap telah bekerja di
perangkat baru. Target binary yang tersedia saat ini baru Mac Apple Silicon.

## Belum siap untuk end user publik

1. Binary kustom, asal build, target platform, dan bootstrap fresh install.
2. Transport media HTTPS dengan key dan isolasi pengguna; bridge SSH pemilik
   tidak didistribusikan dan source menonaktifkannya secara default.
3. Entitlement/distribusi binary terminal untuk pengguna baru masih terpisah dari
   API coding. Key API dengan scope chat/coding dapat memakai workspace privat;
   unlimited tetap ditentukan server dan tidak menghapus batas resource sandbox.

API key harus dibuat per pengguna, dikirim melalui saluran privat, dan bisa
dicabut. Unlimited mengikuti hak akses server; client tidak dapat memberikannya.
API tidak memberikan akses SSH, akun browser, CS, ERP, atau database pelanggan.

## Isi repository

Runtime, skill, tes, dokumentasi dan atribusi upstream. Tidak memuat binary
pribadi, API key, signing key privat, SSH key, cookie, database, history sesi,
audit privat, atau direktori dependency pengguna.

Basis TUI tetap menggunakan perangkat lunak upstream dengan lisensi yang
disertakan. Perubahan merek tidak menghapus lisensi dan atribusinya.

Source preview ini memakai identitas dan kontrak artefak `harmonika`, termasuk
nama executable serta environment `HARMONIKA_*`. Source bukan paket update
langsung untuk artefak lama; build native dan installer publik yang cocok masih
harus disiapkan. Perubahan repository tidak mengganti binary pemilik yang aktif.
