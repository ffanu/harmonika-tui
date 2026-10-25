# Shared Core Foundation

Dokumen `2.txt` memuat99 ID unik. Nomor `rag.query` terulang46 (seharusnya56),
dan daftar Phase1 berisi14 ID meskipun labelnya13. Implementasi menjaga exact-ID
mapping serta mengumumkan readiness, bukan menganggap semua daftar sudah aktif.

## Endpoint

Bearer token Harmonika; core lokal menerima scope `chat`, `skills`, atau `core`.
Delegasi coding/chat memeriksa lagi izin dan kuota engine asal.

| Method | Endpoint | Fungsi |
|---|---|---|
| GET | `/v1/core/capabilities` | Readiness dan batas |
| GET | `/v1/core/skills?status=available&limit=100` | Daftar99 ID; pagination maksimal200 |
| GET | `/v1/core/skills/{id}` | Schema, resolved skill, approval dan execution target |
| POST | `/v1/core/invoke` | Operasi lokal atau delegasi berizin |

Discovery pusat `/v1/skills` juga mencantumkan kontrak ini. Invoke pusat mengenali
ID exact dan meneruskannya ke adaptor yang sama. Tidak ada fuzzy/prefix routing.

```json
{"skill_id":"data.read_csv","input":{"text":"nama,nilai\nA,12\nB,15\n"}}
```

Untuk coding:

```json
{"skill_id":"file.read","input":{"path":"app.py"},"context":{"engine":"coding","workspace_id":"UUID-WORKSPACE-MILIK-TOKEN"}}
```

`context` hanya routing engine/workspace. Identitas actor, credential atau URL
upstream tidak diterima dari body. Principal ditentukan gateway dari Bearer.

## Operasi dan mode

- **12 lokal:** enam data tools, empat memory, workflow.define/run.
- **19 adaptor workspace:** file read/write/edit/search/grep/list; shell exec/
  background; Python/Node/shell; lint/format/test/scaffold; Git status/diff/commit;
  system.info di sandbox. Ini memerlukan konteks/izin, bukan shell host.
- **2 adaptor chat:** llm.chat menerima messages role system/user/assistant, maksimal
  20 pesan,16000 karakter per content; llm.complete menerima prompt. Keduanya
  memakai model publik Harmonika Ai dan response native Chat Completions.
- **6 operasi dokumen lokal:** runtime TUI yang sudah ada, belum menjadi file-host
  API server. Path lokal tidak otomatis menjadi upload.
- **60 ID belum diaktifkan:** scheduler, generic network/db/secret/communication/
  hardware adapters dan kemampuan lain yang belum memiliki kontrak berizin.

Alias memakai input schema operasi target; contoh shell.exec memakai `command`
dan `timeout_seconds`, bukan nama argumen contoh pseudocode. file.search adalah
pencarian glob nama file (`fs.glob`); file.grep mencari isi (`code.grep`). File
read pada workspace adalah UTF-8 terbatas; bukan pembaca binary host bebas.

Job coding mengembalikan envelope202 dari engine asal dan header
`X-Harmonika-Requested-Skill`/`X-Harmonika-Resolved-Skill`. Approval, SSE, status dan
Stop memakai endpoint `/v1/coding/jobs/{job_id}/*` yang sudah ada. Adaptor tidak
menyetujui job, menaikkan privilege, atau mengubah scope token.

## Data

Input maksimal64KiB,500 baris/30 kolom; sel berupa scalar JSON. CSV harus memiliki
header unik dan jumlah kolom konsisten. read_json menolak duplicate keys/nonfinite
numbers/depth berlebih. Transform hanya select/filter/sort/limit, tanpa eval atau
SQL. `data.export` mengembalikan teks CSV/JSON dan MIME, tidak membuat file host;
CSV mengawali text berpotensi formula dengan apostrof, termasuk prefix whitespace.

