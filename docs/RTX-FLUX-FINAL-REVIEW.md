# Final Review Read-Only: Aktivasi FLUX.2 Klein 4B

- **Tanggal:** 2026-10-25
- **Objek:** `backend/rtx_flux/worker.py` (revisi), `backend/rtx_flux/install_windows.py` (revisi), `scripts/deploy-flux-model.py`, file stage di VPS `/opt/harmonika-ai/flux-stage/`
- **Mode:** Review read-only. Tidak ada GPU render dijalankan, tidak ada file diubah, tidak ada service di-restart, worker lama 18182 dan tunnel lama tidak disentuh, tidak memanggil Claude.
- **Verifikasi live (semua read-only):** baseline hash, qualification/visual report, config queue, unit systemd, tunnel 18211/18182, health worker baru via tunnel (GET saja).

## 1. Kesimpulan: **GO — tidak ada blocker aktivasi yang ditemukan**

Seluruh gerbang aktivasi script terpenuhi **saat review ini**:

| Gerbang deploy-flux-model.py | Status terverifikasi |
|---|---|
| `qualification-report.json` status `technical_passed_visual_review_required`, tepat 5 cases | ✓ (5 case; artifact kualifikasi ada: interior/typography/technician/router-product/indonesia-future) |
| `visual-review.json` approved=true + model cocok | ✓ approved=True |
| Health `127.0.0.1:18211` ok+approved+model | ✓ `{"ok":true,"model":"black-forest-labs/FLUX.2-klein-4B","cuda":true,"loaded":true,"busy":false,"approved":true,"recovery_required":false}` — warmup selesai, tanpa karantina |
| Baseline hash router/bridge/config == file live | ✓ ketiganya identik (dd22bee…/18862db…/ce6a80c…) — tidak ada drift |
| Config queue endpoint masih 18182 + mode legacy | ✓ (`assert cfg['endpoint']=='http://127.0.0.1:18182'` akan lolos; mode legacy cocok untuk kontrak worker baru) |
| Queue idle + engine idle | Terverifikasi implisit via struktur; dicek runtime oleh script |
| Tunnel 18211 aktif | ✓ `harmonika-flux-tunnel.service` running; 18182 (`harmonika-rtx-tunnel.service`) tetap running ✓ terisolasi |
| `harmonika-ai` = node api/server.js | ✓ (ExecStart `/usr/bin/node /opt/harmonika-ai/api/server.js`) — restart target benar untuk memuat router baru |
| Rollback path | ✓ restore 3 file + config dari backup 0700, restart harmonika-ai + queue; worker lama 18182 tetap hidup sebagai standby (tidak pernah disentuh script) |

File stage minimalis dan benar:
- **router**: hanya `WORKER 18182 → 18211` (diff 1 baris) — tidak ada perubahan kontrak lain.
- **bridge**: URL 18211, pin model → FLUX, timeout 45→110 s, ekspektasi ukuran `(512,512)` → `(1024,1024)` + width/height return 1024 — konsisten dengan remap native 1024 worker baru.

Revisi worker yang diminta sudah benar diterapkan:
- `PROMPT_LIMIT=12000` **byte** (worker.py:10,31) — H1 tertutup (queue 12000 byte tidak akan 422-uncertain lagi).
- finally bounded 8 s + karantina `uncertain_pid` **tanpa** `/interrupt` global (worker.py:129-148) — fail-closed sesuai permintaan; `/generate` → 503 `gpu_job_recovery_required`; health `recovery_required:true`, ok=false.
- Health `asyncio.wait_for(...,1.5)` (worker.py:92) ✓.
- Task exception retrieved di kedua spawn (worker.py:52,157) ✓.
- `refresh_approval()` dipanggil di health+generate (revokasi approval efektif seketika) ✓; `warmed` hanya true setelah render sukses (worker.py:127) ✓; boot warmup saat approved (worker.py:50-52) ✓; `im.mode in ('RGB','RGBA')` eksplisit (worker.py:125) ✓.
- Installer: file model baru `flux-2-klein-4b-fp8.safetensors` (BFL, sha 97ed34fe…) + `qwen_3_4b_fp4_flux2.safetensors` (FP4 Comfy, sha 3eab03a7…) + VAE — **cocok dengan nama di workflow worker** ✓; gerbang `assert torch.cuda.is_available()` ✓; cek `cuda_base_missing` ✓; per-socket `source_address` dari `download-interface.json` (hanya IPv4 privat, proxy dinonaktifkan via `ProxyHandler({})`) ✓ tanpa perubahan routing global ✓; mirror lokal `download-source.json` dipin ke `127.0.0.1:18205` (unet tetap dari HF BFL pinned-rev) ✓; log error unduhan metadata-saja (tanpa secret) ✓.

