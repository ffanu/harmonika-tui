---
name: harmonika-web-builder
description: Bangun proyek web dari spesifikasi melalui engine Harmonika, job privat, artefak dan pengujian sandbox.
---

Gunakan otomatis untuk tugas website full-stack. Baca `harmonika builder capabilities`
dan spesifikasi lengkap lebih dahulu. Jangan meminta pengguna memilih ID skill satu
per satu. `harmonika build from SPEC.md` memilih stack awal dan menulis proyek privat;
`--run-checks` meminta job pengecekan sintaks dalam sandbox dengan approval.

Satu proyek per pekerjaan. Jangan mencampur source/token/proyek lain. Pertahankan
fitur negatif pada spec ("tanpa payment" tidak boleh menghasilkan payment aktif).
Clarify requirement yang berpengaruh pada privasi, biaya, autentikasi atau produksi.
Baca README/struktur proyek sebelum mengubah. Overwrite perlu revisi yang sudah dibaca.

Gunakan schema live untuk invoke, jangan menebak argumen. Job async dapat di-recover
melalui status/events. Approval terikat input_hash dan revisi, bukan izin host umum.
Stop tidak menerbitkan perubahan terlambat. Download artifacts hanya memakai Bearer.

Bedakan tegas: generated → installed → syntax checked → unit/integration/E2E passed
→ deployed → monitored. Jangan menyebut tahap berikutnya selesai tanpa bukti nyata.
Template provider adalah konfigurasi untuk ditinjau, bukan akun cloud yang sudah aktif.
SSH hanya target terdaftar dan principal yang diizinkan. Jangan memakai key host root
untuk melayani token publik, menonaktifkan firewall, atau menghabiskan saldo cloud.

Jangan mencetak spec privat, source pelanggan, key atau keluaran rahasia ke log.
Untuk skenario baru di luar template, gunakan coding workspace dengan rencana dan
pengujian yang sesuai; jangan mengklaim bisnis lengkap dari skeleton saja.

Kontrak: `tools/docs/WEB-BUILDER.md` pada paket terminal.
