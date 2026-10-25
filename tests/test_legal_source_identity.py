"""P5 deterministic regressions: identity from the actual document header, not mentions.

No live network: OfficialFetcher.fetch is patched; PDFs are generated in-process
and parsed through the real isolated pdf_source.py subprocess.
"""
import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from backend.legal_engine.sources import Sources,OfficialFetcher,citation,TTL_SECONDS
from backend.legal_engine.common import DomainError
HEADER_ITE='UNDANG-UNDANG REPUBLIK INDONESIA NOMOR 1 TAHUN 2024 TENTANG PERUBAHAN KEDUA ATAS UNDANG-UNDANG NOMOR 11 TAHUN 2008 TENTANG INFORMASI DAN TRANSAKSI ELEKTRONIK'
HEADER_KUHP='UNDANG-UNDANG REPUBLIK INDONESIA NOMOR 1 TAHUN 2023 TENTANG KITAB UNDANG-UNDANG HUKUM PIDANA'
HEADER_PDP='UNDANG-UNDANG REPUBLIK INDONESIA NOMOR 27 TAHUN 2022 TENTANG PELINDUNGAN DATA PRIBADI'
HEADER_KUHAP_NOISE='SALINAN FRESIDEN REPUEUK INDONESIA UNDANG-UNDANG REPUBLIK INDONESIA NOMOR 20 TAHUN 2025 TENTANG KITAB UNDANG-UNDANG HUKUM AC.ARA PIDANA'
HEADER_2016='UNDANG-UNDANG REPUBLIK INDONESIA NOMOR 19 TAHUN 2016 TENTANG PERUBAHAN KEDUA ATAS UNDANG-UNDANG NOMOR 11 TAHUN 2008 TENTANG INFORMASI DAN TRANSAKSI ELEKTRONIK'
TARGET_MENTION='sebagaimana diubah dengan Undang-Undang Nomor 1 Tahun 2024 tentang Informasi dan Transaksi Elektronik'
NEUTRAL='Ayat sintetis berisi kalimat contoh panjang untuk memeriksa struktur dokumen resmi secara deterministik tanpa istilah pokok terlarang.'
def _lines(header,pasals,pasal_text,filler,tail):
 out=[header,'BAB I KETENTAN UMUM']
 for n in range(1,pasals+1):out+=['Pasal %d'%n,pasal_text]
 if filler:out.append(filler)
 if tail:out.append(tail)
 return out
def html_doc(header,pasals=6,pasal_text=NEUTRAL,nav=None,filler=None,tail=None):
 body='\n'.join('<p>%s</p>'%x for x in _lines(header,pasals,pasal_text,filler,tail))
 nav='<p>%s</p>'%('</p><p>'.join(nav)) if nav else ''
 return '<html><head><title>Dokumen</title></head><body>%s%s</body></html>'%(nav,body)
def landing_html():
 toc=''.join('<li>Pasal %s</li>'%x for x in ('I','5','13','13A','16A','16B','17','18A','27','27A','27B','28','29','36','40','40A','43','45','45A','45B','II','15'))
 return ('<html><head><title>Undang-Undang Nomor 1 Tahun 2024</title>'
  '<meta name="description" content="Perubahan Kedua Atas Undang-Undang Nomor 11 Tahun 2008 tentang Informasi dan Transaksi Elektronik"/></head>'
  '<body><h1>Undang-Undang Nomor 1 Tahun 2024</h1>'
  '<p>Perubahan Kedua Atas Undang-Undang Nomor 11 Tahun 2008 tentang Informasi dan Transaksi Elektronik</p>'
  '<ul>Dalam Pasal:%s</ul>'%toc+
  '<table><tr><td>Nomor</td><td>1</td></tr><tr><td>Tahun</td><td>2024</td></tr><tr><td>Tentang</td><td>Perubahan Kedua UU ITE</td></tr></table>'
  '<p>Status: berlaku. Unduh PDF. Beranda | JDIH | Kontak</p></body></html>')
