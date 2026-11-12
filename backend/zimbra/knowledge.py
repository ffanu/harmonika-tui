"""Reviewed operational Q&A. No user conversations, credentials or target data."""
VERSION='1.4.3'
SOURCES=['https://zimbra.github.io/documentation/zimbra-10/adminguide.html',
         'https://wiki.zimbra.com/wiki/Fix_the_Zimbra_Collaboration_Permissions']
TLS_SOURCES=['https://docs.python.org/3/library/ssl.html','https://www.rfc-editor.org/rfc/rfc3207',
             'https://www.rfc-editor.org/rfc/rfc8314','https://www.rfc-editor.org/rfc/rfc7208',
             'https://www.rfc-editor.org/rfc/rfc9989','https://eff-certbot.readthedocs.io/en/stable/using.html']
MAILJET_SOURCES=['https://dev.mailjet.com/docs/smtp-relay/configuration',
                 'https://dev.mailjet.com/docs/email-api/getting-started/prerequisites',
                 'https://documentation.mailjet.com/hc/en-us/articles/360043229473-How-can-I-configure-my-SMTP-parameters',
                 'https://documentation.mailjet.com/hc/en-us/articles/360043227053-How-to-reset-my-Secret-Key',
                 'https://zimbra.github.io/documentation/zimbra-10/config-guide.html']


def entry(question,answer,steps,avoid,authorization='read_only'):
    return {'question':question,'answer':answer,'steps':steps,'avoid':avoid,'authorization':authorization}


