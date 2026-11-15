# Panduan keputusan dan konteks engine

## Controller tugas otonom

`agent-controller.cjs` memeriksa kelanjutan pekerjaan, checklist, referensi hasil,
hasil gagal dan pengulangan tanpa kemajuan sebelum keputusan mencapai executor.
Kontrak hanya internal; TUI/Cline tetap menerima content/tool_calls normal.
Aktif di backend dan sudah diuji melalui terminal native pada tiga project nyata
yang sengaja rusak. [Alur, pengujian dan batas](AGENT-WORKFLOW.md).

Ujian debugging repository berikutnya mencakup pagination, cache async dan antrean
persisten. `debug-workflow.cjs` meminta reproduksi, penelusuran lintas modul serta
regresi dari seluruh kontrak README. Controller menolak receipt gagal yang dikutip
sebagai bukti selesai, termasuk kegagalan sebelum hasil terakhir, dan bukti yang
hanya kosong. Metadata executor terstruktur diperiksa tanpa menebak kata error di
source/log. Frasa lanjutan sopan mempertahankan konteks tugas. Bukti aktivasi dan
pengujian: `docs/DEBUG-REPOSITORY-EXAM-20261113.json`.

`job-lifecycle.cjs` membedakan receipt antrean/berjalan, gagal/dibatalkan/timeout,
dan hasil selesai. Penyelesaian membutuhkan bukti yang dikutip setelah receipt
gagal atau belum selesai terakhir; bukti lama tidak cukup. Pengiriman dengan
acknowledgement hilang harus direkonsiliasi melalui request/job yang sama sebelum
retry. Panduan membedakan pemanggilan status (`verify` dengan tool) dari menunggu
(`waiting` tanpa tool), serta tetap mengizinkan laporan checkpoint yang diminta.
Ujian native mencakup rekonsiliasi CSV, data vendor berinstruksi palsu dan job dua
turn dalam sesi terisolasi. Lihat `docs/AGENT-RESILIENCE-EXAM-20261113.json`.

Ujian level tinggi menambah impor SQLite atomic dan perubahan kontrak planner
dalam sesi yang sama. Guard kemajuan kini menghitung pemeriksaan kosong per
signature meskipun tool bergantian. Setelah hasil nonkosong lain, pemeriksaan baru
tetap dibolehkan dalam batas enam hasil kosong per signature pada tugas yang sama;
hanya hasil nonkosong dari signature itu atau tugas baru yang menghapus batas total.
Controller hanya menolak usulan, tidak mengulang atau menjalankan mutasi.
Lihat `docs/HIGH-AGENT-EXAM-20261113.json` dan `tests/agent-progress.test.cjs`.

## Jawaban login dan pekerjaan async

`interaction-workflow.cjs` memperbarui panduan operator yang dibaca ulang tiap
keputusan, tetap di bawah batas 12.000 byte. Jawaban wajib membedakan bukti login,
kapabilitas konektor privat yang benar-benar tersedia, dan izin reset. Timestamp,
MFA serta log kosong tidak membuktikan kondisi sandi atau keberhasilan login.
Setelah dua pemeriksaan async kosong, pilih pemeriksaan status job yang tersedia
dan berikan status terbatas; hindari komentar berulang serta pekerjaan duplikat.

Panduan ini aktif tanpa restart backend, dengan backup dan guard hash. Ini tidak
menambahkan konektor pembaca sandi, membuka kredensial ke chat, mereset akun,
atau membuktikan login proyek tertentu sudah berhasil. Uji:
`node --test tests/interaction-workflow.test.cjs tests/fullstack-workflow.test.cjs
tests/planner-context.test.cjs`. Bukti Q&A sintetis dan aktivasi:
`docs/CLINE-INTERACTION-FIX-20261113.json`.

## Konteks panjang Cline — perbaikan lanjutan 13 November 2026

