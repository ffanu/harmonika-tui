# Kontrak kalkulasi ERP Harmonika

Semua operasi di bawah menerima data yang disediakan pemanggil. Hasil menyertakan
`status: analyzed_input`, `posted: false`, dan `live_database_access: false`.
Tidak ada reservasi stok, penagihan, transfer bank, perubahan jaringan, atau posting
keuangan yang dilakukan oleh kalkulasi ini. Input maksimal mengikuti gateway64KB.

```json
{"skill_id":"erp_all.isp.billing.cycle","input":{"data":{"start":"2028-01-31","frequency":"monthly","count":3}}}
```

Kirim ke `POST https://chat.harmonika.id/v1/skills/invoke` memakai token berizin.
Terminal: JSON stdin → `harmonika erp analyze <operation>`.

| Operasi | Kontrak input | Batas/aturan |
|---|---|---|
| `isp.billing.cycle` | start YYYY-MM-DD, frequency monthly/daily, count | Maks120 tanggal; tanggal31 kembali ke31 setelah Februari |
| `mfg.bom`, `mfg.mrp` | bom[{item,components:[{item,quantity}]}], demand[{item,quantity}], stock[{item,quantity}] | Integer unit;500 node; tolak siklus; net kebutuhan setelah stok barang jadi/komponen |
| `mfg.production.costing` | materials[{quantity,unit_minor}], labor_minor, overhead_minor, units | Nominal string minor unit; sisa pembagian dinyatakan eksplisit |
| `inventory.stock.opname` | rows[{item,recorded,counted}] | Hitung delta; tidak memposting penyesuaian |
| `inventory.batch.tracking` | as_of, required, batches[{id,expiry,quantity}] | FEFO; batch kedaluwarsa pada/sebelum tanggal evaluasi tidak dialokasikan |
| `procurement.three.way.match` | purchase_order[], receipt[], invoice[]; setiap baris{item,quantity,unit_minor,currency} | Pencocokan penuh tanpa toleransi; kuantitas/harga/mata uang harus sama; tidak mengotorisasi pembayaran |
| `retail.promotion` | subtotal_minor, discount_bps, cap_minor | Satu diskon, half-up, dibatasi cap; tanpa pajak asumsi |
| `pos.cash.management` | opening_minor, cash_in_minor, cash_out_minor, counted_minor | Saldo diharapkan tidak negatif; selisih hitung−diharapkan |
| `pm.time.tracking` | entries[{resource,start,end}] | Timestamp dengan zona waktu dan detik utuh; interval maksimal24jam; overlap per resource ditolak |

Sepuluh operasi keuangan/stok sebelumnya tetap tersedia: invoice, jurnal, buku
besar, stock management, rekonsiliasi exact-match, penyusutan, anggaran, kurs rasional,
umur piutang, dan umur utang. Tidak ada angka pajak atau harga pasar yang diasumsikan.

## Runtime sumber ERP yang dihasilkan

Generator menyediakan API ISP tenant-scoped terpisah dari kalkulasi di atas:

- `GET/POST /isp/subscriptions`: customerId, productId, startMonth, optional endMonth,
  taxBps terverifikasi, billingPolicy wajib `full_month`. Harga produk disalin saat dibuat.
- `POST /isp/subscriptions/:id/bill`: period YYYY-MM dan confirmed:true; finance/admin.
  Kunci unik tenant+subscription+period mencegah invoice ganda walau key request berbeda.
- `POST /isp/subscriptions/:id/state`: revision, status ACTIVE/PAUSED/CANCELLED, confirmed:true.
  CANCELLED tidak dapat diaktifkan kembali. Perubahan hanya billing internal.
- `GET/POST /isp/tickets`: customerId, subject, description, priority normal/high/urgent.
- `POST /isp/tickets/:id/state`: revision, status, optional assignedUserId, confirmed:true.
  OPEN→IN_PROGRESS→RESOLVED→CLOSED; RESOLVED dapat kembali IN_PROGRESS. Assignee harus
  user aktif pada tenant yang sama. Tiket tertutup tidak dibuka ulang secara diam-diam.