QA={
 'log_access':entry('Mengapa banyak komponen stopped ketika direktori log tidak dapat diakses?',
    'Akses log/PID yang gagal dapat membuat status keliru sekaligus menghalangi startup. Pulihkan prasyarat sebelum menafsirkan atau menyalakan komponen.',
    ['Periksa metadata, nonsymlink dan akses efektif sebagai zimbra.','Periksa aturan versi vendor terpasang; jangan menganggap 0755 berlaku universal.','Backup ACL/metadata privat, koreksi hanya direktori terbukti rusak dalam otorisasi repair.','Uji akses dan status kembali; start hanya komponen yang masih stopped.'],
    ['chmod/chown rekursif','perbaikan permission seluruh pohon tanpa bukti'], 'scoped_repair'),
 'proxy_backend':entry('Port HTTPS hidup tetapi respons 502; apakah email sudah sehat?',
    'Belum. Listener proxy tidak membuktikan mailbox backend siap.',
    ['Periksa status mailbox, listener backend dan akses runtime.','Perbaiki prasyarat yang terbukti, gunakan start resmi hanya untuk komponen mati.','Verifikasi login HTTPS dengan TLS dan SMTP/IMAP.'],
    ['menyatakan berhasil berdasarkan port','restart proxy sehat tanpa bukti'], 'scoped_repair'),
 'web_route':entry('Mengapa /zimbra/ 404 sementara / membuka login?',
    'Deployment dapat menempatkan portal pada /. Uji jalur aktual dan marker login, bukan asumsi context path.',
    ['GET terbatas pada / lalu /zimbra/ jika diperlukan, dengan TLS terverifikasi.','Pastikan HTTP 200 dan marker halaman login.','Perbaiki probe yang salah bila portal sehat; jangan mengubah deployment demi URL asumsi.'],
    ['menganggap HTML 200 generik sebagai login','restart mailbox atau mengubah Jetty hanya karena context yang tidak dipakai 404']),
 'perl_environment':entry('Controller exit 0 tetapi menulis Can\'t locate Amavis/Boot.pm; apakah berhasil?',
    'Tidak. Pesan kegagalan domain mengalahkan exit 0. Lingkungan shell tanpa login dapat kehilangan PERL5LIB.',
    ['Gunakan controller tetap dalam login zimbra.','Periksa keberadaan modul dan path lingkungan yang diperlukan saja.','Periksa status nyata setelah start; unduh paket hanya jika dependensi benar-benar hilang.'],
    ['menginstal ulang dari pesan nonlogin saja','menampilkan semua environment atau zmlocalconfig -s'], 'scoped_repair'),
 'controller_status':entry('Status controller ada di stderr; bagaimana menentukan hasilnya?',
    'Baca stdout dan stderr yang dibatasi. Running di stderr sah; stopped/error atau output terpotong harus tetap ditangani.',
    ['Gunakan zimbra_component status untuk parsing kedua stream.','Bedakan collection, controller result dan application health.','Hasil unknown atau truncated memerlukan rekonsiliasi.'],
    ['mengabaikan stderr','menganggap exit 0 sebagai bukti seluruh aplikasi sehat']),
 'stats_orphan':entry('Wrapper fd mati tetapi collector root hidup; apakah cukup start ulang atau hapus receipt?',
    'Tidak. Start duplikat akan keluar. Receipt harus cocok dengan proses sebenarnya; menghapusnya dapat menyembunyikan kerusakan kontrol.',
    ['Verifikasi receipt nonsymlink, PID, comm, UID, start ticks dan progres sampel tanpa membaca isi sampel.','Backup metadata privat. Periksa otorisasi process control spesifik target dan komponen.','Jika sudah diizinkan, cocokkan identitas lagi tepat sebelum stop resmi collector fd saja.','Verifikasi proses lama berakhir; start resmi statistik dalam login zimbra agar komponen sehat dilewati.','Verifikasi wrapper/collector baru, dua snapshot sehat dan progres sampel.'],
    ['PID palsu atau penghapusan receipt untuk status hijau','stop/restart semua statistik atau layanan mail sehat','menggunakan izin satu target sebagai izin global'], 'specific_process_control'),
 'pid_identity':entry('Apakah PID yang masih ada pasti proses yang dimaksud?',
    'Tidak. PID bisa dipakai ulang, salah pemilik, atau zombie. Metadata proses harus cocok dengan receipt dan tujuan operasi.',
    ['Periksa nonsymlink, angka PID, comm, UID, state dan start ticks.','Revalidasi identitas sesaat sebelum mutasi.','Bila berubah, hentikan rencana dan rekonsiliasi.'],
    ['mengirim signal hanya berdasarkan nomor PID lama','menghapus receipt hanya berdasarkan umur']),
 'unknown_execution':entry('SSH timeout setelah start; apakah boleh langsung mengulang?',
    'Tidak. Timeout tidak membuktikan operasi belum terjadi. Jangan klaim dibatalkan atau aman diulang.',
    ['Periksa execution_state dan receipt resmi.','Rekonsiliasi status, identitas proses dan health melalui panggilan baca.','Lanjutkan hanya dari keadaan yang terbukti; jangan rollback proses dengan restore PID lama.'],
    ['retry buta','kompensasi pada execution unknown','restore receipt proses mati']),
 'probe_transport':entry('Probe gagal sebelum dijalankan karena command melewati batas?',
    'Source pendek bisa membesar ketika di-quote. Gunakan transport kompresi untuk probe tepercaya dengan batas command tetap.',
    ['Bedakan transport failure dari service failure.','Gunakan fixed probe terkompresi dan validasi ukuran hasil akhir.','Uji payload berisi quote dan pastikan cap 8192 tetap berlaku.'],
    ['menaikkan cap untuk menutupi bug quoting','menjalankan script bebas dari konten remote']),
 'tool_discovery':entry('Tool baru tidak ditemukan di sesi lama; harus mencari berulang?',
    'Tidak. Proses MCP yang sudah hidup dapat mempertahankan daftar tool lama.',
    ['Lakukan satu discovery.','Gunakan pemeriksaan SSH sempit yang sudah tersedia atau checkpoint dan sesi baru setelah reload.','Controller resmi sudah melacak daemon; UI jobs metadata bukan bukti eksekusi.'],
    ['pencarian tool identik berulang tanpa fakta baru','menjalankan ulang mutasi demi metadata job']),
 'systemd_history':entry('zmcontrol sehat tetapi systemd masih failed; apakah perlu restart?',
    'Flag historis tidak selalu menunjukkan kegagalan aplikasi saat ini. Bedakan timestamp, konfigurasi unit dan kesehatan nyata.',
    ['Periksa status unit dan signature jurnal teredaksi.','Untuk logrotate gunakan debug terlebih dahulu.','Catat flag lama dan batas bukti; perbaiki penyebab nyata tanpa menyembunyikan kegagalan.'],
    ['reset-failed sebagai bukti repair','rotasi paksa atau reboot tanpa kebutuhan'], 'scoped_repair'),
 'success_criteria':entry('Kapan ujian dianggap selesai?',
    'Pisahkan kesehatan komponen, protokol, stabilitas dan delivery. Panduan ini bukan training model atau izin tindakan otomatis.',
    ['Periksa seluruh komponen yang diharapkan dan receipt statistik.','Verifikasi HTTPS login/TLS, SMTP EHLO dan IMAP TLS.','Bandingkan dua pengamatan dan progres sampel jika statistik relevan.','Laporkan delivery atau reboot persistence belum diuji jika tidak ada bukti.'],
    ['mengklaim email terkirim dari EHLO','menganggap satu snapshot membuktikan stabilitas','menyimpan percakapan atau credential sebagai pengetahuan']),
}

