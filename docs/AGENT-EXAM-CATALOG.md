# Katalog ujian agent Harmonika

Semua run berikut menggunakan workspace dan sesi native terpisah, data sintetis,
tes/artifact yang diperiksa independen, dan tidak menyambung sesi pengguna.
Kelulusan berlaku pada cakupan laporan; bukan sertifikasi semua pekerjaan produksi.

| Bidang | Kemampuan yang diuji | Bukti |
|---|---|---|
| Otonomi dasar | Baca source, perbaiki, jalankan tes, tulis laporan | [Ujian awal](AGENT-AUTONOMY-EXAM-20261113.json) |
| Debug lintas modul | Pagination, cache async, antrean persisten | [Debug repository](DEBUG-REPOSITORY-EXAM-20261113.json) |
| Ketahanan alur | Data sumber, instruksi palsu, acknowledgement hilang, kelanjutan sesi | [Resilience](AGENT-RESILIENCE-EXAM-20261113.json) |
| Agent level tinggi | Transaksi SQLite atomic, revisi kontrak, edit pengguna dan konflik spesifikasi | [Level tinggi](HIGH-AGENT-EXAM-20261113.json) |
| Operasi dan integrasi | Timeline insiden, readiness, simulasi mitigasi, retry SDK dan audit privat | [Insiden dan API](INCIDENT-AGENT-EXAM-20261113.json) |
| Browser dan data | UI→API→SQLite, tenant/sesi, XSS, mobile serta backup/restore WAL | [Alur lengkap](FULLFLOW-AGENT-EXAM-20261113.json) |
| Level 4 staging Linux | Dependency offline, deploy Docker, lost ACK, same-session resume, rollback/readiness dan crash-restart dengan DB persisten | [Level 4](LEVEL4-AGENT-EXAM-20261114.json) |
| Level 5 aplikasi tiket | UI/API/SQLite, migrasi, concurrency, ACK/lock recovery dan revisi immutable | [Level 5](LEVEL5-AGENT-EXAM-20261114.json) |
| Level 6 durable worker | Crash sesudah commit efek terpisah, unknown ACK, cancellation, backpressure, SSE cursor dan drain persisten | [Level 6](LEVEL6-AGENT-EXAM-20261114.json) |
| Level 7 multiworker | Tiga worker, stale-live fencing,100 HTTP, crash42/ACK recovery, SSE kontinu, clone WAL dan code rollback | [Level 7](LEVEL7-AGENT-EXAM-20261114.json) |
| Level 8 workflow persisten | DAG lintas database, child ACK/crash recovery, pause/cancel, migrasi additive, prioritas dan code rollback | [Level 8](LEVEL8-AGENT-EXAM-20261114.json) |
| Level 9 dua coordinator | Epoch/CAS, stale coordinator tetap hidup, failover, TCP ACK/body/partition dan 200 request | [Level 9](LEVEL9-AGENT-EXAM-20261114.json) |
| Level 10 recovery | Rolling/rollback kode, crash delapan proses, backup WAL, clone nonaktif, storage/lock dan isolasi | [Level 10](LEVEL10-AGENT-EXAM-20261114.json) |
| Antrean dan stream | Fairness tenant, backpressure, lease unknown, cancel, UTF-8/SSE dan cursor reconnect | [Antrean dan SSE](QUEUE-STREAM-AGENT-EXAM-20261114.json) |

## Ujian operasi terbaru

Jalankan dengan direktori baru:

```sh
python3 scripts/incident-agent-exam.py --root /tmp/harmonika-ops-new --case incident
python3 scripts/incident-agent-exam.py --root /tmp/harmonika-ops-new --case client
```

Kasus insiden membedakan cache dan liveness dari readiness, menghubungkan request
serta instance yang benar, menguji kandidat lokal, dan tetap mencatat produksi
belum berubah. Kasus client memakai adapter HTTP sintetis untuk menguji klasifikasi
error, budget retry, outcome mutasi yang ambigu dan audit mekanika.

Script `incident-decision-smoke.cjs` berjalan hanya pada gateway Linux dengan token
internal privat. Ia mengirim fixture ke API loopback aktual dan memeriksa keputusan;
tool yang dipilih model tidak dieksekusi. Jangan menyalin token ke argumen atau Git.

## Runner UI, tenant dan backup SQLite