def pdf_bytes(lines):
 def esc(s):return s.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
 content='BT /F1 12 Tf 72 760 Td 16 TL '+' '.join('(%s) Tj T*'%esc(l) for l in lines)+' ET'
 objs=['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
  '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
  '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  '<< /Length %d >>\nstream\n%s\nendstream'%(len(content.encode()),content)]
 out=b'%PDF-1.4\n';offsets=[]
 for i,o in enumerate(objs,1):
  offsets.append(len(out));out+=('%d 0 obj\n'%i).encode()+o.encode()+b'\nendobj\n'
 xref=len(out);out+=('xref\n0 %d\n'%(len(objs)+1)).encode()+b'0000000000 65535 f \n'
 for off in offsets:out+=('%010d 00000 n \n'%off).encode()
 out+=('trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n'%(len(objs)+1,xref)).encode()+b'%%EOF'
 return out
def payload(raw,url):
 if isinstance(raw,str):raw=raw.encode()
 return {'status':200,'url':url,'body_bytes':raw,'text':raw.decode() if raw.startswith(b'<') else raw.decode(errors='replace'),'content_type':'text/html' if raw.startswith(b'<') else 'application/pdf'}
class SourceIdentity(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.n=0
 def tearDown(self):self.tmp.cleanup()
 def refresh(self,base_id,raw,url,store=None):
  self.n+=1;s=store or Sources(Path(self.tmp.name)/('src%d.sqlite'%self.n));self.last=s
  with patch.object(OfficialFetcher,'fetch',return_value=payload(raw,url)):return s.refresh(base_id)
 def store(self):
  return Sources(Path(self.tmp.name)/'store.sqlite')
 def test_wrong_law_mentioning_target_is_rejected(self):
  filler='Ketentuan pidana umum sintetis. '*420
  tail=TARGET_MENTION
  doc=html_doc(HEADER_2016,pasals=8,filler=filler,tail=tail)
  self.assertGreater(len(doc),9000)
  with self.assertRaises(DomainError) as e:self.refresh('uu_1_2024',doc,'https://jdih.komdigi.go.id/produk_hukum/x')
  self.assertEqual(e.exception.code,'source_identity_mismatch')
  with self.assertRaises(DomainError) as e:self.refresh('uu_1_2024',pdf_bytes(_lines(HEADER_2016,8,NEUTRAL,filler,tail)),'https://peraturan.go.id/files/x.pdf')
  self.assertEqual(e.exception.code,'source_identity_mismatch')
  unverified={'body':'Ketentuan. '+tail,'provenance':'official_live_fetch','verification':{'fetched_by_server':True,'document_identity_verified':False},'has_full_text':False,'id':'x','url':'u','title':'t','sha256':'a'*64,'fetched_at':'2026-01-01T00:00:00Z'}
  with self.assertRaises(DomainError) as e:citation(unverified,'Ketentuan.')
  self.assertEqual(e.exception.code,'unverified_source')
 def test_mismatched_header_rejected(self):
  wrong_number=html_doc('UNDANG-UNDANG REPUBLIK INDONESIA NOMOR 2 TAHUN 2024 TENTANG PERUBAHAN KEDUA ATAS UNDANG-UNDANG NOMOR 11 TAHUN 2008 TENTANG INFORMASI DAN TRANSAKSI ELEKTRONIK')
  wrong_year=html_doc('UNDANG-UNDANG REPUBLIK INDONESIA NOMOR 1 TAHUN 2014 TENTANG PERUBAHAN KEDUA ATAS UNDANG-UNDANG NOMOR 11 TAHUN 2008 TENTANG INFORMASI DAN TRANSAKSI ELEKTRONIK')
  wrong_subject=html_doc('UNDANG-UNDANG REPUBLIK INDONESIA NOMOR 1 TAHUN 2024 TENTANG ANGGARAN PENDAPATAN DAN BELANJA NEGARA')
  for doc in (wrong_number,wrong_year,wrong_subject):
   with self.assertRaises(DomainError) as e:self.refresh('uu_1_2024',doc,'https://jdih.komdigi.go.id/produk_hukum/x')
   self.assertEqual(e.exception.code,'source_identity_mismatch')
  with self.assertRaises(DomainError) as e:self.refresh('uu_1_2023',pdf_bytes(_lines(HEADER_ITE,6,NEUTRAL,None,None)),'https://jdih.bmkg.go.id/storage/x.pdf')
  self.assertEqual(e.exception.code,'source_identity_mismatch')
 def test_real_expected_pdf_header_accepted(self):
  cases=[('uu_1_2023',HEADER_KUHP,'https://jdih.bmkg.go.id/storage/common/dokumen/salinanuunomor1tahun2023.pdf'),
         ('uu_20_2025',HEADER_KUHAP_NOISE,'https://peraturan.go.id/files/uu-no-20-tahun-2025.pdf'),
         ('uu_27_2022',HEADER_PDP,'https://peraturan.bpk.go.id/Download/2722/x.pdf')]
  for base_id,header,url in cases:
   with self.subTest(base_id=base_id):
    meta=self.refresh(base_id,pdf_bytes(_lines(header,6,'Pasal ini contoh teks salinan resmi untuk uji.',None,None)),url)
    self.assertTrue(meta['has_full_text']);self.assertEqual(meta['document_form'],'pdf_full_text')
    self.assertTrue(meta['verification']['document_identity_verified']);self.assertTrue(meta['identity']['verified_from_document_header'])
    self.assertEqual(meta['identity']['method'],'official_document_header');self.assertTrue(meta['citation_allowed'])
    self.assertEqual(meta['provenance'],'official_live_fetch');self.assertEqual(meta['currentness'],'not_certified')
    self.assertNotIn('body',meta)
    source=self.last.get(meta['id']);self.assertIn('Pasal 1',source['body'])
    quote='Pasal ini contoh teks salinan resmi untuk uji.'
    cit=citation(source,quote);self.assertEqual(cit['quote'],quote);self.assertEqual(cit['source_id'],meta['id']);self.assertEqual(cit['sha256'],meta['sha256'])
    with self.assertRaises(DomainError):citation(source,'teks yang tidak pernah ada dalam dokumen')
 def test_landing_page_is_metadata_only_and_never_quoted(self):
  meta=self.refresh('uu_1_2024',landing_html(),'https://jdih.komdigi.go.id/produk_hukum/view/id/884/t/undangundang%20nomor%201%20tahun%202024')
  self.assertFalse(meta['has_full_text']);self.assertEqual(meta['document_form'],'metadata_only')
  self.assertFalse(meta['citation_allowed']);self.assertEqual(meta['text_availability'],'metadata_only_no_document_text')
  self.assertFalse(meta['verification']['document_identity_verified']);self.assertFalse(meta['identity']['verified_from_document_header'])
  self.assertEqual(meta['provenance'],'official_live_fetch');self.assertEqual(meta['currentness'],'not_certified')
  self.assertNotIn('body',meta)
  source=self.last.get(meta['id']);self.assertEqual(source['body'],'')
  self.assertFalse(self.last.search('informasi transaksi elektronik'))
  self.assertFalse(self.last.search('pasal'))
  with self.assertRaises(DomainError):citation(source,'Perubahan Kedua Atas Undang-Undang')
  rows=self.last.history('uu_1_2024');self.assertEqual(len(rows),1);self.assertNotIn('body',rows[0])
  def test_html_full_text_extracts_actual_document_main_content(self):
   doc=html_doc(HEADER_ITE,pasals=6,nav=('Beranda','Profil','Layanan','Regulasi Lainnya','Kontak'))
   store=self.store();meta=self.refresh('uu_1_2024',doc,'https://jdih.komdigi.go.id/produk_hukum/x',store=store)
   self.assertTrue(meta['has_full_text']);self.assertEqual(meta['document_form'],'html_full_text')
   source=store.get(meta['id'])
   self.assertTrue(source['body'].startswith('UNDANG-UNDANG'))
   self.assertNotIn('Beranda',source['body']);self.assertNotIn('Kontak',source['body'])
   self.assertTrue(source['verification']['document_identity_verified'])
   quote='Pasal ini contoh teks salinan resmi untuk uji.'
   self.assertEqual(citation(source,quote)['quote'],quote)
   with self.assertRaises(DomainError):citation(source,'Beranda Profil Layanan')
   found=store.search('informasi transaksi elektronik');self.assertEqual(len(found),1);self.assertEqual(found[0]['id'],meta['id'])
 def test_ttl_and_provenance_fields(self):
  self.assertEqual(TTL_SECONDS,86400)
  store=self.store()
  meta=self.refresh('uu_1_2023',pdf_bytes(_lines(HEADER_KUHP,6,'Pasal ini contoh teks salinan resmi untuk uji.',None,None)),'https://jdih.bmkg.go.id/storage/common/dokumen/salinanuunomor1tahun2023.pdf',store=store)
  self.assertEqual(meta['ttl_seconds'],TTL_SECONDS);self.assertEqual(len(meta['sha256']),64);self.assertEqual(len(meta['raw_sha256']),64)
  self.assertEqual(meta['authority'],'jdih.bmkg.go.id');self.assertTrue(meta['verification']['official_host']);self.assertTrue(meta['verification']['fetched_by_server'])
  self.assertEqual(meta['identity']['expected_number'],1);self.assertEqual(meta['identity']['expected_year'],2023)
  self.assertTrue(store.get(meta['id']));self.assertEqual(len(store.search('salinan')),1)
  with sqlite3.connect(store.path) as d:
   r=json.loads(d.execute('SELECT data FROM sources').fetchone()[0]);r['fetched_at']='2000-01-01T00:00:00Z';d.execute('UPDATE sources SET data=?',(json.dumps(r),))
  with self.assertRaises(DomainError) as e:store.get(meta['id'])
  self.assertEqual(e.exception.code,'source_refresh_required');self.assertEqual(e.exception.status,409)
  self.assertFalse(store.search('salinan'))
