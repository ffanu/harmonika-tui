# Review Read-Only: Worker Baru FLUX.2 Klein (rtx_flux)

- **Tanggal:** 2026-10-25
- **Objek:** `backend/rtx_flux/worker.py`, `install_windows.py`, `start-comfy.ps1`, `start-worker.ps1` (repo lokal)
- **Mode:** Review read-only. Tidak ada file diedit, tidak ada service di-restart, worker lama 18182 tidak disentuh, tidak memanggil Claude.
- **Topologi dinyatakan:** ComfyUI 18190 (loopback, terkonfirmasi di `start-comfy.ps1`), worker 18191 (loopback, terkonfirmasi `worker.py:129`), tunnel baru 18192 (diurus Codex, tidak ada di file ini), worker lama 18182 terisolasi ✓.

## 0. Yang sudah benar (kontrak lama terjaga)

| Item | Bukti |
|---|---|
| `/health` keys lama `ok,model,device,cuda` (+`loaded`) | worker.py:65; model konstanta sama di health & generate → check `model===health.model` (router, crush-bridge) lolos |
| `/generate` terima `steps/guidance_scale` lama, diabaikan (tidak 4xx) | workflow() hard-code steps=4/cfg=1.0 |
| Heartbeat spasi 4 s < sock_read 15 s; header dikirim segera (`prepare` setelah task dibuat) → first-byte instan | worker.py:113,116-117 |
| Body JSON diakhiri satu `write` penuh; spasi hanya di awal → `JSON.parse` (JS) & `json.loads` (Python) toleran whitespace awal | worker.py:120; router `boundedJSON`, adapter `json_request` |
| Base64 standar + padding → lolos regex router `^[A-Za-z0-9+/]+={0,2}$` | `base64.b64encode` |
| Tidak mengaktifkan refs/vision: `reference_files/reference_images` → `references_unavailable`; adapter queue memblokir pre-flight, router langsung fallback Codex | worker.py:41 |
| Loopback-only di kedua proses; access_log=None (prompt tidak masuk log) | worker.py:129; start-*.ps1 |
| ComfyUI: pinned commit, `--disable-all-custom-nodes --disable-api-nodes --disable-metadata --lowvram --fp8_e4m3fn-text-enc --cpu-vae`, offline env | start-comfy.ps1:4,6 |
| `--disable-metadata` → PNG tanpa chunk teks → lolos validator chunk router (chunk kapital ditolak, `tEXt` tidak ada) | start-comfy.ps1 + rtx-image-router.js |
| Installer: sha256 per file model, resume Range-parts, zip-slip guard, receipt lisensi Apache-2.0 (jujur upstream sesuai AGENTS), status atomik `os.replace` | install_windows.py:16-54,62-67,78 |
| Privacy: `POST /history` delete setelah job terminal (prompt tidak menumpuk di history ComfyUI) | worker.py:104-105 |

## 1. Temuan prioritas TINGGI

### H1. Mismatch limit prompt: worker 4000 **karakter** vs queue 12000 **byte** → job unknown mengunci slot queue
- `worker.py:31` menolak `len(prompt)>4000` (karakter). `core.py submit` mengizinkan prompt ≤12000 **byte** dan **tidak memotong**. Prompt 5000 karakter ASCII lolos validasi queue, dikirim ke worker → 422 `invalid_request`.
- Di adapter legacy, 422 (bukan 404/401/403/429/2xx) → `worker_http_error` 503 dengan `uncertain=True` (POST) → job **unknown** → slot dispatch tunggal tertahan, `queue_ready=false`, perlu operator (worker legacy tidak punya recovery).
- Router JS aman (memangkas 4000 char sebelum kirim); jalur queue adalah yang berisiko.

**Rekomendasi diff (usulan, jangan diterapkan sebelum disepakati):**
```diff
- if not isinstance(prompt,str) or not 3<=len(prompt)<=PROMPT_LIMIT or not prompt.strip():raise ValueError('invalid_prompt')
+ # Mirror queue caller limit (12000 bytes) so a long-but-valid queue prompt
+ # cannot 422 into an uncertain/unknown adapter state.
+ if not isinstance(prompt,str) or not prompt.strip() or len(prompt.encode('utf-8'))>12000:raise ValueError('invalid_prompt')
```