```sh
python3 scripts/fullflow-agent-exam.py --root /tmp/harmonika-fullflow-new --case app
python3 scripts/fullflow-agent-exam.py --root /tmp/harmonika-fullflow-new --case backup
```

Kasus `app` menyediakan server loopback pada port acak dan Chromium terpisah.
Browser menguji form, refresh, toggle, DELETE 204, pemisahan tenant, teks berbahaya
yang ditampilkan literal, restart database serta viewport desktop/mobile. Kredensial
sesi hanya referensi fixture sintetis; ini bukan rancangan autentikasi produksi.
Kasus `backup` menguji snapshot SQLite WAL dan restore dengan pemeriksaan hash,
integritas, izin file, penolakan symlink/sidecar dan perlindungan file tujuan.

Penilai terpisah menjaga hash README, tes asli, alat ujian dan catatan pengguna.
`--retest` mempertahankan hasil awal dan menjalankan sesi baru pada pekerjaan yang
sama; retest berikutnya mendapat nomor berkas baru agar log lama tidak tertimpa. Hasil
`--grade-only` harus dibaca dari field `passed`, bukan exit code runner semata.
Artifact dan log native tetap privat di root ujian; jangan memasukkan history atau
kredensial pengguna ke laporan. Server ditutup melalui stdin, tanpa signal proses.

Saran ujian berikutnya yang belum dicakup oleh runner ini:

1. Antrean jenuh dan pembatalan: fairness, backpressure, deadline dan hasil tak pasti.
2. Race pada pergantian akun saat respons tertunda dan beberapa writer bersamaan.
3. Restore saat proses mati/power-loss; keyboard fisik dan IME pada perangkat mobile.

Setiap pengujian perlu scope/izin target dan bukti nyata yang sesuai; fixture
lokal tidak membuktikan restore produksi, isolasi tenant atau keandalan vendor.

## Runner antrean dan pemulihan SSE

```sh
python3 scripts/queue-agent-exam.py --root /tmp/harmonika-queue-new --case queue
python3 scripts/queue-agent-exam.py --root /tmp/harmonika-queue-new --case stream
```

Kasus antrean memakai jam virtual: kapasitas terbatas, round-robin tenant,
idempotency, pembatalan, lease dan rekonsiliasi outcome unknown. Slot unknown tetap
terisi hingga hasil worker diketahui; expiry bukan bukti proses fisik berhenti.
Kasus stream memakai iterator byte yang diinjeksi untuk memutus koneksi, mengulang
event, memecah UTF-8/CRLF serta menguji cursor terakhir yang benar-benar diterima.
Selesai membaca stream tidak berarti pekerjaan selesai.
Review cakupan menambahkan probe di `scripts/queue-exam-acceptance/` untuk batas
tenant saat ada slot global, audit, expiry saat penuh, urutan ID numerik, error
decode/timeout dan penutupan iterator. Runner menyertakan probe tersebut pada
penilaian berikutnya; hasil awal tetap dicatat terpisah dalam laporan.

Penilaian melindungi kontrak/tes/catatan asli dengan hash dan menyimpan setiap run
terpisah. Ujian ini tidak menjalankan GPU, load produksi, jaringan riil, scheduler
multi-thread atau broker persisten. Jalur inference backend diuji terpisah melalui
`scripts/queue-decision-smoke.cjs`; tool klien dari probe tersebut tidak dieksekusi.


## Runner Level 4 pada staging Linux nyata

Provisioning lab dilakukan oleh operator dengan image Python resmi yang dipin,
controller privat, label ownership unik dan volume sintetis. Runner tidak membuat
akses SSH atau target produksi baru. Jangan menjalankannya pada root ujian yang
sudah dipakai kecuali untuk kelanjutan sesi yang sesuai.

```sh
python3 scripts/level4-agent-exam.py --root /path/to/private-prepared-root
python3 scripts/level4-agent-exam.py --root /path/to/private-prepared-root --resume
# Alternatif kelanjutan singkat, pada sesi yang sama, bukan run tambahan:
python3 scripts/level4-agent-exam.py --root /path/to/private-prepared-root --continue-session
python3 scripts/level4-agent-exam.py --root /path/to/private-prepared-root --grade-only --resume
```

Jalankan salah satu bentuk kelanjutan sekali saja. Simpan log gagal/hasil sebelum
uji ulang; jangan replay aktivasi atau POST yang outcome-nya belum pasti. Setelah
penilaian dan ekspor bukti selesai, cleanup hanya container/image-tag/volume yang
terdaftar dan berlabel lab tersebut. Image dasar dan objek milik pengguna tetap ada.

