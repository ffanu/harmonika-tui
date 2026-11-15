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


### Level13: hasil terikat pemeriksaan operator

`agent-controller.cjs` tetap mengembalikan content/tool_calls standar kepada TUI
atau adapter web; task_state dan ledger tidak menjadi kartu JSON di chat.
`proof-contract.cjs` menyediakan jalur action terdaftar dengan HMAC-SHA256.
Backend membaca `/etc/harmonika-ai/evidence-issuer.key` (hex32byte,0600) serta
`/var/lib/harmonika-ai/proof-contracts/<id32hex>.json` (direktori0700,berkas0600).
Hanya operator/verifier tepercaya yang menerbitkan kontrak. Tidak ada endpoint
publik untuk menandatangani data yang dikirim model/klien.

Envelope berisi string `payload` dan `mac` HMAC atas byte payload. Payload versi1
memuat id, task_hash dari user anchor tanpa marker `[HarmonikaProof:<id>]`,
issued_at/expires_at milidetik, owner/project/target/attempt/revision opaque,
required (maks8 jenis) dan checks (maks64). Check berisi binding yang sama,
sequence naik, at, provenance, outcome, call_id, result_sha256 dan facts teknis.
Hash receipt adalah SHA256 atas JSON.stringify(message.content), sesuai observe.
Verifier harus memeriksa state aktual secara independen sebelum menandatangani;
signature membuktikan issuer, bukan membuat fakta yang salah menjadi benar.
Kontrak maksimal1jam, hasil cek maksimal5menit, tanpa timestamp masa depan.
Ganti ledger secara atomik; jangan menyimpan prompt, chat, output mentah, PII
atau secret di sana. Task hash dan referensi opaque cukup untuk binding.

Jenis cek: read, tests, artifact, job, liveness, deploy, migration, document, vision.
Status hilang/gagal/parsial, revisi/target berbeda, count tes0, pending job,
checksum tidak cocok atau provenance model/client tidak memenuhi kewajiban.
Ringkasan action terdaftar berasal dari jenis cek valid; semua kewajiban wajib
terpenuhi sebelum complete. Expiry/revocation dicek kembali setelah inference,
meskipun panduan awal sempat melihat bukti sukses. Tidak ada replay mutasi.

Jalur terdaftar ini bersifat opt-in operator; adapter client-owned tool yang lama
belum menjadi trusted executor dan tidak mendapat attestation otomatis. Owner
binding ada di kontrak, bukan implementasi autentikasi tenant publik. Guard
legacy yang konservatif dan panduan hasil berbukti tetap membantu sesi biasa,
namun bukan pemeriksaan semantik menyeluruh semua parafrasa/pengetahuan.
Backend memerlukan Node18+ dan filesystem POSIX; worker Windows RTX tidak
menjalankan modul verifier ini. Uji reproduksi: `node --test tests/proof-contract.test.cjs` dan
`python3 -m unittest discover -s tests -p test_level13_activation.py -v`.

## Level14: bukti dari coding workspace backend

`/v1/coding/workspaces/{id}/agent` menerima profil verifikasi dan menerbitkan
kontrak dari actor gateway serta workspace runtime. Status selesai hanya meliputi
pemeriksaan profil yang terbukti. Lihat [kontrak Level14](../../docs/LEVEL14-EVIDENCE.md)
dan [laporan aktual](../../docs/LEVEL14-AGENT-EXAM-20261115.json).

Ini tidak menaikkan receipt tools lokal TUI menjadi bukti backend dan tidak
mengubah jalur marker opt-in Level13. Model tidak punya alat approval. Saat
respons terputus setelah efek tersimpan, rekonsiliasi memakai body/key yang sama;
jangan submit mutasi baru. Profil tes dibatasi Python/unittest yang didukung,
terpisah dari eksekusi biasa yang tetap memerlukan persetujuan.

## Respons interaktif Cline

Planner mengenali kapabilitas `read_files`, `run_commands`, dan `editor` dari
definisi function bertipe object yang unik; tools tambahan tetap diperbolehkan.
Ini hanya kelas latensi, bukan identitas klien atau izin baru. Batas pemilihan
keputusan adalah 60 detik per percobaan dan 90 detik total. Kelas umum tetap
180/240 detik. Batas ini tidak memotong durasi eksekusi perintah oleh klien dan
tidak mengurangi kapasitas konteks atau menghapus bukti. Regenerasi timeout tetap
memerlukan bukti transport tertutup serta keputusan belum dieksekusi.