Error baru `invalid_tool_arguments` ternyata berasal dari `tool_input_too_large`:
envelope riwayat 1.035.623 karakter ditambah instruksi melampaui batas satu pesan
CLI. Error tersebut sebelumnya disamakan dengan argumen tool yang tidak valid.

`long-decision.cjs` kini memilih transport app-server jika envelope lengkap
melewati 900.000 byte UTF-8. Riwayat JSON yang sudah melewati firewall dikirim
utuh dalam fragment maksimum 160.000 unit UTF-16, menjaga pasangan surrogate,
melalui `thread/inject_items`; satu turn kecil menghasilkan keputusan schema.
Thread wajib ephemeral, sandbox read-only, tanpa fallback model, tool host atau
approval tambahan. Tidak ada pemotongan riwayat atau replay perintah klien.
Deadline menginterupsi turn milik adapter melalui protokol, tanpa signal proses.

Jalur pendek tetap CLI `exec`. Profil kapasitas model tetap 872.000 token nominal;
kelulusan 1,36 juta **karakter** tidak membuktikan satu juta token. Batas konteks
yang benar-benar terlampaui mengembalikan HTTP 413 / `context_length_exceeded`.
Uji otomatis berjumlah 46; fixture produksi 1.365.078 karakter lulus JSON dan SSE
dengan 5/5 nilai tersebar terbaca tepat. Lihat
`docs/CLINE-LONG-CONTEXT-FIX-20261113.json` untuk bukti dan batas pengujian.

Uji helper: `node --test tests/long-decision.test.cjs`. Probe Linux terisolasi:
`node scripts/long-decision-smoke.cjs <stage>`. Tambahkan `--production` untuk
API loopback yang terautentikasi, dan `--stream` untuk SSE; hanya memakai fixture
sintetis dan tidak membaca atau mengulang sesi pengguna.

## Pemulihan format keputusan Cline

`decision-recovery.cjs` memberi satu kesempatan regenerasi untuk keputusan internal
yang belum dikirim ke executor. JSON luar/argumen rusak, respons kosong dan
pelanggaran kontrak dibedakan menggunakan kode mekanika tetap. Maksimal tiga
generasi termasuk retry koneksi; schema argumen, secret, history dan timeout tetap
menghentikan request. Tidak ada replay perintah yang sudah dijalankan klien.

Schema output kini membatasi jumlah panggilan sesuai `parallel_tool_calls` dan
`tool_choice`. Instruksi awal menyebut argumen sebagai **string objek JSON yang
di-encode**, sesuai schema transport; output boolean `False` tetap data normal.
Firewall juga memeriksa array `commands` dan descriptor file bersarang.

Siapkan kandidat dengan `scripts/patch-decision-recovery.py`. Patch menolak anchor
yang berubah dan idempotent. Uji unit: `node --test tests/decision-recovery.test.cjs
tests/planner-context.test.cjs`. Pada Linux, jalankan integrasi menggunakan
`HARMONIKA_TEST_PLANNER_SOURCE=<kandidat> node --test
tests/decision-planner-integration.test.cjs` dengan dependency AJV/backend aktual.
`scripts/decision-recovery-smoke.cjs <stage>` menguji model nyata dengan fixture
sintetis; tidak menjalankan perintah klien atau memakai sesi pengguna.

Status aktivasi dan batas diagnosis ada di
`docs/CLINE-DECISION-RECOVERY-20261113.json`. Perbaikan aktif sejak 13 November 2026
setelah restart `harmonika-ai` yang diizinkan pengguna; HTTP JSON dan SSE pada
API produksi lulus pengujian sintetis. Cline/terminal pengguna tetap berjalan.
Pembaruan berikutnya tetap memerlukan aktivasi terkontrol karena mengganti file
source saja tidak memuat ulang modul Node yang sedang berjalan.

`planner-context.js` menjaga pasangan call/result dan membatasi konteks sebelum
model memilih tool. `fullstack-workflow.cjs` memperbarui bagian akhir panduan
operator yang dibaca backend pada setiap keputusan.