Batas harness: counter operasi mengukur percobaan, sehingga rejection tanpa efek
juga dapat gagal pada kontrak satu-panggilan; restart_count tepat satu ditujukan
untuk fault terkontrol, bukan sertifikasi kejadian OOM/power-loss tambahan. Ujian
ini tidak membuktikan SSH arbitrer, reboot host, load serentak atau provider produksi.

## Runner Level 5: aplikasi tiket dan perubahan kebutuhan

Status sesi 14 November 2026: TURN A dan TURN B lulus pada lab terkontrol setelah
koreksi target sentuh link lampiran. Pengujian image v1 pada DB v2 juga lulus;
lab sudah dibersihkan. Hasil lengkap
dan batas bantuan operator ada di [laporan Level 5](LEVEL5-AGENT-EXAM-20261114.json).
Ini ujian berskop, bukan sertifikasi otonomi umum.

Lab memakai controller Linux berlabel, image immutable dan volume SQLite sintetis.
Sesi native yang sama membangun login, isolasi owner, CRUD, pencarian dan lampiran;
kemudian menerima perubahan schema/version/priority, edit pengguna dan fault
ACK/lock/readiness. Fixture identity provider disediakan; aplikasi bukan IAM produksi.

```sh
# Hanya pada private root yang sudah diprovision operator; simpan setiap run.
python3 scripts/level5-agent-exam.py --root /path/to/prepared-private-root
python3 scripts/level5-grade.py --root /path/to/prepared-private-root --label v1-first
python3 scripts/level5-start-b.py --root /path/to/prepared-private-root --passed-report /path/to/v1-first-result.json
python3 scripts/level5-agent-exam.py --root /path/to/prepared-private-root --resume
python3 scripts/level5-grade.py --root /path/to/prepared-private-root --v2 --label v2-first
python3 scripts/level5-backward-check.py --root /path/to/prepared-private-root --passed-report /path/to/v2-first-result.json
```

Jangan jalankan grading bersamaan dengan native: proxy memiliki berkas mekanika
bersama. Kelanjutan koreksi memakai label baru dan mempertahankan log lama;
revisi rilis harus mengikuti RELEASE-PROTOCOL, tanpa replay aktivasi yang belum
pasti. Penguji memeriksa hash file terlindungi sebelum eksekusi, memakai interpreter
milik penguji, API dan browser aktual serta HTTP lokal paralel langsung karena
bridge staging diserialkan. Bukti ACK mengikat hash kunci request yang sama, bukan
GET sembarang. Delapan kasus negatif memeriksa bukti recovery tersebut.

Snapshot dan hasil lengkap hanya berisi fixture sintetis di direktori privat;
laporan repositori berisi mekanika serta screenshot aplikasi uji. Origin marker
bukan batas autentikasi. Pemeriksaan privasi fixture bukan DLP lengkap. Uji browser
tidak mengklaim keyboard fisik/IME, load produksi atau akses host yang arbitrer.

## Runner Level 6: pekerjaan persisten dan crash setelah efek tersimpan

Sesi 14 November 2026 lulus 41 pemeriksaan integrasi serta 16 kasus engine yang
sama pada Mac dan image Linux. Native menyelesaikan dua tahap pada satu sesi;
bug atribut error SQLite di Mac ditemukan dan diperbaiki melalui regresinya
sendiri. Panduan backend diperbarui, diaktifkan dan diprobe; tiga container serta
image/volume milik lab sudah dibersihkan. [Hasil lengkap](LEVEL6-AGENT-EXAM-20261114.json).

Operator menyediakan transport HTTP, dua identity fixture, connector efek
sintetis dan alat deploy yang immutable. Native mengimplementasikan queue dan tes
regresi. Sink memiliki transaksi/DB terpisah: fault benar-benar exit42 setelah
commit, diikuti restart Docker dan lookup job yang sama sebelum completion.
Penilai memeriksa satu execute/satu efek, artifact aktual, cancellation, kuota
owner, lock atomic, cursor SSE, drain melewati restart, serta digest source/image.