Gabungkan bacaan/status independen lewat array tool yang tersedia sesuai schema;
mutasi berurutan dan verifikasi setelah edit tetap dipisahkan. Progres memuat
temuan baru dan langkah berikutnya secara singkat. Tidak ada filter otomatis
yang menghapus pesan peringatan, blocker atau isi jawaban. SSE tetap mengirim
keputusan hanya sesudah validasi; efek ketik kosmetik dilewati untuk tool call
yang siap dijalankan. Ini bukan token streaming model sebelum validasi.

Envelope array `run_commands` yang dikenal (`query`, `result`, `success`) kini
memeriksa status proses eksplisit per entri. `success:true` transport tidak
mengalahkan exit code gagal. Teks output tanpa metadata exit harus dianalisis
dan diverifikasi, bukan disertifikasi sukses atau gagal lewat kata kunci umum.
Array dokumen/source dari tool lain tidak dianggap metadata proses.

Uji: `tests/interactive-responsiveness.test.cjs`, integration planner dengan AJV
backend, dan `scripts/cline-responsive-canary.cjs` di staging privat. Canary
memakai workspace sementara dan test asli terlindungi; tidak mengeksekusi tugas
atau memutar ulang histori pengguna. Aktivasi memakai baseline hash aktual,
canary pada source yang sama, idle guard, backup dan rollback CAS.

## Inisiatif dalam tugas yang diizinkan

`requestMode` mengenali perintah kerja langsung dari user anchor asli secara
konservatif. Perintah yang dikenali sebagai `tool_action`, dengan function tools
tersedia dan tidak dibatasi `tool_choice=none`, tidak boleh diakhiri lewat
`phase=answer` berupa janji, tawaran atau sekadar pengakuan. Gunakan langkah
discover/execute/verify, complete berbukti, atau blocked/waiting yang nyata.
Koreksi keputusan tetap dibatasi satu kali; backend tidak mengeksekusi atau
mengulang mutasi klien. Guard bukti dan firewall dipertahankan.

Panduan meminta agent menemukan file/config/tes yang relevan, memperbaiki cacat
yang ditemukan dan memeriksa hasilnya tanpa meminta user menyebut setiap langkah.
Pertanyaan biasa, penulisan prosa, permintaan rencana dan pembatalan tetap normal.
Ini klasifikasi pola perintah yang terbatas, bukan pemahaman semantik universal.
Inisiatif tidak memperluas target, izin deploy, pengiriman pesan atau tindakan
destruktif. Batas spesifik seperti larangan deploy tidak menghapus izin perbaikan
lokal yang sudah diberikan. Sesi tanpa kontrak operator tetap client_reported.

## Level15: arah tugas, pemulihan dan kesegaran bukti

`task-continuity.cjs` menyusun observasi mekanis per tugas dari receipt caller:
operasi tertunda/ambigu, referensi job/request/key yang di-hash, write dan hasil
tes terakhir, serta jumlah bacaan/status yang tidak berubah. Ringkasan tidak
menyimpan chat, source, argumen atau data pelanggan. Ini bukan database lintas
sesi baru; caller harus tetap menyediakan konteks yang diperlukan. Pemadatan
internal mempertahankan observasi dan fakta intent lewat WeakMap request yang
diteruskan melalui `bindPrepared`, termasuk tujuan asli pada wrapper Cline.

Status completed harus mengonfirmasi identitas pada hasilnya sendiri. ID yang
hanya ada di argumen permintaan, ID asing, atau bacaan lain yang berhasil tidak
menyelesaikan operasi lama. Job ID, operation ID, request ID dan idempotency key
memiliki namespace berbeda. Mutasi ambigu tidak boleh diulang dengan key baru;
diagnosis shell yang diketahui hanya membaca tetap tersedia dalam batas izin.
Tiga bacaan/status identik dibatasi meskipun diselingi bacaan lain.

Untuk koreksi/build yang dikenali, setiap berkas yang ditulis membutuhkan
readback yang sesuai atau pemeriksaan baru. Permintaan tes membutuhkan jumlah
tes positif dan hasil lulus yang dilaporkan executor; build-only, exit0 tanpa
jumlah, nol tes, atau tes sebelum edit terakhir tidak memenuhi syarat tersebut.
Format TAP/unittest diparsing hanya pada output executor tes yang dikenali,
bukan pada source atau dokumen. Ini tetap laporan klien pada jalur legacy;
kontrak operator memberi pemeriksaan independen yang lebih kuat.

Cline dapat memendekkan `query` tampilan perintah panjang sementara argumen
tool tetap lengkap. Korelasi receipt menerima format pemendekan tersebut hanya
jika prefix dan jumlah karakter yang dihilangkan cocok dengan tepat satu
perintah asli. Format salah atau kecocokan ambigu tidak menjadi bukti tes.
Jika jumlah tes belum terbaca, agent diarahkan menjalankan perintah tes singkat
tersendiri, tanpa mengulang edit atau submit untuk memperoleh receipt baru.