Pada ujian CRM 12 November 2026, pembuatan satu file frontend besar melewati
deadline keputusan 60 detik dan berulang tanpa source baru. Panduan kini meminta
satu write/edit kecil per respons, maksimal 2.000 karakter kode. Kredensial fixture
tes memakai referensi environment; firewall secret tetap berlaku. Perubahan tidak menaikkan
deadline atau mengirim ulang mutasi yang hasilnya belum pasti.

Panduan juga meminta verifikasi UI + API + database, CRUD termasuk DELETE tanpa
body, role, persistensi, mobile dan error sebelum melaporkan selesai. Ini adalah
perbaikan workflow keputusan, bukan penambahan model atau bukti training.

Aktivasi memakai backup privat, guard SHA-256 baseline dan atomic replace pada
`/opt/harmonika-ai/api/remote-workflow.txt`; reader existing tetap membatasi
12.000 byte. Bagian panduan remote lainnya dipertahankan. Backend membacanya ulang
per request sehingga tidak diperlukan restart layanan atau terminal pengguna.

Pengujian patch: `node --test tests/fullstack-workflow.test.cjs` dari root repo.
Bukti aplikasi nyata, batas pengujian dan aktivasi:
`docs/FULLSTACK-CRM-EXAM-20261112.json`.

## Kapasitas konteks — 13 November 2026

`context-capacity.json` adalah profil operator yang dibaca setiap keputusan,
terpisah dari payload pengguna. Jalur tool planner memilih `gpt-6.1-sol` dengan
`model_context_window=872000` dan `model_auto_compact_token_limit=780000`.
Angka ini mengikuti `max_context_window` katalog runtime Codex server; default
katalognya 500.000. Dokumentasi API model menyebut 1.050.000, tetapi itu belum
membuktikan batas yang sama pada akun/jalur CLI ini. Jangan menaikkan metadata
TUI melebihi kapasitas jalur yang tersedia atau mengubah model cadangan/media/CS.

Profil memperbesar target transport menjadi 2.800.000 **karakter**, dengan
admission 8 MiB UTF-8 dan 4.096 pesan. Karakter, byte, dan token berbeda; target
transport bukan tokenizer. Runtime/provider tetap menentukan apakah prompt dan
output muat. Saat muat, konteks tidak dipangkas. Ketika perlu dipangkas, pasangan
tool dan bukti kritis tetap dilindungi, serta firewall tetap aktif. Kesalahan
ukuran tetap eksplisit; jangan mengirim ulang mutasi atau mengganti model diam-diam.

TUI native memakai `providers.harmonika.models.*.limit.context` setelah migrasi
config v1. Jalur primary/Pro diselaraskan ke 872.000; Google Mode dipertahankan.
Plugin transport memakai profil lokal dengan fallback konservatif jika tidak
ada. Pengaturan output tetap 4.096; memperbesar konteks tidak mewajibkan jawaban
panjang atau mengisi seluruh jendela pada setiap request. Retensi log audit juga
terpisah dari konteks inference.

Aktivasi backend memerlukan backup privat, guard hash sumber, keadaan idle dan
restart layanan Node yang diizinkan operator. Konfigurasi TUI dapat dimuat ulang
melalui `harmonika reload` tanpa menghentikan sesi. Uji dari root repo:
`node --test tests/context-capacity.test.cjs tests/planner-context.test.cjs tests/visual-context.test.cjs`.
Laporan aktivasi: `docs/CONTEXT-CAPACITY-20261113.json`.

Pengenalan source header dinamis pada secret firewall diuji terpisah dari kapasitas
konteks: referensi variabel pada prefix/template Bearer dipertahankan, sedangkan
nilai credential literal serta akses berkas rahasia tetap ditolak. Regresi:
`tests/auth-reference-firewall.test.cjs`; bukti aktivasi dan ujian UI/SQLite:
[FULLFLOW-AGENT-EXAM-20261113.json](../../docs/FULLFLOW-AGENT-EXAM-20261113.json).