### H2. Loop `finally` tak berbatas → gerbang `self.active` bisa terkunci permanen
- `worker.py:91-106`: `asyncio.timeout(110)` hanya membungkus polling; `finally` menunggu `pid` muncul di history/keluar dari queue ComfyUI **tanpa deadline**. GPU job yang hang (job tidak pernah terminal dan tetap di `queue_running`) → loop selamanya → `active` tetap True → semua `/generate` berikut 429 `worker_busy` selamanya. Router akan fallback Codex (aman bagi user) tapi jalur RTX mati diam-diam; queue mendapat `worker_busy` → job gagal.
- Juga menumpuk dengan H3: total durasi task = render(≤110 s) + finally(tak terbatas).

**Rekomendasi diff:**
```diff
-            if pid:
-                while True:
+            if pid:
+                try:
+                    async with asyncio.timeout(15):
+                        while True:
                             h=await self.request('GET','/history/'+pid)
                             ...  # unchanged body
-                try:await self.request('POST','/history',json={'delete':[pid]})
+                except TimeoutError:
+                    try:await self.request('POST','/interrupt')  # stop hung GPU job
+                    except Exception:pass
+                try:await self.request('POST','/history',json={'delete':[pid]})
```

### H3. Tumpukan budget timeout tidak konsisten antara worker vs 3 konsumen
| Konsumen | Total budget | Ukuran | Konsekuensi jika terlampaui |
|---|---|---|---|
| crush-bridge | 45 s | 512→**1024 native** | fallback ENGINE/Codex |
| rtx-image-router.js | 90 s | 768→**1024 native** | fallback + cooldown 300 s |
| rtx-queue adapter | 120 s total (`sock_read` 15 s — sudah diatasi heartbeat) | ≤1024 | `rtx_timeout_or_connection_lost` uncertain → unknown |

- Worker: cap render 110 s + finally tak berbatas (H2). Skenario job selesai di detik 112: router sudah putus di 90 (ok, shielded), **adapter putus di 120 jika finally molor** → unknown.
- Render 1024 fp8 + `--lowvram` + `--cpu-vae` (VAE decode di CPU!) sangat mungkin >90 s saat dingin/pertama → pola fallback-permanen di router.

**Rekomendasi diff:**
```diff
-            async with asyncio.timeout(110):
+            async with asyncio.timeout(100):  # 100s render + 15s bounded finally < 120s adapter total
```
Plus (non-diff): ukur latency warm 512/768/1024 saat kualifikasi; jika 1024 warm >85 s, keputusan eksplisit: turunkan `--cpu-vae` (VRAM 12 GB dengan fp8 text-enc ~4.3 GB + UNet fp8 offload berurutan kemungkinan muat) atau revisi budget router (perubahan VPS, di luar scope worker).

## 2. Temuan prioritas SEDANG

### M1. Cold-start model load terjadi di dalam render pertama; `health.loaded` berbohong
- `loaded=ready` hanya berarti "ComfyUI hidup + ada device CUDA" (worker.py:65). Bobot (UNet 7.75 GB + Qwen 8 GB + VAE) dimuat ComfyUI saat prompt pertama → render pertama bisa jauh >100 s → gagal di momen aktivasi.
- Header `X-Harmonika-Qualification: local-test` (worker.py:110) sudah tersedia untuk uji pra-approval — bagus.

**Rekomendasi:** (a) prosedur kualifikasi wajib: jalankan ≥1 render warmup via header local-test **sebelum** menulis `approval.json`; (b) diff opsional — `loaded` hanya true setelah render sukses pertama di proses ini:
```diff
  def __init__(...):...;self.warmed=False
  # dalam render(), sebelum return:
+             self.warmed=True
  # health():
- 'loaded':ready,
+ 'loaded':ready and self.warmed,
```
(konsumen lama tidak membaca `loaded`; perubahan ini hanya membuat sinyal jujur untuk monitoring.)

### M2. Health memakai timeout sesi 15 s — melampaui budget health konsumen (2-3 s)
- `self.request` untuk `/system_stats` mewarisi `ClientTimeout(total=15)`. ComfyUI hang (bukan mati) → health worker blocking sampai 15 s; konsumen (crush 2 s, router/adapter 3 s) sudah timeout duluan → dianggap offline (aman tapi health worker jadi tidak responsif).

**Rekomendasi diff:**
```diff
-            v=await self.request('GET','/system_stats')
+            v=await asyncio.wait_for(self.request('GET','/system_stats'),1.5)
```

