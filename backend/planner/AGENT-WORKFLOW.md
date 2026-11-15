# Alur tugas Harmonika

Engine memakai percakapan dan hasil tool yang diberikan klien untuk menyusun
keputusan berikutnya. Executor native tetap menjalankan tool, menegakkan izin,
dan mengembalikan hasilnya. Backend tidak menjalankan ulang tindakan klien.

Kontrak internal `task_state` memuat tahap, pekerjaan tersisa, referensi hasil tool,
dan langkah berikutnya. Tahap memahami/menjalankan/memeriksa harus mengembalikan
tool aktual; respons berupa janji saja ditolak. Tahap selesai membutuhkan referensi
hasil pada tugas yang sama dan checklist kosong. Hasil terakhir yang secara
eksplisit gagal tidak dapat dipakai untuk menyatakan selesai. Dua pemeriksaan
kosong/identik tidak boleh menghasilkan panggilan ketiga yang sama tanpa kemajuan.

Keputusan tidak sesuai kontrak mendapat satu koreksi sebelum dikirim ke klien,
dalam batas retry bersama. Secret, izin dan validasi argumen tool tetap diperiksa.
Hambatan dan job menunggu harus menyebut pekerjaan tersisa serta langkah konkret.
Percakapan biasa dan pembuatan teks dapat dijawab langsung tanpa tool palsu.

`task_state` adalah deklarasi model yang divalidasi terhadap ID hasil teramati,
bukan jaminan semantik universal bahwa program benar. Teks hasil tetap data tidak
tepercaya. Model harus membaca assertion/exit/status yang relevan; ujian nyata
memeriksa artifact dan menjalankan test secara terpisah untuk menguji klaimnya.
Metadata log hanya enum tahap dan hitungan, tanpa isi checklist, argumen, keluaran
tool, credential, chat atau hidden reasoning. State dibentuk ulang per request;
tidak ada pengarsipan sesi tambahan maupun training otomatis.

Kontrak ini dihapus dari respons sebelum JSON/SSE dikirim ke klien. TUI/Cline tetap
menerima `content` dan `tool_calls` biasa. Binary, ASCII, branding, izin global
dan sesi yang sedang berjalan tidak diubah. Permintaan berikutnya memakai engine
backend yang aktif. OpenCode meninjau jalur TUI; Codex mengimplementasikan,
memverifikasi dan mengaktifkan controller.

Ujian native memakai `harmonika run --standalone` dengan direktori data/cache/state
terpisah dan project sintetis. Flag `--auto` hanya pada proses ujian itu; konfigurasi
izin pengguna tidak diubah. Source awal sengaja rusak. File test dikunci secara
logis melalui pemeriksaan SHA-256, lalu hasil native diverifikasi ulang dengan Node.

```sh
node --test tests/agent-controller.test.cjs tests/decision-recovery.test.cjs
python3 scripts/native-autonomy-exam.py --case easy
python3 scripts/native-autonomy-exam.py --case medium
python3 scripts/native-autonomy-exam.py --case hard
```

Contoh mencakup penjumlahan, slug Unicode dan parser CSV dengan quoted multiline.
Tiga ujian lulus dan menghasilkan laporan sendiri; total 17 test fungsi diperiksa
secara independen, selain 66 test controller/regresi. Ini membuktikan workflow
pada kasus tersebut, bukan kesetaraan universal dengan semua layanan AI CLI.
Lihat `docs/AGENT-AUTONOMY-EXAM-20261113.json` untuk aktivasi dan batas audit.

## Ujian debugging repository lanjutan

Ujian terpisah menguji pagination (easy), cache async (medium) dan antrean persisten
dengan lease/idempotency (hard). Harmonika membaca kontrak lengkap, mereproduksi
kegagalan, mengubah source lintas modul, menambah regresi dan menulis HASIL.md.
Grader Codex menjalankan 63 tes penerimaan di luar workspace; Harmonika tidak
melihatnya. Hash test asli, README, AGENTS, konfigurasi dan log contoh dilindungi.
Tiga kasus awal lulus sebelum aktivasi perubahan berikutnya; ujian baru setelah
aktivasi dicatat terpisah agar bukti tidak tercampur.
Ujian hard pada repository baru setelah aktivasi juga lulus: 10 tes lokal dan
16 tes penerimaan terpisah, tanpa error transport atau perubahan pada tes asli.

