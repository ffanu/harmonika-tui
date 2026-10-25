#!/usr/bin/env python3
"""Operator-reviewed local API corpus. This does not certify external/legal facts."""
import datetime,hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parent.parent
now=datetime.datetime.now(datetime.timezone.utc).date().isoformat()
items=[
 ('unified-auth','Autentikasi dan isolasi token Harmonika API','Endpoint REST /v1/unified menggunakan Authorization Bearer; WebSocket juga menerima ticket sekali pakai. Identitas actor berasal dari token, bukan field body. Session dan job token lain mengembalikan not_found. Key API tidak dimasukkan ke URL.','https://chat.harmonika.id/v1/unified/capabilities'),
 ('unified-jobs','Status job, Stop, recovery, idempotency','POST /v1/unified/jobs menerima messages dan session_id opsional serta Idempotency-Key. Status queued, running, completed, failed, stopped, recovery_required. Key sama dengan payload sama mengembalikan job yang sama. Stop terminal. Restart tidak otomatis mengulang job. Job disimpan24jam; session7hari.','https://chat.harmonika.id/v1/unified/capabilities'),
 ('unified-streams','GraphQL dan WebSocket API Harmonika','POST /v1/unified/graphql adalah GraphQL read-only untuk capability, skill, job, session, events dan knowledge. WSS /v1/unified/ws memakai Bearer atau ticket satu kali dari POST /v1/unified/ticket. Frame hello, ping, pong, resume; koneksi hanya membaca status/event. Job dibuat lewat REST. Tidak ada delta token teks pada stream job ini.','https://chat.harmonika.id/v1/unified/capabilities'),
 ('unified-knowledge','Provenance dan batas knowledge','Knowledge awal berisi kontrak API Harmonika yang disetujui operator, dengan SHA256 isi dan verified_at. Retrieval BM25 mengembalikan source dan hash, bukan sertifikat kebenaran hukum, harga, pembayaran atau coverage. Input caller verified=true bukan kewenangan memverifikasi sumber. Tidak ada training otomatis dari history.','https://chat.harmonika.id/v1/unified/capabilities'),
 ('unified-secrets','Secret connector privat per token','shared.secrets menyimpan nilai connector privat per token dalam AES-GCM. Metadata tidak mengembalikan nilai atau ciphertext. Write membutuhkan secrets:write dan metadata secrets:read. Ini tidak membuka env/key provider/root server.','https://chat.harmonika.id/v1/unified/capabilities'),
 ('shared-core','Shared core data dan memory Harmonika','GET /v1/core/capabilities menunjukkan pengolahan CSV/JSON terbatas, memory per token dan workflow DAG. Data.visualize menghasilkan spesifikasi Vega-Lite, bukan PNG yang dirender. Memory memiliki TTL dan quota. Workflow tidak menjalankan shell atau network.','https://chat.harmonika.id/v1/core/capabilities')]
docs=[{'id':id,'title':title,'text':text,'source':source,'sha256':hashlib.sha256(text.encode()).hexdigest(),'verified_at':now} for id,title,text,source in items]
(root/'backend/unified_engine/verified_corpus.json').write_text(json.dumps({'documents':docs},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'documents':len(docs),'scope':'operator_verified_harmonika_api_contract_only','external_facts_certified':False}))
