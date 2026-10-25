"""Official-source provenance tests, no live network in unit suite."""
import tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from backend.legal_engine.sources import Sources,OfficialFetcher,citation
from backend.legal_engine.common import DomainError
class SourceTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.s=Sources(Path(self.tmp.name)/'sources.sqlite')
 def tearDown(self):self.tmp.cleanup()
 def payload(self,body=None,url=None):
  header='<p>UNDANG-UNDANG REPUBLIK INDONESIA NOMOR 1 TAHUN 2024 TENTANG PERUBAHAN KEDUA ATAS UNDANG-UNDANG NOMOR 11 TAHUN 2008 TENTANG INFORMASI DAN TRANSAKSI ELEKTRONIK</p>'
  pasal=''.join('<p>Pasal %d</p><p>Kewajiban contoh pasal sintetis nomor %d dengan kalimat cukup panjang untuk pengakuan struktur dokumen resmi.</p>'%(i,i) for i in range(1,7))
  raw=(body or '<html><body>'+header+pasal+'</body></html>').encode()
  return {'status':200,'url':url or 'https://jdih.komdigi.go.id/produk_hukum/x','body_bytes':raw,'text':raw.decode(),'content_type':'text/html'}
 def test_actual_fetch_identity_provenance_and_no_validity_certification(self):
  with patch.object(OfficialFetcher,'fetch',return_value=self.payload()),patch('backend.legal_engine.sources.time.monotonic',return_value=1):meta=self.s.refresh('uu_1_2024')
  source=self.s.get(meta['id']);self.assertEqual(source['provenance'],'official_live_fetch');self.assertEqual(source['currentness'],'not_certified');self.assertTrue(source['verification']['fetched_by_server']);self.assertEqual(len(self.s.search('pasal')),1)
 def test_fake_caller_metadata_cannot_attest_source(self):
  with self.assertRaises(DomainError):citation({'body':'x','provenance':'user_claim','verification':{'fetched_by_server':True}},'x')
 def test_wrong_law_or_changed_host_rejected(self):
  for payload in (self.payload('Wrong statute '*100),self.payload(url='https://example.com/x')):
   with tempfile.TemporaryDirectory() as p:
    s=Sources(Path(p)/'source.sqlite')
    with patch.object(OfficialFetcher,'fetch',return_value=payload),self.assertRaises(DomainError):s.refresh('uu_1_2024')
 def test_repeat_network_refresh_throttles(self):
  with patch.object(OfficialFetcher,'fetch',return_value=self.payload()):
   self.s.refresh('uu_1_2024')
   with self.assertRaises(DomainError) as e:self.s.refresh('uu_1_2024')
   self.assertEqual(e.exception.code,'source_refresh_throttled')
 def test_all_redirect_hosts_enforced_before_connect(self):
  with self.assertRaises(DomainError):OfficialFetcher()._open_connection('127.0.0.1',443,'example.com',True,1)
 def test_expired_cache_does_not_silently_certify_old_text(self):
  with patch.object(OfficialFetcher,'fetch',return_value=self.payload()):meta=self.s.refresh('uu_1_2024')
  import sqlite3,json
  with sqlite3.connect(self.s.path) as d:
   r=json.loads(d.execute('SELECT data FROM sources').fetchone()[0]);r['fetched_at']='2000-01-01T00:00:00Z';d.execute('UPDATE sources SET data=?',(json.dumps(r),))
  self.assertFalse(self.s.search('pasal'))
  with self.assertRaises(DomainError):self.s.get(meta['id'])
 def test_legacy_phrase_only_snapshot_is_not_quotable(self):
  with patch.object(OfficialFetcher,'fetch',return_value=self.payload()):meta=self.s.refresh('uu_1_2024')
  import sqlite3,json
  with sqlite3.connect(self.s.path) as d:
   row=json.loads(d.execute('SELECT data FROM sources').fetchone()[0]);row['verification'].pop('document_identity_verified');d.execute('UPDATE sources SET data=?',(json.dumps(row),))
  self.assertFalse(self.s.search('pasal'))
  with self.assertRaises(DomainError):self.s.get(meta['id'])
  with self.assertRaises(DomainError):citation(row,row['body'][:100])
 def test_refresh_is_serialized_for_all_entrypoints(self):
  self.s.fetch_lock.acquire()
  try:
   with self.assertRaises(DomainError) as e:self.s.refresh('uu_1_2024')
   self.assertEqual(e.exception.code,'source_capacity')
  finally:self.s.fetch_lock.release()
 def test_ocr_logo_and_separator_noise_preserves_exact_number_year(self):
  from backend.legal_engine.sources import _header_start,MANIFEST
  text='SALINAN\nPRES!DEN\nREPIJBUK INDONE!3IA\nUNDANG.UNDANG REPUBLIK INDONESI.A\nNOMOR 1 TAHUN 2024\nTENTANG INFORMASI DAN TRANSAKSI ELEKTRONIK\n'
  self.assertIsNotNone(_header_start(text,MANIFEST['uu_1_2024']))
  self.assertIsNone(_header_start(text.replace('TAHUN 2024','TAHUN 2016'),MANIFEST['uu_1_2024']))
