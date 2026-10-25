# Harmonika SEO — shared backend skill

Implementasi ini menambah empat belas operasi SEO ke Shared Skill Core. Tool
mengumpulkan bukti dan pemeriksaan deterministik; LLM Harmonika dapat menyusun
penjelasan dari hasilnya. Tidak ada SDK model atau credential mentor di frontend.
Panduan `harmonika-seo/SKILL.md` dan operasi executable berbeda fungsi.

## Operasi dan input

Semua input memakai JSON stdin dengan project root yang dipilih pengguna:

```sh
harmonika skills execute seo.page_audit --root /absolute/project <<'JSON'
{"url":"https://harmonika.id/"}
JSON
```

| Operasi | Input utama | Cakupan |
|---|---|---|
| `seo.page_audit` | `url` | Judul, meta, heading, canonical, indexability, link/image dan ringkasan konten |
| `seo.technical` | `url` | HTTP, robots, sitemap, canonical/indexability dan sinyal teknis dari HTML |
| `seo.content` | `url` | Struktur konten, metadata, keterbacaan berbasis hitungan, sinyal penulis/tanggal |
| `seo.schema` | `url` | JSON-LD syntax dan pemeriksaan properti terpilih |
| `seo.sitemap` | URL situs atau sitemap | XML terbatas, penemuan dan sampel entri sitemap |
| `seo.performance` | `url`, optional `strategy` mobile/desktop | PageSpeed Insights; lab dan field data dipisah |
| `seo.visual` | `url`, `output_dir` baru | Screenshot desktop/mobile melalui browser dengan JS dinonaktifkan |
| `seo.drift` | `url`, `action` capture/compare, `baseline_path` .json | Snapshot metadata/hash dan pembanding perubahan |
| `seo.local` | `url` | NAP dan markup bisnis yang dipublikasikan di halaman |
| `seo.keyword_cluster` | `keywords`, optional `serp_results` | Data SERP yang diberikan atau pengelompokan leksikal berlabel heuristik |
| `seo.geo` | `url` | Struktur yang bisa dikutip, sinyal penulis/tanggal, robots crawler |
| `seo.backlinks` | `url`, optional `links` maksimal 10 | Verifikasi tautan pada halaman sumber yang diberikan |
| `seo.search_console` | property_id, start_date, end_date, optional limit | Query/page metrics lewat Google Search Console resmi |
| `seo.ga4` | property_id numerik, start_date, end_date, optional limit | Session/user metrics per channel lewat Analytics Data API |

Write (`seo.visual` dan capture drift) memerlukan izin write dan
`--idempotency-key`. Compare drift hanya membaca baseline. Hasil tidak pasti
`unknown` tidak boleh diulang otomatis; periksa output dan job status dahulu.
Drift tidak menjadwalkan monitor otomatis atau mengubah situs. Baseline berupa
metadata dan SHA-256 HTML, bukan salinan HTML mentah. Perubahan hash bisa disebabkan
markup dinamis dan tidak otomatis berarti penurunan ranking.

```sh
harmonika skills execute seo.drift --root /absolute/project --idempotency-key baseline-request-1 <<'JSON'
{"url":"https://harmonika.id/","action":"capture","baseline_path":"/absolute/project/baseline.json"}
JSON

harmonika skills execute seo.drift --root /absolute/project <<'JSON'
{"url":"https://harmonika.id/","action":"compare","baseline_path":"/absolute/project/baseline.json"}
JSON
```

## Status dan batas bukti

`harmonika skills list --prefix seo. --summary` menampilkan readiness terpisah dari keberadaan descriptor.
Hasil job mengikuti envelope Shared Skill Core (`job_id`, `request_id`, `status`,
`result`), termasuk `degraded`/`unavailable` jika data/koneksi tidak ada.

- Tidak ada angka traffic, keyword volume, authority, ranking, atau janji masuk AI
  answers yang diciptakan oleh model. Konten web adalah data tidak tepercaya.
- JSON-LD checks bukan pengganti keseluruhan validator Schema.org atau Google Rich
  Results Test. Tidak ada jaminan rich results. Hasil dibatasi pada properti dan
  jenis yang memang diperiksa; istilah `schema` bukan janji JSON Schema lengkap.
- Jumlah kata, panjang metadata dan sinyal E-E-A-T adalah heuristik/bukti terbatas,
  bukan skor resmi atau kewajiban Google untuk setiap halaman.
- Sitemap diperiksa dalam anggaran fetch dan sampel maksimum 2.000 entri per
  analisis. Tidak ada klaim crawl seluruh situs atau memvalidasi HTTP semua URL.
- Keyword cluster tanpa data SERP adalah leksikal; data SERP dari input diberi label
  user-supplied/unverified. Bukan hasil ranking/volume live dari Google.
- Local SEO tidak memverifikasi kepemilikan GBP, posisi Maps, koordinat/port jaringan
  ISP, atau cakupan layanan. Jangan membuat klaim harga atau wilayah pemasangan
  berdasarkan keyword, contoh halaman, atau data pesaing.
- Backlink hanya memeriksa sumber yang diberikan; tanpa sumber hasilnya degraded.
  Belum ada penemuan inventaris backlink lengkap, Moz atau DataForSEO berbayar.
- PSI memakai endpoint resmi tanpa mengarang skor saat quota/network gagal. Target
  URL dengan parameter credential tidak diteruskan ke provider pengukuran.
