---
name: harmonika-mobile-builder
description: Analisis spesifikasi dan generator source aplikasi Android/iOS melalui interface internal Harmonika.
---

# Mobile builder

Gunakan saat pengguna membangun aplikasi mobile. TUI hanya berbicara ke kontrak
internal `https://chat.harmonika.id/v1/mobile-builder`, tanpa SDK/key mentor.

1. Jalankan `harmonika mobile capabilities` lalu `harmonika mobile list --status available`.
2. Baca spec melalui tool file lokal yang berizin. Kirim **isi teks**, bukan path host,
   ke `spec.read.mobile` dan `stack.mobile.analyze` melalui JSON stdin.
3. Klarifikasi platform/framework yang bertentangan. Hormati fitur yang dikecualikan.
   Jika referensi UI tersedia, simpan di `docs/mockups/` pada proyek pengguna sebelum
   membangun UI. Jangan menambah fitur yang tidak diminta.
4. Untuk pembuatan source, inspect schema skill dan invoke ID exact. Gunakan
   `mobile.gen.screen`, bukan ID fiktif `mobile.gen.screen.login`.
5. Output `files` adalah source kit dalam respons privat. Terapkan hanya dalam
   workspace proyek yang diizinkan melalui tool file; review diff. Tidak ada
   penulisan atau eksekusi server otomatis. Jangan menimpa perubahan pengguna.
6. Periksa dependency dan native configuration di README hasil. Jalankan build/test
   di runtime proyek yang tersedia, bukan menganggap source generation sebagai test.
7. Uji keyboard, scroll, safe area, landscape, phone/tablet/foldable, loading/empty/
   error dan denial permission. Screenshot nyata hanya setelah emulator/perangkat
   tersedia, jangan membuat bukti QA fiktif.

`mode=planning|source_generation` dan `runtime_verified=false` adalah batas penting.
`build_performed/tests_performed/signed/deployed=false` tidak boleh diubah menjadi
klaim lulus/build/store live. Endpoint build/sign/store/OTA/IAP live hanya boleh
aktif setelah runtime dan connector berizin siap. Jangan menerima key store/keystore
atau credential bisnis pada payload source-generation.

## Contoh

```sh
printf '%s' '{"spec":"Android dan iOS, katalog offline, tanpa pembayaran","screens":["Home","Catalog"]}' | harmonika mobile invoke spec.read.mobile
printf '%s' '{"name":"Katalog","identifier":"id.harmonika.katalog"}' | harmonika mobile invoke scaffold.expo
```

Cek schema aktual sebelum menambahkan parameter. Token berasal dari penyimpanan
privat launcher; jangan mencetak atau memasukkannya ke command, source, atau log.
