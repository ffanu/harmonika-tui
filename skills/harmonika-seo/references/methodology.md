# Metodologi analisis SEO Harmonika

Dokumen ini menjelaskan dari mana setiap aturan di `scripts/seo_analyzers.py`
berasal dan, lebih penting, apa yang sengaja tidak disimpulkan. Teks spesifikasi
dan dokumentasi pihak ketiga tidak disalin ke sini; yang ditautkan adalah sumber
aslinya supaya versi terbaru selalu dibaca langsung dari penerbitnya.

## Prinsip

1. **Bukti sebelum penilaian.** Setiap finding membawa `evidence` berisi angka
   atau string yang benar-benar diamati. Bila sebuah fakta tidak terbaca, hasil
   berstatus `degraded` dan menyebutkan apa yang dilewati, bukan menebak.
2. **Deterministik.** Tanpa jam, tanpa acak, tanpa ketergantungan urutan
   iterasi. Input sama selalu memberi output identik, sehingga diff antar audit
   bermakna. Konsekuensinya, kesegaran konten tidak dinilai relatif "hari ini":
   tanggal hanya diperiksa keterbacaannya.
3. **Data remote adalah data, bukan instruksi.** HTML, XML dan `robots.txt`
   disanitasi dari karakter kontrol lalu dipotong sebelum masuk `evidence`.
   Tidak ada isinya yang dieksekusi, diikuti, atau dipakai memilih cabang logika
   selain lewat aturan di modul ini.
4. **Batas eksplisit.** Setiap limit adalah konstanta bernama di modul. Sampel
   selalu berlabel `sample`/`total`/`sample_truncated`.
5. **Tidak ada metrik karangan.** Ranking, volume, difficulty, CPC, traffic,
   klik, impresi, authority dan skor gabungan tidak ada di input, jadi tidak
   diproduksi. Ini dijaga oleh tes di `tests/test_seo_analyzers.py` yang
   memeriksa key `evidence` dan kode finding, bukan hanya prosa.

## Ekstraksi fakta HTML

`_HtmlFacts` memakai `html.parser.HTMLParser` dari pustaka standar dengan
`convert_charrefs=True`, sehingga entity didekode seperti di peramban. Teks di
dalam `script`, `style`, `noscript`, `template`, `svg`, `math`, `iframe`,
`object`, `canvas` dan `title` dikeluarkan dari teks terlihat karena pembaca
tidak melihatnya; `title` tetap ditangkap terpisah. Atribut ganda mempertahankan
nilai pertama, mengikuti perilaku peramban. Markup rusak ditoleransi: kegagalan
parse menjadi `html_parse_degraded`, bukan exception.

Jumlah kata memakai karakter kata Unicode (`[^\W_]+`) atas teks terlihat,
sehingga bahasa non-Latin tidak dihitung nol. Angka ini mendeskripsikan HTML
yang dikirim server; konten yang dirender klien tidak termasuk dan itu
dinyatakan di `limitations`.