## 2. Risiko sisa (bukan blocker) + diff usulan

### R1 (PROSEDURAL — jadikan syarat GO): selesaikan retest reserve-vram 2.0 GiB SEBELUM deploy
Script menuntut `status=='technical_passed_visual_review_required'` + tepat 5 cases. Jika retest menghasilkan ulang report dengan skema/status/case-count berbeda, assert gagal (safe-abort) — pastikan report final memakai skema yang sama. Peak 11663/12227 MiB (margin ~564 MiB) memang layak diretest; jika retest gagal, jangan deploy sampai turun ke konfigurasi lain (mis. pertahankan `/free unload_models` per-render — sudah aktif dan angka 13-16 s terukur menyertainya).

### R2 (MEDIUM — race window restart): cek idle kedua sesaat sebelum `systemctl restart harmonika-ai`
Cek `ss established sport = :17890` (baris 21) terjadi ~1-2 s sebelum restart (baris 33) — request/SSE yang datang di jendela itu akan terpotong (AGENTS: jangan hentikan writer sesi pengguna). Jalankan di jendela maintenance sepi + tambahkan cek ulang:

```diff
   cfg=json.loads(CONFIG.read_text());assert cfg['endpoint']=='http://127.0.0.1:18182';cfg['endpoint']='http://127.0.0.1:18211'
   tmp=CONFIG.with_suffix('.flux-next');tmp.write_text(json.dumps(cfg));tmp.chmod(0o600);os.replace(tmp,CONFIG)
+  assert not run('ss','-Htn','state','established','sport','=',':17890'),'engine_has_active_request_late'
   run('systemctl','restart','harmonika-ai');run('systemctl','start','harmonika-rtx-queue');stopped=False
```

### R3 (MEDIUM — recovery gap): karantina `uncertain_pid` bisa permanen jika ComfyUI restart
History ComfyUI in-memory: restart ComfyUI menghapus history → pid tak pernah muncul di `/history` → `recovery_required:true` selamanya hingga worker process di-restart operator. Jalur clear di health (worker.py:95-101) hanya cek history. Usul: terima juga "pid tidak ada di /queue" sebagai bukti terminal:

```diff
                 h=await asyncio.wait_for(self.request('GET','/history/'+self.uncertain_pid),1)
-                if self.uncertain_pid in h:
+                q=await asyncio.wait_for(self.request('GET','/queue'),1)
+                rows=q.get('queue_running',[])+q.get('queue_pending',[])
+                if self.uncertain_pid in h or not any(len(r)>1 and r[1]==self.uncertain_pid for r in rows):
                     await asyncio.wait_for(self.request('POST','/history',json={'delete':[self.uncertain_pid]}),1)
                     self.uncertain_pid=None
```
Runbook operator tetap wajib: jika `recovery_required` persisten, verifikasi GPU idle (`/queue` kosong) → baru restart proses worker (bukan saat scope audit ini).

### R4 (LOW — prosedural): urutan approval→boot pada siklus kualifikasi berikutnya
Warmup hanya di-spawn saat boot jika `approval.json` sudah ada. Skema saat ini aman (worker sudah warmed, approval persisten, restart berikutnya auto-warmup). Untuk kualifikasi berikutnya: tulis approval **sebelum** start worker, ATAU satu render `X-Harmonika-Qualification: local-test` setelah approval. Usul hardening opsional (re-arm warmup saat approval False→True di `refresh_approval`).

### R5 (LOW): 429 warmup-vs-queue → unknown
`429` dari worker dipetakan adapter `worker_busy` uncertain=True → job unknown. Setelah aktivasi ini tidak terjadi (warmup selesai, queue serial). Hanya relevan jika local-test render berjalan bersamaan dengan job queue — disiplin operator: jangan local-test saat queue men dispatch.