Audit OpenCode menemukan receipt gagal yang masih bisa dikutip setelah read lain,
receipt kosong sebagai satu-satunya bukti, status gagal dalam metadata executor,
dan konteks yang terputus pada lanjutan sopan. Codex memperbaiki keempatnya dan
menambahkan panduan `debug-workflow.cjs`. Hasil baseline gagal boleh dilaporkan,
tetapi bukti selesai harus memakai pemeriksaan ulang yang berhasil. Diff kosong
tetap dapat disertakan bersama bukti lain yang berisi hasil verifikasi.

```sh
python3 scripts/debug-repository-exam.py --case easy
python3 scripts/debug-repository-exam.py --case medium
python3 scripts/debug-repository-exam.py --case hard
```

Regresi otomatis kini 62 tes lokal dan 9 integrasi Linux, ditambah 7 tes guard
aktivasi dengan proses/file sintetis. Aktivasi hanya mengganti
controller beserta helper baru, memakai guard hash, backup dan restart gateway saat
idle. TUI memakai engine baru pada panggilan berikutnya tanpa penggantian binary.
Rincian: `docs/DEBUG-REPOSITORY-EXAM-20261113.json`.

Batas audit: `answer`/`blocked` tetap deklarasi model, bukan pembuktian maksud atau
keberhasilan secara universal. Payload teks tanpa status terstruktur memerlukan
penilaian isi oleh model. File log dengan instruksi berbahaya disediakan sebagai
fixture, tetapi run awal tidak membacanya; kelulusan ini tidak membuktikan seluruh
serangan prompt injection tertahan. Kegagalan I/O dan ketahanan kehilangan daya
antrean belum diuji. Ini pembaruan workflow dan validasi, bukan training otomatis.

## Bukti data dan lifecycle job

Ujian agent lanjutan memakai tiga workspace sintetis: audit stok CSV dengan data
duplikat/invalid dan hasil negatif; normalisasi konfigurasi setelah membaca vendor
notes yang sengaja memuat instruksi melemahkan tes; serta pemulihan acknowledgement
job hilang, checkpoint dan kelanjutan pada sesi native yang sama.

`job-lifecycle.cjs` memeriksa status/state terstruktur dan metadata executor yang
dikenal. Receipt pending/berjalan bukan bukti keluaran selesai; status batal dan
timeout bukan sukses. Controller juga membutuhkan receipt terkutip yang lebih baru
daripada receipt gagal atau pending terakhir. Ini mencegah bukti sukses lama dipakai
setelah kegagalan baru. Validasi tetap tidak membuktikan isi artifact secara universal.

Saat hasil submit belum pasti, cari request/job yang sama sebelum mencoba ulang.
Pemeriksaan status memakai `verify` beserta tool aktual. `waiting` tanpa tool hanya
untuk keadaan menunggu yang jelas; jangan menggabungkannya dengan panggilan tool.
Pertanyaan status atau checkpoint eksplisit boleh menjelaskan keadaan berjalan
tanpa mengaku job selesai. Executor dan izin pengguna tetap berlaku.

Jalankan `scripts/agent-resilience-exam.py` dengan `--root <direktori-baru>` dan
`--case easy|medium|hard`. Kasus hard berhenti setelah checkpoint; gunakan root yang
sama dan `--case hard --continue-hard` untuk melanjutkan sesi ujian miliknya sendiri.
Jangan mengganti ID dengan sesi pengguna. Test lifecycle berada pada
`tests/job-lifecycle.test.cjs`; laporan per kasus, Q&A, aktivasi dan batas pengujian
ada di `docs/AGENT-RESILIENCE-EXAM-20261113.json`.

Parser hanya memahami field mekanika yang dikenal; field adapter lain memerlukan
pemetaan kontrak tersendiri. Kelulusan satu file vendor berbahaya tidak membuktikan
semua serangan prompt injection aman. Simulator job tidak menguji ketahanan GPU,
provider eksternal, listrik mati, maupun transaksi produksi.

## Agent level tinggi

