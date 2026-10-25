# Checkpoint mobile engine

- Q: Berapa ID? A: 214 unik bernomor; header 165 salah. Source/hash dibekukan.
- Q: Menulis/membaca host? A: Tidak. Spec adalah isi teks; output source JSON privat.
- Q: ID contoh `.login` valid? A: Tidak; gunakan exact `mobile.gen.screen` + input schema.
- Q: Completed berarti APK/IPA? A: Tidak. Mode planning/source_generation, seluruh
  flag build/test/sign/deploy false. Readiness runtime terpisah.
- Q: Akun store/payment/push dipakai? A: Tidak ada credential di request/repo/log.
- Q: Mengganggu engine lama? A: Namespace mobile terpisah, shared limiter/principal,
  existing routing unchanged; deployment hanya master+gateway terkait.
- Q: Tim? A: Codex integrasi/audit/backend/config; OpenCode source generators/test.
  Claude tidak dipanggil.
- Q: UI references? A: Engine source, bukan perubahan UI aplikasi pelanggan. Agent
  wajib membaca mockup proyek saat diminta menghasilkan UI aplikasi spesifik.
- Q: Otomatis update terminal? A: Tetap false; rilis manual ditandatangani.
