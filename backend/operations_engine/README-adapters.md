# Operations scanner dan browser adapters

Modul ini dipanggil service privat sesudah dispatcher mengautorisasi proyek.
Tidak ada endpoint publik, bearer token, provider atau akses sesi pengguna di sini.
`capabilities(config)` menunjukkan pemasangan; hasil scan/run nyata tetap diperlukan.

```python
config = {
    'project_root': '/absolute/canonical/authorized-project',
    'artifacts_root': '/absolute/private/artifacts',  # direktori pemilik service, 0700
    'scanner_policy': {'semgrep': True, 'trivy': ['vuln', 'misconfig', 'secret']},
    'browser_policy': True,
    'tools': {
        'semgrep': '/absolute/semgrep-venv/bin/semgrep',
        'trivy': '/absolute/trivy',
        'trivy_cache': '/absolute/prewarmed-trivy-cache',
        'node': '/absolute/node22/bin/node',
        'playwright_module': '/absolute/registered/node_modules/playwright',
        'versions': {'semgrep': '1.185.0', 'trivy': '0.76.2'},
    },
}
```

Default pin CLI adalah Semgrep **1.185.0** dan Trivy **0.76.2**. Operator dapat
menentukan pin tepat yang telah diuji melalui `tools.versions`; payload tidak
bisa mengganti executable, versi, rule, argumen, config maupun lokasi cache.
Browser memakai instalasi Playwright terdaftar, dengan Chromium terpasang;
fixture nyata diuji pada Playwright **1.64.1**, Node **22.23.2** dan macOS.
Backend Linux perlu user service non-root dan dukungan sandbox Chromium.
`chromiumSandbox: true` selalu diterapkan; kegagalan launch tetap `needs_toolchain`.

## Scanner

```python
from backend.operations_engine.scanners import scan, capabilities
scan(config, {'scanner': 'semgrep', 'path': '.'})
scan(config, {'scanner': 'trivy', 'mode': 'vuln', 'path': '.'})
scan(config, {'scanner': 'trivy', 'mode': 'misconfig', 'path': 'deploy'})
scan(config, {'scanner': 'trivy', 'mode': 'secret', 'path': 'src'})
```

Mode Semgrep menggunakan tiga rule lokal berhash: evaluasi dinamis Python,
JavaScript/TypeScript serta `subprocess(..., shell=True)` Python. Ini audit
bercakupan jelas, bukan katalog aturan komersial penuh. Mode Trivy misconfig
menambahkan rule Rego lokal berhash untuk `USER root` Dockerfile serta checks
lokal bawaan versi binary. Secret scanning tidak mengeluarkan `Match`, potongan
source, nilai secret atau pesan scanner. DTO temuan selalu hanya
`rule`, `file` relatif, `line` (0 berarti tidak diketahui), `severity`.

Trivy vulnerability scan membutuhkan `tools.trivy_cache/db/trivy.db` dan
`db/metadata.json` format DB v2 yang sudah dipasang operator. Bila belum ada,
status `needs_toolchain`; adapter tidak mengunduh DB. Prefetch terpisah oleh
operator memakai binary terverifikasi:

```sh
/absolute/trivy image --download-db-only --cache-dir /absolute/prewarmed-trivy-cache --disable-telemetry --skip-version-check
```