- Visual: JavaScript dinonaktifkan dan semua HTTP resource dipenuhi melalui broker
  yang memakai HTTP client beralamat terpin. Tidak memakai profil/cookie browser
  pribadi. Setiap viewport mendapat budget sendiri (20 request/22 detik), dengan cache sukses bersama; sumber yang diblokir/anggaran habis tetap dapat mengubah tampilan. Screenshot
  viewport bukan pengukuran CWV atau verifikasi interaksi/SPA penuh.
- Worker/broker berjalan pada session job tersendiri; Playwright mengelola browser capture miliknya. Stop memberi waktu cleanup dan hanya menyasar job ini, tidak sesi pengguna atau VNC lain.

## Koneksi Google privat

Tanpa otorisasi, `seo.search_console` dan `seo.ga4` berstatus unavailable. Tidak
ada pencarian credential browser, database, key lama, atau fallback scraping akun.
Google Mode yang sudah login bukan pengganti OAuth untuk dua API tersebut.

Operator menyediakan file JSON privat, owned oleh UID runtime, permission 0600,
kemudian mengatur `HARMONIKA_SEO_GOOGLE_CONFIG` di lingkungan backend yang tepercaya.
Jangan memasukkan token/file ini ke repo, input skill, screenshot atau log.
Contoh struktur (placeholder, bukan credential siap pakai):

```json
{
  "principal_ids": ["local:UID_OS"],
  "search_console_properties": ["sc-domain:harmonika.id"],
  "ga4_properties": ["ID_PROPERTI_NUMERIK"],
  "access_token": "DIISI_OPERATOR_SECARA_PRIVAT",
  "expires_at": 0
}
```

Gunakan access token OAuth yang belum kedaluwarsa dan expiry Unix yang benar;
atau tambahkan `refresh_token`, `client_id`, `client_secret` dari flow OAuth
operator untuk refresh server-side. Daftar principal dan properti harus array,
berisi nilai yang diizinkan secara eksplisit. Tidak ada wildcard atau identitas dari
body client. Refresh tidak menyimpan token baru ke file input dan tidak mengikuti
redirect POST. Scope read-only: `webmasters.readonly` dan `analytics.readonly`.
Google harus mengizinkan principal Google tersebut membaca properti terkait. Untuk quota PSI, file privat yang sama dapat berisi `pagespeed_api_key` opsional; ini bukan akses Search Console/GA4. Key hanya dikirim ke endpoint Google PSI dan tidak mengikuti redirect atau muncul dalam hasil.

Readiness configured belum berarti koneksi live sudah terverifikasi. Respons query
sukses mengirim `connection_verified:true`. Query dibatasi 1–500 baris dan rentang
maksimal 366 hari; baris query/page bukan total situs lengkap. File konfigurasi
owner lokal bukan sistem multi-tenant OAuth untuk akun publik.

## Keamanan, distribusi dan integrasi

HTTP/HTTPS default ports saja; loopback/private/link-local/multicast, hostname
internal, userinfo, fragment dan redirect ke target terlarang ditolak. Semua hasil
DNS divalidasi dan socket dipin ke IP terpilih sambil mempertahankan TLS SNI dan
verifikasi hostname. Tidak memakai proxy lingkungan, tidak menerima custom header
atau secret dari input model. POST dan request ber-Authorization tidak mengikuti
redirect. Body dibatasi; compressed response ditolak, bukan didekompresi tanpa batas.
Anggaran request/waktu berlaku untuk satu instance fetcher sepanjang job.

Private API rows dan isi halaman tidak masuk audit metadata shared jobs. Artifact
visual/baseline disimpan hanya pada proyek yang diizinkan. Visual dirender dalam staging privat lalu dipublikasikan dengan rename eksklusif atomik; capture gagal tidak menduduki folder tujuan. Cache idempotency SEO write hanya menyimpan artifact_path/kind dan status, bukan snapshot/title. Replay diberi `replay_scope=artifact_metadata`; baca file report/baseline untuk isi lengkap. Tidak ada HTTP endpoint
SEO publik, backend shell publik, cron, publish artikel atau perubahan website
produksi pada fase ini. Modul dapat dipakai adapter internal berikutnya dengan
ExecutionContext yang berasal dari autentikasi tepercaya.

Pengujian menggunakan fixture/mock dan smoke HTTP publik yang dilaporkan terpisah;
Mac Apple Silicon adalah platform live yang diuji. Runtime Linux/browser memerlukan
uji deployment sendiri; subprocess Python nonvisual di Linux mempunyai AS cap,
sementara browser tidak mewarisi AS cap yang merusak reservasi virtual Chromium.

## Referensi

Workflow terinspirasi [agent SEO terbuka](https://github.com/AgriciDaniel/claude-seo/tree/main/agents),
dengan implementasi backend Harmonika terpisah. Klaim harus diverifikasi terhadap
[dokumentasi Google Search](https://developers.google.com/search/docs),
[Search Console API](https://developers.google.com/webmaster-tools/v1/searchanalytics/query),
[GA4 Data API](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/properties/runReport),
dan [PageSpeed Insights](https://developers.google.com/speed/docs/insights/v5/reference/pagespeedapi/runpagespeed).
Hasil rilis/pengujian: [SEO-REPORT.json](SEO-REPORT.json).