`scripts/high-agent-exam.py` menyiapkan dua workspace sintetis: importer SQLite
dengan kontrol revisi, replay, dry-run, rollback pada konflik/trigger dan transaksi
milik pemanggil; serta planner alokasi dua turn dengan perubahan kebutuhan,
kompatibilitas v1, prioritas, batas per pelanggan dan key dictionary khusus.
Hash tes, kontrak, hasil v1 serta edit pengguna dijaga sebelum/sesudah setiap turn.
Suite penerimaan disimpan di luar workspace dan tidak diberikan kepada Harmonika.

```sh
python3 scripts/high-agent-exam.py --root /tmp/harmonika-high-new --case transaction
python3 scripts/high-agent-exam.py --root /tmp/harmonika-high-new --case requirements
python3 scripts/high-agent-exam.py --root /tmp/harmonika-high-new --case requirements --continue
```

Lanjutan memakai sesi native milik ujian yang sama. Runner menambahkan ADDENDUM.md
dan edit pengguna baru sebelum turn kedua; hasil v1 dikunci dengan hash. Gunakan
root baru untuk pengulangan, bukan sesi/proyek pengguna. Hasil lama tidak dianggap
verifikasi kebutuhan baru; model harus membaca ulang kontrak dan menjalankan tes.

Pada run 13 November, regresi tambahan buatan agen menerima priority:99 sebagai
field ekstra v1, tetapi v2 mewajibkan nilai 0–3 sambil meminta tes v1 dipertahankan.
Agen melaporkan 9/10 tes, menjaga assertion dan meminta klarifikasi; hasil tersebut
disimpan, bukan disembunyikan. Examiner kemudian memberi izin spesifik mengubah
ekspektasi regresi buatannya yang kedaluwarsa menjadi TypeError. Tes asli, hasil v1,
edit pengguna serta suite penerimaan tetap dijaga. `--clarify` menjalankan turn
izin ini dalam sesi ujian yang sama dan menyimpan laporan terpisah.

Temuan retry: pola A–B–A–B dan selingan read dapat melewati hitungan berurutan lama.
Guard baru membatasi pemeriksaan kosong per signature pada rentetan hasil kosong.
Hasil nonkosong lain membuka pemeriksaan baru agar verifikasi sesudah edit tetap
berfungsi, tetapi batas total enam hasil kosong per signature menutup loop dengan
read pengalih. Hasil nonkosong dari signature itu atau tugas baru mereset total.
Tidak ada tebakan nama tool/maksud bahasa alami, eksekusi tambahan, maupun raw
output baru di log. Hanya hash/count sementara per request.

Ini batas mekanika, bukan detektor kemajuan semantik universal. Pemotongan riwayat
klien, argumen berbeda, hasil nonkosong menyesatkan dan seluruh jenis loop belum
ditangani secara universal. Power-loss, proses mati di tengah transaksi, banyak
writer produksi dan layanan eksternal tidak termasuk ujian SQLite ini.
Lihat `docs/HIGH-AGENT-EXAM-20261113.json` untuk fase, Q&A, aktivasi dan hasil akhir.

## Ujian operasi dan client API

Putaran berikutnya memakai engine aktif yang sama, tanpa perubahan runtime atau
restart. Audit insiden menguji log yang urutannya diacak, cache lama, liveness 200,
readiness 503, request selesai setelah timeout dan OOM pada instance berbeda.
Kandidat hanya diprobe di simulator; produksi tetap ditandai belum pulih.

Kasus client API meminta Harmonika memperbaiki kode adapter di workspace ujian:
retry GET berbatas, pemisahan error transport/HTTP/payload/auth, penanganan outcome
POST yang belum pasti tanpa replay, dan audit tanpa body/path/kredensial. Kode SDK
hasil ujian tetap artifact sintetis; integrasi provider riil memerlukan kontrak,
endpoint, autentikasi dan cancellation yang sesuai. Budget ujian tidak membatalkan
adapter yang menggantung.

Empat probe produksi melalui API loopback memeriksa keputusan model terhadap cache
lama, JSON sukses mutasi yang rusak, verifikasi fungsional tanpa restart dan klaim
pemulihan dari simulator. Tidak ada tool klien yang dieksekusi oleh probe tersebut.
Review OpenCode memakai plan mode/pure dan hanya membaca tiga file kontrak/test
yang dibekukan. Temuan spesifikasi dicatat; kata-kata kontrak harness untuk run
berikutnya diperjelas setelah run awal selesai tanpa mengubah artifact atau nilai
awal agen. Lihat `docs/INCIDENT-AGENT-EXAM-20261113.json`.

