# Harmonika TUI Engine (rebranded OpenCode build)

Mesin TUI **Harmonika** adalah build ulang dari proyek sumber terbuka `opencode` (MIT) yang di-rebrand,
dipatch, dan diarahkan ke provider **Harmonika AI** (`https://chat.harmonika.id/v1`).
Direktori ini berisi **patch, template konfigurasi, launcher, dan installer** yang membuat clone tersebut
reproducible: setelah install, pengguna hanya perlu memasukkan **API key Harmonika**.

## 1. Status clone (audit jujur)

| Bagian | Status |
| --- | --- |
| Nama CLI & versi (`harmonika v1.0.0`) | ✅ di-rebrand (`packages/cli/script/build.ts`) |
| Direktori config (`~/.config/harmonika-terminal`) | ✅ di-rebrand (`packages/util/src/global.ts`) |
| Nama file config (`harmonika.json` didahulukan) | ✅ (`packages/core/src/config/discovery.ts`) |
| Logo & wordmark TUI `HARMONIKA AI` | ✅ (`packages/tui/src/logo.ts`, `component/logo.tsx`) |
| Warna/tema & label input/output berbahasa Indonesia | ✅ (`util/presentation.ts`) |
| Provider & model (`harmonika-ai`, `-codex` → *Harmonika Ai Pro*, `-google` → *Harmonika Ai Fast*) | ✅ template `config/harmonika.json` |
| Identitas asisten (Harmonika Ai dari Harmonika ID) | ✅ via `HARMONIKA.md` + system prompt di sisi server |
| Nama internal (`@opencode/*`, `OPENCODE_*`, schema URL) | ⛔ sengaja dibiarkan agar patch tetap kecil dan mudah di-rebase ke upstream |
| Binary 213 MB & `node_modules` | ⛔ tidak masuk repo |
| `mcp_server.py` (berisi token layanan) | ⛔ tidak masuk repo; dipasang oleh paket terminal |

Nama internal yang tersisa **tidak terlihat pengguna**: yang tampil di layar, help, config path, dan
nama file konfigurasi sudah memakai “harmonika”. Menyisakan namespace internal membuat rebase ke upstream
berikutnya tetap sederhana.

## 2. Build dari sumber (reproducible)

```bash
# 1) clone upstream pada branch yang dipakai fork ini
git clone --branch dev https://github.com/anomalyco/opencode.git harmonika-tui-src
cd harmonika-tui-src

# 2) terapkan patch rebrand Harmonika
git apply /path/to/harmonika-tui/engine/patches/harmonika-rebrand.patch

# 3) install dependency & build binary
bun install
cd packages/cli
OPENCODE_TARGET=darwin-arm64 bun run script/build.ts   # ganti target: linux-x64, windows-x64, ...

# 4) pasang binary ke engine
mkdir -p /path/to/harmonika-engine/bin
cp -R dist/cli-darwin-arm64/bin/opencode /path/to/harmonika-engine/bin/harmonika
```

Target yang didukung `build.ts`: `darwin-arm64`, `darwin-x64`, `linux-x64`, `linux-arm64`, `windows-x64`.

## 3. Install untuk pengguna akhir (cukup API key)

```bash
sh engine/install.sh                 # interaktif: menanyakan API key
export PATH="$HOME/.local/bin:$PATH"
harmonika
```

Opsi:

```bash
sh engine/install.sh --api-key hai_live_xxxxxxxx
sh engine/install.sh --engine-home /opt/harmonika --no-launcher
sh engine/install.sh --mcp-server ~/.config/harmonika-terminal/mcp_server.py
```

Yang dilakukan installer:

1. Menyimpan key ke `~/.config/harmonika/harmonika-api-key` (mode `0600`).
2. Merender `~/.config/harmonika-terminal/harmonika.json` dari `config/harmonika.json`
   dengan `apiKey: "{env:HARMONIKA_API_KEY}"` — **key tidak pernah ditulis ke config**.
