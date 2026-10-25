# Indeks skill pusat Harmonika

Dokumen master dibaca penuh dan diperlakukan sebagai indeks rencana, bukan bukti
1.507 implementasi siap produksi. A–Z berisi **1.203 baris /1.200 ID unik**, dengan
`rebuild.apply_diff` muncul4kali. Jumlah rincian engine1.408; Documents tidak punya
angka dan deklarasi1.507 berbeda99. Sebagian kemampuan lama memakai nama lain.
Kemiripan nama tidak dijadikan alias otomatis.

## API dan terminal

Base `https://chat.harmonika.id/v1/skills`, Bearer key Harmonika biasa.

| Method | Path | Kontrak |
|---|---|---|
| GET | `/capabilities` | Hitungan sumber/registry, readiness dan batas fitur |
| GET | `?status=available&limit=100&offset=0` | Daftar terpaginasikan, maksimal200 |
| GET | `/{skill_id}` | Schema input dan target eksekusi yang sebenarnya |
| POST | `/invoke` | `{"skill_id":"...","input":{...},"context":{...}}` |

```sh
harmonika catalog capabilities
harmonika catalog list --status available
harmonika catalog inspect static.ast.parse
printf '%s' '{"code":"answer = 42"}' | harmonika catalog invoke static.ast.parse
```

Status `available` berarti handler analisis berbasis input tersedia.
`context_required` menunjuk operasi engine terdaftar, **bukan** konektor live yang
pasti sehat. `local_only` tetap memakai worker dokumen/SEO/engineering lokal.
`domain_ambiguous` memerlukan konfirmasi engine; `unavailable` dan `restricted`
tidak pernah dibalas sebagai eksekusi sukses.

## Routing engine lama

Exact-ID coding/builder/legal/telco memakai endpoint asli beserta auth scope,
quota, ownership, hash approval, idempotency, Stop dan event job aslinya.

```json
{
  "skill_id": "scaffold.fastapi",
  "input": {"project":"demo-api"},
  "context": {"engine":"builder","project_id":"UUID_PROYEK"}
}
```

Gunakan header `Idempotency-Key` untuk operasi engine yang memerlukannya.
Coding memakai `workspace_id`; builder memakai `project_id` dan opsi
`expected_revision`/`overwrite`. Legal `case_id` tetap di `input` sesuai schema.
Tidak menerima actor, endpoint, secret atau principal dari JSON pemanggil.

Tujuh ID berbenturan makna lintas domain: `debug.attach`, `debug.breakpoint`,
`test.run`, `test.single`, `test.coverage`, `evidence.chain.custody`, dan
`evidence.store.secure`. Harus menyebut `context.engine` secara eksplisit;
operasi legal bukan implementasi alat forensic/binary umum.

## Analisis dan browser terisolasi

Analisis baru bersifat pasif pada data yang diberikan pemanggil. Hasil/heuristik
bukan putusan bahwa perangkat bersih, seseorang bot/penipu, atau pembayaran sah.
Tidak membaca akun perusahaan, mengirim pesan, mengeksekusi payload, atau
memperbarui saldo/pelanggan. Secret scanner mengembalikan temuan teredaksi.

`workflow.browser` menjalankan Chromium software di container ephemeral dari
`html` + `actions` DOM terbatas. Tidak menerima URL target/profil/login/cookie
server. Network none, rootfs read-only, tanpa host mount/GPU. Screenshot dilaporkan
sebagai hash; tidak menerbitkan gambar/URL publik atau membocorkan cookie.

```json
{"skill_id":"workflow.browser","input":{"html":"<h1>Harmonika</h1>","actions":[{"type":"dom.text","selector":"h1"}],"viewport":{"width":390,"height":844}}}
```

Payload maksimal1MiB, JSON kedalaman20, output analisis maksimal256KiB,
2 operasi serentak global/1per token,60analisis/menit per token. Deadline analisis15s,
browser40s. Worker yang melewati deadline tetap memegang slot sampai benar-benar
berhenti; timeout tidak dianggap sebagai pembatalan proses.

## Batas aktivasi

Konektor eksternal, ERP/CS perusahaan, toolchain Windows/proprietary, pemindai
aktif dan operasi lain yang belum memiliki implementasi tetap unavailable.
Tidak mengaktifkan pencurian kredensial/sesi, persistence/cover-tracks atau
stealth/bypass sebagai operasi otonom publik. Tidak ada klaim semua master telah
selesai hanya karena descriptor sudah dicatat.

Log hanya pseudonim actor, status dan durasi; tidak mencetak prompt/source,
header/credential atau hasil analisis. Native TUI dan sesi pengguna tidak diubah;
update bertanda tangan tetap manual. Lihat `MASTER-SKILLS-REPORT.json` untuk
angka hasil test/aktivasi dan gap yang masih nyata.

Parser header dibatasi pada metadata, mengacu pada
[spesifikasi PE Microsoft](https://learn.microsoft.com/en-us/windows/win32/debug/pe-format),
[header Mach-O Apple](https://github.com/apple-oss-distributions/xnu/blob/main/EXTERNAL_HEADERS/mach-o/loader.h),
dan [struktur ELF glibc](https://github.com/bminor/glibc/blob/master/elf/elf.h).
Parsing header bukan bukti file executable valid, aman, atau bebas malware.
