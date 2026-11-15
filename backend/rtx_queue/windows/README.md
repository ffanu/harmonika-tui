# Windows RTX: autostart dan pemulihan

Untuk arsitektur dua PC: Linux tetap gateway/auth/antrean, Windows RTX menjalankan
ComfyUI dan gateway media lokal. Tidak ada model GPU dipasang di Linux.

Masalah yang ditemukan pada 12 November 2026: task Gateway dan Comfy memakai
`ExecutionTimeLimit=PT72H`, tanpa recovery. Task sudah berhenti dengan hasil
`0x00041306`; API 18200/18190 tidak berjalan. Batas 72 jam merupakan penyebab yang
konsisten dengan keadaan tersebut, bukan bukti PC/GPU mati.

## Komponen

- `Install-RTXAutostart.ps1`: backup XML privat, autostart saat boot sebagai
  SYSTEM tanpa login, batas waktu PT0S, failure recovery, IgnoreNew, kondisi
  listrik, serta DACL task khusus SYSTEM/Administrator.
- `Supervisor.ps1`: Gateway/Comfy berjalan tanpa jendela, mengulang proses yang
  keluar dengan backoff 5–60 detik, termasuk clean exit. Mutex global dan cek
  listener mencegah instance/model GPU ganda. Proses lain tidak diadopsi/dihentikan.
- `ChildRuntime.py`: mengarahkan Python dan Windows standard handles ke NUL
  sebelum aplikasi dimuat. Output tidak disimpan, dan render tetap dapat berjalan
  jika kanal output pengawas terputus. Exit code aplikasi tetap diteruskan.
- `Watchdog.ps1`: boot + tiap menit, menyalakan task yang berhenti dan memulihkan
  task utama yang nonaktif. Tidak menghentikan task Running/Queued.
- `Test-RTXAutostart.ps1`: pemeriksaan konfigurasi dan tes nyata child exit 7,
  restart/backoff, task disposable nonaktif→aktif, SYSTEM, maintenance dan ACL.
- `Repair-ModelReferences.ps1`: tautan enam encoder/VAE dari model vault yang
  sudah ada. Tidak mengunduh, memindah atau mengganti file bobot. Tautan lama
  yang rusak disimpan sebelum diganti; file yang sudah terbaca dipertahankan.

Empat task utama: `HarmonikaGateway`, `HarmonikaComfy`, `HarmonikaFluxWorker`,
`HarmonikaRTXImageWorkerAuto`. Task `HarmonikaRTXWatchdog` mengawasi keempatnya.
Tindakan Flux/image worker existing dipertahankan; Gateway/Comfy memakai
supervisor baru untuk peluncuran berikutnya. Pembaruan task Running tidak
mematikan proses saat ini.

## Pasang dari PowerShell Administrator

Simpan script bersama dalam folder staging privat milik operator. Jangan
masukkan API key, password atau data pelanggan ke argumen/script.

```powershell
.\Install-RTXAutostart.ps1 -ApplicationRoot 'C:\Users\NAMA_AKUN' -VerifyOnly
.\Install-RTXAutostart.ps1 -ApplicationRoot 'C:\Users\NAMA_AKUN'
.\Test-RTXAutostart.ps1
```

Root instalasi default: `C:\ProgramData\Harmonika\RTXKeepAlive`.
`components.json` hanya jalur runtime, port dan opsi proses; tidak berisi token.
Task dan runtime config tidak dapat diubah akun biasa. Administrator Windows
tetap memiliki kendali; mekanisme ini tidak mencoba melawan Administrator,
menonaktifkan keamanan Windows, menyembunyikan layanan atau menghalangi maintenance.

Boot trigger diberi jeda 20 detik. Retry proses dibatasi backoff agar tidak
membanjiri CPU/log. Watchdog memulihkan task sekitar satu menit setelah task
berhenti/nonaktif; scheduler Windows dapat menunda ketika resource sibuk.
Recovery proses tidak mengirim ulang job gambar/video. Job yang hasilnya belum
pasti tetap harus direkonsiliasi dengan ID yang sama.

## Maintenance dan backup

Administrator dapat membuat `maintenance.enabled` di root instalasi. Watchdog
tidak akan mengaktifkan task, dan supervisor tidak memulai proses pengganti.
Flag ini tidak mematikan proses yang sedang berjalan. Untuk penghentian yang
disengaja, periksa antrean/job dahulu dan gunakan kontrol Task Scheduler sebagai
operator. Hapus flag setelah maintenance untuk melanjutkan pemulihan otomatis.

Backup konfigurasi/task ada di `backups\TIMESTAMP`. Backup awal sebelum
perubahan batas 72 jam juga disimpan. Pemulihan XML dilakukan operator pada
waktu maintenance; jangan memulihkan file database/job/customer data.

