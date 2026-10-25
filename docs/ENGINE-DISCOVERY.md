# Harmonika Engine Registry — auto-discovery

Backend `chat.harmonika.id` memindai modul operator saat boot dan setiap 5 detik.
Modul yang lolos langsung muncul pada registry `/v1/skills` dan bisa dipanggil
melalui gateway yang sama, tanpa menambah daftar ID atau me-restart gateway.
Handler lama dipertahankan dan ID-nya dilindungi dari penimpaan.

## Kontrak publik

Semua endpoint menggunakan Bearer API token dengan scope `chat` atau `skills`.
Identitas/kuota/rate limit tetap ditetapkan gateway; header client bukan otorisasi.

```text
GET  /v1/skills/capabilities
GET  /v1/skills/discovery
GET  /v1/skills?engine=discovery
GET  /v1/skills/{skill_id}
POST /v1/skills/invoke
```

Contoh request nyata:

```json
{"skill_id":"text.metrics","input":{"text":"Halo Kak!\nInternet Harmonika"}}
```

```json
{
  "status": "completed",
  "skill_id": "text.metrics",
  "result": {"characters": 28, "words": 4, "lines": 2, "utf8_bytes": 28}
}
```

Ringkasan `/discovery` memuat `status`, `registered`, `valid`, `rejected`,
`skill_ids`, `error_codes`, `finished_at`, `duration_ms`, `generation`, `truncated`
dan `db_result`. Tidak mengirim source, payload, token, path privat atau traceback.
Generation berlaku selama proses hidup; setelah restart source diaudit kembali.
Metadata SQLite dari boot sebelumnya tidak mengaktifkan kode secara otomatis.

## Memasang modul operator

Implementasi mengikuti struktur Python backend yang sudah ada:
`/opt/harmonika-ai/engine_registry/discovery.py`.
Direktori modul aktif terpisah: `/opt/harmonika-ai/skills-runtime`.
Tiap subdirektori langsung memiliki dua file:

```text
skills-runtime/
└── text-metrics/
    ├── skill.py
    └── manifest.json
```

Interface wajib dalam `skill.py`:

```python

def GetSkillIdentity():
    return {
        "id": "example.word_count",
        "name": "Hitung kata",
        "core_capabilities": ["word_count"],
        "requirements": [],
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "maxLength": 12000}},
            "required": ["text"],
            "additionalProperties": False,
        },
    }

def GetContextRequirements():
    return {
        "max_input_tokens": 65536,
        "max_output_tokens": 4096,
        "max_chat_messages": 0,
        "max_tool_calls": 0,
    }

async def Execute(payload):
    return {"words": len(payload["text"].split())}
```

Fungsi metadata harus sinkron tanpa argumen, Execute async dengan satu argumen
posisional biasa tanpa default/decorator. Signature diverifikasi melalui AST
sebelum subprocess dan diperiksa lagi setelah modul dimuat. Kontrol alur top-level
`if/try/with/for/while/raise` ditolak. Tidak menjalankan Execute saat discovery.

`manifest.json` berisi tepat satu field:

```json
{"module_sha256":"SHA256-HEX-DARI-BYTE-SKILL.PY"}
```

Hitung hash dari byte aktual setelah code review. Pasang direktori secara atomik
(rename dari direktori staging di filesystem yang sama); jangan mengedit source
aktif sebelum hash approval siap. File harus milik UID operator service,
bukan symlink/hardlink, dan tidak boleh writable oleh group/other. Direktori juga
harus memiliki owner yang benar tanpa akses tulis group/other.

Hash cocok berarti integritas terhadap pin operator. Ini **bukan tanda tangan
binary atau audit keamanan independen**; descriptor mengirim `hash_verified=true`
dan `signature_verified=false`. Tidak ada endpoint publik untuk upload kode,
menulis manifest, memaksa refresh atau memasang plugin.

## Batas kontrak tahap ini

Runtime baru menerima modul Python operator yang sudah direview dan tidak
memerlukan dependency tambahan (`requirements=[]`), session history atau tool
calls. Kedua batas chat/tool harus 0. Kebutuhan workspace, secret, network atau
engine spesialis memakai gateway lama dengan scope/context-nya sendiri; tidak
diaktifkan diam-diam lewat discovery.

Nama field token dipertahankan untuk kompatibilitas kontrak. Runtime ini tidak
menjalankan tokenizer/model: batas byte JSON UTF-8 juga dibatasi secara
konservatif oleh angka deklarasi token. Input paling besar 64 KiB, output paling
besar 256 KiB. Execute harus mengembalikan object JSON; skema input top-level
harus object. JSON Schema subset: type/properties/required/additionalProperties,
items/minItems/maxItems/minLength/maxLength/minimum/maximum/enum/description.
Keyword lain ditolak. additionalProperties default false dalam subset ini.

Batas operasional:

| Batas | Nilai |
|---|---:|
| Modul langsung | 3.072, tanpa recursive traversal |
| Source per modul | 1 MB |
| Manifest | 4 KiB |
| Total source per scan | 64 MB |
| Metadata snapshot/cache | 8 MiB masing-masing |
| Source spool privat | 128 MiB |
| Chunk pemindaian | 64 modul |
| Tunggu scan | 10 detik/server, 30 detik/status client, 16 waiter |
| Audit metadata baru | 2 detik/modul |
| Budget scan | 30 detik + maksimal satu audit yang sedang berjalan |
| Execute | 10 detik |
| Request aktif | 2 global, 1 per token |
| Rate invoke | 60/menit/token, ditambah kuota gateway |
| Target memory subprocess | 192 MiB, dalam cgroup service 512 MiB |