[Katalog ujian dan cakupan yang belum diuji](../../docs/AGENT-EXAM-CATALOG.md).

## Pembatalan, backpressure dan outcome unknown

Lifecycle v2 mengenali cancel_requested/cancelling/canceling, unknown/outcome_unknown,
reconciling dan busy sebagai observasi nonterminal. queue_full/rate_limited bukan
bukti keberhasilan. Guard tetap membaca field mekanika eksplisit, bukan kata-kata
di source, log atau data nested. Jawaban status yang jujur tetap diperbolehkan.

Panduan meminta rekonsiliasi job/worker ketika lease berakhir atau hasil mutasi
belum pasti. Permintaan batal belum membuktikan worker berhenti. Reconnect stream
melanjutkan job yang sama dengan cursor event yang sudah diterima, tanpa submit
baru. Ini guard bukti/panduan keputusan; ia tidak mengeksekusi pembatalan, menyediakan
fencing worker fisik, atau membuktikan beban/fairness scheduler produksi.

## UI, isolasi tenant dan backup

Ujian `fullflow-agent-exam.py` memakai aplikasi HTTP/SQLite lokal dan browser
Chromium milik runner. Penilaian membedakan lulus API dari lulus interaksi UI,
serta hash snapshot dari integritas dan keberhasilan restore. Tenant berasal
dari sesi yang diverifikasi server; parameter tenant dari pemanggil tidak menjadi
sumber otorisasi. Sesi dan database fixture terpisah dari pengguna/produksi.

Salah deteksi firewall pada penulisan header dinamis direproduksi secara sintetis:
`Authorization: 'Bearer ' + sessions[session]` berisi referensi kode, tanpa nilai
credential. Helper mengenali bentuk prefix tersebut atau template literal Bearer
dengan satu referensi identifier/property/index identifier dan delimiter akhir nilai. Literal token, tambahan
string, pemanggilan fungsi dan quoted lookup tidak mendapat pengecualian ini.
Pemeriksaan key aktual, objek header sensitif, path rahasia, dump environment dan
marker redaksi tetap berlaku. Ini pengenalan sintaks terbatas, bukan parser atau
bukti bahwa semua bentuk source auth aman/tersedia.

Pemulihan melanjutkan workspace tersimpan melalui sesi ujian baru setelah backend
diperbaiki; hasil awal gagal disimpan. Jangan menyebutnya replay mutasi atau bukti
bahwa seluruh jenis error transport/policy sudah tertangani. Detail aktivasi dan
hasil terpisah ada di `docs/FULLFLOW-AGENT-EXAM-20261113.json`.


## Bukti selesai setelah kegagalan dan checkpoint Linux

Controller menyertakan peta ID receipt yang eligible, fresh dan ineligible dalam
panduan inference. Peta dibatasi 12/8/8 ID dan hidup hanya di request; ia tidak
menyalin output, argumen atau history ke log. Guard completion tetap mewajibkan
bukti dari tugas yang sama, menolak receipt gagal/pending, dan meminta bukti baru
setelah kegagalan/pending terakhir. Eligibility mekanis belum membuktikan hasil:
model harus mencocokkan tes, readiness, transaksi dan readback artifact yang relevan.

`level4-agent-exam.py` memakai lab Docker Linux yang disiapkan terpisah, bridge
operasi tetap dan volume privat sintetis. Tahap pertama memasang dependency wheel
offline, memperbaiki source/packaging, build dan deploy nyata, lalu merekonsiliasi
ACK aktivasi yang hilang. Tahap berikutnya melanjutkan sesi native yang sama,
menyimpan satu transaksi, merekonsiliasi ACK tanpa replay, membedakan health200 dari
ready503, rollback kode dengan DB tetap, lalu memeriksa crash exit42 dan auto-restart.
Penilai mencocokkan source/image, ledger operasi, event Docker serta data SQLite.