Referensi: [HTML Standard](https://html.spec.whatwg.org/multipage/),
[`html.parser`](https://docs.python.org/3/library/html.parser.html),
[Google Search Central: dasar-dasar SEO](https://developers.google.com/search/docs/fundamentals/seo-starter-guide).

## Title, meta description, canonical, indexability

Ambang panjang (`TITLE_MIN_CHARS`..`TITLE_MAX_CHARS`,
`DESCRIPTION_MIN_CHARS`..`DESCRIPTION_MAX_CHARS`) dilaporkan sebagai
`*_length_outside_guidance`: observasi panjang, bukan prediksi ranking. Panjang
snippet yang tampil ditentukan mesin pencari dan bisa berbeda per kueri.

Canonical lintas host dilaporkan karena konsekuensinya besar, tetapi
perbandingan host dilakukan apa adanya dengan pengabaian satu awalan `www.`.
Pustaka standar tidak memuat public suffix list, sehingga eTLD+1 **tidak**
dihitung dan subdomain berbeda dilaporkan sebagai host berbeda, bukan ditebak
sama. Catatan ini disertakan dalam `evidence`.

Indexability dibaca dari `meta robots`, `meta googlebot` dan header
`X-Robots-Tag`. `seo.page_audit` tidak membaca `robots.txt`; itu tugas
`seo.technical`, dan pembagian ini disebut di `limitations` agar tidak terbaca
sebagai pemeriksaan crawlability lengkap.

Referensi:
[meta tag dan atribut](https://developers.google.com/search/docs/crawling-indexing/special-tags),
[`X-Robots-Tag` dan meta robots](https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag),
[konsolidasi URL duplikat](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls),
[public suffix list](https://publicsuffix.org/).

## JSON-LD dan structured data

Dua lapis yang berbeda dan tidak boleh dicampur:

1. **Sintaks.** `json.loads` atas isi setiap `<script type="application/ld+json">`.
   Blok yang gagal parse diabaikan total oleh konsumen, jadi ini severity
   `medium` dengan kutipan potongan.
2. **Keberadaan properti terpilih.** `REQUIRED_PROPERTIES` memetakan sebagian
   tipe ke sebagian properti yang kerap wajib. Isinya sengaja kecil dan
   dipublikasikan di `evidence.required_property_table` supaya pembaca tahu
   persis cakupannya.

Yang **tidak** diperiksa: tipe nilai, vokabulari, enumerasi, kebenaran entitas
bersarang, syarat gambar/logo, dan syarat kebijakan. Tipe di luar tabel
dilaporkan tanpa cek properti. Karena itu hasil operasi ini tidak boleh disebut
"valid schema.org" atau "lolos rich result". Kelayakan rich result ditentukan
mesin pencari dan harus dikonfirmasi dengan alat ujinya sendiri. Microdata dan
RDFa hanya dideteksi lewat atribut `itemtype`, tidak diparse.

Kedalaman dan jumlah node graph dibatasi (`MAX_JSONLD_DEPTH`,
`MAX_JSONLD_NODES`) agar dokumen bersarang dalam tidak menjadi kerja tak
terbatas.

Referensi: [schema.org](https://schema.org/),
[JSON-LD 1.1](https://www.w3.org/TR/json-ld11/),
[structured data umum](https://developers.google.com/search/docs/appearance/structured-data/intro-structured-data),
[galeri rich result dan syaratnya](https://developers.google.com/search/docs/appearance/structured-data/search-gallery),
[Rich Results Test](https://search.google.com/test/rich-results),
[Schema Markup Validator](https://validator.schema.org/).

## Sitemap

Pengerasan XML dilakukan sebelum parsing: dokumen yang memuat `<!DOCTYPE`,
`<!ENTITY`, `<!ATTLIST` atau `<!NOTATION` ditolak dengan
`sitemap_doctype_refused` dan tidak pernah diumpankan ke parser. Konstruksi itu
bukan bagian dari protokol sitemap dan merupakan jalur serangan entity
expansion serta external entity. Dengan penolakan di depan, tidak ada resolusi
DTD atau entity eksternal yang terjadi.

Rekursi hanya ke sitemap anak pada host yang sama, dibatasi
`MAX_SITEMAP_FETCHES` dokumen dan `MAX_SITEMAP_URLS` URL yang dianalisis. Entri
pada host lain didaftar sebagai bukti tetapi tidak pernah diambil. URL yang
terdaftar juga tidak diambil, sehingga status dan indexability-nya tidak
terverifikasi oleh operasi ini.

Validasi entri mengikuti protokol: `loc` absolut http(s), `lastmod` W3C/ISO
8601, `changefreq` dari enumerasi protokol, `priority` 0.0-1.0. `priority`
dilaporkan sebagai cacat format saja, disertai catatan bahwa crawler besar
menyatakan mengabaikannya. Sitemap terkompresi gzip tidak didekompresi, dan
daftar teks biasa maupun feed RSS/Atom tidak diparse.

Referensi: [protokol sitemap](https://www.sitemaps.org/protocol.html),
[sitemap di Google Search Central](https://developers.google.com/search/docs/crawling-indexing/sitemaps/overview),
[W3C datetime](https://www.w3.org/TR/NOTE-datetime),
[XML 1.0](https://www.w3.org/TR/xml/),
[kerentanan XXE (OWASP)](https://owasp.org/www-community/vulnerabilities/XML_External_Entity_(XXE)_Processing).

## robots.txt dan klasifikasi crawler

Parser mengelompokkan baris `User-agent` berurutan menjadi satu grup beserta
aturan `Allow`/`Disallow`-nya, mengumpulkan direktif `Sitemap`, dan mencatat
anomali seperti aturan sebelum `User-agent`. Pencocokan grup memilih token
paling spesifik dengan `*` sebagai fallback. `robots_blocks_root` hanya
menyatakan blokir bila ada `Disallow: /` tanpa `Allow: /` penyeimbang; ini
pemeriksaan sempit untuk akses akar, bukan evaluasi pola per path.

`CRAWLER_CLASSES` memisahkan empat konsekuensi yang sering dicampur:

| Kelas | Arti | Dampak blokir |
| --- | --- | --- |
| `search_index` | Crawl untuk indeks pencarian | Halaman bisa hilang dari mesin pencari itu |
| `ai_assistant_retrieval` | Mengambil halaman untuk menjawab pertanyaan pengguna | Mungkin tidak dikutip asisten itu; indexing pencarian tidak terpengaruh |
| `ai_training` | Mengumpulkan data untuk pelatihan model | Token training terpisah tidak otomatis mengubah token search/retrieval |
| `gemini_training_grounding` | Google-Extended: training dan grounding Gemini/Vertex tertentu | Bukan kontrol Google Search/AI Overviews/AI Mode |

Hanya blokir `search_index` yang severity `high`. Kelas lainnya `info` dan
dilaporkan sebagai fakta konfigurasi beserta field `does_not_affect`, karena
blokir crawler training adalah keputusan lisensi konten dan tidak boleh
"diperbaiki" demi visibilitas pencarian. Daftar token adalah daftar statis lokal
dengan tanggal tinjauan di modul: operator mengubah token tanpa pemberitahuan,
dan ketidakhadiran sebuah token dari daftar bukan putusan. `robots.txt`
menyatakan permintaan, bukan jaminan, sehingga penggunaan nyata sebuah halaman
untuk pelatihan tidak dapat diverifikasi dari sana.

Referensi: [RFC 9309 Robots Exclusion Protocol](https://www.rfc-editor.org/rfc/rfc9309.html),
[spesifikasi robots.txt Google](https://developers.google.com/search/docs/crawling-indexing/robots/robots_txt),
[crawler dan fetcher Google](https://developers.google.com/search/docs/crawling-indexing/overview-google-crawlers),
[Google-Extended](https://developers.google.com/search/docs/crawling-indexing/overview-google-crawlers#google-extended).

## Konten dan bukti kepengarangan

`seo.content` melaporkan jumlah kata, outline heading, tag berulang dalam satu
dokumen, serta sinyal penulis dan tanggal dari `meta`, `link[rel=author]`,
`time[datetime]` dan JSON-LD. Semuanya label bukti.

Ini **bukan** skor E-E-A-T. Experience, expertise, authoritativeness dan trust
adalah kerangka penilaian kualitas oleh manusia, bukan nilai yang bisa dihitung
dari markup satu halaman. Byline yang ada tidak membuktikan keahlian, dan
ketiadaannya tidak membuktikan sebaliknya; karena itu temuan kepengarangan
diberi severity `info`. Ambang `THIN_CONTENT_WORDS` juga `low` dan disertai
catatan bahwa sebagian halaman memang pendek secara sah.

Deteksi duplikat terbatas pada tag berulang di dalam satu dokumen. Konten
duplikat atau mirip antar halaman, nilai keterbacaan, sentimen dan cakupan
topikal tidak dihitung.

Referensi:
[membuat konten bermanfaat dan andal](https://developers.google.com/search/docs/fundamentals/creating-helpful-content),
[panduan penilai kualitas pencarian](https://guidelines.raterhub.com/searchqualityevaluatorguidelines.pdf).

## GEO (keterbacaan mesin generatif)

Heuristik yang dikumpulkan: jumlah dan kedalaman heading, item list dan tabel,
tanggal eksplisit, atribusi, tautan sumber keluar, keberadaan structured data,
dan apakah paragraf pembuka berupa kalimat mandiri (dicek regex atas kutipan
awal). Semuanya observasi struktur: konten yang tersegmentasi jelas lebih mudah
ditemukan dan dikutip dalam konteks yang benar.

Tidak ada janji, prediksi, probabilitas atau skor dikutip, diambil, diringkas
atau di-ranking oleh asisten AI mana pun. Sistem itu tertutup, berubah tanpa
pemberitahuan, dan tidak mengekspos sinyal yang bisa diukur dari satu halaman.
Istilah "GEO" di sini berarti higiene struktural, bukan kanal yang bisa
dijanjikan. Finding `geo_lead_not_self_contained` bersifat `info` karena
heuristik regex atas kutipan awal mudah salah untuk gaya tulisan tertentu.

## Bisnis lokal (NAP)

Nama, alamat dan telepon diambil dari dua sumber dengan keandalan berbeda, dan
sumbernya selalu dicatat di `evidence`. JSON-LD `LocalBusiness` dan subtipenya
dibaca apa adanya sebagai nilai yang dipublikasikan penerbit. Telepon dan alamat
dari teks terlihat adalah heuristik regex (`_PHONE`, `_STREET_HINT`,
`_POSTCODE`) yang ditandai `heuristic: true`; pola ini mengenali format Indonesia
dan internasional umum, tetapi dapat salah positif dan melewatkan format sah.
Normalisasi telepon ke digit dipakai hanya untuk mendeteksi varian nomor yang
tidak konsisten.

Google Business Profile tidak diperiksa sama sekali: kepemilikan, status
verifikasi, akurasi listing, kategori, review, foto dan ranking local pack
semuanya `*_checked: false` dengan alasan tertulis. Tidak ada kredensial atau
akses API yang dikonfigurasi, dan tidak satu pun dari itu teramati dari markup
halaman. Konsistensi NAP di direktori eksternal, cakupan area layanan, efek
kedekatan dan penempatan peta juga di luar cakupan.

Referensi: [LocalBusiness di schema.org](https://schema.org/LocalBusiness),
[structured data bisnis lokal](https://developers.google.com/search/docs/appearance/structured-data/local-business),
[bantuan Google Business Profile](https://support.google.com/business/),
[E.164](https://www.itu.int/rec/T-REC-E.164/en).

## Clustering keyword

Dua mode dengan status berbeda, dan modenya selalu terbaca di
`evidence.grouping`.

**Dengan `serp_results`.** URL dinormalisasi ke host tanpa `www.` plus path
tanpa trailing slash, lalu kemiripan Jaccard antar himpunan URL dibandingkan
terhadap `SERP_OVERLAP_THRESHOLD`. Dua keyword yang hasilnya banyak beririsan
biasanya dilayani halaman yang sama. Seluruh hasil ditandai
`user_supplied_unverified`: URL dipakai apa adanya, tidak pernah diambil,
diquery ulang atau diverifikasi, tidak diberi nilai posisi, dan bergantung
lokasi, personalisasi serta waktu penangkapan oleh pemanggil. Keyword tanpa
hasil membuat status menjadi `degraded`.

**Tanpa `serp_results`.** Status `degraded`. Keyword dinormalisasi (casefold,
NFKD, tanda baca menjadi spasi), stopword Indonesia dan Inggris dibuang, lalu
Jaccard antar himpunan token dibandingkan terhadap
`LEXICAL_OVERLAP_THRESHOLD`. Ini pengelompokan kemiripan string, **bukan**
clustering intent: ia tidak bisa tahu dua frasa berbeda melayani satu intent,
atau frasa yang nyaris sama punya intent berbeda.

Pengelompokan memakai union-find single-link atas daftar keyword terurut,
sehingga hasilnya identik tanpa bergantung urutan input. Ambang adalah pilihan
desain yang diekspos di `evidence.threshold`; ambang lain akan menghasilkan
cluster lain. Tidak ada volume, difficulty, CPC, kompetisi, posisi atau estimasi
traffic yang dilampirkan, dan tidak ada mesin pencari atau API keyword yang
dihubungi: operasi ini tidak melakukan akses jaringan sama sekali.

Referensi: [indeks Jaccard](https://en.wikipedia.org/wiki/Jaccard_index),
[normalisasi Unicode UAX #15](https://unicode.org/reports/tr15/),
[struktur data union-find](https://en.wikipedia.org/wiki/Disjoint-set_data_structure).

## Sumber data yang butuh otorisasi terpisah

Google Search Console, Google Analytics 4 dan PageSpeed Insights tidak
terintegrasi dan tidak ada kredensialnya di skill ini. Impresi, klik, posisi
rata-rata, kueri nyata, perilaku pengguna dan Core Web Vitals lapangan hanya
dapat diperoleh bila pemilik proyek mengonfigurasi akses terotorisasi secara
terpisah. Sampai itu ada, jangan menyebut sumber tersebut tersedia dan jangan
menyajikan angkanya.

Referensi (hanya untuk perancangan integrasi di masa depan):
[Search Console API](https://developers.google.com/webmaster-tools/v1/api_reference_index),
[GA4 Data API](https://developers.google.com/analytics/devguides/reporting/data/v1),
[PageSpeed Insights API](https://developers.google.com/speed/docs/insights/v5/get-started),
[CrUX](https://developer.chrome.com/docs/crux).

## Mengubah aturan

Tambahkan atau ubah aturan di fungsi `check_*` dan jaga agar setiap finding
tetap membawa `evidence` yang bisa dilacak ke fakta yang teramati. Batas baru
menjadi konstanta bernama di bagian atas modul, bukan angka di tengah logika.
Tes di `tests/test_seo_analyzers.py` memakai mock fetcher dan harus ikut
diperbarui; hasil tes fixture tidak boleh dilaporkan sebagai pengukuran situs
nyata.

## Klasifikasi operator dan sumber primer

Google-Extended adalah kontrol training **dan grounding** Gemini/Vertex tertentu,
bukan kontrol Google Search/AI Overviews/AI Mode. Karena itu backend memisahkannya
ke kelas `gemini_training_grounding`, tidak menyebut semua jawaban asisten tidak
terpengaruh. `Claude-SearchBot` dan `Claude-User` ditambahkan sebagai token terpisah
dari `ClaudeBot`. Empat kelas dipisahkan dalam hasil backend. Crawler daftar statis tidak membuktikan akses aktual.
Referensi primer diperiksa 24 Oktober 2026:
[Google AI features](https://developers.google.com/search/docs/appearance/ai-features),
[Google common crawlers](https://developers.google.com/crawling/docs/crawlers-fetchers/google-common-crawlers),
[OpenAI crawler definitions](https://developers.openai.com/api/docs/bots),
[Anthropic crawler policy](https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler).
