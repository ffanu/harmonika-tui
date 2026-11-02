# Strategi Mengambil Modul Fitur OpenCode ke Harmonika AI

Dokumen ini menjawab satu pertanyaan: bagaimana Harmonika AI bisa memiliki modul fitur
OpenCode (web UI, LSP, agents, plugins, snapshots, share, dst.) **tanpa** terjebak
memelihara fork besar yang terus menyimpang dari upstream.

## 1. Kondisi terukur saat ini (bukti, bukan asumsi)

| Metrik | Nilai | Arti |
| --- | --- | --- |
| Perubahan rebrand | 11 file, +58 / -35 baris | Fondasi bagus: rebrand memang tipis |
| Commit di fork | **0 commit**, masih working tree kotor | Belum benar-benar fork |
| Tertinggal dari `origin/dev` | **47 commit** | Sudah menumpuk utang rebase |
| Env var `OPENCODE_*` dibaca runtime | 15 nama, 64+ titik | Rebrand env belum tuntas |
| Env var `HARMONIKA_*` | 2 nama | Belum ada lapisan kompatibilitas |
| `serve` untuk web UI | HTTP 404, tanpa asset UI | Modul web tidak ikut di-build |

Kesimpulan: masalahnya **bukan** kekurangan fitur, tapi **model pemeliharaan**. Selama
rebrand hidup sebagai working tree di atas checkout upstream basi, setiap `git pull` atau
`harmonika upgrade` berisiko menghapus Harmonika.

## 2. Prinsip: brand sebagai lapisan tipis, fitur diambil lewat flag

Urutan prioritas pengambilan fitur, dari termurah ke termahal:

1. **Pakai apa yang sudah ada di binary** (config/flag) - nol patch.
2. **Provision dependensi** saat install (LSP, formatter) - nol patch.
3. **Aktifkan plugin** milik upstream - patch kecil di config.
4. **Patch terpusat** lewat satu paket brand - patch tetap kecil.
5. **Fork modul** hanya bila upstream tidak menyediakan hook. Pilihan terakhir.

Target yang sehat: patch rebrand **<= 15 file** dan rebase ke upstream baru selesai
< 1 jam, dengan semua fitur di atas tetap jalan.

## 3. Fase 0 - Jadikan fork benar-benar fork (prioritas tertinggi)

```bash
cd harmonika-tui-src
git remote add upstream https://github.com/anomalyco/opencode.git  # bila belum ada
git checkout -b harmonika/main
git add -A && git commit -m "brand: harmonika terminal rebrand (11 files)"
git tag brand-v1
```

Aturan selanjutnya:

- `harmonika/main` **hanya** berisi commit brand; tidak ada fitur bisnis di situ.
- Rebase mingguan: `git fetch upstream && git rebase upstream/dev`.
- Ekspor patch untuk repo publik `harmonika-tui`:
  `git format-patch brand-v1..HEAD -o engine/patches/`.
- CI menolak rilis bila patch gagal apply ke `upstream/dev` terbaru.

## 4. Fase 1 - Satukan brand di satu paket

Buat `packages/brand` sebagai satu-satunya sumber kebenaran:

```ts
// packages/brand/src/index.ts
export const Brand = {
  product: "Harmonika AI",
  cliName: "harmonika",
  version: "1.0.0",
  channel: "stable",
  vendor: "Harmonika ID",
  website: "https://harmonika.id",
  apiBase: "https://chat.harmonika.id/v1",
  configDirName: "harmonika-terminal",
  configNames: ["config.json", "harmonika.json", "opencode.json", "opencode.jsonc"],
  projectDirNames: [".harmonika", ".opencode"],
  envPrefix: "HARMONIKA",
  envFallbackPrefix: "OPENCODE",
  dbName: "harmonika",
} as const
```

Lalu ganti call-site agar membaca `Brand.*` (logo, presentasi, discovery, global, app).
Efek: rebrand tersisa di **satu paket + lima import**, dan nama internal `@opencode/*`
tidak perlu disentuh (aman saat rebase).

**Lapisan kompatibilitas env** (penting, saat ini belum ada): setiap env dibaca sebagai
`HARMONIKA_X ?? OPENCODE_X`, dan launcher mengekspor keduanya. Ini yang membuat
`HARMONIKA_CONFIG_DIR` dan `OPENCODE_CONFIG_DIR` tidak saling bertentangan.

## 5. Fase 2 - Ambil modul yang tertinggal tanpa fork