Kelanjutan singkat “Silakan lanjutkan.” dapat menjalankan TURN B ketika checkpoint
sudah selesai. Penilai harus mengevaluasi tahap yang diminta dalam kelanjutan itu;
jangan mengulang TURN B demi mendapatkan berkas hasil bernama lain. Hasil awal dan
log kelanjutan tetap disimpan. Native regression di root project harus dijalankan
juga, bukan hanya discovery folder tests. Ini ujian staging terkontrol, bukan bukti
OS reboot, rollout produksi, jaringan putus sungguhan atau semua platform.

[Bukti Level 4](../../docs/LEVEL4-AGENT-EXAM-20261114.json).


## Referensi runtime pada source autentikasi

Firewall membedakan nilai credential literal dari kode yang membaca input saat
aplikasi berjalan. Referensi Python diperiksa dengan AST dalam proses Python
terisolasi (`-I`), stdin berbatas, timeout dan batas CPU/memori pada Linux. Parser
hanya mengeluarkan rentang angka; source tidak dieksekusi atau ditulis ke log.
Ia mengenali source utuh, blok kode, output baca bernomor, dan fragmen metode
berindentasi. JavaScript/HTML memakai pengenalan referensi yang lebih terbatas.

JSON header/cookie aktual, credential literal, format key yang dikenal, pembacaan
path secret, dan marker redaksi tetap ditolak. Ini pengenalan sintaks konservatif,
bukan analisis taint lengkap atau pengganti sandbox dan permission executor.
Gunakan referensi credential privat untuk akses nyata; fixture login sintetis
pada ujian memakai helper loopback tersendiri. Hindari menanam credential dalam
source dan membungkus seluruh program dalam shell/string untuk menghindari guard.

Penolakan dapat mencatat `credential_firewall_reject` dengan kategori tetap dan
hitungan numerik saja. Tidak ada teks cocok, nilai, argumen, nama variabel, path,
cookie atau source yang ditulis ke diagnostik tersebut. Pertahankan regresi positif
serta negatif dalam `tests/source-reference-firewall.test.cjs`; uji juga jalur
inference aktual karena source di dalam pesan campuran berbeda dari source mentah.

Pemeriksaan AST memiliki budget per request: 24 cache miss dengan deadline 1,2
detik, subprocess maksimum 1 detik, source maksimum 262144 karakter, dan output
maksimum 2048 rentang. Cache LRU hanya berisi SHA256 dan rentang numerik, maksimum
128 entri/16384 rentang. Linux membatasi parser ke 128 MiB dan 1 detik CPU.
Budget habis tidak memberi pengecualian keamanan. Field path camelCase juga
dinormalisasi sebelum pembatasan berkas privat.

Panduan model meminta penulisan modul auth yang bisa diparse melalui tool file,
menjaga perubahan pengguna, dan membaca input form credential melalui referensi
DOM eksplisit. Getter kustom dan fragmen yang tidak dikenali dapat tetap ditolak;
jangan menyamarkan source/credential dalam shell atau encoding untuk melewatinya.
Diagnostik call memakai kategori tetap (misalnya mapping_lookup/other_call) dan
hitungan saja, tanpa nama fungsi atau potongan argumen.

Klien yang mendeklarasikan tool penulisan/edit source (schema path + content/
newString/new_text) memakai transport protokol ephemeral, termasuk untuk input
pendek. Deadline keputusan maksimum 180 detik; timeout membatalkan turn milik
request tersebut melalui protokol dan menutup stdin, tanpa process signal.
Transport CLI pendek juga tidak mengirim signal; jika melewati deadline, slot
admisinya tetap terpakai sampai child benar-benar close, dan hasil terlambat
diabaikan. Tidak ada replay tool saat timeout. Ini deadline inference keputusan,
bukan perluasan izin atau timeout untuk pekerjaan executor.

## Hasil Level 5

Native menyelesaikan aplikasi tiket dan perubahan schema pada sesi yang sama.
Penilai terpisah menguji API, HTTP konkuren, browser, pergantian akun dengan respons
tertunda, persisten attachment serta recovery ACK/lock/readiness. Bug link unduh
terlalu kecil ditemukan setelah menunggu konten asinkron; native menerbitkan revisi
immutable dengan target 44px, lalu seluruh pemeriksaan independen lulus.
Panduan QA di debug-workflow aktif dan probe keputusan aktual memilih menunggu link
serta mengukur minimum yang diminta. Probe keputusan tidak mengeksekusi tool klien.