3. Membuang blok `mcp` bila server MCP tidak ditemukan (agar tidak ada tool yang error di mesin baru).
3b. Bila `runtime/license.py` tersedia, key diverifikasi ke server dulu (termasuk aturan
   1 key = 1 perangkat). Jika verifikasi gagal (offline/kuota/sudah terikat), installer tetap
   menyimpan key dan memberi peringatan agar bisa diaktifkan ulang lewat `harmonika-setup`.
4. Membuat `~/.config/harmonika/HARMONIKA.md` (aturan identitas + folder auto-save).
5. Membuat `~/Downloads/harmonika-ai/{gambar,document,zip,pdf}`.
6. Memasang `~/.local/bin/harmonika` (launcher) dan `~/.local/bin/harmonika-setup` (ganti key).

Ganti key kapan saja:

```bash
harmonika-setup                 # interaktif
harmonika-setup --show          # cek key tersimpan (terpotong)
```

## 4. Cara launcher bekerja

`engine/harmonika`:

- membaca `HARMONIKA_API_KEY`, atau file key `0600` di atas;
- mengekspor `HARMONIKA_GLOBAL_CONFIG` / `OPENCODE_CONFIG_DIR` agar engine memakai config Harmonika;
- mencari binary di `$HARMONIKA_ENGINE_HOME/bin/harmonika` lalu `~/.local/share/harmonika-engine/bin/harmonika`;
- melepas atribut quarantine macOS sebelum eksekusi;
- keluar dengan pesan jelas bila key atau binary belum ada.

## 5. Batas kemampuan

- Binary yang dibangun hanya untuk satu target OS/arsitektur per build; build silang butuh toolchain masing-masing.
- Fitur yang bergantung pada layanan (gambar, vision, video, dokumen) butuh MCP server + tunnel ke worker;
  tanpa itu chat tetap berjalan, hanya tool terkait yang tidak tersedia.
- Kebijakan penggunaan ada di [`../SAFETY.md`](../SAFETY.md).

## 6. Skrip rilis & verifikasi

| Skrip | Fungsi |
| --- | --- |
| `scripts/build-engine.sh [target...]` | Build binary dari fork (`darwin-arm64` default, web UI selalu ikut). |
| `scripts/smoke-engine.sh <binary>` | 8 pemeriksaan: versi, brand, absennya endpoint update upstream, `debug paths`, `debug config`, `models`, web UI HTTP 200, penolakan `--method npm`. |
| `scripts/release-engine.sh <versi> [--build] [--upload]` | Manifest `engine/update.json` (+ `.sig` Ed25519) dan unggah binary ke `chat.harmonika.id`. |
| `scripts/verify-patches.sh` | Apply seluruh patch ke commit upstream lalu bandingkan tree dengan `harmonika/main`. |

Contoh rilis penuh:

```bash
engine/scripts/build-engine.sh darwin-arm64 linux-x64 linux-arm64 windows-x64
engine/scripts/smoke-engine.sh engine/bin/harmonika-darwin-arm64
engine/scripts/release-engine.sh 1.0.1 --upload
```

`harmonika upgrade` membaca manifest itu, memverifikasi `sha256`, mencadangkan binary lama ke `.bak`,
lalu mengganti binary berjalan (di Windows file baru disiapkan sebagai `<exe>.<versi>.new`).
Tidak ada jalur ke npm, Homebrew, WinGet, atau API rilis pihak lain.

## 7. Perintah bahasa yang dipakai sehari-hari

```bash
harmonika                 # TUI
harmonika serve --port N  # API + web UI (browser)
harmonika models          # hanya provider Harmonika
harmonika upgrade         # pembaruan engine dari manifest Harmonika ID
harmonika-setup           # ganti API key
```

## 8. Status modul

Lihat [`../docs/ENGINE-MODULE-STRATEGY.md`](../docs/ENGINE-MODULE-STRATEGY.md) bagian *Status pelaksanaan*:
web UI, LSP, formatter, agents, commands, snapshots aktif lewat konfigurasi; `share`, `pair --remote`,
dan seluruh jalur distribusi upstream dimatikan.