```sh
# Operator dengan alias SSH vps terverifikasi dan image dasar yang sudah siap.
# Gunakan root privat baru; jangan gunakan ulang lab yang sudah closed.
python3 scripts/level6-prepare.py --root /path/to/fresh-private-root
python3 scripts/level6-agent-exam.py --root /path/to/fresh-private-root --label turn-a
python3 scripts/level6-grade.py --root /path/to/fresh-private-root --phase v1 --label v1-first
python3 scripts/level6-start-b.py --root /path/to/fresh-private-root --passed-report /path/to/fresh-private-root/v1-first-result.json
python3 scripts/level6-agent-exam.py --root /path/to/fresh-private-root --resume --label turn-b
python3 scripts/level6-grade.py --root /path/to/fresh-private-root --phase v2 --label v2-first
python3 scripts/level6-close.py --root /path/to/fresh-private-root --passed-report /path/to/fresh-private-root/v2-first-result.json
```

Native dan penilai bergantian di bawah native.lock. Revisi/koreksi menggunakan
label baru dan image immutable; jangan menimpa hasil awal atau replay job untuk
menghindari outcome unknown. Exporter laporan bertanggal hanya dipakai operator
sekali setelah grading dan cleanup lulus, bukan untuk menimpa laporan historis.

SSE fixture berupa halaman finite dengan framing dan cursor, bukan stream live
kontinu. Identity fixture bukan IAM produksi; sink bukan vendor eksternal/RTX.
Tes lokal bukan sandbox host adversarial, uji reboot/power-loss, atau sertifikasi
konfigurasi plugin pengguna. Snapshot sintetis lengkap tetap privat; laporan
repositori berisi mekanika, hash dan batas bantuan operator. Review OpenCode
divalidasi ulang dengan source/tes; Claude tidak dilibatkan.

## Runner Level 7: tiga worker, live SSE dan clone WAL

Sesi 14 November 2026 lulus43 pemeriksaan integrasi dan21 kasus engine pada Mac
serta image Linux.51 job selesai dengan satu efek per job; perbaikan timeout
planner aktif di backend. Kegagalan awal dan koreksi tercatat terpisah.

Operator menyediakan lima container Linux terisolasi: API, sink HTTP Unix dan tiga
worker terpisah. Native melanjutkan seed hasil Level6 yang sudah lulus, menambahkan
claim/fencing/completion CAS, parser SSE incremental dan backup/restore nonaktif.
Hasil awal dan revisi dicatat pada [laporan Level7](LEVEL7-AGENT-EXAM-20261114.json).

```sh
# Wajib private seed Level6 yang sudah lulus; tidak membuat koneksi produksi baru.
python3 scripts/level7-prepare.py --root /path/to/fresh-root --seed-root /path/to/passed-level6-root
python3 scripts/level7-agent-exam.py --root /path/to/fresh-root --label turn-a
python3 scripts/level7-grade.py --root /path/to/fresh-root --phase v1 --label v1-first
python3 scripts/level7-start-b.py --root /path/to/fresh-root --passed-report /path/to/fresh-root/v1-first-result.json
python3 scripts/level7-agent-exam.py --root /path/to/fresh-root --resume --label turn-b
python3 scripts/level7-grade.py --root /path/to/fresh-root --phase v2 --label v2-first
python3 scripts/level7-close.py --root /path/to/fresh-root --passed-report /path/to/fresh-root/v2-first-result.json
```

Native dan penilai bergantian melalui native.lock. Revisi setelah kegagalan memakai
label dan image baru; jangan menimpa hasil gagal, checkpoint atau catatan pengguna.
Kontrak100 request dengan hold-all menguji admission8 pending per owner dan replay
20 key, bukan100 job completed. Backlog penerima koneksi dan batas container harus
dicatat terpisah dari hasil queue; seluruh100 request tetap wajib mendapat HTTP.

Fault mencakup worker lama tetap hidup saat lease berakhir, commit sebelum ACK
hilang/exit42, dua lookup503 terikat target, receipt rusak, sink offline dan POST
ACK hilang. Live stream dibuka sebelum write, diputus di tengah frame dan reconnect
via cursor diterima. Backup diuji saat WAL berisi write baru dan lock writer aktif;
clone harus standalone,0600, lolos hash/integritas dan tidak menimpa tujuan. Code
rollback mempertahankan DB/sink lalu mengembalikan image v2 yang telah diuji.

Controller menyimpan critical witnesses terpisah dari polling ring, memindai semua
log container terdaftar dan memisahkan origin examiner/native. Alat penilai menjaga
assertion serta hash file terlindungi; upgrade harness harus dicatat dengan bukti
sebelum/sesudah. Ini lab sintetis terkontrol, bukan sertifikasi IAM, power-loss,
DR multi-database, TCP lintas host, vendor produksi, GPU/RTX atau otonomi universal.