Bila scan terpotong, snapshot dinonaktifkan seluruhnya: modul di luar batas tidak
boleh menyembunyikan ID duplikat. Snapshot baru dipublikasikan atomik setelah
audit tersimpan. Cache metadata berdasarkan hash source mempercepat scan ulang.
Source terverifikasi ditangkap ke content-addressed store privat; Execute memuat
byte secara lazy dari spool serta memeriksa hash/owner/permission lagi, bukan
membaca ulang source aktif. Modul dengan metadata literal inert diaudit melalui
AST tanpa import; metadata dinamis memakai subprocess terisolasi seperti sebelumnya. Job yang sudah berjalan boleh selesai dengan snapshot lama, sedangkan
invoke baru mengikuti snapshot terbaru. Duplikat ditolak seluruhnya; reserved ID
mencakup katalog lama, analyzer, core/unified, 214 ID mobile dan nama rute gateway.

Subprocess memiliki environment bersih, cwd sementara, deadline dan resource
limits Unix. Di Linux service root, child melepas group/gid/uid ke nobody sebelum
menjalankan kode plugin. Timeout/cancel membersihkan child milik pemanggilan.
Pembatasan ini **bukan sandbox untuk kode bermusuhan** dan tidak membuktikan modul
bebas side effect. Install hanya kode operator yang direview. Pembatasan cgroup
service tetap berlaku; rlimits bergantung dukungan OS. Jangan menaikkan deadline
Execute melewati batas HTTP gateway 15 detik.

## SQLite, error dan pemulihan

DB baru: `/var/lib/harmonika-master/discovery.sqlite3`, permission 0600.
Tidak membuka atau memigrasikan database session, pelanggan, CS maupun ERP.

- `engine_discovery_status`: module ID, checksum, hash status, error code,
  timestamp dan latensi audit metadata. Cache hit dicatat sebagai 0 ms.
- `engine_discovery_summary`: snapshot status terakhir.
- `engine_discovery_execution`: latensi eksekusi aktual terakhir per skill,
  timestamp, checksum dan error code; tanpa payload, hasil atau token.

Database yang hilang atau diganti tidak dibuat ulang saat operasi berjalan.
Inode/owner/link/permission diperiksa. Database berisi tabel asing ditolak sebelum
migrasi atau perubahan permission. Restore DB memerlukan restart registry agar
binding inode diaudit ulang. Bila penulisan audit hasil sukses gagal, API gagal
tertutup dengan 503; hasil tidak dianggap sukses tanpa audit. Bila eksekusi sudah
gagal/dibatalkan, kegagalan audit tidak mengganti error/pembatalan aslinya.

Error publik: `invalid_skill_input` (400), `skill_changed` (409),
`skill_execution_failed` (422), `skill_timeout` (504),
`discovery_audit_unavailable` (503). Error source/hash/signature dicatat sebagai
kode `E_*` di DB/ringkasan, modul lain tetap berjalan. Handler lama mempertahankan
kontrak error sebelumnya.

Untuk pengujian lokal macOS, gunakan path temp yang sudah di-`resolve()`;
symlink `/tmp` atau `/var` sengaja tidak diikuti oleh pemeriksa DB.

## CLI dan verifikasi

Source `runtime/tools/state_store.py` menambahkan `engine_discovery` pada
`harmonika state status` dan satu baris ringkas pada `--format text`.
Pembacaan gateway metadata dibatasi 2 detik, tanpa menyalin chat/key ke cache;
`--offline` melewati jaringan. Sesi lokal tetap dilaporkan ketika engine tidak
terjangkau. Perubahan helper ini terpisah dari binary TUI; helper sudah diaktifkan manual sebagai runtime bertanda tangan `2026.10.25.30`;
binary TUI tetap identik dengan `.28`/`.29`, tanpa auto-update atau perubahan sesi.
Katalog rilis publik tidak diubah.

```sh
python -m unittest discover -s tests -p 'test_engine_discovery*.py'
```

Bukti pengujian, benchmark 256 modul, audit OpenCode, aktivasi, cleanup dan batas
rilis client tersedia di [laporan](ENGINE-DISCOVERY-REPORT.json).

## Guard hot reload dan hasil kapasitas terbaru

`refresh(wait=True)` menunggu publikasi snapshot saat scan sibuk. Endpoint invoke
menahan eksekusi modul dinamis sampai scan selesai; deadline mengembalikan
`discovery_scan_busy` (503). Header Retry-After tersedia pada layanan privat;
client publik memakai `scanning/result/retry_after_ms` dari status untuk polling.
Helper CLI mengulang GET status, bukan POST/tool. Tidak menunggu tanpa batas.

Benchmark Linux3.072modul: cold1,713detik, warm1,253detik; peak RSS72,17MiB,
incremental27,96MiB. Invoke selama scan selesai dalam1,469detik. Ini workload
sintetis yang terukur; bukan klaim2.500skill produk baru diaktifkan.
[Laporan terbaru](DISCOVERY-SCALE-REPORT.json).