## Hasil ujian hingga 1M

Ujian lanjutan menggunakan fixture sintetis dengan lima nilai tersebar dan
tokenizer `tiktoken==0.12.0`, encoding `o200k_base`. Hitungan lokal berbeda dari
pemakaian token aktual yang dilaporkan provider.

- Domain produksi menerima fixture 500.000 token lokal dan menjawab 5/5 tepat.
  Respons publik tidak menyediakan usage provider untuk kasus ini.
- Probe terisolasi memakai protokol app-server yang terdokumentasi, thread
  ephemeral, dan `thread/inject_items`: provider menerima **807.776 input token**
  pada fixture 800K lokal, menjawab 5/5 tepat, tanpa compaction.
- Fixture 1.000.000 token lokal ditolak dengan `contextWindowExceeded`.
  Runtime melaporkan `modelContextWindow=828400`, yaitu 95% dari maksimum nominal
  872.000. Nilai `totalTokens=828400` pada penolakan, dengan `inputTokens=0`,
  **bukan** bukti pemrosesan 828K token.
- CLI `exec` juga memiliki batas satu pesan 1.048.576 **karakter**. Fixture 1M
  berukuran 1.945.309 karakter, sehingga ditolak sebelum turn model dimulai.
  Memecah array input tidak melewati validasi total satu pesan. Jalur riwayat
  app-server berhasil memasukkan data, lalu tetap menghadapi batas konteks.
- Domain produksi mengembalikan HTTP 413 / `invalid_tool_arguments` untuk fixture
  1M. Kode kompatibilitas ini kurang membedakan ukuran konteks dari argumen tool.

Pada ujian 1M tersebut, probe app-server masih terpisah dari adapter produksi
CLI `exec`. Hasil 807.776 token tidak membuktikan payload itu diterima
oleh jalur TUI produksi. Tidak ada perubahan profil produksi, restart, pembacaan
sesi pengguna lain, maupun pemindahan provider pada ujian ini.

Runner `scripts/context-load-exam.py fixture <dir> --tokens 1000000` hanya membuat
fixture. `run` memanggil CLI Linux pada direktori itu. Runner
`context-appserver-exam.py <dir>` memakai thread ephemeral; runner
`context-public-exam.py <dir>` mengirim satu request domain dengan credential
owner yang dibaca privat dari file. Tidak ada raw credential di argv/output.
Jangan menganggap status request, hitungan karakter, atau token total kumulatif
sebagai bukti satu request 1M diterima. Laporan lengkap:
`docs/CONTEXT-1M-EXAM-20261113.json`.

## Recovery dan rolling upgrade — Level 10

Panduan `debug-workflow.cjs` yang aktif membedakan admission intent, penolakan
kuota otoritatif, accepted dan hasil belum pasti. Retry mempertahankan identitas
asli. Audit harus menghasilkan event mekanika nyata dalam WAL; error audit tambahan
tidak boleh mengulang mutasi committed. Bearer dengan encoding tidak valid ditolak
secara terstruktur tanpa nilai credential atau raw exception.

Backup memakai SQLite online backup, manifest dari artefak tertutup, fsync dan
publish tanpa overwrite. Uji mencakup pembukaan store baru di jalur RPC ketika
writer aktif, serta clone dari backup DELETE yang perlu konversi WAL. Restore
hanya ke clone nonaktif; watermark control dan identitas lama diperiksa sebelum
rekonsiliasi. Queue/sink dan efek eksternal tidak di-rollback.