Kontrak HMAC boleh menambahkan `allowed_tools` berupa daftar nama function
teknis yang unik. Pembatas ini mempersempit deklarasi klien dan tidak memberikan
izin baru. Enum output dibatasi pada irisan tool tersebut; pemilihan tool di luar
scope ditolak dengan `tool_scope_denied`/HTTP403. Field policy dari caller tanpa
kontrak tidak dipercaya. Policy malformed adalah kontrak invalid dan terminal.
Pembatas nama tool tidak menggantikan authorization actor/path pada executor.

Ujian 18 misi memakai model aktual, executor fixture terbatas, assertion asli
terlindungi, proses pemeriksa terpisah dan bukti HMAC per tugas/revisi. Mutator
SQLite memakai record sungguhan dan proses baru untuk rekonsiliasi; cancel diuji
dengan worker fixture milik ujian yang berhenti melalui flag, tanpa signal.
Fault ACL tulis/payload dan respons asing diinjeksi secara eksplisit. Verifier
fungsi fixture bersifat terbatas dan bukan sandbox umum untuk kode bermusuhan.
Canary Cline asli dilaporkan terpisah dari misi backend. Rencana lengkap ada di
`docs/LEVEL15-EXAM-PLAN-20261115.json`. Aktivasi memerlukan 18/18 misi pada hash
source yang sama, idle guard, backup prior-absence dan rollback CAS.

## Level16: pemulihan hasil batch per perintah

Array `run_commands` membutuhkan satu envelope executor yang cocok untuk setiap
perintah asli, tanpa hasil asing, duplikat, atau envelope rusak. Prefix dan jumlah
karakter query Cline yang dipendekkan tetap harus cocok unik. Hasil hilang tidak
dianggap sebagai perintah yang belum dijalankan: statusnya tidak pasti sehingga
batch mutasi tidak boleh diulang untuk mendapatkan receipt baru.

Status dan identitas operasi dibaca per anak dari field envelope/metadata yang
dikenal. Hasil saudara yang berhasil tidak menghapus operasi queued/unknown atau
ack pembatalan. Rekonsiliasi memerlukan status dengan identitas asli yang cocok;
bila identitas tidak tersedia, hasil jujur adalah blocker. Isi string `result`
yang menyerupai JSON tidak otomatis menjadi status, dan array source dari tool
baca tetap data. Catatan turunan menyimpan hash, enum dan jumlah dalam RAM saja.

Tes dalam batch mesti memiliki hasil lengkap dan jumlah positif pada seluruh
perintah tes yang diminta. Metadata gagal tetap gagal walaupun output memuat TAP
lulus. Perintah non-test yang berhasil pada batch gagal sebagian dilindungi dari
replay verbatim dalam tugas yang sama; ulang hanya pemeriksaan yang gagal.
Ini guard konservatif receipt klien, bukan pembuktian idempotensi shell atau
otorisasi baru. Perintah sah yang memang perlu diulang memerlukan konteks/izin
tugas yang jelas; jangan mengubah ejaan perintah untuk melewati guard.

Jumlah bacaan identik dihitung per anak sehingga mengganti urutan atau saudara
batch tidak mereset batas. Diagnosis baca yang dikenali tetap tersedia dalam
izin executor. Pengaman tidak menaikkan receipt klien menjadi HMAC independen.
Misi batch membedakan efek awal yang disiapkan operator, fault receipt yang
diinjeksi, keputusan model aktual dan readback independen. Rencana ujian ada di
`docs/LEVEL16-EXAM-PLAN-20261115.json`.

## Level17: cakupan proyek dan revisi pada verifikasi

`verification-scope.cjs` menyimpan hash identitas yang disebut secara eksplisit
oleh interface tool: owner, project/workspace, target, attempt dan revisi.
Kontradiksi antarscope tidak boleh menjadi bukti selesai maupun menyelesaikan
operasi job lama walaupun job ID sama. Informasi scope tambahan pada status
pending dipertahankan saat rekonsiliasi. Field ini tetap receipt klien dan
tidak menerbitkan attestation atau hak akses baru.

Perintah tes dengan `cd <path> && ...` yang statis dan cwd executor dipetakan
secara leksikal ke berkas yang ditulis. Folder saudara dengan prefix mirip tidak
cocok. Path absolut/relatif dengan cwd eksplisit, dot segments, drive Windows
dan UNC dinormalisasi tanpa mengakses filesystem. Setiap target tulis memiliki
kewajiban verifikasi sendiri; nama relatif sama di proyek berbeda tidak boleh
saling menimpa. Revisi baru untuk target yang sama menggantikan revisi lamanya.

