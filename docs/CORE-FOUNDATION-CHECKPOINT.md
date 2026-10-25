# Checkpoint fondasi bersama

- Sumber: `2.txt`, dibaca utuh; 99 ID unik. Phase 1 berisi14 ID meski label13.
- Prioritas: samakan kontrak dan discovery; reuse workspace/chat yang sudah ada;
  bangun pengolahan data terbatas, memory privat, dan workflow deterministik.
- Codex: registry, boundary REST/Go, routing, CLI, isolasi, deployment dan review.
  OpenCode: modul data dan tes; Codex memperbaiki validasi/numerik/chart setelah
  review. Worker Codex membantu encrypted state dan pemeriksaan independen.
  Claude tidak dilibatkan.
- Permission asal workspace tetap berlaku; `git.commit` uji berhenti pada approval,
  tanpa commit nyata. `system.info` memakai sandbox, bukan host produksi.
- Memory bukan training; terpisah per token, AES-GCM, TTL, kuota dan replay.
  Key autentikasi/storage berbeda; tidak masuk repo/paket/log.
- Workflow maksimal12 langkah; input eksplisit, DAG, urutan depends_on,
  berhenti saat error. Tidak ada shell/network/penjadwalan atau template dinamis.
- Chart menghasilkan spesifikasi Vega-Lite, bukan PNG/SVG hasil render.
- 60 gap tetap unavailable; enam dokumen tetap local_only. Tidak mengklaim99aktif.
- Fraud engine tetap ditunda; tidak ada perubahan database pelanggan/CS/ERP.
- Verifikasi dan status aktivasi aktual dicatat pada CORE-FOUNDATION-REPORT.json.
- Tahap lanjut terpisah: adapter jaringan terkontrol, transport streaming/MCP,
  scheduler, connector berizin, renderer chart, dan tool yang belum diimplementasi.
