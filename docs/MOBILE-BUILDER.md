# Harmonika Mobile Builder

Engine mobile menggunakan domain terpisah. Dokumen master memiliki **214 ID unik**
bernomor 1–214; judul 165 tidak sesuai daftar. Readiness per ID adalah sumber
kebenaran, bukan jumlah katalog. Source master dibekukan bersama SHA-256.

Aktif: **34 perencana +57 generator source (91)**. **123 ID belum siap**:
56 belum diimplementasikan,22 memerlukan toolchain,45 connector/runtime berizin.
Lihat daftar tepat per ID pada `MOBILE-BUILDER-REPORT.json`.

## Endpoint

Bearer token Harmonika dengan scope `chat`, `skills`, atau `mobile`:

| Method | Path | Fungsi |
|---|---|---|
| GET | `/v1/mobile-builder/capabilities` | Kesiapan dan batas aktual |
| GET | `/v1/mobile-builder/skills?status=available&offset=0&limit=100` | Discovery (maksimal 200 per halaman) |
| GET | `/v1/mobile-builder/skills/{skill_id}` | Schema input dan mode |
| POST | `/v1/mobile-builder/invoke` | Perencanaan / source-generation privat |

Contoh body:

```json
{"skill_id":"spec.read.mobile","input":{"spec":"Aplikasi Android dan iOS untuk katalog offline, tanpa pembayaran","screens":["Home","Catalog"]}}
```

Pembuatan source menggunakan schema spesifik yang dikembalikan endpoint inspect.
Contoh ID benar `mobile.gen.screen`; contoh berakhiran `.login` pada naskah master
bukan ID terdaftar. Tidak ada alias berdasarkan prefix atau permission engine lain.

## Semantik hasil

`status=completed` berarti operasi **perencanaan** atau **pembuatan source** selesai.
Respons selalu mencantumkan `mode`, `execution_kind`, `runtime_verified=false`,
`build_performed=false`, `tests_performed=false`, `signed=false`, `deployed=false`,
dan `external_actions_performed=false`. Generator mengembalikan `files[]` berisi
`path` relatif dan `content`, dependency/configuration serta warnings bila relevan.
Tidak ada APK/AAB/IPA, public artifact URL, atau publikasi store implisit.

Isi spec dikirim sebagai teks; path pada spec tidak memberi akses membaca host.
Engine stateless: tidak menyimpan source/chat pelanggan atau melatih model.
Penerapan source di TUI dilakukan oleh tool workspace lokal setelah review.

## Batas dan error

- Input JSON maksimal 64 KiB, spec 32.000 karakter; respons JSON UTF-8 256 KiB.
- Maksimal 60 file, setiap file 64 KiB; path traversal/absolute/duplicate dan file
  keystore/credential produksi ditolak. Placeholder `.env.example` diperbolehkan.
- Input tidak diketahui, duplicate JSON keys, nonfinite numbers, deep JSON dan
  injection actor/credential/context ditolak. Tidak ada fetch URL atau shell.
- Kuota token di gateway satu kali per invoke. Service berbagi kapasitas terbatas
  dengan analyzer: 2 aktif global, 1 per actor, 60 invoke/menit per actor.
- Deadline service 15 detik, gateway 20 detik. Worker mempertahankan slot sampai
  benar-benar selesai; tidak ada retry duplikat tersembunyi.

Error berbentuk `{"error":{"code":"..."}}`: HTTP 400 `invalid_input`/
`invalid_request`, 403 auth/scope, 404 `unknown_skill`, 413 payload/output limit,
429 rate/busy, 503 `registered_mobile_toolchain_required`,
`authorized_connector_and_runtime_required`, atau `implementation_missing`.

## Readiness runtime

Koneksi toolchain Android/macOS, emulator/device farm, signing vault, akun Play/
App Store, layanan pembayaran, push, telemetry dan OTA adalah integrasi terpisah.
Generate modul/config bukan bukti integrasi live. Seluruh capability eksekusi itu
masih false sampai smoke di worker berizin lulus. Server Linux bukan worker Xcode.
Xcode/Android SDK di Mac pemilik tidak otomatis menjadi worker remote publik.

Jangan mengirim server API secret melalui `EXPO_PUBLIC_*`/source APK/IPA. AsyncStorage
hanya untuk data non-sensitif; token pengguna memakai Keychain/Keystore-backed
storage. [Panduan keamanan React Native](https://reactnative.dev/docs/security).
Starter Expo memakai baseline terverifikasi Expo58.0.19 / React19.3.0 /
React Native0.88.0. Dependency modul tambahan tetap perlu diselaraskan dengan SDK
dan dikunci sebelum build. Starter Kotlin memakai contoh pin historis yang harus
diverifikasi ulang untuk SDK dan kebijakan store saat rilis. [Setup Expo](https://docs.expo.dev/get-started/create-a-project/).

## TUI

```sh
harmonika mobile capabilities
harmonika mobile list --status available
harmonika mobile inspect scaffold.expo
printf '%s' '{"name":"Katalog","identifier":"id.harmonika.katalog"}' | harmonika mobile invoke scaffold.expo
```

TUI menampilkan JSON source tanpa menjalankannya otomatis. Guide
`harmonika-mobile-builder` mengarahkan agent untuk membaca spec, memeriksa
capability, menerapkan file di proyek berizin, lalu menguji pada runtime aktual.
Tidak ada auto-update binary, tidak ada credential dalam paket.