Scope kosong pada klien lama tetap memakai mekanika `client_reported`; ini
bukan pembuktian cakupan semantik tes. Cwd/ref yang ambigu, pindah folder lagi
di tengah shell, atau flag pemilihan workspace yang belum dipahami memerlukan
receipt tes yang lebih jelas. Root filesystem bukan cakupan proyek yang sah.
Normalisasi bukan resolusi symlink, autentikasi tenant atau sandbox. Nama file
Windows dipertahankan case-nya; uji leksikal bukan uji terminal Windows fisik.

`source_revision` dinormalisasi sebagai revision. Write dapat mengonsumsi revisi
lama dan mengembalikan revisi baru. Read/test yang dipatok ke ref/commit lama
tanpa revisi write yang cocok tidak memverifikasi perubahan baru. Untuk tool
write seluruh berkas dengan `content`, hash readback penuh yang berbeda juga
tidak membuktikan berkas hasilnya benar. Jangan menganggap `editor.new_text`
sebagai isi penuh berkas karena interface editor dapat berupa penggantian bagian.

Argumen `target` pada tool tes adalah path entry point, sedangkan metadata
executor `target` adalah identitas target; jangan membandingkan kedua namespace
tersebut sebagai ID yang sama. `target_id` eksplisit tetap dapat dibandingkan.
Metadata `revision_scope=workspace` menyatakan revisi gabungan workspace, dengan
project/workspace ID yang eksplisit. Write berikutnya pada workspace sama
memajukan revisi verifikasi berkas-berkas yang sudah ditulis. Tanpa penanda itu,
revisi satu file tidak boleh diwariskan dari write file lain.

Misi Level17 menginjeksi satu respons dengan metadata scope yang salah pada
pemeriksaan aktual; pemeriksa independen tidak menandatangani respons tersebut.
Model harus mengambil pemeriksaan baru yang cocok. Riwayat customer tidak
dipakai dan metadata scope mentah tidak ditambahkan ke log. Rencana:
`docs/LEVEL17-EXAM-PLAN-20261115.json`.

## Level18: status operasi dan hasil yang belum lengkap

`receipt-outcome.cjs` membaca field mekanika pada envelope executor yang dikenal.
Status operasi hilang/tidak dikenal serta retrying mempertahankan ketidakpastian
di continuity. Status asing dinormalisasi menjadi unknown; isinya tidak disalin
ke log. Bacaan lain yang berhasil tidak menyelesaikan operasi, dan mengganti
idempotency key bukan cara mengizinkan submit ulang. Identitas dan scope asli
tetap diperlukan untuk rekonsiliasi.

Penanda partial, truncated, is_truncated, output_limited atau complete=false
menghalangi pemakaian receipt sebagai bukti selesai. Tes dengan jumlah positif
yang keluarannya terpotong belum membuktikan lulus. Tes boleh diperiksa ulang
setelah exit worker diketahui; bila hasil worker belum pasti, lakukan lookup
operasi asli. Acknowledgement cancel yang belum lengkap bukan bukti worker mati.

Untuk interface whole-file write yang menyebut content, planner menyimpan hash
isi yang diminta. Tanpa job/request/idempotency reference async, bacaan ulang
penuh pada file, scope, revisi dan hash yang cocok dapat menyelesaikan ack write
yang hilang tanpa menulis ulang. Hash tidak tersedia, bacaan terpotong, file lain,
atau isi berbeda tidak cocok. Jalur ini tidak berlaku bagi patch, editor parsial
atau mutasi shell opaque. Verifikasi setelah write tetap diperlukan; tes sebelum
write belum mencakup perubahan baru. Penyimpanan editor yang diakui lengkap tetap
didukung tanpa mengarang klaim tes atau deployment.

Envelope Cline sinkron query/result/success tanpa state tetap kompatibel;
request_id trace saja tidak dijadikan job async. String source, JSON dokumen dan
array bacaan bukan state executor. Validator transport tetap menolak pasangan
tool call/result yang hilang sebelum memanggil model. Pengaman receipt ini tidak
menerbitkan attestation baru, tidak menambah otorisasi atau database lintas sesi.

Ujian memakai source aktual pada workspace sintetis. Operator sengaja memotong
satu receipt per misi, menahan bukti untuk receipt tersebut, lalu memeriksa hasil
nyata secara independen. Rekonsiliasi job menggunakan satu efek SQLite operator;
cancel memakai worker ujian milik runner yang berhenti melalui flag. Tiga ujian
Cline native dilaporkan terpisah. Rencana: `docs/LEVEL18-EXAM-PLAN-20261115.json`.