Kelulusan lab: 56 kasus pada Mac/Linux, 34 pemeriksaan integrasi dan 14 guard alat
uji. Panduan backend lolos 148 tes lokal, 76 Linux, 7 guard aktivasi dan 13 canary
keputusan nyata tanpa eksekusi tool klien. Sesi native menggunakan coaching Codex;
OpenCode mereview kontrak/penilai secara read-only. Bukan training model, universal
agent score, HA antarhost atau API recovery publik. Bukti awal gagal, koreksi alat
uji, aktivasi dan cleanup tercatat di
[laporan Level 10](../../docs/LEVEL10-AGENT-EXAM-20261114.json).

## Isolasi proyek dan UI async — Level 11

Panduan aktif mengikat identitas ke owner dan proyek, mempertahankan job saat
daftar tiket direfresh, serta membedakan generation scope dari sequence daftar.
Login/logout perlu antrean transport: menolak callback lama saja tidak mencegah
respons Set-Cookie logout menghapus login yang baru. Tes async harus mencapai
assertion akhirnya; sentinel lama dan Promise menggantung bukan bukti lulus.

Setelah penolakan fragmen credential, baca modul lengkap dan tulis source valid
yang mempertahankan perubahan; firewall tidak dilonggarkan. Batch paralel tidak
menjamin urutan admission. Periksa state antrean aktual, pertahankan identitas
saat ACK hilang, serta verifikasi CSV Decimal, byte/hash dan completion manifest.

Hasil lab: dua sesi native berbeda, 54 kasus engine pada Mac/Linux, 37 pemeriksaan
integrasi, 17 browser, dua regresi UI dan 15 guard harness. Kontrol aktivasi punya
tiga simulasi terisolasi termasuk rollback parsial; tidak ada fault produksi yang
disuntikkan. Panduan aktif setelah 148 tes lokal, 76 Linux, 7 guard aktivasi dan
13 canary inference tanpa menjalankan tool klien. Prototype aplikasi, reference
terminal, koreksi penilai dan coaching dibedakan dari kemampuan backend publik
dalam [laporan Level 11](../../docs/LEVEL11-AGENT-EXAM-20261115.json).

## Fullstack Linux dan recovery — Level12

Native membangun domain CRM/CMS, business HTTP dispatch dan UI dari referensi
berlisensi yang dipin. Autentikasi dan HTTP runtime disediakan sebagai fixture
terlindungi; Codex menilai secara independen dan OpenCode mereview. Rilis V2 pada
host ujian lulus 16 domain/API dan 29 pemeriksaan browser, termasuk create/edit/filter
General–VIP, CSV, publikasi, isolasi, serta edit→hapus→create POST201.

Migrasi, lock nyata, port fallback dengan HTTPS saat holder aktif, bad-config,
backup online bersama writer, clone nonaktif serta code rollback schema2 diuji
melalui operator bertarget tetap. Snapshot 9 pelanggan dan live 10 membuktikan
clone tidak menimpa live pada saat drill; pemeriksaan sesudahnya dapat menambah
data sintetis. Current release/source, PID/listener dan data diperiksa terpisah.

Gateway menjaga enum error policy tanpa raw upstream errors. Pengecualian
UI-MAP terbatas pada metadata selector valid. Output-limit hanya diregenerasi
setelah transport close dan keputusan belum dieksekusi; batas schema tetap
berlaku ketika format repair berikutnya terjadi. Deadline bersama tetap 240s,
tanpa signal proses atau replay mutasi. Setelah restart, await HTTPS memakai
retry GET terbatas, jangan menyamakan status proses dengan kesiapan TLS.

Aktivasi memakai hash baseline aktual, backup dan idle guard. 161 tes lokal,
90 integrasi Linux, 7 guard dan 5 canary sumber lulus. Batasnya tetap lab dipercaya,
coaching dan fixture; tidak membuktikan training bobot model, physical IME,
failover antarhost atau produksi ERP. BGP/core/VyOS JKT tidak menjadi target.
[Bukti, kegagalan awal dan koreksi](../../docs/LEVEL12-AGENT-EXAM-20261115.json).
