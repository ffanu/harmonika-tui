# Inventori privat

Starter FastAPI + SQLite dengan UI responsif dan CRUD barang. Tidak menambahkan
pembayaran, akun pelanggan, multi-tenant bisnis atau klaim fitur di luar starter ini.

1. Buat venv dan install `requirements.txt` yang dipin.
2. Isi `APP_ADMIN_PASSWORD` (minimal12 karakter) dan `APP_SESSION_SECRET` (minimal32)
   melalui secret store; jangan commit nilai asli. Default kosong menolak login.
3. Jalankan `uvicorn app:app --host 127.0.0.1 --port 3000`.
4. Untuk HTTP lokal saja, set `APP_COOKIE_SECURE=false`; produksi tetaptrue dan HTTPS.
5. Test: `python -m unittest discover -s tests -v`. Test memakai database sementara.

Proteksi: cookie HttpOnly/SameSiteStrict, CSRF terikat sesi, pemeriksaan Origin,
query parameterized, validasi request, CSP tanpa inline script dan tanpa public data.
Database lokal perlu backup dan pemantauan sebelum dipakai untuk data penting.
Tidak ada akun/sandi bawaan atau API key pembayaran yang tertanam.