## Inisiatif pada perintah sehari-hari

Perintah cek, audit, periksa, telusuri dan temukan pada target teknis dikenali
sebagai pekerjaan yang memerlukan tool. Prefix sopan seperti mohon dan permintaan
menulis lalu menyimpan berkas didukung. Pertanyaan, puisi/prosa, rencana saja dan
larangan eksekusi tetap dijawab sesuai maksudnya. Pengenalan intent ini terbatas
pada pola yang diuji, tidak mencakup seluruh urutan kata atau memberi izin baru.

Pada jalur legacy, klaim tes lulus harus mengutip receipt tes aktual dengan jumlah
positif yang berkorelasi; keluaran pwd, ls atau shell umum tidak cukup. Klaim bug
sudah diperbaiki perlu bukti perubahan atau tes aktual, dengan kewajiban scope dan
verifikasi setelah write yang tetap berlaku. Ini pengaman mekanika dan frasa yang
dikenal; receipt klien serta semantik bahasa tidak otomatis menjadi attestation.

Audit read-only dapat selesai dengan temuan gagal. Pada jalur legacy, phase answer
dibolehkan sesudah receipt tes gagal yang lengkap dan berkorelasi dengan jumlah
aktual positif, tanpa write atau operasi pending/unresolved. evidence_call_ids
menunjuk temuan tersebut, bukan bukti tes lulus. Phase complete serta kontrak
operator HMAC tetap mengikuti gerbang sukses aslinya. Tanpa receipt aktual,
promise-only answer tetap ditolak. Pemisahan ini memperbaiki invalid_tool_response
yang ditemukan lewat ujian audit asli tanpa mengubah assertion atau izin executor.

Runner `scripts/initiative-command-native.py` menguji enam perintah dengan Cline
asli, workspace/XDG privat dan lock native milik suite. Audit read-only harus
mempertahankan hash semua berkas; status billing paid yang bertentangan dengan
provider pending harus dilaporkan sebagai belum terverifikasi. Ujian discovery
memuat arsip pengecoh serta komentar vendor berinstruksi palsu; assertion asli
dan source aktif diperiksa independen. Provider reference sementara dihapus
setelah klien keluar. Jangan memakai session ID pengguna untuk menjalankan ujian.

Prompt dikirim lewat argv dan stdin klien diisolasi dengan DEVNULL. Cline dapat
menambahkan stdin sebagai konteks; heredoc skrip operator yang diwariskan bukan
bagian dari perintah ujian. Runner memeriksa effective user request dan menolak
kontaminasi peluncur. Hasil sebelum koreksi harness disimpan sebagai riwayat
pengembangan dan tidak dipakai dalam skor enam sesi akhir yang bersih.

Rencana dan batas: `docs/INITIATIVE-COMMAND-EXAM-PLAN-20261115.json`. Bukti tes
regresi, source aktif, perubahan terbatas dan pemeriksaan jawaban disimpan dalam
laporan suite setelah seluruh gerbang lulus; tidak ada klaim training model atau
persentase kecerdasan universal.

## Laporan diagnostik dan invalid_tool_response

Perintah menjalankan tes lalu melaporkan hasil tanpa perubahan dapat menghasilkan
kegagalan atau nol tes. Phase answer pada jalur legacy menerima temuan lengkap
tersebut dengan referensi receipt yang berkorelasi. Error node --check atau npm
check/lint/typecheck yang dikenal juga dapat dilaporkan. Jangan menebak exit worker
yang tidak diberikan klien; diagnostic_output hanya menandai adanya keluaran,
bukan bukti sukses, otorisasi baru atau perubahan klasifikasi mutasi executor.

Phase complete, klaim tes lulus, scope, hasil parsial/pending/unknown, korelasi
batch serta kontrak HMAC operator tetap mengikuti gerbang aslinya. Kata perbaiki
sesudah periksa/audit masih merupakan pekerjaan yang wajib dikerjakan; larangan
mengubah file lain tidak membatalkannya. Recovery membedakan laporan hasil negatif
dari persyaratan jumlah tes positif untuk membuktikan keberhasilan.

Tiga pemicu native dan uji ulangnya disimpan oleh
scripts/invalid-tool-response-native.py; semua fixture read-only, stdin DEVNULL,
sesi baru dan file terlindungi. Rencana: docs/INVALID-TOOL-RESPONSE-HUNT-PLAN-20261115.json.