### M3. Installer: reuse torch CUDA via `.pth` ke venv legacy — rapuh, tanpa gerbang verifikasi CUDA
- `install_windows.py:72-74` menulis `harmonika-cuda-base.pth` menunjuk `~/harmonika-image-worker/.venv/Lib/site-packages` lalu `pip install -r requirements.txt`. pip *biasanya* melihat dist-info torch lewat path `.pth` dan menganggap satisfied — **tetapi** tidak dijamin: jika legacy tidak punya `torchvision`/versi tak cocok dengan requirement ComfyUI, pip bisa menarik wheel **CPU-only Windows** (default PyPI) yang lalu men-shadow torch CUDA → ComfyUI jalan di CPU → `devices` tanpa `cuda` → worker tidak pernah ready.
- Tidak ada langkah verifikasi `torch.cuda.is_available()` setelah instalasi; `.pth` ditulis meski path legacy tidak ada (gagal senyap).

**Rekomendasi diff:**
```diff
  site=ROOT/'.venv/Lib/site-packages';legacy=Path.home()/'harmonika-image-worker/.venv/Lib/site-packages'
+ if not (legacy/'torch').is_dir():raise RuntimeError('legacy_cuda_torch_not_found')
  (site/'harmonika-cuda-base.pth').write_text(str(legacy)+'\n')
- subprocess.run([str(py),'-m','pip','install','--disable-pip-version-check','-r',str(comfy/'requirements.txt'),'fastapi','uvicorn'],check=True)
+ subprocess.run([str(py),'-m','pip','install','--disable-pip-version-check','-r',str(comfy/'requirements.txt'),'fastapi','uvicorn'],check=True)
+ cuda=subprocess.run([str(py),'-c','import torch;exit(0 if torch.cuda.is_available() else 1)']).returncode
+ if cuda!=0:raise RuntimeError('torch_cuda_unavailable_after_install')
```
Catatan: `installed-requirements.lock` dari `pip freeze --local` tidak memuat paket via `.pth` (torch) → receipt tidak lengkap; simpan juga output `pip freeze` tanpa `--local` atau catat versi torch legacy di receipt.

### M4. `prepare()` di luar try + eksepsi task tidak pernah diambil (noise saat client disconnect)
- worker.py:113: jika client putus tepat sebelum `prepare`, ConnectionError lempar keluar handler (log error aiohttp, tidak fatal). Saat disconnect di tengah heartbeat, task lanjut (by design ✓) tapi jika task berakhir exception, tidak ada yang mengambil `task.result()` → warning "Task exception was never retrieved".

**Rekomendasi diff:**
```diff
-        response=web.StreamResponse(status=200,headers={'Content-Type':'application/json'});await response.prepare(req)
+        response=web.StreamResponse(status=200,headers={'Content-Type':'application/json'})
         try:
+            await response.prepare(req)
             while not task.done():
```
```diff
-        self.active=True;task=asyncio.create_task(self.render(data));self.tasks.add(task);task.add_done_callback(self.tasks.discard)
+        self.active=True;task=asyncio.create_task(self.render(data));self.tasks.add(task)
+        task.add_done_callback(self.tasks.discard)
+        task.add_done_callback(lambda t:t.exception() if not t.cancelled() else None)
```

## 3. Temuan prioritas RENDAH / catatan