## Runner Level 8: workflow DAG lintas database

Sesi 14 November 2026 lulus 45 pemeriksaan integrasi, 22 kasus engine yang sama pada
Mac/Linux, serta 16 pengaman harness. 24 workflow selesai, 2 dibatalkan, 49 job dan 49
efek tersimpan diverifikasi. Native sempat terputus pada header timeout 300 detik;
Codex memperbaiki anggaran retry 240 detik, lalu native melanjutkan session dan
checkpoint yang sama sampai rollback/restore selesai. Perbaikan backend lulus 148
tes lokal, 76 Linux, 7 activation guards dan 9 canary keputusan aktual. Lab ditutup
dengan nol container/image/volume berlabel tersisa.

Enam container terisolasi memisahkan API, tiga queue worker, effect sink dan
workflow coordinator. Native membangun WorkflowStore di atas queue Level7 yang
sudah lulus. Workflows, jobs, sink dan mekanika fault memakai SQLite terpisah;
transport child berupa HTTP melalui Unix socket privat, tanpa jaringan host.

TURN A menguji validasi DAG, tenant/idempotency, admission atomic, identitas child
stabil sebelum I/O, dependency dan byte artifact. TURN B melanjutkan sesi serta
checkpoint yang sama: ACK hilang dengan dua lookup503, coordinator exit42,
artifact rusak, sink offline, pause melewati restart, cancel queued/running,
prioritas, migrasi transactional dan code rollback saat seluruh pekerjaan settled.
Beban60 request diulang pada image final; initial admission, fill serta probe kuota
memiliki hitungan terpisah. Batasnya tetap empat workflow aktif per tenant.

```sh
python3 scripts/level8-prepare.py --root /path/to/fresh-root --seed-root /path/to/passed-level7-root
python3 scripts/level8-agent-exam.py --root /path/to/fresh-root --label turn-a
python3 scripts/level8-grade.py --root /path/to/fresh-root --phase v1 --label v1-first
python3 scripts/level8-start-b.py --root /path/to/fresh-root --passed-report /path/to/fresh-root/v1-first-result.json
python3 scripts/level8-agent-exam.py --root /path/to/fresh-root --resume --label turn-b
python3 scripts/level8-grade.py --root /path/to/fresh-root --phase v2 --label v2-first
# Setelah grade lulus dan upgrade panduan backend diverifikasi/diaktifkan:
python3 scripts/level8-close.py --root /path/to/fresh-root --passed-report /path/to/fresh-root/v2-first-result.json
python3 scripts/finalize-level8-report.py --root /path/to/fresh-root --passed-report /path/to/fresh-root/v2-first-result.json
```

Native dan examiner wajib bergantian melalui native.lock. Snapshot v1 dan operasi
examiner memerlukan autentikasi HMAC privat di luar project/image/Git. Fault,
probes dan stress terikat release/image; koreksi memakai label baru dan menyimpan
hasil gagal. Mock child mengikuti protokol UUID32 queue nyata. Raw native JSONL,
input sintetis dan snapshot lengkap tetap privat; laporan publik berisi mekanika.

[Hasil aktual, koreksi fixture/operator dan batas](LEVEL8-AGENT-EXAM-20261114.json)
membedakan aplikasi ujian terisolasi dari panduan planner yang dipasang di backend.
Ujian ini tidak membuktikan training model, IAM produksi, power-loss, rolling
migration beberapa versi, exactly-once vendor arbitrer atau otonomi universal.

## Runner Level 9: dua coordinator dan proxy TCP privat

Operator memulai dari source Level8 yang sudah lulus dan membuat satu checkpoint
schema2 nyata. Native mengimplementasikan claim owner/epoch integer/lease dan CAS
schema3; API tetap admission/control-only. Delapan container memakai volume privat
bersama: API, dua coordinator, tiga worker, sink dan proxy. Hanya API/proxy/coordinator
bergabung dengan network Docker `--internal`; tidak ada port yang dipublikasikan.
Tidak ada target BGP/core/VyOS/JKT atau perubahan host routes/firewall.