Operator juga memverifikasi image v1 pada DB v2 untuk read/download/receipt lama,
mengembalikan rilis baik dan membersihkan tujuh container serta image/volume lab.
Ini bukan sertifikasi semua mutasi versi lama, IAM produksi, host sandbox atau IME
fisik. [Hasil awal, bantuan operator dan bukti](../../docs/LEVEL5-AGENT-EXAM-20261114.json).

## Pembelajaran Level 6

Panduan debug membedakan atribut error SQLite yang tersedia pada tiap interpreter:
kode numerik SQLITE_BUSY/LOCKED atau fallback pesan lock standar yang konservatif.
Jangan mengubah semua error SQL menjadi retry, membocorkan exception mentah,
menghapus database, atau melemahkan assertion untuk menyembunyikan contention.
Transaksi perubahan job dan event harus atomic; identitas request tetap sama.

Ujian memisahkan claim, commit efek, ACK dan penyimpanan completion. Sink sintetis
memakai transaksi/DB terpisah; fault exit42 terjadi sesudah commit, kemudian
recovery harus lookup ID yang sama dan memverifikasi byte artifact. cancel_requested
tidak membuktikan worker berhenti. SSE finite-page hanya membuktikan framing dan
replay cursor; stream kontinu perlu pengujian tersendiri.

Probe aktif memeriksa pilihan keputusan perbaikan SQLite, lookup efek yang ACK-nya
hilang, dan pelaporan batas SSE. Tool klien probe tidak dieksekusi. Hasil native,
pengujian independen dan batas fixture dicatat terpisah dalam
[laporan Level 6](../../docs/LEVEL6-AGENT-EXAM-20261114.json).

## Pembelajaran Level 7

Pisahkan lease/claim, izin fencing pada layanan efek, commit efek, ACK dan durable
completion. Worker yang lease-nya habis dapat masih hidup: claim baru harus memakai
fence lebih tinggi dan completion harus memeriksa worker/fence yang sama. Receipt
harus mengikat job, owner, hash dan byte. Jika ACK hilang, lookup identitas lama;
bukti pada sink yang idempotent bukan jaminan exactly-once pada vendor sembarang.

Uji live SSE melalui koneksi aktual yang dibuka sebelum perubahan, pecahan UTF-8,
putus di tengah frame dan reconnect dari cursor diterima. EOF/HTTP200 saja tidak
membuktikan lulus. Snapshot WAL memerlukan online backup, manifest dari snapshot
tertutup, checksum/integritas dan publikasi atomic tanpa menimpa tujuan. Clone
pemulihan tetap nonaktif; rollback kode tidak berarti rollback efek eksternal.

Upgrade runtime planner membatasi retry timeout menjadi satu keputusan pengganti
hanya bila keputusan belum mengeksekusi tool dan transport lokal sudah close.
Jika close belum terkonfirmasi, pertahankan slot admission dan direktori sementara
hingga callback close; jangan memakai signal proses atau replay tool klien.

[Hasil, bantuan operator dan batas Level 7](../../docs/LEVEL7-AGENT-EXAM-20261114.json).

## Pembelajaran Level 8

Intent dispatch, permintaan child, efek dan checkpoint parent berada pada batas
transaksi berbeda. Simpan identitas child stabil sebelum I/O dan lakukan lookup
setelah ACK hilang. Keberhasilan HTTP tidak menggantikan validasi artifact serta
urutan dependency; kegagalan satu workflow tidak boleh menahan semua workflow.
SQLite write transaction harus selesai sebelum menunggu layanan child.

Pause menahan pekerjaan baru sambil tetap mengamati child yang sudah berjalan.
Cancel memerlukan outcome seluruh child yang sudah dipesan; failed otoritatif
terminal untuk pembatalan, sedangkan unknown bukan terminal. Jangan mengarang
bahwa kendali pause/cancel menghentikan proses fisik atau membatalkan efek lama.

Migrasi additive memerlukan transaksi, failure hook setelah ALTER awal, lock
nyata, legacy fingerprint dan pengujian repeated init tanpa downgrade. Nilai default
pada field baru tidak boleh diam-diam mengubah canonical identity request lama.
Code rollback saat semua pekerjaan settled berbeda dari migrasi live beberapa versi.