QA.update({
 'tls_certificate':entry('Apakah HTTPS200 atau port terbuka membuktikan SSL sehat?',
    'Tidak. Periksa rantai kepercayaan, hostname/SNI, notBefore/notAfter dan protokol untuk setiap layanan. Sertifikat web sehat tidak membuktikan sertifikat SMTP/IMAP sehat.',
    ['Gunakan trust store dan verifikasi hostname; catat expiry/fingerprint tanpa membaca private key.','Pisahkan expired, not-yet-valid, hostname mismatch, unknown CA dan timeout.','Ulangi dari perspektif eksternal jika hanya loopback yang diuji; renewal membutuhkan pemeriksaan terpisah.'],
    ['verify=False atau curl -k sebagai bukti lulus','mengganti sertifikat layanan sehat tanpa bukti'], 'read_only'),
 'smtp_starttls':entry('Apakah iklan STARTTLS saja cukup dan port465 wajib selalu aktif?',
    'Tidak. Negosiasikan TLS, verifikasi certificate/hostname, lalu EHLO ulang. Port465 memakai implicit TLS, sedangkan25/587 STARTTLS. Port opsional tertutup bukan otomatis kegagalan server.',
    ['Verifikasi EHLO250 sebelum TLS, STARTTLS aktual dan EHLO250 sesudahnya.','Periksa tiap endpoint yang diharapkan; bedakan optional disabled dari enabled dengan sertifikat rusak.','Tanpa AUTH, MAIL, RCPT atau DATA; jangan mengklaim relay policy atau delivery sudah teruji.'],
    ['menganggap EHLO sebelum TLS tetap berlaku sesudah upgrade','mengirim kredensial atau email untuk tes handshake']),
 'dns_mail_policy':entry('SPF memuat IP eksplisit lain; apakah pasti salah? DMARC none harus diubah reject?',
    'Belum tentu. Mekanisme a/mx/include/redirect dapat mengizinkan pengirim. Butuh evaluasi penuh dengan identitas pengirim; p=none adalah kebijakan yang sah dan tidak boleh diubah otomatis.',
    ['Periksa A/AAAA, MX, PTR dan jumlah record SPF/DMARC; simpan metadata, bukan alamat email reporting.','Pisahkan NXDOMAIN/no-data dari timeout/SERVFAIL/EDE resolver; bandingkan resolver jika bukti berbeda.','Jangan simpulkan gagal dari ip4 literal saja; catat evaluasi SPF dan alignment DMARC belum dilakukan.','DMARC record exact tidak membuktikan hasil tree-walk/inheritance atau hasil pesan. DKIM butuh selector dan bukti signing terpisah.'],
    ['mengubah DNS atau memaksa p=reject dari audit saja','menebak selector DKIM','menyatakan DNS ada berarti email terkirim']),
 'tls_policy_evidence':entry('TLS1.0 handshake gagal pada client; apakah berarti server menolaknya?',
    'Tidak selalu. Client dapat menonaktifkan protokol/cipher lama sendiri. Negosiasi TLS1.3 juga tidak membuktikan semua protokol lama ditolak server.',
    ['Catat kemampuan client, protokol yang dipaksa dan alasan kegagalan.','Hanya simpulkan versi ditolak jika bukti server memadai; selain itu unknown/not tested.','HSTS adalah hardening web yang perlu dampak dipertimbangkan, bukan tes delivery email.'],
    ['menyamakan no ciphers available pada client dengan penolakan server','mengklaim semua cipher aman dari satu handshake']),
 'renewal_readiness':entry('Timer certbot aktif berarti renewal dan deploy Zimbra pasti berhasil?',
    'Tidak. Timer hanya bukti penjadwalan. ACME issuance, deploy hook Zimbra dan sertifikat yang disajikan adalah tahapan berbeda.',
    ['Periksa state, OnCalendar dan versi Certbot tanpa environment/key/config sensitif.','Bandingkan jadwal dengan masa berlaku dan ambang renewal versi terpasang: sebelum4.0 default30hari, versi baru dapat memakai proporsi lifetime/ARI; periksa override dan scheduler lain. Jadwal bulanan berisiko memberi sedikit kesempatan retry.','Catat masa berlaku sertifikat yang benar-benar disajikan tiap endpoint.','Uji renewal/deploy hanya dalam otorisasi terpisah dan verifikasi ulang fingerprint layanan setelah perubahan.'],
    ['menjalankan certbot renew/dry-run/deploy sebagai tes baca','mengklaim timeraktif sebagai bukti renewal mendatang berhasil'], 'read_only'),
 'external_perspective':entry('Loopback lulus tetapi akses luar timeout; apakah Zimbra harus direstart?',
    'Tidak langsung. Perspektif jaringan berbeda: DNS, rute, NAT, firewall atau jaringan client dapat menjelaskan perbedaan.',
    ['Bandingkan host/SNI, alamat tujuan, port dan waktu observasi loopback versus client eksternal.','Catat timeout berbeda dari cert verification failure.','Jika aplikasi sehat lokal, investigasi jalur jaringan secara baca; jangan restart atau ubah firewall tanpa bukti dan scope.'],
    ['menyebut layanan sehat global dari localhost saja','menyalahkan sertifikat dari timeout jaringan']),
})