```sh
python3 scripts/level9-prepare.py --root /path/to/fresh-root --seed-root /path/to/passed-level8-root
python3 scripts/level9-bootstrap.py --root /path/to/fresh-root
python3 scripts/level9-agent-exam.py --root /path/to/fresh-root --label turn-v3
python3 scripts/level9-grade.py --root /path/to/fresh-root --phase v3 --label v3-first
# Setelah lulus dan panduan backend teruji/diaktifkan melalui hash + idle guard:
python3 scripts/level9-close.py --root /path/to/fresh-root --passed-report /path/to/fresh-root/v3-first-result.json
python3 scripts/finalize-level9-report.py --root /path/to/fresh-root --passed-report /path/to/fresh-root/v3-first-result.json
```

Respons child ditahan setelah admission saat coordinator pertama tetap hidup;
coordinator kedua harus menyelesaikan workflow sebelum respons lama dilepas.
Audit settlement coordinator kedua harus ada pada snapshot aktual sebelum release,
lalu stale_rejected epoch lama muncul tanpa regresi artifact. Proxy juga memutus
ACK setelah commit, memotong body HTTP dan menutup TCP sebelum forwarding saat
partition. Health/readiness, identitas request dan efek diperiksa secara terpisah.

Beban 200 attempt memakai 20 key dan kuota empat workflow aktif per tenant. Initial
admission, fill yang didahului lookup404, replay dan probe kuota dihitung terpisah;
bukan klaim 200 workflow selesai. Latency p50/p95/max dan resource berasal dari
pengukuran, bukan SLA. Native dan penilai bergantian melalui native.lock.

Run 14 November lulus 45 pemeriksaan integrasi dan 32 kasus engine pada kedua
platform, dengan 19 pengaman alat uji. Regresi cancel paused gagal sebelum fix R1.
Probe running dekat API menghindari kehilangan lease singkat; audit mengembalikan
jumlah baris dan snapshot lengkap berbatas, bukan memotong diam-diam di 1.000 row.
Satu bukti stale R1 diambil ulang dengan key baru dan budget fault yang belum
dipakai setelah koreksi audit; kasus serta receipt lama tetap disimpan. Koreksi
operator terikat image dan dijelaskan dalam laporan, bukan penghapusan hasil gagal.

[Hasil aktual dan batas](LEVEL9-AGENT-EXAM-20261114.json) membedakan prototype lab,
panduan planner backend dan kemampuan model. Ini bukan physical-host HA, database
terdistribusi, IAM produksi, comprehensive prompt-injection security atau training.

## Runner Level 10: recovery dan rolling upgrade terisolasi

Runner `scripts/level10-prepare.py` memerlukan source Level 9 yang sudah lulus
dan hash laporan yang cocok. Operator memprovision delapan role pada network
Docker internal tanpa published ports; fixture bearer dan HMAC penilai privat
berada di luar proyek/image. Tidak ada akses core/BGP/VyOS/JKT, data pelanggan,
RTX, host firewall atau proses pengguna. Disk penuh hanya pada tmpfs2MiB lab.

Harmonika mengimplementasikan WorkflowStore schema4 dan RecoveryManager, menambah
regresi, lalu menjalankan drill A–H melalui typed `tools/ops.py`. Codex menilai
secara terpisah di bawah `native.lock`. Rilis awal dan R1 tetap disimpan; R2
memperbaiki pembukaan store schema4 WAL saat writer aktif tanpa merusak migrasi
atau konversi clone DELETE ke WAL. Audit WAL/kuota429 memakai UUID retry stabil.

Bukti final meliputi legacy coordinator claim/settle pada schema4 sebelum rolling,
rollback kode tanpa rollback data, delapan main process die42/start aktual,
checkpoint/byte artefak tetap, snapshot saat writer masih memegang transaksi,
clone dengan ID asli tanpa child POST/efek baru, serta stale restore ditolak
setelah pause/cancel. Lock journal mengembalikan health200/ready503 dan retry pulih;
SQLITE_FULL mengembalikan507 pada fixture terbatas. Delapan kasus autentikasi
termasuk nonASCII dan lima fixture backup rusak ditolak sesuai kontrak.

Hasil: 56 kasus engine yang sama pada Mac dan Linux, 34 pemeriksaan integrasi,
14 guard alat uji. Satu perbaikan penilai membatasi uptime sampai proof sebelum
replacement yang disengaja; koreksi lain membaca label legacy writer/lock dari
observasi asli tanpa mengganti timestamp atau menjalankan ulang mutasi. Seluruh
hasil gagal dan provenance koreksi disimpan. Lab dibersihkan setelah kelulusan.