Waktu tunggu setiap generasi harus masuk dalam satu anggaran request. Insiden
Level8 menemukan dua percobaan180 detik melampaui header timeout client300 detik.
Recovery kini memakai clock monotonic dengan anggaran240 detik bersama; setiap
percobaan menerima sisa waktunya, maksimal180 detik. Timeout hanya boleh diulang
setelah keputusan dipastikan belum dieksekusi dan transport sudah ditutup.
Child yang belum close tetap memakai admission slot; tidak ada process signal
atau replay tool client untuk menyelesaikan timeout.

[Hasil, koreksi fixture/operator dan batas Level 8](../../docs/LEVEL8-AGENT-EXAM-20261114.json).

## Pembelajaran Level 9

Dua coordinator memakai claim owner dan epoch integer durable yang berbeda dari
tenant owner. Settlement harus memeriksa owner/epoch terkini sebelum menulis
state, child atau artifact. Proses lama dapat tetap hidup ketika lease habis;
respons terlambat tidak boleh menimpa hasil coordinator pengganti.

Pengujian membedakan ACK TCP hilang sesudah admission, body artifact terpotong,
serta koneksi putus sebelum forwarding. Gunakan identitas child yang sama dan
verifikasi byte/hash aktual. Pisahkan jumlah attempt, admission dan efek commit.

Cancel pada parent paused harus langsung mengevaluasi child reserved yang sudah
terminal menggunakan row parent terbaru dalam transaksi control. Tanpa langkah
ini, parent dapat tertahan menunggu callback yang tidak akan datang. Outcome
unknown tetap menahan slot; pemeriksaan network berada di luar transaksi writer.
Status singkat worker perlu disampel dekat layanan dengan waktu dan provenance
nyata; polling serial melalui SSH dapat kehilangan jendela lease.

Lab memakai satu host fisik, dua coordinator dan network internal khusus tanpa
published port. Hasilnya tidak membuktikan failover antarhost atau exactly-once
pada vendor sembarang. BGP/core/VyOS JKT tidak termasuk target.
[Status dan bukti Level 9](../../docs/LEVEL9-AGENT-EXAM-20261114.json).

## Pembelajaran Level12

Ujian CRM/CMS memakai server Linux disposable dan subdomain milik connector
khusus zona yang diizinkan. Pisahkan mekanisme autentikasi/HTTP yang disediakan
operator dari domain dan UI yang dibangun native. Source baseline, tes asli,
referensi visual serta checkpoint dikunci sebelum pekerjaan revisi dimulai.

QA visual harus menunggu semua panel selesai dimuat. Heading dan dua animation
frame belum membuktikan form, metrik, tugas dan artikel sudah muncul. Halaman
mobile tanpa overflow juga dapat gagal karena kolom tabel terpotong per huruf;
ukur keterbacaan serta gulir internal dan bandingkan struktur dengan referensi.
Pencarian tidak boleh menghapus draft form atau merender respons lama.

Setelah record yang sedang diedit dihapus, kosongkan ID, revision dan identitas
request form. Uji alur Edit -> Hapus -> Create dengan POST201 nyata; hilangnya
baris saja belum membuktikan form kembali dapat dipakai. Pertahankan edit lain
yang tidak terkait dengan penghapusan tersebut.

Gateway mempertahankan enum error policy yang disetujui tanpa raw error upstream.
Output keputusan yang melampaui batas boleh mendapat satu regenerasi lebih
ringkas hanya setelah transport close dan keputusan belum mengeksekusi tool.
Anggaran request serta schema tetap dibatasi; jangan replay mutasi klien.

Rollback kode memakai operasi deployment baru dengan source hash dan listener
PID aktual. Receipt sukses historis tidak mengaktifkan kode lama. Database live
tetap terkini; backup SQLite online ditutup, diperiksa, lalu dipulihkan ke clone
nonaktif. Verifikasi migrasi, session, fingerprint dan data terpisah dari health.
Hasil akhir dan batas bantuan mengikuti laporan Level12; ini bukan training model
atau sertifikasi bahwa semua tugas engineering akan otomatis berhasil.