FINDING_TOPICS={'ZIMBRA_LOG_ACCESS_DENIED':'log_access','ZIMBRA_COMPONENTS_STOPPED':'controller_status',
                'ZIMBRA_PROXY_BACKEND_FAILURE':'proxy_backend','ZIMBRA_STATS_RECEIPT_INCONSISTENT':'stats_orphan',
                'ZIMBRA_STATUS_INCOMPLETE':'success_criteria'}

QA.update({
 'mailjet_account_audit':entry('Tombol Generate Secret Key Mailjet tidak berhasil; terus klik lagi?',
    'Tidak. Periksa prasyarat akun dan hasil aksi terlebih dahulu. Flag verifikasi email, bisnis, peran primary/subaccount, izin API dan kuota harus dibedakan dari kesalahan adapter browser.',
    ['Gunakan mailjet_credentials audit_account pada tab Mailjet yang tepat, bukan snapshot yang dapat membocorkan key. Baca Account/SMTP settings lewat tautan yang benar-benar diamati.',
     'requiresEmailVerification=true adalah prasyarat yang teramati, bukan bukti tunggal bahwa backend menolak request. Bedakan verifikasi akun dari verifikasi keamanan per operasi.',
     'isMaster=true dan canCreateApiKey=true tidak menunjukkan kekurangan izin. Paket Free dan batas2 key bukan bukti kuota habis tanpa jumlah pemakaian.',
     'Jangan menyimpulkan layanan rusak dari invalid_tab_id atau klik custom element tanpa efek. Koreksi argumen/adapter, observasi ulang, lalu rekonsiliasi; jangan ulang mutasi credential yang hasilnya unknown.',
     'Generate/reset memerlukan izin khusus; simpan hasil sekali tampil langsung ke reference privat, bukan chat, screenshot, clipboard, log atau knowledge. Email/OTP hanya boleh diproses melalui adapter khusus bila pemilik secara eksplisit mengizinkan pesan verifikasi tersebut; jika tidak, tunggu pemilik.'],
    ['fiksasi satu tombol','menganggap klik dispatched berarti key dibuat','mengulang reset tanpa rekonsiliasi','mengatakan email akun pasti belum verified hanya dari satu flag'], 'specific_credential_generation'),
 'mailjet_relay':entry('Bagaimana menghubungkan Zimbra ke Mailjet relay?',
    'Gunakan SMTP in-v3.mailjet.com:587 dengan STARTTLS dan verifikasi certificate/hostname. API Key adalah username, Secret Key adalah password; URL HTTP Send API bukan relayhost.',
    ['Periksa zimbra_mailjet_plan dan prasyarat akun sebelum mengganti routing. AUTH tanpa kirim pesan dahulu menggunakan credential reference privat.',
     'Gunakan server-scoped atribut zmprov yang didukung Zimbra10. Jangan edit generated main.cf sebagai konfigurasi persisten.',
     'Pilih map database yang benar-benar tersedia. Lookup key password dan TLS policy harus cocok dengan [in-v3.mailjet.com]:587, termasuk bracket dan port; jangan tampilkan hasil postmap -q credential.',
     'Verifikasi seluruh domain envelope sender dengan API key yang sama, SPF/DKIM dan DMARC sebelum aktivasi. Jangan mengubah inbound MX atau melonggarkan relay restrictions.',
     'Backup atribut/map privat; amati zmconfigd dan effective settings, lalu uji pesan hanya ke alamat pemilik yang diotorisasi. SMTP accepted berbeda dari delivery.'],
    ['menggunakan API Key tanpa Secret Key','AUTH sebelum TLS','membuka open relay','flush/delete antrean massal','mengarang sender verified atau delivery'], 'scoped_relay_configuration'),
 'mailjet_provisioning':entry('Akun Mailjet baru masih provisioning pending; bagaimana melanjutkan tanpa salah akun atau server?',
    'Amati jalur dan status akun saat ini terlebih dahulu. Perpindahan ke onboarding bukan bukti akun sudah aktif, SMTP AUTH berhasil, atau relay terpasang.',
    ['Cocokkan tab akun baru dan reference credential baru. Jangan memakai key/grant akun lama atau merotasinya untuk menyelesaikan onboarding akun lain.',
     'Bedakan chrome_apple_events_javascript_disabled, macos_automation_permission_required, browser_script_error dan status provider. Satu error adapter tidak membuktikan akun Mailjet bermasalah. Jika opsi dicentang tetapi JavaScript tetap ditolak, gunakan mailjet_native status pada Mac untuk observasi aksesibilitas yang dibatasi origin/path; bukan cookies/CDP atau perubahan izin diam-diam. Jangan ulang discovery tanpa perubahan keadaan.',
     'Jika pemilik mengizinkan email verifikasi tertentu, batasi adapter pada tab inbox dan pesan Mailjet yang sesuai. Proses OTP/link langsung secara privat; verifikasi origin tujuan dan jangan mengeluarkan token, isi email atau data mailbox lain ke model/log/Q&A.',
     'Lengkapi onboarding hanya memakai informasi pemilik yang terbukti. Jangan mengarang identitas bisnis, menyetujui paket berbayar atau menganggap email verified sama dengan approval pengiriman.',
     'Izin mengisi dan mengajukan verifikasi yang sudah diberikan tetap berlaku. Gunakan informasi bisnis publik yang terverifikasi untuk isian rutin, jelaskan rencana SMTP Zimbra secara jujur, dan jangan meminta konfirmasi berulang untuk fakta yang sudah ada. Jangan mengarang angka pemakaian, sumber database atau bukti opt-in.',
     'Pada formulir native, wrapper dan input sebenarnya dapat menjadi dua elemen aksesibilitas pada posisi yang sama. Tulis AXValue pada input yang benar dan verifikasi nilainya melalui observasi baru sebelum submit; field tampak terisi atau klik dispatched bukan bukti request diterima. Simpan receipt mekanik dan rekonsiliasi hasil sebelum percobaan lain.',
     'Your ticket was successfully submitted dan status under review membuktikan pengajuan diterima, bukan akun aktif. Jika halaman API menampilkan Account activation is required, gunakan mailjet_native status untuk membuktikan gate tersebut; hentikan loop Show/Generate. Tunggu approval provider, bukan reset key, kirim ulang tiket atau bypass verifikasi.',
     'Pastikan target relay dari instruksi pemilik dan alias SSH/DNS sebelum memasang relay. Tab inbox verifikasi boleh berada pada server berbeda dan bukan bukti tujuan relay. Bila target ambigu, konfirmasikan; jangan memakai target lama secara otomatis atau mencoba ulang password ke host lain.',
     'Aktivasi membutuhkan bukti provider, pasangan credential privat, SMTP AUTH melalui TLS dan verifikasi domain pengirim dengan key yang sama. Catat blocker persisnya dan lanjutkan dari checkpoint setelah prasyarat tersedia.'],
    ['mengklaim aktif hanya dari URL onboarding','mencampur key akun baru dan lama','menerapkan relay pada server ujian lama tanpa kecocokan target','membaca seluruh inbox untuk satu OTP'], 'specific_account_activation'),
 'browser_account_capture':entry('Aplikasi memakai Shadow DOM dan secret hanya muncul sekali; bagaimana membaca dengan benar?',
    'Gunakan adapter khusus origin yang memproses DOM lokal dan menyimpan secret ke file0600 tanpa mengembalikan nilainya ke model. Kontrol web component perlu target, state dan postcondition aktual.',
    ['Batasi origin dan tab; inspeksi metadata tersanitasi, open Shadow DOM dan frame same-origin saja. Tidak menyalin cookies/profil atau mengaktifkan CDP.',
     'Bedakan API key yang disembunyikan, secret belum dibuat, dan secret yang sudah pernah ditampilkan. Jangan otomatis merotasi key untuk mengatasi keterbatasan pembacaan.',
     'Sebelum aksi account-changing, simpan grant scoped dan status dispatch. Pada unknown hasil, audit akun/DOM lebih luas, jangan replay tombol yang sama.',
     'Lakukan satu discovery; jika runtime lama tidak mengenal tool/action baru, checkpoint dan gunakan sesi ujian terisolasi baru. Koreksi argumen lokal yang jelas sekali, bukan meminta pengguna memperbaiki kesalahan adapter.'],
    ['dump DOM/screenshots dengan credential ke trace','membaca storage/session cookies','mencampur account/email/customer data ke Q&A bersama']),
 'ssl_automation':entry('Bagaimana membuat SSL gratis Let\'s Encrypt diperbarui otomatis setiap bulan?',
    'Gunakan pemeriksaan dua kali sehari dan renew hanya saat due menurut versi/policy Certbot. Jangan memaksa issuance bulanan. Sertifikat baru harus diverifikasi dan diterapkan ke Zimbra, lalu fingerprint setiap endpoint diperiksa.',
    ['Gunakan zimbra_ssl_plan untuk preflight baca: ConfigObj, challenge, hook, port80 dan validitas sumber.',
     'Pisahkan otorisasi maintenance proxy/SSL dari izin recovery fd yang lama. Standalone membutuhkan port80 bebas; webroot harus terbukti untuk semua SAN.',
     'Uji parser Certbot terpasang untuk override hook config; --no-directory-hooks saja tidak menonaktifkan hook config. Staging dry-run tetap bisa menjalankan pre/post hooks.',
     'Gunakan worker scoped, lock, receipt staging dan SQLite empat field mekanik; pertahankan seluruh SAN, verifikasi trust/hostname/SPKI RSA atau EC sebelum vendor deploy.',
     'Skip jika belum due dan fingerprint source/deployed/served sama. Perubahan source yang belum terdeploy tetap perlu rekonsiliasi walau tidak due.',
     'Timeout mutasi memerlukan rekonsiliasi, bukan retry buta. Kegagalan deployment yang terkonfirmasi memakai backup dan redeploy resmi.',
     'Aktifkan jadwal hanya setelah staging dan izin maintenance terbukti. Timer aktif bukan bukti renewal end-to-end.'],
    ['force-renewal setiap bulan','hook global tanpa scope lineage','restart sehat pada setiap pemeriksaan','mengklaim aktivasi dari unit file atau mock test'], 'specific_ssl_service_control'),
 'certbot_config_format':entry('ConfigParser menolak renewal .conf; apakah konfigurasi Certbot rusak?',
    'Belum tentu. Certbot memakai ConfigObj yang mengizinkan key top-level sebelum section. Gunakan parser yang sesuai dan keluarkan hanya field teknis yang diizinkan.',
    ['Validasi metadata file dan field terpilih; jangan tampilkan account/email/hook shell atau credentials.',
     'Bedakan mode symlink live0777 dari mode target key0600 di archive; validasi resolusi tetap di lineage terdaftar.',
     'Periksa perilaku versi terpasang dengan fixture sintetis, tanpa mengeksekusi hook/ACME.'],
    ['menulis ulang file valid karena salah parser','chmod rekursif seluruh letsencrypt','mencetak private key atau seluruh konfigurasi']),
})