Panduan planner backend aktif setelah 148 tes lokal, 76 Linux, 7 pemeriksaan
aktivasi dan 13 canary keputusan tanpa eksekusi tool klien. TUI/API mengikuti
panduan backend ini; binary/ASCII/brand terminal tidak diganti. Aplikasi recovery
sendiri tetap prototype ujian satu host, bukan endpoint publik, host HA, jaminan
power loss atau training model. Lihat [laporan](LEVEL10-AGENT-EXAM-20261114.json).

## Runner Level 11: dua proyek dan pemulihan sesi

Atlas membangun aplikasi tiket/job SQLite WAL, REST, finite SSE, UI browser dan
reference terminal. Boreal menyelesaikan laporan CSV dengan Decimal, deduplikasi
per proyek, manifest byte/hash serta publikasi tanpa overwrite. Kedua sesi native
dimulai bersamaan dengan workspace dan XDG terpisah. Ini operator tepercaya dalam
scope sintetis, bukan klaim isolasi OS terhadap agent adversarial.

Runner membaca root privat dari `/tmp/harmonika-level11-root.path`. Gunakan root
baru berizin0700, alias SSH `vps` yang identitasnya sudah diverifikasi, image Python
cached yang dipin digest, serta runtime Playwright lokal. `level11-prepare.py`
menolak lab yang sudah diprovision. Fixture token/HMAC tidak dimasukkan ke proyek.

```sh
python3 scripts/level11-prepare.py
# Jalankan pada dua terminal terpisah untuk ujian bersamaan:
python3 scripts/level11-agent-exam.py --case atlas --label turn-a
python3 scripts/level11-agent-exam.py --case boreal --label turn-a
# Setelah native berhenti, periksa JSON hasil, bukan exit CLI penilai semata:
python3 scripts/level11-grade.py --case boreal --label first
python3 scripts/level11-grade.py --case atlas --phase checkpoint --label first
# Hanya setelah checkpoint lulus; --resume mempertahankan sesi Atlas:
python3 scripts/level11-agent-exam.py --case atlas --resume --label turn-b
python3 scripts/level11-grade.py --case atlas --phase final --label final
```

Untuk perbaikan TURN A yang belum selesai, gunakan `--resume --prompt` dengan
instruksi TURN A eksplisit: prompt default resume melanjutkan TURN B. Release
immutable hanya boleh bertambah karena bug source nyata, tanpa menghapus database,
checkpoint atau receipt fault lama. Satu lock holder per release mengakhiri dirinya
sendiri. Lost ACK membutuhkan satu POST, crash42/restart nyata, lookup asli dan
validasi hasil; bukan replay submit atau sekadar status health200.

Penilai memeriksa 30 kasus Atlas dan 24 Boreal pada Mac/Linux, regresi UI async
independen, browser nyata pada tiga viewport, serta bukti database/HTTP/container.
Traceback helper penilai yang mencetak sentinel sintetis historis harus dijelaskan
terpisah dan diikat ke CID nonaplikasi serta hash output awal; log aplikasi tetap
wajib bersih. Pengujian tidak memakai chat, token atau data pelanggan nyata.

Setelah grade final lulus, staging panduan memakai
`stage-source-reference-tests.py --verify-only`. `level11-finish-backend.py`
memerlukan kedua laporan final yang lulus, hash kandidat cocok dan tiga lock idle;
aktivasi menjaga layanan lain serta mengembalikan backup jika canary gagal.
`level11-close.py` menyimpan bukti lalu membersihkan objek lab terdaftar saja;
`finalize-level11-report.py` menolak publikasi sebelum cleanup dan health lulus.
ASCII/brand/binary TUI tetap. Panduan backend dan prototype lab dibedakan dalam
[laporan Level 11](LEVEL11-AGENT-EXAM-20261115.json).

Run 15 November lulus 37 pemeriksaan integrasi, 54 kasus engine yang sama pada
Mac/Linux, 17 pemeriksaan browser, dua regresi UI, 15 guard harness dan tiga
simulasi kontrol aktivasi. Panduan aktif dengan 13 canary; seluruh objek lab
dibersihkan dan health lokal/publik200. Contoh hasil sintetis:
[CSV Boreal](assets/exams/level11-20261115/boreal-demo.csv),
[browser 360px](assets/exams/level11-20261115/screen-360.png) dan
[browser 1280px](assets/exams/level11-20261115/screen-1280.png).