1. **Semua 422 collapse ke `invalid_request`** (worker.py:108-109) — code spesifik (`invalid_prompt/invalid_dimensions/invalid_seed`) hilang; tidak merusak kontrak (konsumen hanya cek `ok`), tapi menyulitkan diagnosa. Usul: `getattr(e,'args',[ 'invalid_request'])[0]`.
2. **Remap dimensi asimetris** (worker.py:36-37): `768×768→1024×1024` ✓, tapi `768×1024` tetap 768×1024 (bukan native). Tidak ada konsumen saat ini mengirim non-square; dokumentasikan saja.
3. **crush-bridge mati di jalur langsung** sampai dua hal diperbarui: pin model `stabilityai/sd-turbo` (audit #1) **dan** ekspektasi ketat `im.size==(512,512)` + budget 45 s — worker baru selalu merender 1024. Konsekuensi: crush-bridge akan selalu fallback ke ENGINE (router→worker baru→1024, jalur ENGINE tidak cek 512). Ini perilaku yang bisa diterima sementara, tapi harus disadari (biaya Codex fallback).
4. **Batas ukuran PNG 16 MiB → b64 ≈ 22.9 MiB < 24 MiB** batas JSON router/adapter — aman, tapi marginya tipis; PNG FLUX 1024 realistis 1-2 MiB. Tidak perlu perubahan; jangan menaikkan cap binary tanpa mengecek 24 MiB JSON.
5. **`stop()` mengumpulkan task tanpa batas** (worker.py:51) — shutdown bisa menggantung mengikuti finally (terkait H2). Catatan operasional; tidak me-restart service di scope ini.
6. **`urlretrieve` tanpa timeout** untuk zip ComfyUI (install_windows.py:61) — potensi hang jarang; commit dipin + sha dicatat setelahnya (cukup).
7. **`quality:'1024'`, `busy`, `approved`, `seconds`, `seed`, `width/height`** adalah key tambahan — konsumen lama mengabaikan; `width/height` mengembalikan dimensi aktual (1024) — konsisten dengan validator PNG router yang membaca dimensi nyata, bukan request. ✓
8. **`validate()` dipanggil dua kali** (generate + render) — idempoten, hanya redundan kecil.
9. **`client_max_size=32768`** — cukup untuk semua konsumen (payload terbesar <1 KiB); kelebihan → 422 generik. ✓
10. **`type(x) is not int`** menolak bool/float dengan benar; range 512-1024 step 64 lebih ketat dari kontrak lama tapi superset dari semua nilai yang dikirim konsumen (512/768/1024). ✓

## 4. Ringkasan checklist yang diminta

| Permintaan | Status |
|---|---|
| Bug concurrency | **H2** (finally tak berbatas mengunci `active`); check-then-set `active` aman (event loop tunggal, tanpa await di antaranya); M4 (task exception noise); single-flight + heartbeat benar |
| Request body limit | 32 KiB (`client_max_size=32768`) memadai; overflow → 422 generik ✓; **H1** adalah body-limit serumpun (limit prompt vs queue) |
| Client disconnect | Shield benar (task tetap jalan, gerbang dipegang, GPU tidak diinterupsi) ✓; `prepare` di luar try (M4); heartbeat berhenti bersih di `ConnectionError` ✓ |
| Render deadline/finally | **H2 + H3**: deadline 110 s hanya untuk polling; finally unbounded; total vs adapter 120 s & router 90 s |
| PNG normalisasi | Verify-only (format+size+verify) tanpa konvert; aman karena `--disable-metadata` + PIL 8-bit RGB non-interlaced; usul kecil: `assert im.mode in ('RGB','RGBA')` eksplisit; tEXt tidak ada karena flag ComfyUI ✓ |
| Checksum download | Installer solid: Range-resume per part 64 MB, verifikasi 206+Content-Range, sha256 file final, parts dibersihkan, receipt. **M3** adalah gap dependensi, bukan checksum |
| Post-activation timeout | **M1 + H3**: bobot 16 GB dimuat pada render pertama (di dalam cap 100-110 s); wajib warmup pra-approval; `loaded` belum jujur; budget per konsumen lihat H3 |

## 5. Urutan tindakan yang disarankan (untuk Codex; semua di PC / repo worker baru)

1. Terapkan H1 (limit prompt 12000 byte) — mencegah deadlock queue via 422-uncertain.
2. Terapkan H2 (finally bounded 15 s + `POST /interrupt`) dan H3 (cap render 100 s).
3. Terapkan M2 (health 1.5 s), M4 (prepare/exception), M3 (gerbang CUDA installer + cek path legacy).
4. Kualifikasi: warmup render via header local-test di 512/768/1024 → catat latency warm → baru tulis `approval.json`; jika 1024 warm >85 s, putuskan opsi H3 sebelum tunnel 18192 diarahkan.
5. Baru arahkan tunnel 18192 → 18191 dan jalankan regresi audit #1 (`node --test rtx-image-router.test.js`, `unittest tests/rtx`, smoke.py, E2E manifest).
6. Lanjutkan pin crush-bridge (model + ekspektasi 512×512) sebagai tugas VPS terpisah — jalur langsungnya memang mati sementara, fallback ENGINE menutup fungsi.

---
*Review read-only; tidak ada file repo/production yang diubah, worker lama 18182 dan semua service tidak disentuh. Diff di atas adalah usulan, bukan perubahan yang diterapkan.*