| Modul | Cara mengambil | Patch |
| --- | --- | --- |
| **Web UI browser** | Build tanpa `--skip-web-ui` (`packages/web` + `buildAppArchive`), buka via `harmonika serve` / `pair` | 0 |
| **LSP 5 bahasa** | Provision `typescript-language-server`, `pyright-langserver`, `gopls`, `vscode-{html,css,json}-language-server` saat install, set `lsp` di config | 0 |
| **Websearch** | Aktifkan plugin provider upstream (`firecrawl`/`parallel`/`tinyfish`) + API key | 0 |
| **Formatter** | `formatter` di config + pasang `prettier`, `gofmt` | 0 |
| **Agents / subagents** | Definisikan `agents` (plan/review/explore) + `commands` di config | 0 |
| **Snapshots / undo** | `snapshots: true` | 0 |
| **Plugins** | Pakai mekanisme `plugin` untuk perilaku khas Harmonika (bukan patch core) | 0-1 |
| **Experimental** (schedules, artifacts, policies) | Preset `harmonika-safe` yang menyalakan satu per satu setelah lolos smoke | 0 |
| **Desktop / VS Code / console** | Butuh build & distribusi terpisah; tunda sampai binary stabil | - |

Semua di atas **tidak menambah risiko rebase**, karena memakai hook resmi upstream.

## 6. Fase 3 - Ganti layanan yang terikat infra upstream

Modul berikut bergantung pada akun/infra OpenCode, jadi tidak boleh dibiarkan
menunjuk ke upstream:

| Modul | Tindakan |
| --- | --- |
| `harmonika upgrade` | Arahkan ke kanal rilis Harmonika (`chat.harmonika.id`) + changelog `[Y] Yes / [N] No / [L] Later`. **Jangan** biarkan mengunduh OpenCode upstream. |
| `shared` / share session | Nonaktifkan (`share: disabled`) sampai ada endpoint `harmonika.id`, atau host sendiri. |
| `pair --remote` (OpenTunnel) | Blokir atau arahkan ke tunnel sendiri; string `opentunnel` muncul 67x di binary. |
| `stats` (mencetak opencode.ai) | Ganti ke `harmonika.id` atau hapus subcommand. |
| Provider `opencode/*` + OAuth | Sembunyikan dari `models`/pemilih model agar pengguna tidak memakai kuota upstream. |
| `$schema` config | Host schema Harmonika sendiri, atau pertahankan URL upstream secara sadar. |

## 7. Fase 4 - Pipeline rilis yang menjaga rebrand

1. **Build matrix**: `bun install` lalu `build.ts` per target
   (`darwin-arm64/x64`, `linux-x64/arm64`, `windows-x64`), tanpa `--skip-web-ui`.
2. **Patch series** tersimpan di `engine/patches/` (`git format-patch`), bukan diff tangan.
3. **Smoke wajib** sebelum rilis: login API key, kirim chat, 1 tool call, MCP `harmonika`,
   LSP `ready`, `serve` mengembalikan 200 untuk UI, generate image, `debug config` bersih.
4. **Tanda tangan**: hash binary + manifest (pola yang sudah dipakai `binaries.json`).
5. **Guard**: hook pre-release yang menolak binary berisi string `github.com/anomalyco/opencode`
   pada jalur `upgrade`, atau `--method brew/npm` aktif.

## 8. Kepatuhan lisensi (wajib, bukan opsional)

OpenCode berlisensi **MIT**. Yang wajib dipertahankan:

- File `LICENSE` upstream beserta copyright notice aslinya.
- `NOTICE.md` / `UPSTREAM-LICENSE.md` yang menyebut proyek asal dan perubahan merek.
- Tidak menghapus atribusi di source yang diambil.

Rebrand nama produk diperbolehkan MIT, tetapi menghapus notice tidak.

## 9. Yang sebaiknya **tidak** dilakukan

- Rename massal namespace internal (`@opencode/*`, 419 kemunculan) - rebase jadi mimpi buruk
  tanpa manfaat yang terlihat pengguna.
- Menjalankan `harmonika upgrade` yang mengarah ke upstream - rebrand bisa hilang total.
- Menyalin seluruh `packages/*` ke repo Harmonika - dua sumber kebenaran, cepat menyimpang.
- Mengaktifkan semua `experimental.*` sekaligus - sulit dilacak saat ada regresi.

## 10. Ringkasan eksekusi

| Fase | Hasil | Risiko |
| --- | --- | --- |
| 0 | Fork nyata + patch series + rebase terukur | Rendah |
| 1 | Brand satu paket, patch <= 15 file | Rendah |
| 2 | Web UI, LSP, websearch, formatter, agents, snapshots hidup | Rendah (config) |
| 3 | Tidak ada lagi jalur ke infra upstream | Sedang |
| 4 | Rilis bisa diulang & diaudit | Rendah |

Urutan yang disarankan: **0 -> 1 -> 3 -> 2 -> 4**. Fase 3 didahulukan dari 2 karena
risiko brand/keamanan lebih mahal daripada risiko fitur yang belum ada.

## 11. Status pelaksanaan (terukur, bukan rencana)

Fase 0-4 dikerjakan pada branch fork `harmonika/main` dan diverifikasi ulang setelah build:

| Item | Hasil terukur |
| --- | --- |
| Fase 0 | Fork nyata: `harmonika/main` (2 commit brand), tag `brand-v1`, `upstream` remote ditambahkan. |
| Patch series | `engine/patches/0001..0002`; `engine/scripts/verify-patches.sh` membuktikan apply ke `8bca532` menghasilkan tree identik `662b31bd0589`. |
| Fase 1 | `packages/util/src/brand.ts` jadi satu-satunya sumber brand; `env()` membaca `HARMONIKA_*` lalu `OPENCODE_*`. Rebrand kini 17 file / +250 -113. |
| Fase 2 | Web UI tertanam: `serve` menjawab `GET /` **HTTP 200**, 9.489 byte HTML. `LSP`, `formatter`, `agents` (plan/review/explore), `commands` (test/ship), `snapshots: true` ada di template; entri yang binary-nya tidak ada otomatis dibuang oleh `render-config.py`. |
| Fase 3 | `upgrade` hanya memakai manifest `chat.harmonika.id/.../engine/update.json` (sha256 diverifikasi, binary diganti dengan cadangan `.bak`). `npm/pnpm/bun/yarn/vp/brew/winget` ditolak. Binary tidak lagi memuat `opencode.ai/update/api`. `pair --remote` butuh `HARMONIKA_ALLOW_REMOTE_TUNNEL=1`. `stats` mencetak `harmonika.id`. Provider `opencode/*` hilang dari `models` (0 baris) via `disabled_providers`. |
| Fase 4 | `engine/scripts/{build,smoke,release,verify-patches}-engine.sh`; smoke 8/8 lolos termasuk cek ketiadaan endpoint upstream dan web UI 200. |

Perintah verifikasi cepat:

```bash
engine/scripts/verify-patches.sh                  # tree patch == harmonika/main
engine/scripts/smoke-engine.sh engine/bin/harmonika-darwin-arm64
engine/scripts/release-engine.sh 1.0.1 --build    # manifest + tanda tangan
```

Sisa pekerjaan yang **belum** dikerjakan dan alasannya:

- **Desktop app, VS Code extension, console/web stats**: butuh pipeline build dan hosting terpisah; belum ada permintaan produk.
- **Websearch provider**: butuh API key pihak ketiga (firecrawl/parallel/tinyfish); template sengaja tidak menyalakannya agar tidak ada fitur yang gagal senyap.
- **Rename `.opencode/` -> `.harmonika/` di project**: `Brand.projectDirs` sudah menyiapkannya, tetapi pemetaan masih membaca `.opencode`; migrasi butuh keputusan produk.
- **LSP live `ready`**: config sudah menyiapkan 6 server; instalasi binary-nya bergantung pada `npm`/`go`/`pip` di mesin pengguna (installer melaporkan bila dilewati).
- **Rebase 47 commit upstream**: sengaja ditunda sampai patch series ini stabil dan teruji.

## 12. Lanjutan: A2, A3, A5, A4, A1 (selesai)

| Item | Hasil terukur |
| --- | --- |
| **A2** LSP + formatter nyata | `typescript-language-server`, `vscode-{html,css,json}-language-server`, `prettier` (npm) dan `pyright-langserver` (venv) terpasang. Config hasil install: LSP = `typescript, python, html, css, json`; formatter = `prettier`. `go` otomatis dibuang karena `gopls` tidak ada. |
| **A3** Direktori proyek `.harmonika/` | `discovery.ts` memakai `Brand.projectDirs`; `mcp/add.ts`, `plan.ts`, `retained-image.ts`, `updater.ts` mengikuti. Proyek dengan `.opencode/` lama tetap terbaca, dan `plan` tidak memindahkan direktori yang sudah berisi rencana. |
| **A5** Prompt `[Y] Yes / [N] No / [L] Later` | Changelog dari `manifest.notes` tampil sebelum prompt. Uji PTY: memilih **Later** menghasilkan `Ditunda. Harmonika akan menanyakan lagi...` dan marker `.harmonika-update-deferred` berisi `1.0.9`. Non-TTY otomatis lanjut; `HARMONIKA_UPGRADE_ASSUME_YES=1` memaksa lanjut, `HARMONIKA_UPGRADE_PROMPT=1` memaksa prompt. Deteksi TTY memakai `stdin` **atau** `stdout` karena Bun tidak selalu menandai stdin. |
| **A4** Binary semua platform | `darwin-arm64` 230 MB, `darwin-x64` 244 MB, `linux-x64` 257 MB, `linux-arm64` 252 MB, `windows-x64.exe` 264 MB. Manifest memuat 5 platform; sha256 tiap binary diverifikasi cocok antara lokal dan server. |
| **A1** Rebase 47 commit upstream | `git rebase origin/dev` ke `67139d6` **tanpa konflik**; patch series diperbarui jadi 3 commit dan `verify-patches.sh` membuktikan reproduksi tree. Seluruh binary A4 dibangun dari source yang sudah ter-rebase. |

Verifikasi cepat:

```bash
engine/scripts/verify-patches.sh                       # tree patch == harmonika/main
engine/scripts/smoke-engine.sh engine/bin/harmonika-darwin-arm64
curl -s https://chat.harmonika.id/assets/harmonika-terminal/engine/update.json | python3 -m json.tool
```