State di `state\` hanya mekanika: waktu, komponen, PID yang dimulai sendiri,
jumlah launch, exit code dan backoff. Output/error aplikasi dikuras tanpa ditulis
oleh supervisor; tidak ada prompt, gambar, chat, API key atau raw exception di
state watchdog. File state berukuran tetap per komponen; tidak menjadi log tak
terbatas. Log lama milik aplikasi tidak dihapus.

## Temuan model setelah aktivasi

API kembali hidup, tetapi uji generate menemukan encoder/VAE tidak terdaftar
di ComfyUI. Enam bobot yang diperlukan ternyata masih berada di model vault D:.
Referensi file diperbaiki dengan symbolic link sehingga nama yang dipakai
workflow kembali tersedia, tanpa restart proses lain atau unduhan multi-GB.
Uji beban selanjutnya menemukan dynamic VRAM tertahan pada `HostBuffer.read_file_slice`
ketika membaca bobot FLUX. Profil Comfy menggunakan `--disable-dynamic-vram`
agar offload memakai pengelolaan memori statik. Opsi `--lowvram` dilepas karena
memaksa text encoder ke CPU pada mode statik; reserve/headroom dan CPU VAE tetap
dipakai. Hanya proses Comfy
yang dimulai oleh sesi operator ini boleh diperbarui setelah prompt uji diverifikasi
dan dibatalkan berdasarkan ID. Tidak ada global interrupt atau reset driver GPU.
Profil bridge `fluxhd` diperlakukan sebagai preset kualitas untuk RTX 12 GiB:
memakai FLUX.2 Klein lokal dengan jadwal distilled empat langkah. Preset persegi
512/768 dirender minimum 1024; dimensi aktual dikembalikan pada artifact.
Batas minimum file 2 MiB milik bridge dipertahankan: PNG kecil dienkode ulang
lossless tanpa kompresi, dengan piksel identik, tanpa padding. Ukuran byte
bukan ukuran kualitas visual. Pilihan model `flux`
eksplisit serta automatic picker lain tetap dipertahankan. Helper pemilihan
profil berada di `image_profile.py`, dengan pengujian pure di
`tests/test_rtx_image_profile.py`. Encoder/VAE Klein diarahkan ke salinan SSD
yang sudah ada setelah pemeriksaan ukuran dan header; bobot tidak disalin atau
diunduh ulang. Backup gateway dibuat sebelum patch dengan guard hash baseline.
Patch integrasi gateway terpasang pada deployment Windows yang sudah ada;
installer task hanya mengelola runtime dan task, bukan mengganti source gateway.
Diff integrasinya tersimpan di `GatewayImageProfile.patch`. Baseline SHA-256
gateway sebelum perubahan adalah
`6ab5ee8e1037d7d17ce035d32a5d15e913cc3fc68224268912d5597c5a766fd2`;
hasil patch terpasang
`07d6309c1946e9c85399a169b96403dc0843ad6f7b847dec6f735d58cf6428eb`.
Jangan menerapkan ulang patch pada versi source lain tanpa review.
Kesiapan API dipisahkan dari kesiapan model: HTTP 200 `/health` saja tidak
membuktikan `engine_ready.image.generate=true` atau render benar-benar lulus.

## Bukti dan batas

Lihat [laporan aktivasi](../../../docs/RTX-AUTOSTART-REPORT.json). Tes dijalankan
di Windows nyata, termasuk ulang di task SYSTEM noninteractive. Reboot fisik
tidak dilakukan untuk pengujian; boot trigger/config dan launch SYSTEM sudah
diperiksa. Pemulihan setelah child crash diuji dengan fixture sendiri, tanpa
menghentikan job/proses produksi lain.

Kualifikasi akhir: 64 pemeriksaan PowerShell dan tiga tes peluncur Python lulus
sebagai SYSTEM. Delapan tes profil gambar juga lulus (75 pemeriksaan unik;
tes peluncur yang diulang pada Mac tidak dihitung dua kali). Satu job sintetis
melalui API Linux selesai dalam 27,45 detik, mengembalikan PNG 1024×1024
berukuran 3.147.861 byte; signature, MIME dan checksum unduhan diverifikasi.
Ini membuktikan jalur `image.generate`, bukan seluruh skill audio/video.

## Broker video privat

`Install-VideoToolkitAutostart.ps1` memasang task tambahan
`HarmonikaVideoToolkit` untuk broker WanGP/FramePack yang sudah terpasang pada
Windows loopback 18251. Supervisor dan watchdog yang sama dipakai tanpa mengganti
empat task gambar/gateway. Boot SYSTEM, PT0S, IgnoreNew, maintenance dan backup
tetap berlaku. Tidak ada model baru diunduh oleh installer ini.

GitPython pada WanGP membutuhkan Git yang tersedia bagi SYSTEM. Lokasi Git dan
direktori FFmpeg ditetapkan hanya pada environment komponen video; PATH sistem
dan user tidak diubah. Validasi impor dan `get_default_settings` sebagai pengguna
saja tidak cukup: lakukan probe yang sama sebagai SYSTEM, berikut `git --version`
dan `ffmpeg -version`, sebelum render nyata.

Menghentikan task supervisor tidak menjamin child durable ikut berhenti. Perubahan
environment berlaku pada peluncuran child berikutnya. Jangan menganggap task
restart sebagai bukti runtime sudah berganti; periksa PID/birth time dan listener.
Jangan menghentikan listener milik operator lain. Pemulihan child milik sesi
pengujian memerlukan identitas terverifikasi serta antrean terminal/kosong.

Referensi Microsoft: [pengaturan Scheduled Task](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasksettingsset?view=windowsserver2025-ps),
[boot/repetition trigger](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasktrigger?view=windowsserver2025-ps),
[keamanan task dan DACL](https://learn.microsoft.com/en-us/windows/win32/taskschd/security-contexts-for-running-tasks).
