# Upgrade generator Windows RTX

Status: **aktif dan terverifikasi** untuk default generator web/API/TUI pada 25 Oktober 2026.

Codex menangani runtime/model/aktivasi; OpenCode mengaudit kontrak dan worker.
Claude tidak dipanggil. Pekerjaan ini hanya generator gambar, bukan aktivasi
vision, edit referensi, atau perubahan pipeline CS.

## Arsitektur

- VPS tanpa GPU: API, auth/ownership, antrean, validasi artifact dan routing.
- Windows RTX 5070 12 GB / RAM sekitar62 GB: ComfyUI dan inferensi lokal.
- ComfyUI: loopback18190, hanya core nodes, offline, metadata PNG dimatikan.
- Adapter kompatibel: loopback18191, `/health` + `/generate`.
- Tunnel baru: VPS loopback18211 → Windows loopback18191.
- Port18192 telah dipakai layanan lain dan tidak diubah.
- Worker/tunnel lama18181/18182 tetap tersedia untuk rollback; tidak dihentikan.

## Model dan sumber

- FLUX.2 Klein4B distilled FP8 resmi, Apache2.0.
- Diffusion revision `5b4408e59397a4a37ccb46afe426d8ed86379441`.
- Encoder Qwen3 4B FP4 untuk Flux2 dan VAE dari Comfy-Org, revision
  `533c4cbc3d3b89af1743d9466c434e89810391c0`.
- ComfyUI dipin ke commit `4281eb0a1478ce445dc32c4b0895f14c9fc3c1be`.
- Installer memverifikasi SHA-256 semua bobot. Native CUDA torch2.11/cu128
  yang sudah bekerja dipakai melalui path read-only dari venv baru. Dependensi
  baru dipasang ke venv baru; paket worker lama tidak diubah.
- Unduhan dapat dilanjutkan per rentang, 12 koneksi; Ethernet dibind hanya untuk
  socket pengunduh. Tidak ada perubahan route, DNS, firewall atau driver global.

Sumber: [model FP8](https://huggingface.co/black-forest-labs/FLUX.2-klein-4b-fp8),
[encoder dan VAE](https://huggingface.co/Comfy-Org/Flux-2-Klein-4B),
[workflow resmi](https://docs.comfy.org/tutorials/flux/flux-2-klein).

## Kontrak dan batas

Prompt hingga12.000 byte UTF-8 tanpa pemotongan diam-diam. Satu inferensi aktif;
permintaan bersamaan ditolak sebelum eksekusi. Distilled memakai4 step/CFG1,
bukan preset SD-Turbo lama. Preset persegi512/768 dirender1024 dan ukuran artifact
mengikuti PNG sebenarnya. Aspect ratio lain dipertahankan dalam batas512–1024.

Worker mengirim whitespace JSON setiap4 detik sehingga read timeout15 detik
pada adapter tidak menganggap inferensi yang berjalan sebagai koneksi mati.
Jika pekerjaan GPU tidak pasti, worker menutup admission dan health degraded;
hanya terminal history dari job yang sama dapat membuka kembali admission.
Tidak ada blind retry atau global interrupt pada pekerjaan lain.

Persetujuan teknis+visual diperlukan sebelum aktivasi. Setelah reboot, model
memerlukan warmup sebelum health dianggap ready. PC offline tetap mengikuti
fallback yang sudah ada; tidak ada klaim generator lokal tersedia saat PC mati.

## Verifikasi

-10 tes worker: validasi, prompt panjang, status jujur, single-flight, health tetap
 responsif saat render, penolakan saat job tidak pasti.
-4 tes installer: resume, checksum mismatch, existing verified model, salah range.
-14 tes router legacy pada staged endpoint.
-Lima render nyata pada profil akhir lulus secara teknis:17,88 /14,52 /14,55 /14,55 /14,55 detik; rata-rata15,21 detik.
-Puncak penggunaan seluruh GPU9.619 MiB, termasuk aplikasi/model lama; idle sekitar5.852 MiB bebas. Headroom dynamic2 GiB, reserve2 GiB dan aggressive offload aktif.
-SHA semua5 contoh lokal cocok dengan receipt. Review visual menyetujui upgrade umum; jumlah antena pada satu contoh tidak tepat, sehingga fidelitas produk presisi belum dijamin.
-Stdio MCP → HTTPS → antrean → RTX lulus20,886 detik.
-Perintah asli `harmonika run --session 5846390` berhasil menyimpan PNG1024 di Downloads (2.103.624 byte).
-Jalur engine chat web + URL preview HTTPS lulus22,95 detik; HTTP200/image/png. UI browser setelah login tidak diuji interaktif.
-Owner adapter lama dipanggil langsung operator dan menghasilkan PNG1024 melalui RTX,19,42 detik.
-Boot warmup teruji: health belum ready sebelum pemanasan, lalu ready setelah render lokal sukses.
-Lihat [receipt lengkap](RTX-FLUX-UPGRADE-REPORT.json).

## Aktivasi dan rollback

`scripts/deploy-flux-model.py` hanya berjalan di VPS sebagai operator setelah
receipt lima render dan review visual tersedia. Baseline hash mencegah menimpa
perubahan baru. Aktivasi menunggu queue idle dan tidak ada request engine aktif.
Backup konfigurasi/router/owner bridge privat disimpan sebelum perubahan.
Hanya engine dan media queue terkait yang direstart, database tidak dimigrasi.
Jika pemeriksaan gagal, file konfigurasi dikembalikan dan layanan dinyalakan
kembali. Service lain dan proses pengguna tidak dihentikan.

## Hasil dan batas visual

Contoh nyata berada di `~/Downloads/harmonika-flux-qualification/`. Hasil dari
perintah terminal tersimpan di `~/Downloads/indonesia-masa-depan/indonesia-2045-flux-20261025.png`.
Model lebih mengikuti komposisi, arsitektur, suasana dan teks pada fixture
dibanding SD-Turbo lama. Ini bukan jaminan jumlah objek, logo atau anatomi selalu
tepat. Landmark pada ilustrasi futuristis tidak menjadi bukti geografis.

Tidak ada driver/GPU/DRM yang dipasang pada VPS CPU. ComfyUI berjalan pada
Windows, memakai CUDA torch2.11/cu128 yang telah bekerja; warning backend kernel
CUDA130 yang lebih optimal tidak berarti inferensi saat ini berjalan pada CPU.

## Rollback operator

Backup aktivasi: `/var/backups/harmonika-media/flux-20261025T040553Z`.
Sebelum rollback, pastikan queue tidak memiliki job aktif/unknown dan engine
tidak memiliki request aktif. Verifikasi source belum berubah setelah receipt.
Hentikan hanya media queue; pulihkan `router` ke `lib/rtx-image-router.js`,
`bridge` ke owner adapter dan `config` ke `/etc/harmonika-ai/rtx-queue/config.json`.
Restart `harmonika-ai` dan media queue terkait, lalu periksa health endpoint lama
18182 dan preview. Jangan restore database atau menghapus job/customer data.
Worker Windows lama tetap berjalan; prosesnya tidak dihentikan selama upgrade.