def lookup(topic='index'):
    if topic=='index':
        return {'ok':True,'version':VERSION,'topics':[{'id':key,'question':value['question']} for key,value in QA.items()],
                'privacy':'Reviewed system mechanics only; no chat history, target credentials or customer data.'}
    if not isinstance(topic,str) or topic not in QA:return {'ok':False,'error':'unknown_zimbra_qa_topic'}
    sources=MAILJET_SOURCES if topic in ('mailjet_account_audit','mailjet_relay','mailjet_provisioning','browser_account_capture')else TLS_SOURCES if topic in ('tls_certificate','smtp_starttls','dns_mail_policy','tls_policy_evidence','renewal_readiness','external_perspective','ssl_automation','certbot_config_format')else SOURCES
    return {'ok':True,'version':VERSION,'topic':topic,**QA[topic],'sources':sources,
            'basis':'Reviewed engineering rules from supervised failure/recovery tests; verify installed version before acting.',
            'grants_authorization':False,'runs_commands':False,'trains_model':False}


def guidance(findings):
    topics=list(dict.fromkeys(FINDING_TOPICS[f['code']] for f in findings if f.get('code') in FINDING_TOPICS))
    # A root collector still running is a prerequisite to reconcile, not an
    # invitation to repeat the generic start advice for a stopped stats wrapper.
    priority={'log_access':0,'stats_orphan':1,'proxy_backend':2,'controller_status':3,'success_criteria':4}
    topics.sort(key=lambda topic:priority.get(topic,5))
    return [lookup(topic) for topic in topics]
