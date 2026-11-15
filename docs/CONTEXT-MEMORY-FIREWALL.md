# Konteks, memori proyek, dan data firewall

Pembaruan manual 2026.11.12.1 memakai core Harmonika yang ada. Tidak memasang
plugin pruning eksternal, mengubah provider, mengganti UI/binary, atau melakukan
training dari percakapan. Implementasi ditulis untuk kontrak Harmonika sendiri.

## Jalur aktif

`TUI/internal-agent → planner-context → decision model → validator → client tool`.
Planner memadatkan seluruh turn lama secara utuh, menghilangkan duplikasi hasil
baca dan memotong output pembacaan besar dengan hash serta penanda eksplisit.
Urutan instruksi system/developer, skema tool, user terbaru dan evidence tindakan/
verifikasi dipertahankan. Pasangan tool yang tidak lengkap ditolak. Konteks kritis
di atas budget 180.000 karakter ditolak dengan error terstruktur; tidak diam-diam
menghapus bukti. Budget ini merupakan batas payload planner, bukan token model.

`Owner MCP → input guard → handler berizin → output scrubber → client/model`.
Input credential mentah, pembacaan beberapa path secret yang dikenal dan
printenv ditolak. Nilai sensitif pada object, JSON text dan output teks umum
teredaksi; exception tak dikenal menjadi kode mekanis. Connector credential
terdaftar tetap mengembalikan private reference. Pola deteksi ini merupakan
pertahanan tambahan, bukan sandbox shell atau jaminan mendeteksi setiap secret.
Transport media/provider lain tetap mengikuti boundary dan izin adapter aslinya;
pembaruan ini tidak membuktikan audit semua jalur provider lain.

## Memori proyek

API tetap POST `/v1/core/invoke`, skill_id `memory.set/get/search/clear`.
Input dengan project_id masuk wrapper curated, input generic lama tetap didukung.
Discovery skill menampilkan oneOf schema keduanya. Principal berasal dari Bearer
gateway; scope proyek SHA-256 disimpan sebagai HMAC lookup pada Store AES-GCM.
Namespace `p:` dicadangkan. TTL, kuota, idempotency dan key enkripsi lama dipakai
tanpa migrasi DB. Katalog tetap 99 ID; fitur ini tidak mengarang skill baru.

```json
{"skill_id":"memory.set","input":{"project_id":"demo-web","consent":true,"key":"build","value":{"kind":"verified_command","text":"npm test"},"ttl_seconds":86400}}
```

Mutation mengirim Idempotency-Key stabil melalui header. Read menggunakan
`{"project_id":"demo-web","key":"build"}`. Scope tidak boleh diberikan bersamaan.
Set hanya menerima decision/convention/verified_command dan teks maksimal 2000
karakter. Clear memerlukan all=true. Data dibatasi per token dan proyek; tidak
ditransfer otomatis ketika pengguna mengganti token. Tidak ada pengarsipan chat
atau auto-injection catatan ke system prompt. Permintaan eksplisit pengguna
menjadi prasyarat agent menyimpan catatan. Filter secret/PII adalah heuristik;
operator/agent tetap harus memilih catatan teknis yang sesuai.

## Verifikasi dan aktivasi

Unit/integration memakai fixture sintetis untuk isolasi token/proyek, reserved
namespace, schema, AES-GCM, TTL, idempotency, penolakan credential, tool pairing,
budget, evidence dan regresi clipboard/media/SSH. Uji validator aktual dilakukan
dengan module staging di Linux. Smoke HTTPS memakai token lokal melalui file
privat, hanya membuat/membaca/menghapus catatan sintetis TTL pendek.

`scripts/deploy-context-memory.py` memeriksa hash baseline dan stage, menunggu
request/model child idle, membuat backup privat, mengaktifkan lima file dan
restart hanya harmonika-ai serta harmonika-shared-core. Health/smoke gagal
memulihkan file lama. Tidak mengubah storage key atau DB schema. Extension MCP
dipasang atomik dengan backup privat; sesi native yang sudah terbuka tidak
dihentikan. Tool baru dimuat saat proses MCP/TUI baru diluncurkan.

Codex menangani compactor, firewall, HTTP/schema, MCP dan aktivasi. OpenCode
menulis wrapper memori awal serta uji isolasi pada worktree terpisah; Codex
meninjau dan memperketat filter/schema sebelum integrasi. Claude tidak dipanggil.

Hasil akhir: 308 tes lokal lulus, 115 tes Python staging Linux lulus, validator
aktual lulus, sembilan pemeriksaan memory HTTPS lulus, dan terminal native
berhasil memanggil MCP memory lalu menjawab sesuai found=false. Binary/ASCII
dipertahankan. Detail state/hash: [laporan aktivasi](CONTEXT-MEMORY-FIREWALL-REPORT.json).