Invocation scan tetap `trivy fs`, seluruh update DB/check/versi serta telemetry
dinonaktifkan, `--offline-scan`, `--cache-backend memory`, `--ignorefile` kosong
milik job dan config kosong eksplisit. Tidak memuat config/ignorefile proyek,
registry rules, image/container/daemon Docker atau script paket.
Semgrep memakai config lokal eksplisit, metrics/version check off, satu worker,
`--strict`, timeout per file, dan snapshot tanpa `.semgrepignore`/`.gitignore`.
Referensi flag: [Semgrep CLI](https://semgrep.dev/docs/cli-reference),
[Trivy fs CLI](https://trivy.dev/docs/latest/references/configuration/cli/trivy_filesystem/),
[Trivy custom checks](https://trivy.dev/docs/latest/tutorials/misconfiguration/custom-checks/).

Snapshot maksimum 512 file, 4 MiB per file dan 32 MiB total. Nama privat/hidden,
credentials, logs, uploads, node_modules dan vendor dikecualikan; symlink,
reparse point, special file, perubahan saat copy dan traversal ditolak.
Tidak ada source yang diimport/dieksekusi host. Temuan dan coverage tidak
mengandung full stdout/stderr. Output child maksimum 4 MiB dan 25 detik.
Target tidak didukung/hasil parsial tidak diberi klaim `passed: true`.
Berkas jar/binary/dependency vendor tidak termasuk snapshot; scan bukan SBOM
seluruh host atau sertifikasi bahwa aplikasi bebas kerentanan.

## Browser

```python
from backend.operations_engine.browser import run
run(config, {'path': 'index.html', 'steps': [
    {'action': 'fill', 'selector': {'by': 'test_id', 'value': 'name'}, 'value': 'Fixture'},
    {'action': 'click', 'selector': {'by': 'role', 'value': 'button', 'name': 'Save'}},
    {'action': 'assert_text', 'selector': {'by': 'css', 'value': '#result'}, 'value': 'Fixture'},
], 'viewport': {'width': 390, 'height': 844}})
```

Aksi: `click`, `fill`, `check`, `select`, `assert_visible`, `assert_text`,
`assert_count`, `assert_value`. Maksimum 24 langkah dan wajib ada assertion.
Selector bertipe `css`, `test_id`, atau `role`, bukan JavaScript/eval. Elemen
ambigu tidak lulus. Assertion teks/nilai/count menunggu kondisi secara berbatas;
payload tidak memuat URL luar, shell, snippet evaluator atau credential.
Pengisian elemen password/credential/token ditolak.

Server loopback **milik job ini** melayani snapshot statis; GET/HEAD browser
hanya ke origin tersebut. Tidak menjalankan npm/dev server proyek. CSP,
interception HTTP/WebSocket, service-worker blocking dan context baru mencegah
akses situs/sesi luar; popup dan download dibatalkan. Browser berjalan dengan
sandbox Chromium, tanpa profil login lama. Ini bukan sandbox jaringan OS bagi
aplikasi/server arbitrer: service/container deployment tetap milik operator.
Server dibatasi 256 request/64 MiB respons. Deadline worker 25 detik, stdout
128 KiB, artifact watcher 32 MiB; screenshot hanya viewport.

DTO hanya langkah/index/status/count dan metadata artifact. Screenshot/trace
berisi tampilan/DOM **privat** proyek terotorisasi, disimpan pada direktori
`browser_<random>/` privat. `retention.json` mencatat `created_at` dan
`expires_at = created_at + 86400`; service pruner wajib menghapus seluruh
job/artifact yang kedaluwarsa. Tidak ada cleanup direktori/proses pengguna.
ID artifact relatif, hash/byte aktual dan expiry dikembalikan; tidak ada path
host absolut, teks DOM, selector, isi form maupun exception mentah dalam DTO.

## Verifikasi

```sh
python3 -m unittest discover -s tests/operations_engine -p 'test_*.py'
```

`HARMONIKA_TEST_PLAYWRIGHT_MODULE` dan `HARMONIKA_TEST_BROWSER_NODE` memilih
runtime fixture terdaftar. Tes browser memakai Chromium nyata dan server
loopback fixture, termasuk assertion gagal, password field, jaringan luar,
popup/download/WebSocket. Bila runtime tidak ada, tes tersebut diskip secara
eksplisit; jangan melaporkan skip sebagai kelulusan fungsional.
Tes scanner lokal menggunakan fixture report/parser dan subprocess milik tes;
scan Semgrep/Trivy nyata masih wajib di host toolchain terpasang sebelum aktivasi.