Statistik menggunakan Decimal dengan precision1000 internal. `mean` dapat berupa
angka JSON aproksimasi; `mean_decimal`, `precision_digits`, dan
`mean_is_approximate` menunjukkan representasi yang lebih presisi. Numeric text
maksimal128 karakter dan exponent dibatasi; tidak ada klaim ketepatan tak terbatas.
Visualisasi adalah **spec-only**, data inline tanpa fetch/script; scatter perlu
X/Y numeric dan y selalu quantitative. Nilai plot dibatasi rentang angka aman JS.
[Field escaping Vega-Lite](https://vega.github.io/vega-lite/docs/field.html) dan
[encoding](https://vega.github.io/vega-lite/docs/encoding.html).

## Memory privat

Input memory.set `{scope?,key,value,ttl_seconds?}`; get `{scope?,key}`; search
`{scope?,query,limit?,offset?}`; clear `{scope,all:true}`. Default scope `default`,
TTL1hari, minimum60detik/maksimal7hari. Batas100 record per actor (termasuk definisi
workflow),16KiB/value,1MiB/actor (termasuk replay), state global100MiB dan1000actor.

Nilai, nama/key memory, definisi workflow, hasil replay dienkripsi AES-GCM; indeks
actor/scope/key/idempotency memakai HMAC dengan pemisahan purpose. File DB0600,
direktori0700. Key enkripsi terpisah dari secret autentikasi dan tidak dikirim ke
client. Backup DB memerlukan key storage yang sama; jangan menghapus/merotasinya
sembarangan. Tidak ada append riwayat chat atau pelatihan otomatis.

Idempotency wajib memory.set/clear/workflow.define/run, header8–128 karakter
`[A-Za-z0-9_-]`. Payload sama direplay; beda ditolak409. TTL replay24jam dengan
batas1000/actor dan50000global. Memory kedaluwarsa dibersihkan saat operasi berikut.

## Workflow nyata dan batas

```json
{"skill_id":"workflow.define","input":{"name":"Cek data","steps":[{"id":"parse","skill_id":"data.read_json","input":{"text":"{\"a\":1}"}},{"id":"analyze","skill_id":"data.analyze","input":{"rows":[{"nilai":12},{"nilai":15}]},"depends_on":["parse"]}]}}
```

Run memakai `{workflow_id}` milik token. Maksimal12 langkah, budget15detik,
allowlist hanya enam data tool dan enam analyzer defensif. Tidak ada dynamic
references/interpolation: input tiap langkah eksplisit. Tidak ada lock SQLite saat
runner bekerja. Gagal satu langkah menghentikan langkah berikut; hasil gagal tetap
tersimpan sehingga replay tidak menjalankannya lagi.

Response top-level completed berarti invocation selesai; cek `data.status` dan
`data.steps[].status` untuk hasil workflow. Run lama yang tidak pasti menjadi
`recovery_required`, tidak otomatis diulang setelah restart/TTL. Scheduler belum
aktif. Tidak ada side effect komunikasi, process management atau transaksi bisnis.

## Keamanan dan operasi

Private service loopback18195. Gateway deadline25detik, service20detik, shared-core
maksimal2invocation aktif global/1per actor,60per menit. Setelah timeout worker tetap
memegang slot sampai selesai. Input JSON/depth/node/byte dibatasi, duplicate keys,
nonfinite numbers dan actor/credential injection ditolak. Integer besar tidak
melewati float64 saat gateway meneruskan request.

Error `{"error":{"code":"..."}}`:400 input/idempotency,403 auth/scope/policy,
404 unknown/ownership,409 idempotency/conflict/delegation/recovery,413 limit,
429 rate/capacity,503 unavailable. Audit hanya HMAC actor/status/durasi; tidak ada
prompt/value/source/result/credential di log. Core tidak mengakses DB pelanggan,
ERP, CS atau menyalakan proyek fraud yang ditunda.

## TUI

```sh
harmonika core capabilities
harmonika core list
harmonika core inspect memory.set
printf '%s' '{"key":"language","value":"Kotlin"}' | harmonika core invoke memory.set --idempotency-key remember-demo-001
```

Guide `harmonika-shared-core` mengarahkan pemilihan tool otomatis sesuai kebutuhan.
TUI hanya memakai interface internal; backend mentor dan credential tidak masuk
frontend. Pembaruan terminal tetap manual, ditandatangani dan dapat di-rollback.

`file.grep.path` adalah direktori workspace (contoh `.`), sesuai kontrak
engine coding; gunakan `file.read` untuk satu file tertentu.

`code.scaffold.name` mengikuti identifier engine: huruf pertama A–Z/a–z,
lalu huruf/angka/underscore, maksimal64 karakter (contoh `CoreDemo`).
