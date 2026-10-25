# Harmonika TUI — pemulihan integritas rilis 2026.10.26.1

Status: implementasi dan uji lokal selesai; distribusi memakai paket serta katalog bertanda tangan yang sama.

## Masalah yang diselesaikan

Pekerjaan branding sebelumnya terhenti setelah binary berubah tetapi sebelum manifest
rilis diperbarui. Akibatnya launcher menolak binary dengan `binary_checksum_mismatch`.
Binary macOS Intel juga memerlukan pembaruan tanda tangan setelah perubahan tersebut.

Pemulihan mempertahankan branding Harmonika AI, lisensi komponen, konfigurasi,
credential, sesi pengguna, serta rilis sebelumnya yang masih terverifikasi. Tidak ada
patch byte binary baru, penghentian TUI, atau restart engine/server.

## Verifikasi dan perubahan

- Perubahan binary yang sudah dibuat sebelumnya ditelusuri ke katalog lama yang
  bertanda tangan; rekonstruksi pembanding dilakukan di memori, tanpa menulis ulang kode.
- macOS ARM: isi sebelum blob tanda tangan sama persis dengan hasil perubahan
  branding yang tercatat; `codesign --verify --strict` dan `--version` berhasil.
- macOS Intel: tanda tangan ad-hoc diperbarui. Di luar blob tanda tangan, perubahan
  hanya pada ukuran `__LINKEDIT` dan metadata `LC_CODE_SIGNATURE`; kode executable
  tidak berubah. Verifikasi `codesign` berhasil.
- Manifest runtime baru menyertakan `binary_hashes` untuk kelima platform. Launcher,
  bootstrap, preflight, dan rollback memakai checksum platform dari envelope
  Ed25519, bukan memaksakan checksum macOS ARM pada Linux/Windows.
- Sidecar `binary.sha256` tidak dapat menggantikan pin bertanda tangan pada rilis
  baru. Kompatibilitas pembacaan rilis lama tetap tersedia.
- `install_package(..., binary_source=...)` hanya menerima binary yang cocok dengan
  pin dalam manifest. Seluruh paket divalidasi dan dipreflight di direktori staging;
  binary dan metadata rilis aktif tidak ditimpa. Update interaktif menggunakan jalur
  yang sama sehingga rollback sebelumnya tetap utuh.
- Binary publik menggunakan URL versi immutable di
  `/assets/harmonika-terminal/bin/2026.10.26.1/`. Katalog, ZIP dan checksum diselaraskan.
- Identitas produk pada halaman distribusi tetap Harmonika AI. Pemberitahuan
  lisensi komponen asli dipertahankan pada `UPSTREAM-LICENSE.md`.

## Bukti pengujian

- macOS: 10 tes release/rollback dan 10 tes integritas platform lulus.
- Linux x64: dua suite yang sama, 20 tes lulus.
- Dua tes branding provider Node.js lulus.
- Penolakan paket rusak, traversal, signature palsu, binary salah, sidecar palsu,
  bypass melalui flag lama, platform hilang, dan kegagalan preflight diuji.
- Upgrade binary terverifikasi diikuti rollback diuji dengan fixture; binary lama
  tetap identik dan pointer aktif tidak berubah bila preflight gagal.
- Paket aktual: 92 file runtime diverifikasi dan source audit tidak menemukan
  credential. `harmonika doctor` dan `harmonika --version` berhasil setelah aktivasi.

Pengujian Windows menggunakan fixture identitas/platform dan logika verifikasi,
bukan instalasi GUI pada Windows fisik. Binary Linux ARM dan macOS Intel tidak
berarti telah diuji native pada semua perangkat; provenance/checksum masing-masing
diverifikasi. Tanda tangan rilis Ed25519 dan ad-hoc macOS bukan sertifikasi
notarization/Authenticode.

## Build dan pemeriksaan

```sh
python3 -m unittest discover -s tests -p test_releases.py
python3 -m unittest discover -s tests -p test_platform_release_integrity.py
node --test tests/provider-branding.test.cjs
python3 runtime/check-public-source.py
harmonika doctor
harmonika update --check
```

Build lintas platform menggunakan `HARMONIKA_PLATFORM_MANIFEST` yang menunjuk
katalog binary bertanda tangan. `HARMONIKA_BINARY_SOURCE` harus menunjuk artefak
macOS ARM yang sudah diverifikasi. Kunci penandatangan tetap privat dan tidak masuk
paket, repository, atau log. Tidak ada auto-update tanpa tindakan pengguna.

Pembagian kerja: OpenCode mengaudit kondisi rilis secara read-only; Codex melakukan
pemulihan, koreksi verifikasi platform, pengujian, dan penyelarasan distribusi.