Seluruh perubahan memerlukan sesi/role, CSRF untuk cookie, dan Idempotency-Key.
Endpoint list mendukung page1..1000 dan limit1..100; format response tetap array.
Pencarian q tersedia untuk pelanggan (nama/email) dan produk (nama/SKU).
Katalog akun dan periode tetap memiliki batas tersendiri. Dropdown referensi masih
memuat halaman pertama; pencarian referensi lintas halaman merupakan backlog UI.

Belum ada scheduler billing otomatis, prorata, speed/quota plan, provisioning
RADIUS/OLT, verifikasi bank, atau integrasi tiket dengan sistem CS yang sudah ada.
Tidak ada deployment instance ERP bisnis nyata dalam pengujian ini.

## Perencanaan manufaktur tambahan

- `mfg.routing`: operations[{id,duration_minutes,predecessors[]}]. Memvalidasi DAG
  dan menghitung waktu paling awal/jalur dependensi, tanpa batas mesin.
- `mfg.production.scheduling`: jobs[{id,duration_minutes,predecessors[],work_center,
  ready_minute}], work_centers[{id,available_minute}]. Jadwal serial per mesin,
  urutan ID deterministik; maksimal500 operasi dan horizon10^9 menit. Tidak ada
  klaim jadwal optimal, kalender libur, pergantian shift, atau eksekusi order.
- `mfg.quality.control`: inspected,accepted,rejected,criteria[{id,unit,value_scaled,
  minimum_scaled,maximum_scaled}]. Angka pengukuran integer berskala; rentang inklusif.
  Tidak lulus bila ada barang ditolak atau kriteria gagal. Bukan sertifikasi.
- `mfg.scrap.management`: issued_quantity,good_quantity,scrap_quantity. Menghasilkan
  WIP tersisa dan yield dalam basis poin; jumlah baik+scrap tidak boleh melebihi issued.

