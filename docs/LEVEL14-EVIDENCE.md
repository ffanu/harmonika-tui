# Bukti tindakan pada workspace backend

Level14 memperkuat **coding workspace yang dikelola backend**. Pernyataan model,
phase, hasil salinan klien dan kata `PASS` bukan bukti selesai. Pembaruan ini tidak
melatih bobot model, tidak memverifikasi otomatis berkas lokal TUI, dan tidak
mensertifikasi seluruh kemampuan Harmonika. Status aktivasi dan hasil aktual ada
di `LEVEL14-AGENT-EXAM-20261115.json` setelah pengujian selesai.

Gateway publik memvalidasi token lalu menerbitkan principal internal. Runtime
memeriksa kepemilikan workspace, menyimpan kontrak dan intent sebelum dispatch,
mengikat job pada actor/workspace/idempotency, lalu memeriksa hasil pada versi
workspace yang sama. Kunci HMAC lokal berada di luar snapshot worker; tanda tangan
melindungi integritas ledger/cache, bukan attestation hardware atau IAM baru.

## API

`POST /v1/coding/workspaces/{id}/agent` menerima `messages`, `stream` dan
`verification`. Gunakan Bearer key melalui header serta `Idempotency-Key` stabil.
Eksekusi tetap membutuhkan approval terpisah melalui endpoint job; model tidak
mempunyai alat approval.

```json
{
  "messages": [{"role": "user", "content": "Perbaiki app.py lalu jalankan tes asli."}],
  "verification": {"profile": "tests", "tests": ["test_app.py"]},
  "stream": true
}
```

| Profil | Yang diperiksa |
|---|---|
| `observed_steps` (default) | Merekam tindakan; selalu belum terverifikasi tanpa kriteria penerimaan |
| `read`, `paths` | Job baca milik actor dan isi berkas aktual pada versi terkait |
| `artifact`, `paths` | Hasil penulisan serta byte/hash snapshot saat verifikasi |
| `tests`, `tests` | Assertion asli tidak berubah; job asli disetujui; rerun terpisah, count >0, seluruhnya lulus |
| `migration` | SQLite read-only: schema version, integrity, kolom/tipe per tabel, minimum baris dan digest opsional |
| `conversation` / `plan_only` | Jawaban tanpa tools; tidak membuat klaim implementasi |

Resume memakai `verification: {"task_id":"ID_DARI_BACKEND"}`. Permintaan sama
setelah ACK hilang memakai key dan body yang sama. Perubahan syarat perlu kontrak
baru dengan `new_task:true`; receipt lama tidak otomatis meluluskan revisi baru.

Status `completed` hanya menyatakan **pemeriksaan profil yang tercantum** lulus.
Hash artifact tidak membuktikan mutu desain, relevansi tulisan atau seluruh
persyaratan bisnis. Tidak ada profil deploy, vision, atau verifikasi host lokal
otomatis pada modul ini. Q&A biasa di gateway chat tetap memakai jalur yang ada.

## Batas verifier

Verifier tes mendukung subset konservatif Python: fungsi biasa, unittest,
sqlite3, decimal, math, json dan modul proyek yang sesuai. Source dengan reflection,
process imports, modul native/bytecode, stdlib shadowing atau penggantian framework tetap bisa dijalankan
melalui tools berizin, tetapi tidak mendapat bukti tes independen dari profil ini.
Ini bukan pengganti sandbox Docker atau pembuktian program Python adversarial.

Verifier memakai operasi internal `python -I` agar startup mengabaikan cwd,
PYTHONPATH dan user site; operasi tersebut tidak ada dalam katalog/API publik.
Verifier memuat stdlib dari direktori interpreter tepercaya sebelum mengimpor
aplikasi. Migrasi tidak mengimpor modul workspace. Worker memakai snapshot COW,
network none, non-root dan resource limits; hasil verifikasi tidak dipublikasikan
sebagai mutasi workspace. TTL kontrak satu jam dan cache hasil lima menit.

Ledger tambahan menyimpan mekanika, hash dan referensi job; cache turn menyaring
hasil/preview dan tidak menyimpan isi bacaan atau percakapan baru. Penyimpanan
payload/artifact operasional workspace yang sudah ada tetap terpisah dari log
bukti. Retensi ledger satu hari dan kuota membatasi pertumbuhan SQLite.

## Pengujian dan aktivasi

Python runtime membutuhkan 3.11+. Jalankan unit dengan Python backend:

```sh
python3 -m unittest discover -s tests -p 'test_level14*.py' -v
python3 -m unittest discover -s tests -p 'test_coding*.py' -v
```

Unit memakai fixture/double sintetis; sembilan misi native memakai agent loop
aktual, planner hidup, Docker dan penilai terpisah pada host lab. TUI lokal diuji
terpisah dan tetap `client_reported`. Kegagalan awal dan koreksi alat penguji
dipertahankan dalam laporan, bukan diubah menjadi hasil lulus.

Aktivasi `scripts/level14-activate.py` memerlukan manifest source/hasil tersegel,
review, idle guard, fence admission SQLite, backup source/database, penghentian
terkendali layanan coding, penggantian atomik dan CAS rollback. Database backup
tidak direstore ke data live. Gateway/planner, binary dan ASCII TUI tidak diganti.

Insiden bootstrap: instalasi dependensi lab memicu `needrestart` pada aplikasi
demo Level12. Unit kembali aktif, tetapi PID berubah; ini merupakan penyimpangan
batas operator dan tidak boleh dilaporkan sebagai pengujian tanpa efek samping.
Instalasi apt berikutnya wajib memakai `NEEDRESTART_MODE=l` dan audit unit/PID,
tanpa restart unit di luar scope. Tidak ada tindakan ke BGP/core/VyOS JKT.