### R6 (LOW — pasca-aktivasi): housekeeping output ComfyUI
Setiap render (+warmup) meninggalkan PNG di `ComfyUI/output/harmonika-flux-*.png` tanpa retensi. Tambahkan pembersihan berkala (mis. hapus >7 hari) sebagai tugas terpisah — bukan blocker.

### R7 (INFO): margin timeout
- bridge 110 s vs jalur terburuk worker ~95+8+4+overhead ≈ 107 s — tipis tapi heartbeat menjaga socket; real 13-16 s.
- router 90 s < cap render 95 s: render yang mendekati cap akan diputus router (fallback + cooldown 300 s) sementara worker menyelesaikan shielded — by design (H3 audit #2), tidak relevan pada 13-16 s.

### R8 (INFO — opsional): masukkan `harmonika-flux-tunnel` ke daftar guard PID
`services` menjaga 6 unit (termasuk tunnel lama) agar tidak berubah PID; tunnel baru 18211 tidak dijaga. Script tidak me-restart-nya, jadi aman; menambahkannya memperketat bukti "unrelated_service_changed". Usul satu baris pada list `services`.

### R9 (INFO): post-rollback verification
Rollback mengembalikan 3 file + config ke 18182 dan menyalakan queue, tetapi tidak memverifikasi health pasca-rollback. Nice-to-have: poll singkat `18186/health` (worker lama) sebelum menyatakan rolled_back sukses.

## 3. Checklist eksekusi aktivasi (untuk operator, setelah retest reserve-vram lulus)

1. Pastikan report kualifikasi final ber-skema sama (status + 5 cases) dan visual approved.
2. Jalankan di jendela sepi; verifikasi manual `ss -Htn state established 'sport = :17890'` kosong sebelum eksekusi.
3. `python3 scripts/deploy-flux-model.py` (root, di VPS) — baca output state JSON `active_pending_end_to_end`.
4. E2E wajib sebelum menyatakan aktif (urutan dari audit #1):
   - `node --test /opt/harmonika-ai/lib/rtx-image-router.test.js` (router live dengan WORKER baru via deploy; fixture tidak mem-pin model);
   - `/opt/harmonika-ai/rtx-queue-venv/bin/python -m unittest discover -s tests/rtx` (baseline);
   - `deploy/smoke.py` (job generate nyata via queue → 18211);
   - satu generate via API 17890 → manifest `provider:'rtx-local'`, model baru, dimensi 1024;
   - satu `generate_image` crush-bridge → jalur langsung 18211 (bukan fallback), PNG 1024;
   - negative: analyze → `vision_unavailable`; refs → `references_unavailable`; size liar → `invalid_size`; prompt 12.000 byte → diterima (H1 fix).
5. Monitor 1×24 jam: log `image_route provider:codex` (harus jarang saat PC online), `recovery_required` pada health 18211, VRAM worker.
6. Rollback siap: backup path tercatat di `flux-model-activation.json`; worker lama 18182 hidup; prosedur = jalankan bagian restore script (atau operator manual) — sudah diuji desainnya oleh except-path.

## 4. Status item review sebelumnya

| Item audit #2 | Status di revisi |
|---|---|
| H1 prompt 12000 byte | ✓ diterapkan (worker.py:10,31) |
| H2 finally bounded + tanpa global interrupt | ✓ 8 s + karantina pid (bukan `/interrupt`) — sesuai permintaan; sisa gap R3 |
| H3 render cap | ✓ 95 s (+8+4 bounded) < 120 s adapter; router 90 s残留 R7 (by design) |
| M1 loaded jujur + warmup | ✓ warmed hanya setelah render sukses; boot warmup; catatan prosedural R4 |
| M2 health 1.5 s | ✓ |
| M3 installer CUDA gate | ✓ + cek cuda_base_missing + log pip |
| M4 prepare/exception | ✓ prepare di dalam try; exception retrieved |
| Audit #1: pin crush-bridge | ✓ via staged bridge (model + 1024 + timeout 110 s) |
| Audit #1: isolation 18182 | ✓ terkonfirmasi live: dua tunnel terpisah, script tidak menyentuh unit lama |

---
*Final review read-only; tidak ada file diubah, tidak ada service dihentikan/di-restart, tidak ada GPU render dijalankan. Semua diff di atas adalah usulan, bukan perubahan yang diterapkan.*