Penanganan konflik transaksi mengacu pada kode error, bukan teks query atau pesan:
[PostgreSQL16](https://www.postgresql.org/docs/16/mvcc-serialization-failure-handling.html),
[Prisma](https://docs.prisma.io/docs/orm/reference/error-reference). Hasil uji nyata
menangkap DriverAdapterError dengan cause.kind TransactionWriteConflict, yang kini
dikembalikan sebagai409. Diagnosis sementara hanya mencatat kelas/kode mekanis,
tidak payload; instrumentation diagnosis telah dilepas dari sumber rilis.

## Kalkulator struk tunai

`pos.transaction` menerima lines[{unit_minor,quantity}], tax_bps dan tendered_minor.
Nominal adalah string integer minor-unit. Kalkulator menolak uang kurang dan
menghasilkan total/kembalian. Harga dan uang merupakan input pemanggil; tidak
memverifikasi penerimaan tunai, bank, atau mencatat penjualan (`sale_recorded:false`).
API kasir pada source ERP terpisah menggunakan harga produk server, shift milik
kasir, konfirmasi manusia dan transaksi stok/kas yang atomik.

Pengaman retry POS: fingerprint idempotency terikat pada actorId. Konflik DB
berstatus409; error infrastruktur tak dikenal berstatus500, bukan422. Frontend
mempertahankan kunci request pada500/network error. Uji browser menyuntikkan
500 pada percobaan pertama, lalu memastikan retry konfirmasi memakai kunci sama.
Hanya error validasi bertipe khusus yang boleh diklasifikasikan sebagai422.

## CRM dan proyek/jasa

- `crm.pipeline`: opportunities[{id,stage NEW/QUALIFIED/WON/LOST,value_minor,
  probability_bps}]. Probabilitas eksplisit pemanggil, bukan hasil prediksi; WON
  harus10000 dan LOST0. Weighted minor dihitung half-up per opportunity.
- `crm.satisfaction.survey`: csat[] skor1..5 (positif4..5), nps[] skor0..10
  (promoter9..10, detractor0..6). Statistik basis poin/dua desimal; tidak menebak CS.
- `pm.project.billing`: entries[{id,seconds,rate_minor_per_hour}],tax_bps. Tarif
  eksplisit, durasi≤86400 detik per baris, half-up per entri; tidak membuat invoice.
- `pm.gantt.chart`: operations[{id,duration_minutes,predecessors[]}],maks100.
  Menghasilkan SVG escaped/validated dengan jadwal relatif dependency-only; tidak
  mempertimbangkan kapasitas mesin, kalender, atau mengubah jadwal proyek.

Perubahan ownership/penugasan pada source runtime wajib dicek sebelum cache
idempotency. Uji nyata memindahkan lead/tugas lalu memastikan pemilik lama tidak
bisa mengambil kembali catatan melalui replay kunci request yang sebelumnya sah.


### Project controls (supplied data only)

- `pm.resource.allocation`: `{resources:[{id,capacity_units}],assignments:[{id,
  resource_id,start_minute,end_minute,units}]}`. Sweep over relative-minute half-open
  intervals; return peak, overcapacity segments and unit-minutes. Does not assign staff.
- `pm.risk.management`: `{risks:[{id,probability_bps,impact_minor}]}`. Explicit probability
  0..10000, integer minor money; half-up exposure sorted descending. No correlations,
  prediction, inferred probability or reserve posting.
- `pm.project.portfolio`: `{projects:[{id,status,budget_minor,actual_cost_minor,
  completion_bps}]}`. States PLANNED/ACTIVE/COMPLETED/CANCELLED; planned progress0,
  completed10000. Exact integer totals/variance, per-project rounded earned value;
  budget-weighted progress uses unrounded numerator. Zero budget => null weighted progress.

### Generated project billing API

`work/projects/:id/billing-preview` and `work/projects/:id/bills` are generated
instance routes, not pure skill-gateway database mutations. Review generated README
for exact contract, finance role, revision/previewHash/idempotency and fiscal gates.
Project rates/date/tax/entry choice must be supplied; no automatic invoicing or GL.

- `pm.milestone`: `{as_of,tasks:[{id,status}],milestones:[{id,status,due_on,task_ids}]}`.
  Task states TODO/IN_PROGRESS/BLOCKED/DONE/CANCELLED. Milestone states PENDING/REACHED/
  CANCELLED. Explicit nonempty distinct known task IDs; REACHED with incomplete tasks
  is invalid. Return eligibility, completion and overdue (strictly due<as_of), no writes.
- `pm.issue.tracking`: `{as_of,issues:[{id,status,priority,opened_on,resolved_on,closed_on}]}`.
  Dates ISO calendar; unused dates must be null. OPEN/IN_PROGRESS unresolved; RESOLVED
  requires resolution date and no closure; CLOSED requires both in chronological order.
  No future dates. Return calendar-day metrics and counts, no inferred dates/notifications.


### Supplied posted-journal financial reporting

The following five operations share a validated ledger calculation and return the
requested view: `finance.financial.statement`, `report.financial.statement` (full
balance/P&L/cash/account totals); `finance.cashflow.statement`, `report.cash.flow`
(cash view); `report.profit.loss` (P&L view).

Input: `{period:"YYYY-MM",currency:"IDR",accounts:[{code,type,is_cash}],journals:[{id,
period,status:"POSTED",cashflow_category,lines:[{account_code,debit_minor,credit_minor}]}]}`.
Account type asset/liability/equity/revenue/expense; cash only explicit asset account.
Each nonzero line has exactly one positive side; each journal balances independently.
Categories operating/investing/financing/unclassified/internal_transfer. The latter
requires all lines cash accounts and net0. All money is nonnegative decimal minor
strings at input, signed strings at output. One caller-declared currency, no FX.

Up to200 accounts,1000 journals,100 lines/journal,10000 total lines; duplicate account/
journal IDs, missing account references, DRAFT/unbalanced journals and numeric limit
violations rejected. Report includes periods<=selected month, with current-month
profit/flows and earlier-period cash opening. Original and reversal remain separate
posted entries. Unclassified cash movements require classification even if they
net to0. This is a calculation on supplied journals, not independent verification of
posting, source completeness, accounting policy, compliance or business DB state.

Every supplied line counts against processing bounds, including future-excluded
journals. Empty activity is valid but explicitly marked no_posted_activity_supplied.
Zero balance/cash equations are arithmetic self-checks, not proof of source
completeness or bank reconciliation (both verification flags remain false).
Unclassified cash-to-cash movements also require explicit classification despite
net0; only a caller-declared and validated internal_transfer has that label.


### HR calculations — explicit inputs only

All six return caller_supplied_data_only, business_data_updated:false,
disbursement_sent:false and compliance_verified:false. No evidence verification,
statutory law, tax/BPJS or operational payroll execution.

- `hr.attendance`: `{punches:[{id,employee,check_in,check_out}]}` max1000. Real calendar
  timestamps use T + seconds, optional zero fraction (max6 digits), explicit Z/offset
  up to14:00. Whole-second duration >0..86400; overlap per employee rejected; adjacent
  intervals allowed. No breaks, shifts or payable overtime inferred.
- `hr.leave.management`: `{balances:[{employee,leave_type,granted_units,taken_units}],
  requests:[{id,employee,leave_type,units}]}` max500 each. Explicit integer units, not
  calendar days. Recorded remainder and remaining_units_after_requests are distinct;
  requests simulate sequential deductions, not approvals/reservations. No overdraw.
- `hr.payroll`: `{employees:[{id,earnings:[{component,amount_minor}],deductions:[...]}]}`
  max500 employees,100 per component side,10000 total components. Positive/zero decimal
  minor strings, no floats. Explicit sums, deductions<=gross; no tax/rate inference.
- `hr.time.overtime`: `{days:[{id,employee,date,regular_minutes,worked_minutes,
  overtime_rate_minor_per_hour}]}` max1000; minutes0..1440, unique employee/date.
  max(0,worked-regular) × supplied rate /60, per-day half-up. Not a legal wage calculation.
- `hr.expense.claim`: `{policy:[{category,cap_minor}],claims:[{id,employee,category,
  amount_minor}]}` max100 policies/1000 claims. Cap is PER CLAIM; capped/excess amounts
  are estimates, not approval. Receipt authenticity and reimbursement remain unverified.
- `hr.loan.management`: `{loans:[{id,employee,principal_minor,charges_minor,
  payments:[{id,amount_minor}],installments}]}` max500 loans,1000 payments/loan,
  10000 total payments. IDs for payment allocations unique across all supplied loans;
  no overpayment. Installments0 means no allocation;1..1200 generates exact remainder
  allocation, max2000 total schedule rows and no installment below one minor unit.
  Charges are supplied, never inferred interest. This does not schedule deductions.

HR results have a180000-byte bound; large requests must be split. Invalid Unicode
identifiers, bool-as-int, duplicate identifiers and amount overflow are rejected.

### Real XLSX export

`report.export.excel` takes `{title,headers,rows}`. Title/header strings1..100,1..30
unique headers; max1000 data rows/10000 cells; every cell text<=1000 characters,
max131072 UTF8 text bytes. Final OOXML<=1MiB uncompressed and180000 bytes zipped.
Output has base64 XLSX, MIME, SHA256 and counts, with formulas/external links disabled.
Headers freeze and autofilter apply. Explicit text preserves leading zeroes and
large identifiers; numbers are not inferred or calculated. The CLI verifies SHA,
fixed package members, XML safety and internal relationships before saving0600.
Actual openpyxl read verified text/formula behavior, header freeze and filter range.


## Procurement sourcing

`procurement.rfq`: `{as_of,currency,required:[{item,quantity}],quotes:[{vendor,
submitted_on,valid_until,tax_bps,shipping_minor,discount_minor,shipping_taxable,
lines:[{item,unit_minor}]}]}`. Minor amounts are decimal integer strings. Exact
unique basket required; at most200 items and100 vendors. Shipping taxability must
be explicit. Goods discount is deducted before tax; shipping enters the tax base
only when shipping_taxable=true. Inclusive validity dates, no future submissions,
no FX. All lowest-cost ties remain visible. No automatic award or verified evidence.

`procurement.vendor.performance`: `{as_of,weights:[{metric,weight_bps}],records:
[{id,vendor,ordered_on,promised_on,delivered_on,ordered_quantity,delivered_quantity,
accepted_quantity,rejected_quantity}]}`. Require on_time_delivery,quantity_accuracy,
quality_acceptance with weights totaling10000. At most1000 records. Missing delivery
uses null and zero delivery/inspection quantities. Delivered sample only is scored;
zero delivered means score=null. Open/overdue orders and sample coverage are exposed
separately. Dates/quantities/IDs are validated, no fabricated grade or supplier action.
